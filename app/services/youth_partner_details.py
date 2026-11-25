"""[新增] 读取公开青年伙伴详情和管理用户个人收藏。"""

from sqlalchemy import func, select

from app.models import (
    BenefitServiceOrder,
    User,
    YouthPartnerBooking,
    YouthPartnerMark,
    YouthPartnerProfile,
)
from app.schemas.youth_partners import zone_for
from app.services.benefit_common import (
    BenefitError,
    active_row,
    display,
    missing,
    new_row,
    now_utc,
    page_data,
    select_page,
    stamp,
    utc,
)
from app.services.youth_partner_profiles import public_profile
from app.services.youth_partner_schedule import (
    availability_label,
    available_slots,
    balance_for,
    eligible_partners,
    location_for,
    public_partners,
    public_service_labels,
    rating_stats,
)


def _public_partner(ctx, partner_id):
    row = ctx.db.scalar(public_partners().where(YouthPartnerProfile.id == partner_id))
    if row is None:
        raise missing()
    return row


def _availability(ctx, partner, balance_id, location_id):
    balance, grant, version = balance_for(ctx, balance_id, require_available=False)
    location = location_for(ctx, grant.id, location_id) if location_id else None
    now = now_utc()
    if (
        not location
        or location.area_code not in partner.service_area_codes
        or partner.user_id == ctx.actor_id
        or not utc(grant.valid_from) <= now < utc(grant.valid_until)
        or balance.reserved_count + balance.used_count >= balance.granted_count
    ):
        return {}
    if not ctx.db.scalar(eligible_partners(version.id).where(YouthPartnerProfile.id == partner.id)):
        return {}
    slot = next(
        available_slots(ctx.db, partner, location, version, grant, now, first_only=True), None
    )
    if slot is None:
        return {}
    return {
        "earliest_start_at": slot["starts_at"],
        "can_book": True,
        "availability_label": availability_label(
            slot["starts_at"], now, zone_for(location.time_zone)
        ),
    }


def get_details(ctx, storage, partner_id, balance_id=None, location_id=None):
    if location_id is not None and balance_id is None:
        raise BenefitError(422, "benefit_balance_required", "查询地点档期须提供本人权益余额")
    with ctx.db.begin():
        partner = _public_partner(ctx, partner_id)
        availability = _availability(ctx, partner, balance_id, location_id) if balance_id else {}
        mark = ctx.db.get(YouthPartnerMark, (ctx.actor_id, partner.id))
        count, mean, ratings = rating_stats(ctx.db, [partner.id]).get(partner.id, (0, None, 0))
        booking, order = YouthPartnerBooking, BenefitServiceOrder
        seconds = ctx.db.scalar(
            select(func.sum(booking.service_duration_seconds))
            .join(order, order.id == booking.service_order_id)
            .where(
                booking.partner_id == partner.id,
                booking.is_del.is_(False),
                order.is_del.is_(False),
                order.status == "completed",
            )
        )
        return {
            **public_profile(ctx.db, partner, storage),
            **display(partner, "specialties message_to_users"),
            **availability,
            "partner_id": partner.id,
            "services": public_service_labels(ctx.db, [partner.id])[partner.id],
            "completed_service_count": count,
            "total_service_seconds": seconds or 0,
            "average_rating": round(float(mean), 2) if mean is not None else None,
            "rating_count": ratings,
            "is_marked": bool(mark and not mark.is_del),
        }


def set_mark(ctx, partner_id, *, marked):
    with ctx.db.begin():
        # 同账号的添加、恢复和移除沿用同一个锁，重复添加保留原收藏时间。
        active_row(ctx.db, User, ctx.actor_id, lock=True)
        row = ctx.db.get(YouthPartnerMark, (ctx.actor_id, partner_id))
        if row is not None and row.is_del != marked:
            return
        if marked:
            _public_partner(ctx, partner_id)
        if row is None:
            if marked:
                ctx.db.add(
                    new_row(
                        YouthPartnerMark, ctx.actor_id, user_id=ctx.actor_id, partner_id=partner_id
                    )
                )
            return
        row.is_del = not marked
        stamp(row, ctx.actor_id)


def list_marks(ctx, storage, page, size, sort):
    model = YouthPartnerMark
    date_order = model.updated_at.asc() if sort == "marked_at_asc" else model.updated_at.desc()
    with ctx.db.begin():
        marks, page, size, total = select_page(
            ctx.db,
            select(model)
            .where(model.user_id == ctx.actor_id, model.is_del.is_(False))
            .order_by(date_order, model.partner_id.desc()),
            page,
            size,
            paginate_results=True,
        )
        profiles = {
            row.id: row
            for row in ctx.db.scalars(
                public_partners().where(
                    YouthPartnerProfile.id.in_([mark.partner_id for mark in marks])
                )
            )
        }
        return page_data(
            [
                {
                    "partner_id": mark.partner_id,
                    "marked_at": utc(mark.updated_at),
                    "is_available": mark.partner_id in profiles,
                    "partner": public_profile(ctx.db, profiles[mark.partner_id], storage)
                    if mark.partner_id in profiles
                    else None,
                }
                for mark in marks
            ],
            page,
            size,
            total,
        )
