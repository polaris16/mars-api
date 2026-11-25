"""[新增] 青年伙伴资料、私有证件和平台审核。"""

from datetime import timedelta

from sqlalchemy import select

from app.models import (
    BenefitServiceVersion,
    BenefitStaff,
    MediaAsset,
    User,
    YouthPartnerIdentityAccess,
    YouthPartnerIdentityDocument,
    YouthPartnerProfile,
    YouthPartnerService,
)
from app.services.benefit_common import (
    active_row,
    conflict,
    display,
    missing,
    new_row,
    now_utc,
    paginate,
    stamp,
)
from app.services.product_media import (
    CONFIRMED,
    authorize_upload,
    complete_upload,
    image_link,
    owned_image,
)

PHOTO = "partner_photo"
IDENTITY = "partner_identity"
EDITABLE = {"draft", "rejected"}
PROFILE_FIELDS = (
    "display_name major education_label bio tags photo_asset_id status reviewed_at review_notes"
)
SIDE_FIELDS = {"portrait": "portrait_side_asset_id", "emblem": "emblem_side_asset_id"}


def partner_for_user(ctx, *, lock=False, required=True):
    stmt = select(YouthPartnerProfile).where(
        YouthPartnerProfile.user_id == ctx.actor_id, YouthPartnerProfile.is_del.is_(False)
    )
    row = ctx.db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None and required:
        raise missing()
    return row


def _require_editable(profile):
    if profile and profile.status not in EDITABLE:
        raise conflict("partner_profile_locked", "当前状态下无法更换注册资料")


def _services(ctx, partner_id):
    return list(
        ctx.db.scalars(
            select(YouthPartnerService)
            .where(
                YouthPartnerService.partner_id == partner_id,
            )
            .order_by(YouthPartnerService.service_version_id)
        )
    )


def _identity(ctx, partner_id):
    row = ctx.db.get(YouthPartnerIdentityDocument, partner_id)
    return row if row and not row.is_del else None


def profile_view(ctx, row, storage=None):
    identity = _identity(ctx, row.id)
    output = {
        **display(row, PROFILE_FIELDS),
        "id": row.id,
        "requested_service_version_ids": [
            s.service_version_id for s in _services(ctx, row.id) if not s.is_del
        ],
        "requested_area_codes": row.service_area_codes,
        "identity_upload_status": {
            side: bool(identity and getattr(identity, field)) for side, field in SIDE_FIELDS.items()
        },
    }
    if storage is not None and row.photo_asset_id:
        asset = ctx.db.get(MediaAsset, row.photo_asset_id)
        if (
            asset
            and not asset.is_del
            and asset.purpose == PHOTO
            and asset.upload_status in CONFIRMED
        ):
            output["photo"] = image_link(asset, storage)
    return output


def _versions(ctx, ids):
    found = set(
        ctx.db.scalars(
            select(BenefitServiceVersion.id).where(
                BenefitServiceVersion.id.in_(ids),
                BenefitServiceVersion.is_del.is_(False),
                BenefitServiceVersion.appointment_handler == "youth_partner",
                BenefitServiceVersion.status.in_(("published", "retired")),
            )
        )
    )
    if found != set(ids):
        raise conflict("partner_services_unavailable", "所选服务版本当前不可申请")


def _replace_services(ctx, row, ids, status):
    wanted = set(ids)
    for service in _services(ctx, row.id):
        keep = service.service_version_id in wanted
        service.status = status if keep else "disabled"
        service.is_del = not keep
        if keep:
            wanted.remove(service.service_version_id)
        stamp(service, ctx.actor_id)
    for version_id in sorted(wanted):
        ctx.db.add(
            new_row(
                YouthPartnerService,
                ctx.actor_id,
                partner_id=row.id,
                service_version_id=version_id,
                status=status,
            )
        )


def my_profile(ctx, storage):
    with ctx.db.begin():
        return profile_view(ctx, partner_for_user(ctx), storage)


def save_profile(ctx, body, storage):
    with ctx.db.begin():
        active_row(ctx.db, User, ctx.actor_id, lock=True)
        row = partner_for_user(ctx, lock=True, required=False)
        _require_editable(row)
        if body.photo_asset_id is not None:
            asset = owned_image(ctx, body.photo_asset_id, PHOTO)
            if asset.upload_status not in CONFIRMED:
                raise conflict("partner_photo_pending", "请先确认展示照已上传")
        _versions(ctx, body.requested_service_version_ids)
        if row is None:
            row = new_row(YouthPartnerProfile, ctx.actor_id, user_id=ctx.actor_id)
            ctx.db.add(row)
        for name in ("display_name", "major", "education_label", "bio", "photo_asset_id"):
            setattr(row, name, getattr(body, name))
        row.tags = list(dict.fromkeys(body.tags))
        row.service_area_codes = list(dict.fromkeys(body.requested_area_codes))
        stamp(row, ctx.actor_id)
        ctx.db.flush()
        _replace_services(ctx, row, body.requested_service_version_ids, "pending")
        ctx.db.flush()
        return profile_view(ctx, row, storage)


def submit_profile(ctx):
    with ctx.db.begin():
        row = partner_for_user(ctx, lock=True)
        if row.status == "pending_review":
            return profile_view(ctx, row)
        _require_editable(row)
        identity = _identity(ctx, row.id)
        if not identity or any(not getattr(identity, name) for name in SIDE_FIELDS.values()):
            raise conflict("partner_identity_required", "请先上传并确认身份证两面")
        for side in SIDE_FIELDS.values():
            asset = owned_image(ctx, getattr(identity, side), IDENTITY)
            if asset.upload_status not in CONFIRMED:
                raise conflict("partner_identity_required", "请先上传并确认身份证两面")
        if (
            not row.photo_asset_id
            or owned_image(ctx, row.photo_asset_id, PHOTO).upload_status not in CONFIRMED
        ):
            raise conflict("partner_photo_pending", "请先上传并确认展示照")
        ids = [
            service.service_version_id for service in _services(ctx, row.id) if not service.is_del
        ]
        if not row.service_area_codes or not ids:
            raise conflict("partner_profile_incomplete", "请选择服务及可服务区域")
        _versions(ctx, ids)
        row.status = "pending_review"
        row.reviewed_at = row.reviewed_by_user_id = row.review_notes = None
        stamp(row, ctx.actor_id)
        return profile_view(ctx, row)


def issue_upload(ctx, body, storage, *, side=None):
    with ctx.db.begin():
        _require_editable(partner_for_user(ctx, lock=True, required=bool(side)))
        result = authorize_upload(
            ctx,
            storage,
            body,
            IDENTITY if side else PHOTO,
            namespace="youth-partners",
            scope=f"{side}/" if side else "",
        )
        return {
            "asset_id": result["photo_id"],
            "expires_at": result["url_expires_at"],
            "upload_url": result["upload_url"],
            "required_headers": result["required_headers"],
        }


def confirm_upload(ctx, asset_id, storage, *, side=None):
    purpose = IDENTITY if side else PHOTO
    with ctx.db.begin():
        _require_editable(partner_for_user(ctx, required=bool(side)))
        asset = owned_image(ctx, asset_id, purpose)
        if side and not asset.object_key.startswith(
            f"youth-partners/{ctx.actor_id}/{purpose}/{side}/"
        ):
            raise missing()
    # 图片读取和校验在事务外，写入证件引用前再次核对申请状态。
    asset_id = complete_upload(ctx, asset_id, purpose, storage, include_link=False)["photo_id"]
    if side:
        with ctx.db.begin():
            row = partner_for_user(ctx, lock=True)
            _require_editable(row)
            identity = ctx.db.get(YouthPartnerIdentityDocument, row.id)
            if identity is None:
                identity = new_row(YouthPartnerIdentityDocument, ctx.actor_id, partner_id=row.id)
                ctx.db.add(identity)
            other = "emblem" if side == "portrait" else "portrait"
            if getattr(identity, SIDE_FIELDS[other]) == asset_id:
                raise conflict("partner_identity_duplicate", "身份证两面须使用不同照片")
            # 去重后也保留已确认的两面引用，证件原图不从此接口签发。
            setattr(identity, SIDE_FIELDS[side], asset_id)
            identity.is_del = False
            stamp(identity, ctx.actor_id)
    return {"asset_id": asset_id, "upload_status": "uploaded", **({"side": side} if side else {})}


def applications(ctx, page, size, status, sort):
    with ctx.db.begin():
        ctx.require_platform("admin", "ops", "identity_reviewer")
        stmt = select(YouthPartnerProfile).where(YouthPartnerProfile.is_del.is_(False))
        if status:
            stmt = stmt.where(YouthPartnerProfile.status == status)
        order = (
            YouthPartnerProfile.id.asc()
            if sort == "created_at_asc"
            else YouthPartnerProfile.id.desc()
        )
        return paginate(
            ctx.db, stmt.order_by(order), page, size, lambda row: profile_view(ctx, row)
        )


def application(ctx, partner_id, storage):
    with ctx.db.begin():
        ctx.require_platform("admin", "ops", "identity_reviewer")
        return profile_view(ctx, active_row(ctx.db, YouthPartnerProfile, partner_id), storage)


def identity_documents(ctx, partner_id, storage):
    with ctx.db.begin():
        ctx.require_platform("identity_reviewer")
        row = active_row(ctx.db, YouthPartnerProfile, partner_id, lock=True)
        identity = _identity(ctx, row.id)
        if row.status != "pending_review" or not identity:
            raise missing()
        documents = {}
        ttl = 120
        for side, field in SIDE_FIELDS.items():
            asset = active_row(ctx.db, MediaAsset, getattr(identity, field))
            if asset.purpose != IDENTITY or asset.uploaded_by_user_id != row.user_id:
                raise missing()
            documents[side] = {
                "download_url": storage.create_read_url(asset.object_key, ttl),
                "expires_at": now_utc() + timedelta(seconds=ttl),
            }
        ctx.db.add(
            new_row(
                YouthPartnerIdentityAccess,
                ctx.actor_id,
                partner_id=partner_id,
                reviewer_user_id=ctx.actor_id,
            )
        )
        return documents


def review_application(ctx, partner_id, body):
    with ctx.db.begin():
        ctx.require_platform("admin", "ops")
        original = active_row(ctx.db, YouthPartnerProfile, partner_id)
        active_row(ctx.db, User, original.user_id, lock=True)
        row = active_row(ctx.db, YouthPartnerProfile, partner_id, lock=True)
        if row.status != "pending_review":
            raise conflict("partner_review_state", "仅待审核的申请可处理")
        if body.decision == "approve":
            selected = set(body.approved_service_version_ids)
            requested = {s.service_version_id for s in _services(ctx, row.id) if not s.is_del}
            if not selected <= requested or not set(body.approved_area_codes) <= set(
                row.service_area_codes
            ):
                raise conflict("partner_review_scope", "核准范围须来自本次申请")
            _versions(ctx, selected)
            _replace_services(ctx, row, selected, "approved")
            row.service_area_codes = list(dict.fromkeys(body.approved_area_codes))
            staff = ctx.db.scalar(
                select(BenefitStaff)
                .where(
                    BenefitStaff.user_id == row.user_id,
                    BenefitStaff.role == "provider",
                )
                .with_for_update()
            )
            if staff is None:
                staff = new_row(BenefitStaff, ctx.actor_id, user_id=row.user_id, role="provider")
                ctx.db.add(staff)
            staff.is_del, staff.status = False, "active"
            stamp(staff, ctx.actor_id)
        row.status = "approved" if body.decision == "approve" else "rejected"
        row.reviewed_by_user_id, row.reviewed_at, row.review_notes = (
            ctx.actor_id,
            now_utc(),
            body.reason,
        )
        stamp(row, ctx.actor_id)
        ctx.db.flush()
        return profile_view(ctx, row)
