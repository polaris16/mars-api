"""[新增] 青年伙伴预约、权益次数、到场核销和评分。"""

import hmac
import json
import secrets
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import and_, or_, select

from app.models import (
    BenefitBalance,
    BenefitOrderEvent,
    BenefitServiceOrder,
    User,
    YouthPartnerBooking,
    YouthPartnerCheckInCode,
    YouthPartnerProfile,
    YouthPartnerRating,
)
from app.services.benefit_common import (
    BenefitContext,
    BenefitKeys,
    active_row,
    conflict,
    display,
    flush_unique,
    missing,
    new_row,
    now_utc,
    stamp,
    utc,
)
from app.services.benefit_orders import BenefitOrderService, EVENT_FIELDS
from app.services.youth_partner_schedule import (
    MIN_LEAD,
    classify_slots,
    balance_for,
    eligible_partner,
    location_for,
    location_view,
)

orders = BenefitOrderService()
EARLY_CHECK_IN = timedelta(minutes=15)
CODE_TTL = timedelta(minutes=5)
CONFIRM_MAX = timedelta(hours=12)


def active_booking(db, order_id):
    booking = db.get(YouthPartnerBooking, order_id)
    return booking if booking and not booking.is_del else None


def _expiry(row, booking, now):
    if row.status != "reserved":
        return None
    if booking.confirmed_at is None:
        return "confirmation_expired" if utc(booking.confirm_by) <= now else None
    if utc(row.scheduled_end_at) <= now:
        return "no_show"
    return None


def _can_check_in(row, booking, now):
    return (
        row.status == "reserved"
        and booking.confirmed_at is not None
        and utc(row.scheduled_start_at) - EARLY_CHECK_IN <= now < utc(row.scheduled_end_at)
    )


def order_view(ctx, row, balance, booking=None, *, fulfill=False, detail=False, at=None):
    if booking is None:
        booking = active_booking(ctx.db, row.id)
    if booking is None:
        return orders.view(row, balance, private=detail)
    now = at or now_utc()
    status = row.request_data.get("booking_outcome") or _expiry(row, booking, now)
    if status is None:
        status = {
            "reserved": "confirmed" if booking.confirmed_at else "pending_confirmation",
            "in_progress": "in_service",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[row.status]
    actions = []
    is_assignee = row.assignee_user_id == ctx.actor_id
    if fulfill and is_assignee:
        actions = {
            "pending_confirmation": ["confirm", "reject"],
            "confirmed": ["cancel"] + (["check_in"] if _can_check_in(row, booking, now) else []),
            "in_service": ["finish"],
        }.get(status, [])
    if not fulfill:
        if status in {"pending_confirmation", "confirmed"}:
            actions.append("cancel")
        if _can_check_in(row, booking, now):
            actions.append("check_in_code")
    rating = (
        ctx.db.get(YouthPartnerRating, row.id) if not fulfill and status == "completed" else None
    )
    if not fulfill and status == "completed" and rating is None:
        actions.append("rating")
    view = {
        **orders.view(row, balance),
        "booking_status": status,
        "order_id": row.id,
        **display(booking, "confirm_by confirmed_at checked_in_at service_duration_seconds"),
        "location": dict(row.request_data["location"]),
        "reason_text": row.request_data.get("reason_text"),
        "available_actions": actions,
        "rating": display(rating, "score comment created_at") if rating else None,
        "reserved_count": balance.reserved_count,
        "used_count": balance.used_count,
    }
    if detail and (not fulfill or (is_assignee and booking.confirmed_at is not None)):
        view["location"].update(
            json.loads(
                BenefitKeys().open(
                    row.private_data_key_version, "order-private", row.private_data_ciphertext
                )
            )
        )
    return view


def create_booking(ctx, balance_id, idem, body):
    keys = BenefitKeys()
    starts_at = utc(body.starts_at)
    submitted = {
        "balance_id": balance_id,
        **body.model_dump(mode="json"),
        "starts_at": starts_at.isoformat(),
    }
    with ctx.db.begin():
        active_row(ctx.db, User, ctx.actor_id, lock=True)
        # 对已提交的请求先查幂等键，历史地点被停用后仍可重放。
        previous = ctx.db.scalar(
            select(BenefitServiceOrder).where(
                BenefitServiceOrder.requested_by_user_id == ctx.actor_id,
                BenefitServiceOrder.idempotency_key == idem,
            )
        )
        if previous is not None:
            digest = keys.fingerprint(previous.digest_key_version, "youth-booking", submitted)
            if previous.is_del or not hmac.compare_digest(digest, previous.request_digest):
                raise conflict("idempotency_conflict", "该幂等键已用于不同服务申请")
            balance, _, _ = balance_for(ctx, balance_id, require_available=False)
            return order_view(ctx, previous, balance, detail=True), False
        profile = active_row(ctx.db, YouthPartnerProfile, body.partner_id)
        if profile.user_id == ctx.actor_id:
            raise conflict("partner_self_booking", "不能为自己预约本人提供的服务")
        # 通用派单和青年伙伴操作都先锁履约岗位，再锁余额与订单。
        if profile.user_id not in orders.lock_providers(ctx.db, None, profile.user_id):
            raise conflict("benefit_provider_unavailable", "该伙伴当前不可预约")
        balance, grant, version = balance_for(ctx, balance_id, lock=True)
        location = location_for(ctx, grant.id, body.location_id, lock=True)
        profile = eligible_partner(ctx, version.id, profile.id, location.area_code)
        now = now_utc()
        slot = next(
            (
                slot
                for slot in classify_slots(ctx.db, profile, location, version, grant, now)
                if slot["starts_at"] == starts_at
            ),
            None,
        )
        if slot is not None and slot["reason"] == "recipient_schedule_conflict":
            raise conflict("recipient_schedule_conflict", "您在该时段已有其他青年伙伴预约")
        if slot is None or slot["status"] != "available":
            raise conflict("appointment_slot_unavailable", "该时段已不可预约，请刷新档期")
        ends_at = slot["ends_at"]
        full_location = location_view(location, keys)
        public_location = {
            key: full_location[key]
            for key in (
                "id",
                "area_code",
                "time_zone",
                "travel_before_minutes",
                "travel_after_minutes",
            )
        }
        private = {key: full_location[key] for key in ("name", "address", "arrival_notes")}
        private["time_zone"] = location.time_zone
        digest = keys.fingerprint(keys.current, "youth-booking", submitted)
        row = new_row(
            BenefitServiceOrder,
            ctx.actor_id,
            balance_id=balance.id,
            requested_by_user_id=ctx.actor_id,
            idempotency_key=idem,
            request_digest=digest,
            digest_key_version=keys.current,
            status="reserved",
            scheduled_start_at=starts_at,
            scheduled_end_at=ends_at,
            reserved_at=now,
            assignee_user_id=profile.user_id,
            request_data={"location": public_location},
            private_data_ciphertext=keys.seal(keys.current, "order-private", json.dumps(private)),
            private_data_key_version=keys.current,
        )
        ctx.db.add(row)
        flush_unique(ctx.db)
        booking = new_row(
            YouthPartnerBooking,
            ctx.actor_id,
            service_order_id=row.id,
            partner_id=profile.id,
            location_id=location.id,
            blocking_start_at=starts_at - timedelta(minutes=location.travel_before_minutes),
            blocking_end_at=ends_at + timedelta(minutes=location.travel_after_minutes),
            confirm_by=min(now + CONFIRM_MAX, starts_at - MIN_LEAD),
        )
        ctx.db.add(booking)
        balance.reserved_count += 1
        stamp(balance, ctx.actor_id)
        orders.record_event(ctx, row, idem, keys.current, digest, "create", None, 1)
        return order_view(ctx, row, balance, booking, detail=True), True


def _locked_order(ctx, order_id, *, fulfill=False, system=False):
    stmt = orders.order_statement().where(BenefitServiceOrder.id == order_id)
    if not system:
        stmt, _ = orders.access_scope(ctx, stmt, fulfill=fulfill)
        if fulfill:
            stmt = stmt.where(BenefitServiceOrder.assignee_user_id == ctx.actor_id)
    original = ctx.db.execute(
        stmt.with_only_columns(
            BenefitServiceOrder.balance_id,
            BenefitServiceOrder.assignee_user_id,
        )
    ).one_or_none()
    if original is None or active_booking(ctx.db, order_id) is None:
        raise missing()
    providers = orders.lock_providers(ctx.db, None, original.assignee_user_id)
    balance = active_row(ctx.db, BenefitBalance, original.balance_id, lock=True)
    row = ctx.db.scalar(
        stmt.with_for_update(of=BenefitServiceOrder).execution_options(populate_existing=True)
    )
    booking = ctx.db.get(YouthPartnerBooking, order_id, populate_existing=True)
    if (
        row is None
        or not booking
        or booking.is_del
        or row.assignee_user_id != original.assignee_user_id
    ):
        raise missing()
    if fulfill and ctx.actor_id not in providers:
        raise missing()
    return row, booking, balance


def _previous(ctx, order_id, idem, keys, submitted):
    event = ctx.db.scalar(
        select(BenefitOrderEvent).where(
            BenefitOrderEvent.service_order_id == order_id,
            BenefitOrderEvent.action_key == idem,
        )
    )
    if event is not None:
        digest = keys.fingerprint(event.digest_key_version, "youth-action", submitted)
        if not hmac.compare_digest(digest, event.request_digest):
            raise conflict("idempotency_conflict", "该幂等键已用于不同服务动作")
    return event


def _record(
    ctx, row, balance, keys, idem, submitted, action, before, *, reserved=0, used=0, data=None
):
    row.version += 1
    stamp(row, ctx.actor_id)
    if reserved or used:
        balance.reserved_count += reserved
        balance.used_count += used
        stamp(balance, ctx.actor_id)
    return orders.record_event(
        ctx,
        row,
        idem,
        keys.current,
        keys.fingerprint(keys.current, "youth-action", submitted),
        action,
        before,
        reserved,
        used,
        data,
    )


def _close(row, now, outcome, reason_code, reason_text=None):
    row.status, row.finished_at, row.terminal_reason = "cancelled", now, reason_code
    row.request_data = {**row.request_data, "booking_outcome": outcome, "reason_text": reason_text}


def change_booking(ctx, order_id, idem, body, *, fulfill=True):
    keys = BenefitKeys()
    action = body.action if fulfill else "cancel"
    submitted = {"actor_id": ctx.actor_id, "action": action, **body.model_dump(mode="json")}
    with ctx.db.begin():
        row, booking, balance = _locked_order(ctx, order_id, fulfill=fulfill)
        if _previous(ctx, row.id, idem, keys, submitted):
            return order_view(ctx, row, balance, booking, fulfill=fulfill, detail=True)
        if not fulfill and body.expected_version != row.version:
            raise conflict("version_conflict", "服务单已变更，请刷新")
        now, before = now_utc(), row.status
        if _expiry(row, booking, now):
            raise conflict("order_state_conflict", "预约已过期")
        reserved, data = 0, {}
        if action == "finish":
            if before != "in_progress" or booking.checked_in_at is None:
                raise conflict("order_state_conflict", "请先完成到场核销")
            row.status, row.finished_at = "completed", now
            # 只记录预约区间内的履约秒数，随完成流水一次提交。
            begins = max(utc(booking.checked_in_at), utc(row.scheduled_start_at))
            ends = min(now, utc(row.scheduled_end_at))
            booking.service_duration_seconds = max(0, int((ends - begins).total_seconds()))
            stamp(booking, ctx.actor_id)
            row.result_ref = f"youth-partner/{row.id}/checked-in"
            event_action = "finish_partner_service"
        else:
            if before != "reserved" or (action in {"confirm", "reject"} and booking.confirmed_at):
                raise conflict("order_state_conflict", "当前预约状态不支持该操作")
            if action == "confirm":
                booking.confirmed_at = now
                stamp(booking, ctx.actor_id)
                event_action = "confirm_booking"
            else:
                if fulfill and action == "cancel" and booking.confirmed_at is None:
                    raise conflict("order_state_conflict", "请拒绝待确认预约并填写原因")
                reason = body.reason_code if fulfill else body.reason
                notes = body.reason_text if fulfill else None
                outcome = "rejected" if action == "reject" else "cancelled"
                _close(row, now, outcome, reason, notes)
                reserved, event_action = -1, "cancel"
                data = {"reason": reason, "reason_text": notes, "outcome": outcome}
        _record(
            ctx,
            row,
            balance,
            keys,
            idem,
            submitted,
            event_action,
            before,
            reserved=reserved,
            data=data,
        )
        return order_view(ctx, row, balance, booking, fulfill=fulfill, detail=True)


def issue_check_in(ctx, order_id):
    keys = BenefitKeys()
    with ctx.db.begin():
        row, booking, _ = _locked_order(ctx, order_id)
        now = now_utc()
        if not _can_check_in(row, booking, now):
            raise conflict("partner_check_in_window", "仅在已确认预约的到场时间内可生成二维码")
        code = ctx.db.get(YouthPartnerCheckInCode, row.id)
        if code is None:
            code = new_row(YouthPartnerCheckInCode, ctx.actor_id, service_order_id=row.id)
            ctx.db.add(code)
        token = secrets.token_urlsafe(32)
        code.token_digest = keys.digest(keys.current, "youth-check-in", token)
        code.crypto_key_version = keys.current
        code.expires_at = min(now + CODE_TTL, utc(row.scheduled_end_at))
        code.consumed_at, code.is_del = None, False
        stamp(code, ctx.actor_id)
        return {"qr_payload": f"{keys.current}.{token}", "expires_at": code.expires_at}


def report_booking_failure(ctx, order_id, idem, body):
    keys = BenefitKeys()
    submitted = {"actor_id": ctx.actor_id, **body.model_dump(mode="json")}
    with ctx.db.begin():
        ctx.require_platform("dispatcher")
        row, booking, balance = _locked_order(ctx, order_id, system=True)
        event = _previous(ctx, row.id, idem, keys, submitted)
        if event is None:
            if row.version != body.expected_version:
                raise conflict("version_conflict", "服务单已变更，请刷新")
            if row.status != "in_progress" or booking.checked_in_at is None:
                raise conflict("order_state_conflict", "仅能登记已核销服务的履约异常")
            row.status, row.finished_at, row.terminal_reason = "failed", now_utc(), body.reason
            event = _record(
                ctx,
                row,
                balance,
                keys,
                idem,
                submitted,
                "fail",
                "in_progress",
                data={"reason": body.reason},
            )
        return {
            "order": order_view(ctx, row, balance, booking, fulfill=True),
            "event": display(event, EVENT_FIELDS),
        }


def scan_check_in(ctx, idem, payload):
    keys = BenefitKeys()
    version_text, token = payload.split(".", 1)
    version = int(version_text)
    if version not in keys.keys:
        raise missing()
    token_digest = keys.digest(version, "youth-check-in", token)
    submitted = {"actor_id": ctx.actor_id, "action": "check_in", "token_digest": token_digest.hex()}
    with ctx.db.begin():
        order_id = ctx.db.scalar(
            select(YouthPartnerCheckInCode.service_order_id).where(
                YouthPartnerCheckInCode.token_digest == token_digest,
                YouthPartnerCheckInCode.is_del.is_(False),
            )
        )
        if order_id is None:
            raise missing()
        row, booking, balance = _locked_order(ctx, order_id, fulfill=True)
        code = ctx.db.get(YouthPartnerCheckInCode, row.id, populate_existing=True)
        if code.is_del or not hmac.compare_digest(code.token_digest, token_digest):
            raise missing()
        if _previous(ctx, row.id, idem, keys, submitted):
            return order_view(ctx, row, balance, booking, fulfill=True, detail=True)
        now = now_utc()
        if code.consumed_at or utc(code.expires_at) <= now or not _can_check_in(row, booking, now):
            raise conflict("partner_check_in_unavailable", "二维码已核销、失效或不在到场时间内")
        code.consumed_at = booking.checked_in_at = now
        row.status, row.started_at = "in_progress", now
        stamp(code, ctx.actor_id)
        stamp(booking, ctx.actor_id)
        _record(
            ctx, row, balance, keys, idem, submitted, "check_in", "reserved", reserved=-1, used=1
        )
        return order_view(ctx, row, balance, booking, fulfill=True, detail=True)


def rate_booking(ctx, order_id, body):
    with ctx.db.begin():
        row, booking, _ = _locked_order(ctx, order_id)
        if row.status != "completed" or booking.checked_in_at is None:
            raise conflict("partner_rating_unavailable", "完成服务后才可评价")
        rating = ctx.db.get(YouthPartnerRating, row.id)
        if rating is not None:
            if rating.is_del or (rating.score, rating.comment) != (body.score, body.comment):
                raise conflict("partner_rating_exists", "该次服务已评价")
        else:
            rating = new_row(
                YouthPartnerRating,
                ctx.actor_id,
                service_order_id=row.id,
                score=body.score,
                comment=body.comment,
            )
            ctx.db.add(rating)
            ctx.db.flush()
        return display(rating, "score comment created_at")


def expire_bookings(db, limit=100):
    now = now_utc()
    with db.begin():
        booking, order = YouthPartnerBooking, BenefitServiceOrder
        candidates = list(
            db.scalars(
                orders.order_statement()
                .with_only_columns(order.id)
                .join(booking, booking.service_order_id == order.id)
                .where(
                    order.is_del.is_(False),
                    booking.is_del.is_(False),
                    order.status == "reserved",
                    or_(
                        and_(booking.confirmed_at.is_(None), booking.confirm_by <= now),
                        and_(booking.confirmed_at.is_not(None), order.scheduled_end_at <= now),
                    ),
                )
                .order_by(order.id)
                .limit(limit)
            )
        )
    if not candidates:
        return 0
    keys, ctx, count = BenefitKeys(), BenefitContext(db, None), 0
    for order_id in candidates:
        with db.begin():
            row, booking, balance = _locked_order(ctx, order_id, system=True)
            reason = _expiry(row, booking, now)
            if reason:
                _close(row, now, reason, reason)
                idem = uuid5(NAMESPACE_URL, f"mars:youth-booking-expiry:{order_id}")
                _record(
                    ctx,
                    row,
                    balance,
                    keys,
                    idem,
                    {"reason": reason},
                    "cancel",
                    "reserved",
                    reserved=-1,
                    data={"reason": reason},
                )
                count += 1
    return count
