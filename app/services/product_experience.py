"""[新增] 管理体验轮次、预览额度以及正式作品的权益核销。"""

import hashlib
import hmac
import json
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.models import (
    BenefitBalance,
    BenefitGrant,
    BenefitOrderEvent,
    BenefitPackageItem,
    BenefitServiceOrder,
    BenefitServiceVersion,
    MediaAsset,
    ProductExperiencePreview,
    ProductExperienceResult,
    ProductExperienceRound,
    ProductVersion,
    User,
)
from app.services.benefit_common import (
    BenefitError,
    BenefitKeys,
    active_row,
    conflict,
    flush_unique,
    missing,
    new_row,
    now_utc,
    stamp,
    utc,
)
from app.services.benefit_orders import BenefitOrderService
from app.services.product_catalog import product_images, public_products
from app.services.product_media import (
    CONFIRMED,
    GENERATED_IMAGE,
    PERSON_IMAGE,
    RUNNING,
    authorize_upload,
    image_link,
    owned_image,
)


def require_provider() -> str:
    provider = get_settings().product_experience_provider
    if provider == "disabled":
        raise BenefitError(503, "product_provider_unavailable", "产品体验生成服务尚未启用")
    return provider


def balance_for(ctx, balance_id, *, lock=False):
    query = (
        select(BenefitBalance, BenefitGrant)
        .join(BenefitGrant, BenefitGrant.id == BenefitBalance.grant_id)
        .where(
            BenefitBalance.id == balance_id,
            BenefitBalance.is_del.is_(False),
            BenefitGrant.is_del.is_(False),
            BenefitGrant.status == "claimed",
            BenefitGrant.recipient_user_id == ctx.actor_id,
        )
    )
    if lock:
        query = query.with_for_update(of=BenefitBalance).execution_options(populate_existing=True)
    result = ctx.db.execute(query).one_or_none()
    if result is None:
        raise missing()
    balance, grant = result
    item = active_row(ctx.db, BenefitPackageItem, balance.package_item_id)
    version = active_row(ctx.db, BenefitServiceVersion, item.service_version_id)
    if version.fulfillment_mode != "online" or version.online_handler != "product_experience":
        raise conflict("benefit_type_mismatch", "该权益不支持产品体验")
    return balance, grant


def require_quota(balance, grant, now):
    if utc(grant.valid_until) <= now:
        raise conflict("benefit_expired", "体验权益已过期")
    BenefitOrderService.eligibility(balance, grant, now)


def get_round(ctx, round_id, *, lock=False):
    model = ProductExperienceRound
    query = select(model).where(
        model.id == round_id, model.owner_user_id == ctx.actor_id, model.is_del.is_(False)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = ctx.db.scalar(query)
    if row is None:
        raise missing()
    return row


def round_status(row, now):
    if row.status == "open" and utc(row.expires_at) <= now:
        return "expired"
    return row.status


def require_open(row, now):
    status = round_status(row, now)
    if status == "expired":
        raise conflict("benefit_expired", "体验轮次已过期")
    if status != "open":
        raise conflict("round_already_confirmed", "本轮已确认作品")


def round_view(row, now) -> dict:
    status = round_status(row, now)
    remaining = (
        row.preview_limit - row.preview_used - row.preview_reserved if status == "open" else 0
    )
    return {
        "round_id": row.id,
        "status": status,
        "expires_at": utc(row.expires_at),
        "preview_limit": row.preview_limit,
        "preview_remaining": remaining,
    }


def close_round(db, row, status, now, actor=None) -> None:
    """轮次关闭使旧 Worker 租约失效，并释放所有未完成的预览名额。"""
    db.execute(
        update(ProductExperiencePreview)
        .where(
            ProductExperiencePreview.round_id == row.id,
            ProductExperiencePreview.status.in_(RUNNING),
        )
        .values(
            status="cancelled",
            finished_at=now,
            next_attempt_at=None,
            lease_token=None,
            lease_until=None,
            error_code="round_closed",
            updated_by=actor,
            updated_at=now,
        )
    )
    # 保留已经确认的图片，其他成功预览在轮次关闭后进入清理期。
    previews = select(ProductExperiencePreview.preview_asset_id).where(
        ProductExperiencePreview.round_id == row.id
    )
    outputs = select(ProductExperiencePreview.output_asset_id).where(
        ProductExperiencePreview.round_id == row.id
    )
    db.execute(
        update(MediaAsset)
        .where(
            MediaAsset.purpose == GENERATED_IMAGE,
            MediaAsset.purge_after.is_not(None),
            or_(MediaAsset.id.in_(previews), MediaAsset.id.in_(outputs)),
        )
        .values(purge_after=now + timedelta(hours=1))
    )
    row.status, row.closed_at, row.preview_reserved = status, now, 0
    row.updated_at, row.updated_by = now, actor


def preview_view(round_row, preview, now, *, storage=None, asset=None):
    view = {
        "preview_id": preview.id,
        "status": preview.status,
        "preview_remaining": round_view(round_row, now)["preview_remaining"],
        "error_code": preview.error_code,
    }
    if asset is not None and round_status(round_row, now) == "open":
        link = image_link(asset, storage)
        link["preview_url"] = link.pop("image_url")
        view.update(link)
    return view


class ProductExperienceService:
    def open_round(self, ctx, balance_id):
        with ctx.db.begin():
            balance, grant = balance_for(ctx, balance_id, lock=True)
            now = now_utc()
            row = ctx.db.scalar(
                select(ProductExperienceRound).where(
                    ProductExperienceRound.balance_id == balance_id,
                    ProductExperienceRound.status == "open",
                )
            )
            if row is not None:
                if row.is_del or row.owner_user_id != ctx.actor_id:
                    raise missing()
                require_open(row, now)
                return round_view(row, now), False
            require_quota(balance, grant, now)
            row = new_row(
                ProductExperienceRound,
                ctx.actor_id,
                owner_user_id=ctx.actor_id,
                balance_id=balance.id,
                expires_at=utc(grant.valid_until),
            )
            ctx.db.add(row)
            flush_unique(ctx.db)
            return round_view(row, now), True

    def detail(self, ctx, storage, round_id):
        with ctx.db.begin():
            row = get_round(ctx, round_id)
            now = now_utc()
            view = round_view(row, now)
            view["previews"] = []
            view["result_id"] = None
            if view["status"] == "completed":
                view["result_id"] = ctx.db.scalar(
                    select(ProductExperienceResult.id).where(
                        ProductExperienceResult.round_id == row.id,
                        ProductExperienceResult.is_del.is_(False),
                    )
                )
            elif view["status"] == "open":
                records = ctx.db.execute(
                    select(ProductExperiencePreview, MediaAsset)
                    .outerjoin(
                        MediaAsset, MediaAsset.id == ProductExperiencePreview.preview_asset_id
                    )
                    .where(
                        ProductExperiencePreview.round_id == row.id,
                        ProductExperiencePreview.status.in_((*RUNNING, "succeeded")),
                        ProductExperiencePreview.is_del.is_(False),
                    )
                    .order_by(ProductExperiencePreview.id)
                )
                view["previews"] = [
                    preview_view(row, preview, now, storage=storage, asset=asset)
                    for preview, asset in records
                ]
            return view

    def photo_authorization(self, ctx, storage, body):
        with ctx.db.begin():
            row = get_round(ctx, body.round_id)
            now = now_utc()
            require_open(row, now)
            require_quota(*balance_for(ctx, row.balance_id), now)
            return authorize_upload(ctx, storage, body, PERSON_IMAGE, round_row=row)

    def submit(self, ctx, round_id, idem, body):
        values = body.model_dump()
        digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        with ctx.db.begin():
            row = get_round(ctx, round_id, lock=True)
            now = now_utc()
            previous = ctx.db.scalar(
                select(ProductExperiencePreview).where(
                    ProductExperiencePreview.round_id == row.id,
                    ProductExperiencePreview.idempotency_key == idem,
                )
            )
            if previous is not None:
                if not hmac.compare_digest(previous.request_fingerprint, digest):
                    raise conflict("idempotency_conflict", "该幂等键已用于其他预览")
                return preview_view(row, previous, now), False
            require_open(row, now)
            require_quota(*balance_for(ctx, row.balance_id), now)
            if row.preview_limit <= row.preview_used + row.preview_reserved:
                raise BenefitError(
                    409,
                    "preview_quota_exhausted",
                    "本轮预览次数已用完",
                    extra={"preview_remaining": 0},
                )
            provider = require_provider()
            photo = owned_image(ctx, body.photo_id, PERSON_IMAGE, lock=True)
            if photo.upload_status not in CONFIRMED:
                raise conflict("photo_not_ready", "请先确认人物照片上传")
            version = ctx.db.scalar(
                public_products().where(ProductVersion.product_id == body.product_id)
            )
            if version is None:
                raise missing()
            if version.id != body.product_version_id:
                raise conflict("product_version_changed", "产品已换版，请刷新后重试")
            if body.mode not in version.generation_modes:
                raise conflict("product_mode_unavailable", "该产品不支持所选生成方式")
            references = [
                asset.id
                for link, asset in product_images(ctx.db, [version.id])[version.id]
                if link.is_reference
            ]
            if not references:
                raise conflict("product_image_not_ready", "产品参考图不可用")
            preview = new_row(
                ProductExperiencePreview,
                ctx.actor_id,
                round_id=row.id,
                source_asset_id=photo.id,
                product_version_id=version.id,
                mode=body.mode,
                provider_code=provider,
                model_code="mock-v1",
                idempotency_key=idem,
                request_fingerprint=digest,
                max_attempts=2,
                next_attempt_at=now,
                generation_options={
                    "reference_asset_ids": references,
                    "preserve_pose_and_scene": True,
                },
            )
            row.preview_reserved += 1
            stamp(row, ctx.actor_id)
            ctx.db.add(preview)
            flush_unique(ctx.db)
            return preview_view(row, preview, now), True

    def preview(self, ctx, storage, round_id, preview_id):
        with ctx.db.begin():
            row = get_round(ctx, round_id)
            preview = self._preview(ctx, row.id, preview_id)
            asset = (
                ctx.db.get(MediaAsset, preview.preview_asset_id)
                if preview.preview_asset_id
                else None
            )
            return preview_view(row, preview, now_utc(), storage=storage, asset=asset)

    @staticmethod
    def _preview(ctx, round_id, preview_id):
        preview = active_row(ctx.db, ProductExperiencePreview, preview_id)
        if preview.round_id != round_id:
            raise missing()
        return preview

    def confirm(self, ctx, round_id, idem, body):
        keys = BenefitKeys()
        submitted = {
            "kind": "product_experience",
            "round_id": round_id,
            "preview_id": body.preview_id,
        }
        with ctx.db.begin():
            start = get_round(ctx, round_id)
            # 与权益服务单使用相同的用户和余额加锁顺序。
            active_row(ctx.db, User, ctx.actor_id, lock=True)
            balance, grant = balance_for(ctx, start.balance_id, lock=True)
            row = get_round(ctx, round_id, lock=True)
            previous = ctx.db.scalar(
                select(BenefitServiceOrder).where(
                    BenefitServiceOrder.requested_by_user_id == ctx.actor_id,
                    BenefitServiceOrder.idempotency_key == idem,
                )
            )
            if previous is not None:
                digest = keys.fingerprint(previous.digest_key_version, "order-create", submitted)
                if not hmac.compare_digest(previous.request_digest, digest):
                    raise conflict("idempotency_conflict", "该幂等键已用于其他服务申请")
            result = ctx.db.scalar(
                select(ProductExperienceResult).where(ProductExperienceResult.round_id == row.id)
            )
            if result is not None:
                if result.preview_id != body.preview_id or (
                    previous and previous.id != result.service_order_id
                ):
                    raise conflict("round_already_confirmed", "本轮已确认其他作品")
                return self._receipt(ctx, result), False
            if previous is not None:
                raise conflict("round_already_confirmed", "该确认记录不可重新使用")
            now = now_utc()
            require_open(row, now)
            require_quota(balance, grant, now)
            preview = self._preview(ctx, row.id, body.preview_id)
            if preview.status != "succeeded":
                raise conflict("preview_not_ready", "仅能确认生成成功的预览")
            for asset_id in sorted({preview.preview_asset_id, preview.output_asset_id}):
                asset = owned_image(ctx, asset_id, GENERATED_IMAGE, lock=True)
                if asset.upload_status not in CONFIRMED:
                    raise conflict("preview_not_ready", "预览图片不可用")
                asset.purge_after = None
            result = self._record_confirmation(
                ctx, keys, balance, row, preview, idem, submitted, now
            )
            return self._receipt(ctx, result), True

    @staticmethod
    def _receipt(ctx, result):
        event = ctx.db.scalar(
            select(BenefitOrderEvent).where(
                BenefitOrderEvent.service_order_id == result.service_order_id,
                BenefitOrderEvent.action == "complete",
            )
        )
        return {
            "result_id": result.id,
            "service_order_id": result.service_order_id,
            "round_id": result.round_id,
            "remaining_benefit_count": event.event_data["remaining_benefit_count"],
        }

    @staticmethod
    def _record_confirmation(ctx, keys, balance, row, preview, idem, submitted, now):
        digest = keys.fingerprint(keys.current, "order-create", submitted)
        order = new_row(
            BenefitServiceOrder,
            ctx.actor_id,
            balance_id=balance.id,
            requested_by_user_id=ctx.actor_id,
            idempotency_key=idem,
            request_digest=digest,
            digest_key_version=keys.current,
            request_data=submitted,
            status="reserved",
            reserved_at=now,
        )
        ctx.db.add(order)
        balance.reserved_count += 1
        flush_unique(ctx.db)
        BenefitOrderService.record_event(
            ctx, order, idem, keys.current, digest, "create", None, reserved_delta=1
        )
        order.status, order.version, order.started_at = "in_progress", 2, now
        start_key = uuid5(NAMESPACE_URL, f"mars:product-experience:{order.id}:start")
        BenefitOrderService.record_event(
            ctx,
            order,
            start_key,
            keys.current,
            keys.fingerprint(keys.current, "product-start", submitted),
            "start",
            "reserved",
        )
        result = new_row(
            ProductExperienceResult,
            ctx.actor_id,
            round_id=row.id,
            preview_id=preview.id,
            service_order_id=order.id,
            image_asset_id=preview.output_asset_id,
        )
        ctx.db.add(result)
        flush_unique(ctx.db)
        order.status, order.version, order.finished_at = "completed", 3, now
        order.result_ref = f"product_experience_result:{result.id}"
        balance.reserved_count -= 1
        balance.used_count += 1
        stamp(balance, ctx.actor_id)
        completion = {
            "result_id": result.id,
            "remaining_benefit_count": balance.granted_count
            - balance.reserved_count
            - balance.used_count,
        }
        complete_key = uuid5(NAMESPACE_URL, f"mars:product-experience:{order.id}:complete")
        BenefitOrderService.record_event(
            ctx,
            order,
            complete_key,
            keys.current,
            keys.fingerprint(keys.current, "product-complete", completion),
            "complete",
            "in_progress",
            reserved_delta=-1,
            used_delta=1,
            data=completion,
        )
        close_round(ctx.db, row, "completed", now, ctx.actor_id)
        return result
