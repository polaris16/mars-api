"""[新增] 保存模板和每日档期，并按地点渲染预约日历。"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from app.models import (
    YouthPartnerAvailability,
    YouthPartnerAvailabilityTemplate,
    YouthPartnerCalendarDay,
)
from app.services.benefit_common import (
    BenefitError,
    conflict,
    missing,
    new_row,
    now_utc,
    stamp,
    utc,
)
from app.services.benefit_orders import BenefitOrderService
from app.services.youth_partner_profiles import partner_for_user
from app.services.youth_partner_schedule import (
    HORIZON_DAYS,
    _day_bounds,
    _merged,
    _opening_rows,
    _zone,
    balance_for,
    busy_windows,
    classify_slots,
    eligible_partner,
    location_for,
    protected_youth_starts,
    published_days,
)

DEFAULT_ZONE = "Asia/Shanghai"
TEMPLATE_KINDS = ("workday", "restday")


def _partner(ctx, *, write=False):
    if write:
        if ctx.actor_id not in BenefitOrderService.lock_providers(ctx.db, None, ctx.actor_id):
            raise missing()
    else:
        ctx.require_platform("provider")
    row = partner_for_user(ctx)
    if row.status != "approved":
        raise missing()
    return row


def _minutes(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _local_plan(body):
    return (
        [
            {"start_minute": _minutes(w.start_local), "end_minute": _minutes(w.end_local)}
            for w in body.windows
        ],
        [_minutes(value) for value in body.booking_start_times],
    )


def _template_view(kind, row):
    is_configured = row is not None and not row.is_del
    return {
        "kind": kind,
        "is_configured": is_configured,
        "windows": [
            {"start_local": _clock(w["start_minute"]), "end_local": _clock(w["end_minute"])}
            for w in row.windows
        ]
        if is_configured
        else [],
        "booking_start_times": [_clock(m) for m in row.booking_start_minutes]
        if is_configured
        else [],
        "updated_at": utc(row.updated_at) if is_configured else None,
    }


def list_templates(ctx):
    with ctx.db.begin():
        partner = _partner(ctx)
        table = YouthPartnerAvailabilityTemplate
        rows = ctx.db.scalars(select(table).where(table.partner_id == partner.id))
        by_kind = {row.kind: row for row in rows}
        return {"items": [_template_view(kind, by_kind.get(kind)) for kind in TEMPLATE_KINDS]}


def save_template(ctx, kind, body):
    with ctx.db.begin():
        partner = _partner(ctx, write=True)
        table = YouthPartnerAvailabilityTemplate
        row = ctx.db.get(table, (partner.id, kind))
        if row is None:
            row = new_row(table, ctx.actor_id, partner_id=partner.id, kind=kind)
            ctx.db.add(row)
        row.windows, row.booking_start_minutes = _local_plan(body)
        row.is_del = False
        stamp(row, ctx.actor_id)
        return _template_view(kind, row)


def _publication_zone(partner, requested):
    if requested is not None and partner.calendar_time_zone not in (None, requested):
        raise conflict("calendar_time_zone_conflict", "已发布日历的时区固定")
    return _zone(partner.calendar_time_zone or requested or DEFAULT_ZONE)


def _editable_date(day, zone, now):
    if day < now.astimezone(zone).date() or day == date.max:
        raise BenefitError(422, "invalid_schedule_day", "只可发布当天或以后的有效日期")


def _absolute(day, minute, zone):
    local = datetime.combine(day, time.min) + timedelta(minutes=minute)
    converted = {utc(local.replace(tzinfo=zone, fold=fold)) for fold in (0, 1)}
    valid = [
        candidate
        for candidate in converted
        if candidate.astimezone(zone).replace(tzinfo=None) == local
    ]
    if len(valid) != 1:
        raise BenefitError(422, "invalid_calendar_local_time", "该日期的本地时间不存在或有歧义")
    return valid[0]


def _dated_plan(day, windows, starts, zone):
    ranges = [
        (_absolute(day, w["start_minute"], zone), _absolute(day, w["end_minute"], zone))
        for w in windows
    ]
    return ranges, [_absolute(day, minute, zone) for minute in starts]


def _day_output(day, row, openings, busy, zone):
    first, last = _day_bounds(day, zone)
    windows = [
        {
            "start_local": max(a, first).astimezone(zone).strftime("%H:%M"),
            "end_local": "24:00" if b >= last else b.astimezone(zone).strftime("%H:%M"),
        }
        for a, b in openings
        if a < last and first < b
    ]
    occupied = [
        {"starts_at": max(a, first), "ends_at": min(b, last)}
        for a, b in busy
        if a < last and first < b
    ]
    if row:
        status = "published" if windows else "closed"
        version = row.version
        starts = [utc(value).astimezone(zone).strftime("%H:%M") for value in row.booking_starts]
    else:
        status = "legacy" if windows else "unset"
        version, starts = 0, []
    return {
        "date": day,
        "status": status,
        "version": version,
        "windows": windows,
        "booking_start_times": starts,
        "occupied_windows": occupied,
    }


def get_calendar(ctx, from_date, to_date):
    days = (to_date - from_date).days + 1
    if from_date == date.min or to_date == date.max or not 1 <= days <= 31:
        raise BenefitError(422, "invalid_calendar_range", "请选择最多 31 天的有效日期范围")
    with ctx.db.begin():
        partner = _partner(ctx)
        zone = _publication_zone(partner, None)
        first = _day_bounds(from_date, zone)[0]
        last = _day_bounds(to_date, zone)[1]
        published = published_days(ctx.db, partner, first, last)
        openings = [
            (utc(row.starts_at), utc(row.ends_at))
            for row in _opening_rows(ctx.db, partner.id, first, last)
        ]
        busy = busy_windows(ctx.db, partner.user_id, first, last, now_utc())
        return {
            "time_zone": zone.key,
            "days": [
                _day_output(day, published.get(day), openings, busy, zone)
                for day in (from_date + timedelta(days=i) for i in range(days))
            ],
        }


def _publish(ctx, partner, day, zone, version, windows, starts, now):
    db = ctx.db
    _editable_date(day, zone, now)
    first, last = _day_bounds(day, zone)
    published = db.get(YouthPartnerCalendarDay, (partner.id, day))
    current = published.version if published and not published.is_del else 0
    if version != current:
        raise conflict("calendar_version_conflict", "档期已更新，请刷新后再保存")
    ranges, starts = _dated_plan(day, windows, starts, zone)
    busy = busy_windows(db, partner.user_id, first, last, now)
    protected = protected_youth_starts(db, partner.user_id, first, last, now)
    merged = _merged(ranges)
    if not protected <= set(starts) or any(
        end > now and not any(a <= max(start, first) and min(end, last) <= b for a, b in merged)
        for start, end in busy
    ):
        raise conflict("appointment_slot_in_use", "修改会影响已预约的时段")

    # 旧版窗口可能跨越新日历的午夜，仅替换目标日期相交的部分。
    replacement = list(ranges)
    for old in _opening_rows(db, partner.id, first, last):
        start, end = utc(old.starts_at), utc(old.ends_at)
        old.is_del = True
        stamp(old, ctx.actor_id)
        if start < first:
            replacement.append((start, first))
        if end > last:
            replacement.append((last, end))
    for start, end in replacement:
        db.add(
            new_row(
                YouthPartnerAvailability,
                ctx.actor_id,
                partner_id=partner.id,
                starts_at=start,
                ends_at=end,
            )
        )
    if published is None:
        published = new_row(
            YouthPartnerCalendarDay, ctx.actor_id, partner_id=partner.id, schedule_date=day
        )
        db.add(published)
    published.version, published.booking_starts, published.is_del = current + 1, starts, False
    stamp(published, ctx.actor_id)
    if partner.calendar_time_zone is None:
        partner.calendar_time_zone = zone.key
        stamp(partner, ctx.actor_id)
    # 跨日窗口保留片段须对同一批次的下一个日期可见。
    db.flush()
    return _day_output(day, published, ranges, busy, zone)


def publish_date(ctx, day, body):
    with ctx.db.begin():
        partner = _partner(ctx, write=True)
        zone = _publication_zone(partner, body.time_zone)
        windows, starts = _local_plan(body)
        return _publish(ctx, partner, day, zone, body.version, windows, starts, now_utc())


def apply_templates(ctx, body):
    with ctx.db.begin():
        partner = _partner(ctx, write=True)
        zone = _publication_zone(partner, body.time_zone)
        now = now_utc()
        plans, skipped = [], []
        for item in sorted(body.dates, key=lambda entry: entry.date):
            day = item.date
            _editable_date(day, zone, now)
            first, last = _day_bounds(day, zone)
            if body.mode == "fill_unset" and (
                published_days(ctx.db, partner, first, last)
                or _opening_rows(ctx.db, partner.id, first, last)
            ):
                skipped.append({"date": day, "reason": "already_configured"})
                continue
            kind = item.template_kind or ("workday" if day.weekday() < 5 else "restday")
            template = ctx.db.get(YouthPartnerAvailabilityTemplate, (partner.id, kind))
            if template is None or template.is_del:
                raise BenefitError(422, "calendar_template_missing", "所选日期的模板尚未配置")
            plans.append((day, item.version if body.mode == "replace" else 0, template))
        result = [
            _publish(
                ctx,
                partner,
                day,
                zone,
                version,
                template.windows,
                template.booking_start_minutes,
                now,
            )
            for day, version, template in plans
        ]
        return {"time_zone": zone.key, "published_days": result, "skipped_days": skipped}


def booking_calendar(ctx, balance_id, partner_id, location_id):
    with ctx.db.begin():
        balance, grant, version = balance_for(ctx, balance_id, require_available=False)
        location = location_for(ctx, grant.id, location_id)
        partner = eligible_partner(ctx, version.id, partner_id, location.area_code)
        if partner.user_id == ctx.actor_id:
            raise missing()
        zone = _zone(location.time_zone)
        now = now_utc()
        today = now.astimezone(zone).date()
        days = {today + timedelta(days=i): [] for i in range(HORIZON_DAYS)}
        for slot in classify_slots(ctx.db, partner, location, version, grant, now, balance=balance):
            starts_at = slot["starts_at"].astimezone(zone)
            days[starts_at.date()].append(
                {
                    **slot,
                    "starts_at": starts_at,
                    "ends_at": slot["ends_at"].astimezone(zone),
                }
            )
        return {
            "time_zone": zone.key,
            "today": today,
            "location_id": location.id,
            "service_duration_minutes": version.offline_duration_minutes,
            "days": [
                {
                    "date": day,
                    "available_count": sum(s["status"] == "available" for s in slots),
                    "slots": slots,
                }
                for day, slots in days.items()
            ],
        }
