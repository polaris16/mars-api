"""[新增] 线下预约、履约状态和权益次数流水。"""

import hmac
import json

from sqlalchemy import select

from app.models import (
    BenefitBalance,
    BenefitGrant,
    BenefitOrderEvent,
    BenefitPackageItem,
    BenefitServiceOrder,
    BenefitServiceVersion,
    BenefitStaff,
    MemorySession,
    User,
)
from app.schemas.benefits import BenefitOrderSort
from app.services.benefit_common import (
    BenefitError,
    BenefitKeys,
    active_row,
    conflict,
    display,
    flush_unique,
    forbidden,
    missing,
    new_row,
    now_utc,
    paginate,
    stamp,
    utc,
)

ORDER_FIELDS = (
    "id balance_id status version memory_session_id assignee_user_id scheduled_start_at "
    "scheduled_end_at reserved_at started_at finished_at result_ref terminal_reason created_at"
)
EVENT_FIELDS = "id order_version action from_status to_status reserved_delta used_delta event_data"
ACTION_STATES = {
    "confirm_booking": {"requested"},
    "reschedule": {"reserved"},
    "start": {"reserved"},
    "complete": {"in_progress"},
    "cancel": {"requested", "reserved", "in_progress"},
    "fail": {"requested", "reserved", "in_progress"},
}


class BenefitOrderService:
    @staticmethod
    def order_statement():
        return (
            select(BenefitServiceOrder)
            .join(BenefitBalance, BenefitBalance.id == BenefitServiceOrder.balance_id)
            .join(BenefitGrant, BenefitGrant.id == BenefitBalance.grant_id)
            .where(
                BenefitServiceOrder.is_del.is_(False),
                BenefitBalance.is_del.is_(False),
                BenefitGrant.is_del.is_(False),
                BenefitGrant.status == "claimed",
            )
        )

    @staticmethod
    def view(row, balance, *, private=False):
        view = {
            **display(row, ORDER_FIELDS),
            "request_data": row.request_data,
            "remaining_count": balance.granted_count - balance.reserved_count - balance.used_count,
        }
        if private and row.private_data_ciphertext is not None:
            clear = BenefitKeys().open(
                row.private_data_key_version, "order-private", row.private_data_ciphertext
            )
            view["contact"] = json.loads(clear)
        return view

    @staticmethod
    def eligibility(balance, grant, now, *, check_quota=True):
        if not utc(grant.valid_from) <= now < utc(grant.valid_until):
            raise conflict("benefit_not_active", "权益不在可用时段")
        if check_quota and balance.reserved_count + balance.used_count >= balance.granted_count:
            raise conflict("benefit_quota_exhausted", "权益已无可用次数")

    @staticmethod
    def record_event(
        ctx, row, idem, version, digest, action, previous, reserved_delta=0, used_delta=0, data=None
    ):
        event = new_row(
            BenefitOrderEvent,
            ctx.actor_id,
            service_order_id=row.id,
            action_key=idem,
            request_digest=digest,
            digest_key_version=version,
            action=action,
            from_status=previous,
            to_status=row.status,
            order_version=row.version,
            reserved_delta=reserved_delta,
            used_delta=used_delta,
            event_data=data or {},
        )
        ctx.db.add(event)
        flush_unique(ctx.db)
        return event

    def create(self, ctx, balance_id, idem, body):
        keys = BenefitKeys()
        values = body.model_dump(mode="json")
        with ctx.db.begin():
            # 同一申请人跨余额并发重放也使用统一的幂等范围。
            active_row(ctx.db, User, ctx.actor_id, lock=True)
            balance = active_row(ctx.db, BenefitBalance, balance_id, lock=True)
            grant = active_row(ctx.db, BenefitGrant, balance.grant_id)
            if grant.status != "claimed" or grant.recipient_user_id != ctx.actor_id:
                raise missing()
            submitted = {"balance_id": balance_id, **values}
            previous = ctx.db.scalar(
                select(BenefitServiceOrder).where(
                    BenefitServiceOrder.requested_by_user_id == ctx.actor_id,
                    BenefitServiceOrder.idempotency_key == idem,
                )
            )
            if previous is not None:
                digest = keys.fingerprint(previous.digest_key_version, "order-create", submitted)
                if not hmac.compare_digest(digest, previous.request_digest):
                    raise conflict("idempotency_conflict", "该幂等键已用于不同服务申请")
                return self.view(previous, balance, private=True), False
            now = now_utc()
            self.eligibility(balance, grant, now)
            item = active_row(ctx.db, BenefitPackageItem, balance.package_item_id)
            service = active_row(ctx.db, BenefitServiceVersion, item.service_version_id)
            if service.fulfillment_mode != "appointment":
                raise conflict("benefit_fulfillment_unavailable", "该服务的线上履约尚未接入")
            if body.preferred_start_at is not None and not (
                max(now, utc(grant.valid_from))
                <= body.preferred_start_at
                < body.preferred_end_at
                <= utc(grant.valid_until)
            ):
                raise BenefitError(422, "benefit_appointment_window", "期望时段须在权益有效期内")
            if body.memory_session_id is not None:
                memory = active_row(ctx.db, MemorySession, body.memory_session_id)
                if memory.owner_user_id != ctx.actor_id:
                    raise missing()
            version = keys.current
            private = {
                key: values[key] for key in ("contact_phone", "address") if values[key] is not None
            }
            digest = keys.fingerprint(version, "order-create", submitted)
            row = new_row(
                BenefitServiceOrder,
                ctx.actor_id,
                balance_id=balance_id,
                requested_by_user_id=ctx.actor_id,
                idempotency_key=idem,
                request_digest=digest,
                digest_key_version=version,
                status="requested",
                memory_session_id=body.memory_session_id,
                private_data_ciphertext=keys.seal(version, "order-private", json.dumps(private))
                if private
                else None,
                private_data_key_version=version if private else None,
                request_data={
                    key: values[key]
                    for key in ("preferred_start_at", "preferred_end_at")
                    if values[key] is not None
                },
            )
            ctx.db.add(row)
            flush_unique(ctx.db)
            self.record_event(ctx, row, idem, version, digest, "create", None)
            return self.view(row, balance, private=True), True

    @staticmethod
    def access_scope(ctx, stmt, *, fulfill):
        if not fulfill:
            return stmt.where(BenefitGrant.recipient_user_id == ctx.actor_id), set()
        # 派单入口只管理预约式履约，线上作品留在用户的权益记录。
        appointment = (
            select(BenefitPackageItem.id)
            .join(
                BenefitServiceVersion,
                BenefitServiceVersion.id == BenefitPackageItem.service_version_id,
            )
            .where(BenefitServiceVersion.fulfillment_mode == "appointment")
        )
        stmt = stmt.where(BenefitBalance.package_item_id.in_(appointment))
        roles = ctx.platform_roles()
        if "dispatcher" in roles:
            return stmt, roles
        if "provider" not in roles:
            raise forbidden()
        return stmt.where(BenefitServiceOrder.assignee_user_id == ctx.actor_id), roles

    def orders(
        self,
        ctx,
        page,
        size,
        *,
        fulfill=False,
        grant_id=None,
        assignee=None,
        status=None,
        start_from=None,
        start_before=None,
        order_id=None,
        sort: BenefitOrderSort = "default",
    ):
        with ctx.db.begin():
            stmt, _ = self.access_scope(ctx, self.order_statement(), fulfill=fulfill)
            if order_id is not None:
                row = ctx.db.scalar(stmt.where(BenefitServiceOrder.id == order_id))
                if row is None:
                    raise missing()
                return self.view(row, ctx.db.get(BenefitBalance, row.balance_id), private=True)
            if grant_id is not None:
                stmt = stmt.where(BenefitGrant.id == grant_id)
            if assignee is not None:
                stmt = stmt.where(BenefitServiceOrder.assignee_user_id == assignee)
            if status is not None:
                stmt = stmt.where(BenefitServiceOrder.status == status)
            if start_from is not None:
                stmt = stmt.where(BenefitServiceOrder.scheduled_start_at >= start_from)
            if start_before is not None:
                stmt = stmt.where(BenefitServiceOrder.scheduled_start_at < start_before)
            # 预约列表将尚未分配预约时间的订单放在最后。
            order = BenefitServiceOrder
            ordering = {
                "default": (order.id.desc(),),
                "created_at_asc": (order.created_at.asc(), order.id.desc()),
                "created_at_desc": (order.created_at.desc(), order.id.desc()),
                "scheduled_start_at_asc": (
                    order.scheduled_start_at.asc().nulls_last(),
                    order.id.desc(),
                ),
                "scheduled_start_at_desc": (
                    order.scheduled_start_at.desc().nulls_last(),
                    order.id.desc(),
                ),
            }[sort]
            return paginate(
                ctx.db,
                stmt.order_by(*ordering),
                page,
                size,
                lambda row: self.view(row, ctx.db.get(BenefitBalance, row.balance_id)),
            )

    @staticmethod
    def lock_providers(db, old_id, new_id):
        staff = db.scalars(
            select(BenefitStaff)
            .where(
                BenefitStaff.user_id.in_(set(filter(None, (old_id, new_id)))),
                BenefitStaff.role == "provider",
            )
            .order_by(BenefitStaff.user_id)
            .with_for_update()
        ).all()
        return {row.user_id for row in staff if row.status == "active" and not row.is_del}

    @staticmethod
    def booking(db, row, grant, body, now):
        if (
            not max(now, utc(grant.valid_from))
            <= body.scheduled_start_at
            < body.scheduled_end_at
            <= utc(grant.valid_until)
        ):
            raise BenefitError(422, "benefit_appointment_window", "正式预约须在权益有效期内")
        found = db.scalar(
            select(BenefitServiceOrder.id)
            .where(
                BenefitServiceOrder.id != row.id,
                BenefitServiceOrder.assignee_user_id == body.assignee_user_id,
                BenefitServiceOrder.status.in_(("reserved", "in_progress")),
                BenefitServiceOrder.is_del.is_(False),
                BenefitServiceOrder.scheduled_start_at < body.scheduled_end_at,
                BenefitServiceOrder.scheduled_end_at > body.scheduled_start_at,
            )
            .limit(1)
        )
        if found is not None:
            raise conflict("appointment_slot_unavailable", "此时段已为该履约人员安排服务")
        row.assignee_user_id = body.assignee_user_id
        row.scheduled_start_at = body.scheduled_start_at
        row.scheduled_end_at = body.scheduled_end_at

    def action(self, ctx, order_id, idem, body, *, fulfill):
        keys = BenefitKeys()
        action = body.action if fulfill else "cancel"
        submitted = {"action": action, "actor_id": ctx.actor_id, **body.model_dump(mode="json")}
        with ctx.db.begin():
            stmt, roles = self.access_scope(ctx, self.order_statement(), fulfill=fulfill)
            stmt = stmt.where(BenefitServiceOrder.id == order_id)
            original = ctx.db.execute(
                stmt.with_only_columns(
                    BenefitServiceOrder.balance_id, BenefitServiceOrder.assignee_user_id
                )
            ).one_or_none()
            if original is None:
                raise missing()
            if action in {"confirm_booking", "reschedule"}:
                if "dispatcher" not in roles:
                    raise forbidden()
                providers = self.lock_providers(
                    ctx.db, original.assignee_user_id, body.assignee_user_id
                )
            elif fulfill and action in {"start", "complete"} and "provider" not in roles:
                raise forbidden()
            balance = active_row(ctx.db, BenefitBalance, original.balance_id, lock=True)
            row = ctx.db.scalar(
                stmt.with_for_update(of=BenefitServiceOrder).execution_options(
                    populate_existing=True
                )
            )
            if row is None:
                raise missing()
            if action in {"start", "complete"} and row.assignee_user_id != ctx.actor_id:
                raise forbidden()
            previous_event = ctx.db.scalar(
                select(BenefitOrderEvent).where(
                    BenefitOrderEvent.service_order_id == row.id,
                    BenefitOrderEvent.action_key == idem,
                )
            )
            if previous_event is not None:
                digest = keys.fingerprint(
                    previous_event.digest_key_version, "order-action", submitted
                )
                if not hmac.compare_digest(digest, previous_event.request_digest):
                    raise conflict("idempotency_conflict", "该幂等键已用于不同服务动作")
                return {
                    "order": self.view(row, balance),
                    "event": display(previous_event, EVENT_FIELDS),
                }
            if (
                row.version != body.expected_version
                or row.assignee_user_id != original.assignee_user_id
            ):
                raise conflict("version_conflict", "服务单已变更，请刷新")
            if row.status not in ACTION_STATES[action] or (
                not fulfill and row.status not in {"requested", "reserved"}
            ):
                raise conflict("order_state_conflict", "当前服务单状态不支持该操作")
            grant = active_row(ctx.db, BenefitGrant, balance.grant_id)
            now = now_utc()
            before = row.status
            reserved_delta = used_delta = 0
            event_data = {}
            if action in {"confirm_booking", "reschedule"}:
                if body.assignee_user_id not in providers:
                    raise conflict("benefit_provider_unavailable", "该履约人员当前不可安排")
                active_row(ctx.db, User, body.assignee_user_id)
                self.eligibility(balance, grant, now, check_quota=action == "confirm_booking")
                event_data["previous"] = self.schedule_data(row)
                self.booking(ctx.db, row, grant, body, now)
                event_data["current"] = self.schedule_data(row)
                if action == "confirm_booking":
                    row.status, row.reserved_at, reserved_delta = "reserved", now, 1
            elif action == "start":
                if row.scheduled_start_at is None or not utc(row.scheduled_start_at) <= now < utc(
                    row.scheduled_end_at
                ):
                    raise conflict("benefit_appointment_window", "尚未到达或已超出正式预约时段")
                row.status, row.started_at = "in_progress", now
            elif action == "complete":
                row.status, row.finished_at, row.result_ref = "completed", now, body.result_ref
                reserved_delta, used_delta = -1, 1
                event_data["result_ref"] = body.result_ref
            else:
                row.status, row.finished_at = ("failed" if action == "fail" else "cancelled"), now
                row.terminal_reason = body.reason
                event_data["reason"] = body.reason
                reserved_delta = -1 if before in {"reserved", "in_progress"} else 0
            if reserved_delta or used_delta:
                balance.reserved_count += reserved_delta
                balance.used_count += used_delta
                stamp(balance, ctx.actor_id)
            row.version += 1
            stamp(row, ctx.actor_id)
            event = self.record_event(
                ctx,
                row,
                idem,
                keys.current,
                keys.fingerprint(keys.current, "order-action", submitted),
                action,
                before,
                reserved_delta,
                used_delta,
                event_data,
            )
            return {"order": self.view(row, balance), "event": display(event, EVENT_FIELDS)}

    @staticmethod
    def schedule_data(row):
        return {
            "assignee_user_id": row.assignee_user_id,
            "scheduled_start_at": row.scheduled_start_at.isoformat()
            if row.scheduled_start_at
            else None,
            "scheduled_end_at": row.scheduled_end_at.isoformat() if row.scheduled_end_at else None,
        }
