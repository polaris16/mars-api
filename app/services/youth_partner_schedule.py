"""[新增] 线下地点、青年伙伴开放时段与可约查询。"""

import json
from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlalchemy import and_, func, or_, select

from app.models import (
    BenefitBalance,
    BenefitBatchContact,
    BenefitGrant,
    BenefitGrantServiceLocation,
    BenefitIssueRequest,
    BenefitPackageItem,
    BenefitPurchaseBatch,
    BenefitServiceOrder,
    BenefitServiceVersion,
    BenefitStaff,
    User,
    YouthPartnerAvailability,
    YouthPartnerBooking,
    YouthPartnerCalendarDay,
    YouthPartnerProfile,
    YouthPartnerRating,
    YouthPartnerService,
)
from app.schemas.youth_partners import zone_for
from app.services.benefit_common import (
    BenefitError,
    BenefitKeys,
    active_row,
    conflict,
    display,
    forbidden,
    missing,
    new_row,
    now_utc,
    page_data,
    stamp,
    utc,
)
from app.services.benefit_orders import BenefitOrderService
from app.services.youth_partner_profiles import partner_for_user, public_profile

STEP = timedelta(minutes=15)
MIN_LEAD = timedelta(minutes=30)
HORIZON_DAYS = 7
LOCATION_FIELDS = (
    "id name area_code time_zone travel_before_minutes travel_after_minutes sort_order"
)


def balance_for(ctx, balance_id, *, lock=False, require_available=True):
    balance = active_row(ctx.db, BenefitBalance, balance_id, lock=lock)
    grant = active_row(ctx.db, BenefitGrant, balance.grant_id)
    if grant.status != "claimed" or grant.recipient_user_id != ctx.actor_id:
        raise missing()
    item = active_row(ctx.db, BenefitPackageItem, balance.package_item_id)
    version = active_row(ctx.db, BenefitServiceVersion, item.service_version_id)
    if version.appointment_handler != "youth_partner":
        raise conflict("benefit_fulfillment_unavailable", "该权益未配置青年伙伴预约")
    if require_available:
        BenefitOrderService.eligibility(balance, grant, now_utc())
    return balance, grant, version


def location_for(ctx, grant_id, location_id, *, lock=False):
    stmt = select(BenefitGrantServiceLocation).where(
        BenefitGrantServiceLocation.id == location_id,
        BenefitGrantServiceLocation.grant_id == grant_id,
        BenefitGrantServiceLocation.is_del.is_(False),
    )
    row = ctx.db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise missing()
    return row


def _locations(db, grant_id):
    model = BenefitGrantServiceLocation
    return list(
        db.scalars(
            select(model)
            .where(model.grant_id == grant_id, model.is_del.is_(False))
            .order_by(model.sort_order, model.id)
        )
    )


def location_view(row, keys):
    return {
        **display(row, LOCATION_FIELDS),
        **json.loads(keys.open(row.crypto_key_version, "benefit-location", row.address_ciphertext)),
    }


def locations_for_balance(ctx, balance_id):
    with ctx.db.begin():
        _, grant, _ = balance_for(ctx, balance_id, require_available=False)
        keys = BenefitKeys()
        return [location_view(row, keys) for row in _locations(ctx.db, grant.id)]


def manage_locations(ctx, enterprise_id, grant_id, body=None):
    with ctx.db.begin():
        _, member = ctx.enterprise(enterprise_id, "admin", "issuer", "support")
        grant = active_row(ctx.db, BenefitGrant, grant_id, lock=body is not None)
        issue = active_row(ctx.db, BenefitIssueRequest, grant.issue_request_id)
        batch = active_row(ctx.db, BenefitPurchaseBatch, issue.purchase_batch_id)
        if batch.enterprise_id != enterprise_id:
            raise missing()
        contact = ctx.db.get(BenefitBatchContact, (batch.id, member.id))
        if (
            member.role != "admin"
            and issue.issued_by_user_id != ctx.actor_id
            and (contact is None or contact.is_del)
        ):
            raise forbidden()
        keys = BenefitKeys()
        rows = _locations(ctx.db, grant.id)
        if body is None:
            return [location_view(row, keys) for row in rows]
        if grant.status not in ("issued", "claimed"):
            raise conflict("benefit_location_unavailable", "当前发放记录不可维护服务地点")
        unchanged = {row.id: row for row in rows}
        for item in body.items:
            if item.id is None:
                row = new_row(BenefitGrantServiceLocation, ctx.actor_id, grant_id=grant_id)
                ctx.db.add(row)
            else:
                row = unchanged.pop(item.id, None)
                if row is None:
                    raise missing()
            for field in LOCATION_FIELDS.split()[1:]:
                setattr(row, field, getattr(item, field))
            private = {"address": item.address, "arrival_notes": item.arrival_notes}
            row.crypto_key_version = keys.current
            row.address_ciphertext = keys.seal(
                keys.current, "benefit-location", json.dumps(private)
            )
            stamp(row, ctx.actor_id)
        for row in unchanged.values():
            row.is_del = True
            stamp(row, ctx.actor_id)
        ctx.db.flush()
        return [location_view(row, keys) for row in _locations(ctx.db, grant.id)]


def busy_windows(db, user_id, first, last, now, *, exclude_id=None):
    """普通预约和青年伙伴预约采用相同的服务者占用检测。"""
    first, last = utc(first), utc(last)
    order, booking = BenefitServiceOrder, YouthPartnerBooking
    start = func.coalesce(booking.blocking_start_at, order.scheduled_start_at)
    end = func.coalesce(booking.blocking_end_at, order.scheduled_end_at)
    query = (
        select(start, end)
        .select_from(order)
        .outerjoin(booking, and_(booking.service_order_id == order.id, booking.is_del.is_(False)))
        .where(
            order.assignee_user_id == user_id,
            order.is_del.is_(False),
            order.status.in_(("reserved", "in_progress")),
            start < last,
            end > first,
            or_(
                booking.service_order_id.is_(None),
                _valid_youth_hold(now),
            ),
        )
    )
    if exclude_id is not None:
        query = query.where(order.id != exclude_id)
    return [(utc(a), utc(b)) for a, b in db.execute(query.order_by(start))]


def _valid_youth_hold(now):
    order, booking = BenefitServiceOrder, YouthPartnerBooking
    return or_(
        order.status == "in_progress",
        and_(booking.confirmed_at.is_(None), booking.confirm_by > now),
        and_(booking.confirmed_at.is_not(None), order.scheduled_end_at > now),
    )


def _youth_holds(first, last, now):
    order, booking = BenefitServiceOrder, YouthPartnerBooking
    return (
        select(order.scheduled_start_at, order.scheduled_end_at)
        .join(booking, booking.service_order_id == order.id)
        .where(
            order.is_del.is_(False),
            booking.is_del.is_(False),
            order.status.in_(("reserved", "in_progress")),
            order.scheduled_start_at < last,
            order.scheduled_end_at > first,
            _valid_youth_hold(now),
        )
    )


def recipient_windows(db, user_id, first, last, now):
    rows = db.execute(
        _youth_holds(first, last, now).where(BenefitServiceOrder.requested_by_user_id == user_id)
    )
    return [(utc(start), utc(end)) for start, end in rows]


def protected_youth_starts(db, user_id, first, last, now):
    query = (
        _youth_holds(first, last, now)
        .where(
            BenefitServiceOrder.assignee_user_id == user_id,
            BenefitServiceOrder.scheduled_start_at >= first,
        )
        .with_only_columns(BenefitServiceOrder.scheduled_start_at)
    )
    return {utc(start) for start in db.scalars(query)}


def _opening_rows(db, partner_id, first, last):
    model = YouthPartnerAvailability
    return list(
        db.scalars(
            select(model)
            .where(
                model.partner_id == partner_id,
                model.is_del.is_(False),
                model.starts_at < last,
                model.ends_at > first,
            )
            .order_by(model.starts_at, model.id)
        )
    )


def _merged(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _day_bounds(day, zone):
    return tuple(
        utc(datetime.combine(day + timedelta(days=i), time.min, tzinfo=zone)) for i in (0, 1)
    )


def _zone(value):
    try:
        return zone_for(value)
    except ValueError as exc:
        raise BenefitError(422, "invalid_time_zone", str(exc)) from exc


def published_days(db, partner, first, last):
    if not partner.calendar_time_zone:
        return {}
    zone = _zone(partner.calendar_time_zone)
    start_day = first.astimezone(zone).date()
    end_day = (last - timedelta(microseconds=1)).astimezone(zone).date()
    day = YouthPartnerCalendarDay
    return {
        row.schedule_date: row
        for row in db.scalars(
            select(day).where(
                day.partner_id == partner.id,
                day.schedule_date.between(start_day, end_day),
                day.is_del.is_(False),
            )
        )
    }


def schedule(ctx, day, time_zone, body=None):
    zone = _zone(time_zone)
    now = now_utc()
    today = now.astimezone(zone).date()
    if day is not None and not today <= day < today + timedelta(days=HORIZON_DAYS):
        raise BenefitError(422, "invalid_schedule_day", "仅支持未来 7 个日历日")
    first, last = _day_bounds(day or today, zone)
    if day is None:
        last, _ = _day_bounds(today + timedelta(days=HORIZON_DAYS), zone)
    with ctx.db.begin():
        ctx.require_platform("provider")
        row = partner_for_user(ctx)
        if row.status != "approved":
            raise missing()
        if body is not None and ctx.actor_id not in BenefitOrderService.lock_providers(
            ctx.db, None, ctx.actor_id
        ):
            raise missing()
        existing = _opening_rows(ctx.db, row.id, first, last)
        busy = busy_windows(ctx.db, row.user_id, first, last, now)
        if body is not None:
            # 发布可能在等待岗位锁时改变了日历时区。
            ctx.db.refresh(row)
            if published_days(ctx.db, row, first, last):
                raise conflict("calendar_day_managed", "请通过新版预约日历修改已发布日期")
            windows = _merged([(utc(item.starts_at), utc(item.ends_at)) for item in body.windows])
            if any(start < first or end > last for start, end in windows):
                raise BenefitError(422, "invalid_schedule_window", "开放时段须位于所选日期内")
            # 一天的替换保留跨边界历史部分，也保留已预约的有效占用。
            for start, end in busy:
                if end > now and not any(
                    a <= max(start, first) and min(end, last) <= b for a, b in windows
                ):
                    raise conflict("appointment_slot_unavailable", "不能移除已预约的时段")
            for old in existing:
                old.is_del = True
                stamp(old, ctx.actor_id)
                if utc(old.starts_at) < first:
                    windows.append((utc(old.starts_at), first))
                if utc(old.ends_at) > last:
                    windows.append((last, utc(old.ends_at)))
            for start, end in windows:
                ctx.db.add(
                    new_row(
                        YouthPartnerAvailability,
                        ctx.actor_id,
                        partner_id=row.id,
                        starts_at=start,
                        ends_at=end,
                    )
                )
            ctx.db.flush()
            existing = _opening_rows(ctx.db, row.id, first, last)
        return {
            "time_zone": time_zone,
            "available_windows": [
                {
                    "starts_at": max(utc(row.starts_at), first),
                    "ends_at": min(utc(row.ends_at), last),
                }
                for row in existing
            ],
            "occupied_windows": [
                {"starts_at": max(a, first), "ends_at": min(b, last)} for a, b in busy
            ],
        }


def public_partners():
    return (
        select(YouthPartnerProfile)
        .join(User, User.id == YouthPartnerProfile.user_id)
        .join(
            BenefitStaff,
            BenefitStaff.user_id == YouthPartnerProfile.user_id,
        )
        .where(
            YouthPartnerProfile.is_del.is_(False),
            YouthPartnerProfile.status == "approved",
            User.is_del.is_(False),
            BenefitStaff.role == "provider",
            BenefitStaff.status == "active",
            BenefitStaff.is_del.is_(False),
        )
    )


def eligible_partners(version_id):
    return (
        public_partners()
        .join(
            YouthPartnerService,
            YouthPartnerService.partner_id == YouthPartnerProfile.id,
        )
        .where(
            YouthPartnerService.is_del.is_(False),
            YouthPartnerService.status == "approved",
            YouthPartnerService.service_version_id == version_id,
        )
    )


def eligible_partner(ctx, version_id, partner_id, area_code):
    row = ctx.db.scalar(
        eligible_partners(version_id)
        .where(YouthPartnerProfile.id == partner_id)
        .execution_options(populate_existing=True)
    )
    if row is None or area_code not in row.service_area_codes:
        raise missing()
    return row


def schedule_bounds(location, now):
    zone = _zone(location.time_zone)
    today = now.astimezone(zone).date()
    first, _ = _day_bounds(today, zone)
    last, _ = _day_bounds(today + timedelta(days=HORIZON_DAYS), zone)
    return zone, first, last


def _start_times(first, last, zone, calendar_zone, managed, openings, before, after, duration):
    published = sorted(utc(start) for row in managed.values() for start in row.booking_starts)
    for origin, close in openings:
        lower, upper = max(first, origin + before), min(last, close - after) - duration
        if lower > upper:
            continue
        candidates = {start for start in published if lower <= start <= upper}
        # 旧版只按地点时区的 15 分钟步长生成，显式发布的日期只使用库中时间。
        local = lower.astimezone(zone)
        start = utc(local.replace(minute=local.minute // 15 * 15, second=0, microsecond=0))
        if start < lower:
            start += STEP
        while start <= upper:
            if start.astimezone(calendar_zone).date() not in managed:
                candidates.add(start)
            start += STEP
        yield from sorted(candidates)


def classify_slots(db, partner, location, version, grant, now, *, balance=None):
    zone, first, last = schedule_bounds(location, now)
    duration = timedelta(minutes=version.offline_duration_minutes)
    before = timedelta(minutes=location.travel_before_minutes)
    after = timedelta(minutes=location.travel_after_minutes)
    opening = _merged(
        [
            (utc(row.starts_at), utc(row.ends_at))
            for row in _opening_rows(db, partner.id, first - before, last + after)
        ]
    )
    managed = published_days(db, partner, first, last)
    calendar_zone = _zone(partner.calendar_time_zone) if partner.calendar_time_zone else zone
    provider_busy = busy_windows(db, partner.user_id, first - before, last + after, now)
    customer_busy = recipient_windows(db, grant.recipient_user_id, first, last, now)
    valid_from, valid_until = utc(grant.valid_from), utc(grant.valid_until)
    no_quota = (
        balance is not None and balance.used_count + balance.reserved_count >= balance.granted_count
    )
    for start in _start_times(
        max(first, now), last, zone, calendar_zone, managed, opening, before, after, duration
    ):
        finish = start + duration
        status, reason = "available", None
        if any(a < finish + after and start - before < b for a, b in provider_busy):
            status = "occupied"
        else:
            if start <= now + MIN_LEAD:
                reason = "booking_cutoff"
            elif not valid_from <= now < valid_until or finish > valid_until:
                reason = "benefit_not_active"
            elif no_quota:
                reason = "benefit_quota_exhausted"
            elif any(a < finish and start < b for a, b in customer_busy):
                reason = "recipient_schedule_conflict"
            if reason:
                status = "unavailable"
        yield {"starts_at": start, "ends_at": finish, "status": status, "reason": reason}


def available_slots(db, partner, location, version, grant, now, *, first_only=False):
    for slot in classify_slots(db, partner, location, version, grant, now):
        if slot["status"] == "available":
            yield {key: slot[key] for key in ("starts_at", "ends_at")}
            if first_only:
                return


def get_slots(ctx, balance_id, partner_id, location_id):
    with ctx.db.begin():
        _, grant, version = balance_for(ctx, balance_id)
        location = location_for(ctx, grant.id, location_id)
        partner = eligible_partner(ctx, version.id, partner_id, location.area_code)
        return {
            "time_zone": location.time_zone,
            "slots": list(available_slots(ctx.db, partner, location, version, grant, now_utc())),
        }


def rating_stats(db, partner_ids):
    booking, order, rating = YouthPartnerBooking, BenefitServiceOrder, YouthPartnerRating
    return {
        row[0]: row[1:]
        for row in db.execute(
            select(
                booking.partner_id,
                func.count(order.id),
                func.avg(rating.score),
                func.count(rating.score),
            )
            .join(order, order.id == booking.service_order_id)
            .outerjoin(rating, and_(rating.service_order_id == order.id, rating.is_del.is_(False)))
            .where(
                booking.partner_id.in_(partner_ids),
                booking.is_del.is_(False),
                order.is_del.is_(False),
                order.status == "completed",
            )
            .group_by(booking.partner_id)
        )
    }


def availability_label(start, now, zone):
    day, today = start.astimezone(zone).date(), now.astimezone(zone).date()
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return "this_week" if day <= today + timedelta(days=6 - today.weekday()) else "later"


def public_service_labels(db, page_ids, current_version_id=None):
    service_labels = defaultdict(list)
    for partner_id, service_id, name in db.execute(
        select(YouthPartnerService.partner_id, BenefitServiceVersion.id, BenefitServiceVersion.name)
        .join(
            BenefitServiceVersion,
            BenefitServiceVersion.id == YouthPartnerService.service_version_id,
        )
        .where(
            YouthPartnerService.partner_id.in_(page_ids),
            YouthPartnerService.is_del.is_(False),
            YouthPartnerService.status == "approved",
            BenefitServiceVersion.is_del.is_(False),
            or_(
                BenefitServiceVersion.status == "published",
                BenefitServiceVersion.id == current_version_id,
            ),
        )
        .order_by(BenefitServiceVersion.id)
    ):
        service_labels[partner_id].append({"service_version_id": service_id, "name": name})
    return service_labels


def list_partners(
    ctx, storage, balance_id, page, size, location_id, keyword, tag, today_only, sort
):
    if today_only and location_id is None:
        raise BenefitError(422, "benefit_location_required", "按今日可约筛选时请选择地点")
    with ctx.db.begin():
        _, grant, version = balance_for(ctx, balance_id)
        location = location_for(ctx, grant.id, location_id) if location_id else None
        stmt = eligible_partners(version.id).where(YouthPartnerProfile.user_id != ctx.actor_id)
        if keyword:
            stmt = stmt.where(
                or_(
                    YouthPartnerProfile.display_name.icontains(keyword, autoescape=True),
                    YouthPartnerProfile.major.icontains(keyword, autoescape=True),
                )
            )
        candidates = [
            p
            for p in ctx.db.scalars(stmt.order_by(YouthPartnerProfile.id))
            if (not tag or tag in p.tags)
            and (not location or location.area_code in p.service_area_codes)
        ]
        stats = {}
        offset = (page - 1) * size
        total = len(candidates)
        if sort == "rating_desc":
            stats = rating_stats(ctx.db, [p.id for p in candidates])
            candidates.sort(key=lambda p: (-(stats.get(p.id, (0, None, 0))[1] or 0), p.id))
        if not today_only:
            candidates = candidates[offset : offset + size]
        now = now_utc()
        eligible = []
        for partner in candidates:
            earliest, label = None, None
            if location:
                slot = next(
                    available_slots(
                        ctx.db, partner, location, version, grant, now, first_only=True
                    ),
                    None,
                )
                if slot:
                    earliest = slot["starts_at"]
                    label = availability_label(earliest, now, _zone(location.time_zone))
            if not today_only or label == "today":
                eligible.append((partner, earliest, label))
        if today_only:
            total = len(eligible)
            eligible = eligible[offset : offset + size]
        page_ids = [partner.id for partner, _, _ in eligible]
        if sort != "rating_desc":
            stats = rating_stats(ctx.db, page_ids)
        service_labels = public_service_labels(ctx.db, page_ids, version.id)
        results = []
        for partner, earliest, label in eligible:
            count, mean, rating_count = stats.get(partner.id, (0, None, 0))
            results.append(
                {
                    **public_profile(ctx.db, partner, storage),
                    "partner_id": partner.id,
                    "service_version_id": version.id,
                    "services": service_labels[partner.id],
                    "completed_service_count": count,
                    "average_rating": round(float(mean), 2) if mean is not None else None,
                    "rating_count": rating_count,
                    "earliest_start_at": earliest,
                    "availability_label": label,
                }
            )
        return page_data(results, page, size, total)
