"""[新增] 企业发放、用户领取以及权益展示。"""

import hmac
import re
import secrets
from collections import defaultdict
from threading import Lock
from time import monotonic

from sqlalchemy import func, or_, select

from app.models import (
    BenefitBalance,
    BenefitGrant,
    BenefitIssueRequest,
    BenefitPackage,
    BenefitPackageItem,
    BenefitPurchaseBatch,
    BenefitService,
    BenefitServiceVersion,
    Enterprise,
)
from app.schemas.benefits import BenefitInvitationSort, BenefitNameSort, BenefitPlanSort
from app.services.benefit_catalog import (
    ENTERPRISE_FIELDS,
    BenefitCatalogService,
    contacts,
    purchase_grants,
    purchase_view,
    read_document,
    scoped_purchase,
)
from app.services.benefit_common import (
    BenefitError,
    BenefitKeys,
    active_row,
    conflict,
    display,
    flush_unique,
    missing,
    new_row,
    normalize_phone,
    now_utc,
    page_data,
    paginate,
    stamp,
    utc,
)

CARD_STATES = ("not_started", "expired", "exhausted", "reserved_only", "available")
_claim_attempts: dict[int, tuple[float, int]] = {}
_claim_rate_lock = Lock()


# 发放列表和个人邀请使用一致的截止时间排序。
def _grant_order(sort: BenefitInvitationSort):
    grant = BenefitGrant
    return {
        "default": (grant.id.desc(),),
        "expires_at_asc": (grant.claim_expires_at.asc(), grant.id.desc()),
        "expires_at_desc": (grant.claim_expires_at.desc(), grant.id.desc()),
    }[sort]


def limit_claim(actor_id):
    """按进程和账号限制短时领码尝试，规划中的多实例限流由入口统一执行。"""
    window = monotonic() // 60
    with _claim_rate_lock:
        if len(_claim_attempts) >= 4096 and actor_id not in _claim_attempts:
            for key, (old_window, _) in tuple(_claim_attempts.items()):
                if old_window < window:
                    del _claim_attempts[key]
            if len(_claim_attempts) >= 4096:
                raise BenefitError(429, "benefit_rate_limited", "请求过多，请稍后再试")
        previous, count = _claim_attempts.get(actor_id, (window, 0))
        count = count + 1 if previous == window else 1
        _claim_attempts[actor_id] = (window, count)
        if count > 20:
            raise BenefitError(429, "benefit_rate_limited", "请求过多，请稍后再试")


def grant_state(row, now):
    return "expired" if row.status == "issued" and utc(row.claim_expires_at) <= now else row.status


def balance_state(start, end, granted, reserved, used, now):
    if now < utc(start):
        return "not_started"
    if now >= utc(end):
        return "expired"
    if used == granted:
        return "exhausted"
    return "reserved_only" if used + reserved == granted else "available"


def claim_unavailable():
    return conflict("benefit_claim_unavailable", "该权益不可领取")


class BenefitGrantService:
    @staticmethod
    def issue_response(db, request, keys, now):
        rows = db.scalars(
            select(BenefitGrant)
            .where(BenefitGrant.issue_request_id == request.id, BenefitGrant.is_del.is_(False))
            .order_by(BenefitGrant.item_no)
        )
        items = []
        for row in rows:
            state = grant_state(row, now)
            code = (
                keys.open(row.crypto_key_version, "delivery", row.delivery_ciphertext)
                if (state == "issued" and row.delivery_ciphertext is not None)
                else None
            )
            items.append(
                {
                    "grant_id": row.id,
                    "code": code,
                    "status": state,
                    "claim_expires_at": row.claim_expires_at,
                }
            )
        return {"issue_request_id": request.id, "items": items}

    def issue(self, ctx, enterprise_id, batch_id, idem, body):
        keys = BenefitKeys()
        with ctx.db.begin():
            ctx.enterprise(enterprise_id, "admin", "issuer", lock=True)
            batch = scoped_purchase(ctx.db, batch_id, enterprise_id, lock=True)
            now = now_utc()
            values = [
                (
                    normalize_phone(item.recipient_phone),
                    utc(item.claim_expires_at or batch.claim_until),
                )
                for item in body.items
            ]
            submitted = {"items": [(phone, expires_at.isoformat()) for phone, expires_at in values]}
            previous = ctx.db.scalar(
                select(BenefitIssueRequest).where(
                    BenefitIssueRequest.purchase_batch_id == batch_id,
                    BenefitIssueRequest.issued_by_user_id == ctx.actor_id,
                    BenefitIssueRequest.idempotency_key == idem,
                )
            )
            if previous:
                digest = keys.fingerprint(previous.digest_key_version, "issue", submitted)
                if not hmac.compare_digest(digest, previous.request_digest):
                    raise conflict("idempotency_conflict", "该幂等键已用于不同请求")
                return self.issue_response(ctx.db, previous, keys, now), False
            if batch.status != "active" or now >= utc(batch.issue_until):
                raise conflict("benefit_issue_closed", "采购批次当前不可发放")
            if any(not now < expiry <= utc(batch.claim_until) for _, expiry in values):
                raise BenefitError(422, "benefit_claim_window", "领取截止时间超出批次范围")
            if purchase_view(ctx.db, batch)["available_units"] < len(values):
                raise conflict("purchase_quota_exhausted", "采购剩余份数不足")
            version = keys.current
            request = new_row(
                BenefitIssueRequest,
                ctx.actor_id,
                purchase_batch_id=batch.id,
                issued_by_user_id=ctx.actor_id,
                idempotency_key=idem,
                digest_key_version=version,
                request_digest=keys.fingerprint(version, "issue", submitted),
            )
            ctx.db.add(request)
            flush_unique(ctx.db)
            for position, (phone, expiry) in enumerate(values, 1):
                code = f"{version}-{secrets.token_urlsafe(18)}"
                ctx.db.add(
                    new_row(
                        BenefitGrant,
                        ctx.actor_id,
                        issue_request_id=request.id,
                        item_no=position,
                        crypto_key_version=version,
                        code_digest=keys.digest(version, "code", code),
                        delivery_ciphertext=keys.seal(version, "delivery", code),
                        recipient_phone_digest=keys.digest(version, "phone", phone),
                        recipient_phone_last4=phone[-4:],
                        claim_expires_at=expiry,
                    )
                )
            flush_unique(ctx.db)
            return self.issue_response(ctx.db, request, keys, now), True

    def issued_grants(
        self,
        ctx,
        enterprise_id,
        batch_id,
        status,
        page,
        size,
        *,
        sort: BenefitInvitationSort = "default",
    ):
        with ctx.db.begin():
            ctx.enterprise(enterprise_id, "admin", "issuer")
            scoped_purchase(ctx.db, batch_id, enterprise_id)
            now = now_utc()
            stmt = purchase_grants(batch_id).where(BenefitGrant.is_del.is_(False))
            if status == "expired":
                stmt = stmt.where(
                    or_(
                        BenefitGrant.status == "expired",
                        (BenefitGrant.status == "issued") & (BenefitGrant.claim_expires_at <= now),
                    )
                )
            elif status is not None:
                stmt = stmt.where(BenefitGrant.status == status)
                if status == "issued":
                    stmt = stmt.where(BenefitGrant.claim_expires_at > now)
            return paginate(
                ctx.db,
                stmt.order_by(*_grant_order(sort)),
                page,
                size,
                lambda row: {
                    "grant_id": row.id,
                    "phone_mask": f"***{row.recipient_phone_last4}",
                    "claim_expires_at": row.claim_expires_at,
                    "status": grant_state(row, now),
                },
            )

    def void(self, ctx, enterprise_id, grant_id, body):
        with ctx.db.begin():
            ctx.enterprise(enterprise_id, "admin", "issuer", lock=True)
            grant = active_row(ctx.db, BenefitGrant, grant_id)
            request = active_row(ctx.db, BenefitIssueRequest, grant.issue_request_id)
            scoped_purchase(ctx.db, request.purchase_batch_id, enterprise_id, lock=True)
            grant = active_row(ctx.db, BenefitGrant, grant_id, lock=True)
            state = grant_state(grant, now_utc())
            if state == "issued":
                grant.status = "voided"
                grant.void_reason = body.reason
                grant.delivery_ciphertext = None
                stamp(grant, ctx.actor_id)
            elif state != "voided":
                raise conflict("benefit_grant_not_issued", "仅有效且未领取的权益可作废")
            return {"grant_id": grant.id, "status": grant.status}

    @staticmethod
    def grant_sources():
        return (
            select(BenefitGrant, BenefitPurchaseBatch, BenefitPackage, Enterprise)
            .join(BenefitIssueRequest, BenefitIssueRequest.id == BenefitGrant.issue_request_id)
            .join(
                BenefitPurchaseBatch,
                BenefitPurchaseBatch.id == BenefitIssueRequest.purchase_batch_id,
            )
            .join(BenefitPackage, BenefitPackage.id == BenefitPurchaseBatch.package_id)
            .join(Enterprise, Enterprise.id == BenefitPurchaseBatch.enterprise_id)
            .where(BenefitGrant.is_del.is_(False))
        )

    @staticmethod
    def invitation_filter(ctx, keys, now):
        phone = ctx.phone()
        return (
            BenefitGrant.status == "issued",
            BenefitGrant.claim_expires_at > now,
            or_(
                *[
                    (BenefitGrant.crypto_key_version == version)
                    & (BenefitGrant.recipient_phone_digest == keys.digest(version, "phone", phone))
                    for version in sorted(keys.keys)
                ]
            ),
        )

    def invitations(self, ctx, page, size, *, sort: BenefitInvitationSort = "default"):
        keys = BenefitKeys()
        with ctx.db.begin():
            stmt = self.grant_sources().where(*self.invitation_filter(ctx, keys, now_utc()))
            total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = ctx.db.execute(
                stmt.order_by(*_grant_order(sort)).offset((page - 1) * size).limit(size)
            )
            items = []
            for grant, _, package, enterprise in rows:
                items.append(
                    {
                        "grant_id": grant.id,
                        "enterprise": display(enterprise, ENTERPRISE_FIELDS),
                        "plan_name": package.title,
                        "claim_expires_at": grant.claim_expires_at,
                        "items": [
                            display(item, "service_version_id quota_per_unit icon_key")
                            for item in BenefitCatalogService.package_items(ctx.db, package.id)
                        ],
                    }
                )
            return page_data(items, page, size, total)

    def claim(self, ctx, body):
        limit_claim(ctx.actor_id)
        keys = BenefitKeys()
        with ctx.db.begin():
            stmt = select(BenefitGrant).where(BenefitGrant.is_del.is_(False))
            if body.code is not None:
                match = re.fullmatch(r"([1-9][0-9]{0,4})-[A-Za-z0-9_-]{24}", body.code)
                if not match or int(match[1]) not in keys.keys:
                    raise claim_unavailable()
                version = int(match[1])
                stmt = stmt.where(
                    BenefitGrant.crypto_key_version == version,
                    BenefitGrant.code_digest == keys.digest(version, "code", body.code),
                )
            else:
                stmt = stmt.where(BenefitGrant.id == body.grant_id)
            row = ctx.db.scalar(stmt)
            if row is None:
                raise claim_unavailable()
            request = active_row(ctx.db, BenefitIssueRequest, row.issue_request_id)
            batch = active_row(ctx.db, BenefitPurchaseBatch, request.purchase_batch_id, lock=True)
            # 先锁定采购批次，再锁发放记录，并重新读取锁定后的状态。
            row = ctx.db.scalar(stmt.with_for_update().execution_options(populate_existing=True))
            if row is None:
                raise claim_unavailable()
            if row.status == "claimed" and row.recipient_user_id == ctx.actor_id:
                return self.grant_detail(ctx.db, ctx.actor_id, row.id), False
            now = now_utc()
            if grant_state(row, now) != "issued" or not hmac.compare_digest(
                row.recipient_phone_digest,
                keys.digest(row.crypto_key_version, "phone", ctx.phone()),
            ):
                raise claim_unavailable()
            row.status = "claimed"
            row.claimed_at = now
            row.recipient_user_id = ctx.actor_id
            row.valid_from = batch.service_valid_from
            row.valid_until = batch.service_valid_until
            row.delivery_ciphertext = None
            stamp(row, ctx.actor_id)
            for item in BenefitCatalogService.package_items(ctx.db, batch.package_id):
                ctx.db.add(
                    new_row(
                        BenefitBalance,
                        ctx.actor_id,
                        grant_id=row.id,
                        package_item_id=item.id,
                        granted_count=item.quota_per_unit,
                    )
                )
            flush_unique(ctx.db)
            return self.grant_detail(ctx.db, ctx.actor_id, row.id), True

    @staticmethod
    def card_rows(db, grant_ids):
        return db.execute(
            select(BenefitBalance, BenefitPackageItem, BenefitServiceVersion, BenefitService)
            .join(BenefitPackageItem, BenefitPackageItem.id == BenefitBalance.package_item_id)
            .join(
                BenefitServiceVersion,
                BenefitServiceVersion.id == BenefitPackageItem.service_version_id,
            )
            .join(BenefitService, BenefitService.id == BenefitServiceVersion.service_id)
            .where(BenefitBalance.grant_id.in_(grant_ids), BenefitBalance.is_del.is_(False))
            .order_by(BenefitPackageItem.sort_order, BenefitBalance.id)
        )

    @staticmethod
    def card(grant, batch, balance, item, version, service, now):
        available = balance.granted_count - balance.reserved_count - balance.used_count
        state = balance_state(
            grant.valid_from,
            grant.valid_until,
            balance.granted_count,
            balance.reserved_count,
            balance.used_count,
            now,
        )
        return {
            "balance_id": balance.id,
            "available_balance_id": balance.id if state == "available" else None,
            "service_code": service.code,
            "service_version_id": version.id,
            "icon_key": item.icon_key,
            **display(
                version,
                "name summary description_ref terms_ref fulfillment_mode online_handler "
                "appointment_handler online_duration_minutes offline_duration_minutes",
            ),
            **display(grant, "valid_from valid_until"),
            "display_timezone": batch.display_timezone,
            **display(balance, "granted_count reserved_count used_count"),
            "remaining_count": available,
            "status": state,
        }

    def grant_detail(self, db, actor_id, grant_id):
        row = db.execute(
            self.grant_sources().where(
                BenefitGrant.id == grant_id,
                BenefitGrant.status == "claimed",
                BenefitGrant.recipient_user_id == actor_id,
            )
        ).one_or_none()
        if row is None:
            raise missing()
        grant, batch, package, enterprise = row
        now = now_utc()
        return {
            "grant_id": grant.id,
            "plan_name": package.title,
            "package_id": package.id,
            "enterprise": display(enterprise, ENTERPRISE_FIELDS),
            **display(grant, "claimed_at valid_from valid_until"),
            "display_timezone": batch.display_timezone,
            "contacts": contacts(db, batch.id),
            "cards": [self.card(grant, batch, *row, now) for row in self.card_rows(db, [grant.id])],
        }

    def detail(self, ctx, grant_id):
        with ctx.db.begin():
            return self.grant_detail(ctx.db, ctx.actor_id, grant_id)

    def grouped_plans(self, db, actor_id, enterprise_id=None):
        stmt = self.grant_sources().where(
            BenefitGrant.recipient_user_id == actor_id,
            BenefitGrant.status == "claimed",
        )
        if enterprise_id is not None:
            stmt = stmt.where(Enterprise.id == enterprise_id)
        grants = db.execute(stmt.order_by(BenefitGrant.claimed_at, BenefitGrant.id)).all()
        by_id = {
            grant.id: (grant, batch, package, enterprise)
            for grant, batch, package, enterprise in grants
        }
        by_grant = defaultdict(list)
        for row in self.card_rows(db, list(by_id)):
            by_grant[row[0].grant_id].append(row)
        groups = {}
        now = now_utc()
        for grant, batch, package, enterprise in grants:
            plan, cards, batches = groups.setdefault(
                (enterprise.id, package.id),
                (
                    {
                        "enterprise": display(enterprise, ENTERPRISE_FIELDS),
                        "package_id": package.id,
                        "plan_name": package.title,
                        "icon_url": package.icon_url,
                        "grant_ids": [],
                    },
                    {},
                    set(),
                ),
            )
            plan["grant_ids"].append(grant.id)
            batches.add(batch.id)
            for row in by_grant[grant.id]:
                item = row[1]
                card = self.card(grant, batch, *row, now)
                key = tuple(
                    card[name]
                    for name in (
                        "name",
                        "service_version_id",
                        "description_ref",
                        "terms_ref",
                        "valid_from",
                        "valid_until",
                        "display_timezone",
                    )
                )
                if key not in cards:
                    card.pop("balance_id")
                    card["sort_order"] = item.sort_order
                    cards[key] = card
                else:
                    merged = cards[key]
                    for field in (
                        "granted_count",
                        "reserved_count",
                        "used_count",
                        "remaining_count",
                    ):
                        merged[field] += card[field]
                    if merged["available_balance_id"] is None:
                        merged["available_balance_id"] = card["available_balance_id"]
                    merged["status"] = balance_state(
                        grant.valid_from,
                        grant.valid_until,
                        merged["granted_count"],
                        merged["reserved_count"],
                        merged["used_count"],
                        now,
                    )
        planned = []
        for plan, cards, batches in groups.values():
            plan["cards"] = sorted(cards.values(), key=lambda card: card["sort_order"])
            # 同一客户经理可能在该计划的多个采购批次出现，按成员 ID 去重。
            by_contact = {}
            for batch_id in sorted(batches):
                by_contact.update({row["id"]: row for row in contacts(db, batch_id)})
            plan["contacts"] = list(by_contact.values())
            planned.append(plan)
        return sorted(planned, key=lambda plan: (plan["enterprise"]["id"], plan["package_id"]))

    def plans(self, ctx, enterprise_id, page, size, *, sort: BenefitPlanSort = "default"):
        with ctx.db.begin():
            plans = self.grouped_plans(ctx.db, ctx.actor_id, enterprise_id)
            # 先合并同一计划的卡片，再排序和切页。同名沿用原始顺序。
            if sort != "default":
                plans.sort(
                    key=lambda plan: plan["plan_name"].casefold(), reverse=sort == "title_desc"
                )
            return page_data(plans[(page - 1) * size : page * size], page, size, len(plans))

    def enterprises(
        self,
        ctx,
        page,
        size,
        *,
        sort: BenefitNameSort = "default",
        paginate_results: bool = True,
    ):
        with ctx.db.begin():
            summaries = {}
            for plan in self.grouped_plans(ctx.db, ctx.actor_id):
                org = plan["enterprise"]
                summary = summaries.setdefault(
                    org["id"],
                    {
                        **org,
                        "plan_count": 0,
                        "card_count": 0,
                        "status_counts": dict.fromkeys(CARD_STATES, 0),
                    },
                )
                summary["plan_count"] += 1
                summary["card_count"] += len(plan["cards"])
                for card in plan["cards"]:
                    summary["status_counts"][card["status"]] += 1
            rows = list(summaries.values())
            # 权益页的企业标签可读全量摘要，保留计划和卡片的计数。
            if sort != "default":
                rows.sort(
                    key=lambda row: row["display_name"].casefold(), reverse=sort == "name_desc"
                )
            if not paginate_results:
                return page_data(rows, 1, len(rows), len(rows))
            return page_data(rows[(page - 1) * size : page * size], page, size, len(rows))

    def document(self, ctx, version_id, document_type, storage):
        with ctx.db.begin():
            source = (
                self.grant_sources()
                .join(BenefitPackageItem, BenefitPackageItem.package_id == BenefitPackage.id)
                .where(
                    BenefitPackageItem.service_version_id == version_id,
                    BenefitPackageItem.is_del.is_(False),
                )
            )
            owned = source.where(
                BenefitGrant.status == "claimed", BenefitGrant.recipient_user_id == ctx.actor_id
            )
            if ctx.db.execute(owned.limit(1)).first() is None:
                invited = source.where(*self.invitation_filter(ctx, BenefitKeys(), now_utc()))
                if ctx.db.execute(invited.limit(1)).first() is None:
                    raise missing()
            version = active_row(ctx.db, BenefitServiceVersion, version_id)
            ref = version.description_ref if document_type == "description" else version.terms_ref
        return {
            "service_version_id": version_id,
            "document_type": document_type,
            "content": read_document(storage, ref),
        }
