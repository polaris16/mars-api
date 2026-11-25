"""[新增] 用 Bearer、Mock 存储和隔离数据库验证青年伙伴预约。"""

from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from io import BytesIO
from logging import LogRecord
from uuid import uuid4
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy import select

from app.db.database import get_db
from app.main import MockStorageAccessFilter, app
from app.models import BenefitBalance, BenefitOrderEvent, YouthPartnerIdentityAccess
from app.services import youth_partner_bookings, youth_partner_schedule
from test_benefit_flows import FULFILL, OPS, USER, benefits, configure, send
from test_user_relationships import client

PROFILE = f"{USER}/youth-partner-profile"
PHOTO = f"{USER}/youth-partner-photo"
IDENTITY = f"{USER}/youth-partner-identity"
ZONE = ZoneInfo("Asia/Shanghai")


def test_signed_mock_paths_are_redacted_from_access_logs():
    message = '%s - "%s %s HTTP/%s" %d'
    record = LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        message,
        ("127.0.0.1", "GET", "/api/mock-storage/download/private-token", "1.1", 200),
        None,
    )
    assert MockStorageAccessFilter().filter(record)
    assert "private-token" not in record.getMessage()
    assert "/api/mock-storage/download/[redacted]" in record.getMessage()


def upload(client, actor, path, color, **extra):
    raw = BytesIO()
    Image.new("RGB", (40, 60), color).save(raw, format="PNG")
    data = raw.getvalue()
    granted = send(
        client,
        "POST",
        f"{path}/upload-authorizations",
        actor,
        {
            "filename": "photo.png",
            "content_type": "image/png",
            "size_bytes": len(data),
            **extra,
        },
    )
    reply = client.put(granted["upload_url"], headers=granted["required_headers"], content=data)
    assert reply.status_code == 200
    saved = send(
        client, "POST", f"{path}/complete", actor, {"asset_id": granted["asset_id"], **extra}
    )
    assert "download_url" not in saved and "image_url" not in saved
    return saved["asset_id"]


def prepare(benefits, *, mixed=False):
    client, admin, customer, partner, enterprise, batch, version, now = configure(
        benefits, youth=True, different_description=mixed
    )
    grant = send(
        client,
        "POST",
        f"/api/enterprises/{enterprise}/benefit-purchases/{batch}/issues",
        admin,
        {"items": [{"recipient_phone": "13800000002"}]},
        201,
        uuid4(),
    )["items"][0]["grant_id"]
    location_path = f"/api/enterprises/{enterprise}/benefit-grants/{grant}/service-locations"
    location = {
        "name": "社区",
        "address": "测试地址",
        "area_code": "A1",
        "time_zone": "Asia/Shanghai",
        "travel_before_minutes": 15,
        "travel_after_minutes": 30,
    }
    saved = send(client, "PUT", location_path, admin, {"items": [location]})[0]
    send(client, "GET", location_path, customer, expected=404)
    claim = send(client, "POST", f"{USER}/benefit-claims", customer, {"grant_id": grant}, 201)
    card = claim["cards"][0]
    assert (card["appointment_handler"], card["offline_duration_minutes"]) == ("youth_partner", 45)
    photo = upload(client, partner, PHOTO, "blue")
    application = send(
        client,
        "PUT",
        PROFILE,
        partner,
        {
            "display_name": "青青",
            "tags": ["数码达人"],
            "photo_asset_id": photo,
            "requested_service_version_ids": [version],
            "requested_area_codes": ["A1"],
        },
    )
    partner_id = application["id"]
    send(client, "POST", f"{PROFILE}/submit", partner, expected=409)
    first = upload(client, partner, IDENTITY, "red", side="portrait")
    second = upload(client, partner, IDENTITY, "green", side="emblem")
    send(client, "GET", f"/api/media-assets/{first}", partner, expected=404)
    send(
        client,
        "POST",
        f"{IDENTITY}/complete",
        partner,
        {"side": "portrait", "asset_id": second},
        404,
    )
    assert send(client, "POST", f"{PROFILE}/submit", partner)["status"] == "pending_review"
    review = f"{OPS}/youth-partners/{partner_id}"
    send(client, "GET", f"{review}/identity-documents", admin, expected=403)
    send(
        client,
        "PUT",
        f"{OPS}/benefit-staff/1",
        admin,
        {"roles": ["dispatcher", "identity_reviewer"]},
    )
    output = send(client, "GET", f"{review}/identity-documents", admin)
    assert set(output) == {"portrait", "emblem"}
    assert client.get(output["portrait"]["download_url"]).status_code == 200
    assert send(client, "GET", f"{OPS}/youth-partners?status=pending_review", admin)["total"] == 1
    approved = send(
        client,
        "POST",
        f"{review}/review",
        admin,
        {
            "decision": "approve",
            "approved_service_version_ids": [version],
            "approved_area_codes": ["A1"],
        },
    )
    assert approved["status"] == "approved"
    send(client, "GET", f"{review}/identity-documents", admin, expected=404)
    start = datetime.combine(now.astimezone(ZONE).date() + timedelta(days=1), time(10), tzinfo=ZONE)
    open_path = f"{USER}/youth-partner-availability?date={start.date()}"
    send(
        client,
        "PUT",
        open_path,
        partner,
        {
            "windows": [
                {
                    "starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=5)).isoformat(),
                },
            ]
        },
    )
    return (
        client,
        admin,
        customer,
        partner,
        card["balance_id"],
        saved,
        location_path,
        partner_id,
        start,
    )


def test_youth_partner_identity_reservation_check_in_and_review(benefits, monkeypatch):
    client, admin, customer, partner, balance, location, location_path, partner_id, start = prepare(
        benefits
    )
    base = f"{USER}/benefit-balances/{balance}"
    places = send(client, "GET", f"{base}/service-locations", customer)
    assert places[0]["id"] == location["id"]
    path = f"{base}/youth-partners?location_id={location['id']}"
    candidates = send(client, "GET", path, customer)["items"]
    assert len(candidates) == 1 and candidates[0]["partner_id"] == partner_id
    assert candidates[0]["rating_count"] == 0 and candidates[0]["availability_label"] == "tomorrow"
    slots_url = f"{base}/youth-partners/{partner_id}/slots?location_id={location['id']}"
    slot = send(client, "GET", slots_url, customer)["slots"][0]
    request = {
        "partner_id": partner_id,
        "location_id": location["id"],
        "starts_at": slot["starts_at"],
    }
    send(client, "POST", f"{base}/service-orders", customer, {}, 409, uuid4())
    key = uuid4()
    created = send(client, "POST", f"{base}/youth-partner-bookings", customer, request, 201, key)
    assert created["booking_status"] == "pending_confirmation"
    order_id = created["id"]
    assert (
        send(client, "POST", f"{base}/youth-partner-bookings", customer, request, key=key)["id"]
        == order_id
    )
    send(client, "POST", f"{base}/youth-partner-bookings", customer, request, 409, uuid4())
    read = f"{FULFILL}/{order_id}"
    assert "address" not in send(client, "GET", read, partner)["location"]
    send(
        client,
        "POST",
        f"{read}/actions",
        admin,
        {"action": "start", "expected_version": 1},
        409,
        uuid4(),
    )
    action = f"{read}/youth-partner-actions"
    send(client, "POST", action, partner, {"action": "confirm"}, key=uuid4())
    send(client, "PUT", location_path, admin, {"items": []})
    assert send(client, "GET", read, partner)["location"]["address"] == "测试地址"
    customer_order = f"{USER}/benefit-service-orders/{order_id}"
    send(client, "POST", f"{customer_order}/check-in-code", customer, expected=409)
    send(client, "POST", f"{customer_order}/rating", customer, {"score": 5}, 409)
    monkeypatch.setattr(
        youth_partner_bookings,
        "now_utc",
        lambda: start.astimezone(timezone.utc) + timedelta(minutes=15),
    )
    qr = send(client, "POST", f"{customer_order}/check-in-code", customer)
    renewed = send(client, "POST", f"{customer_order}/check-in-code", customer)
    scan = f"{FULFILL}/youth-partner-check-in"
    payload = {"qr_payload": renewed["qr_payload"]}
    send(client, "POST", scan, partner, {"qr_payload": qr["qr_payload"]}, 404, uuid4())
    send(client, "POST", scan, admin, payload, 404, uuid4())
    replay = uuid4()
    started = send(client, "POST", scan, partner, payload, key=replay)
    assert (started["booking_status"], started["reserved_count"], started["used_count"]) == (
        "in_service",
        0,
        1,
    )
    assert send(client, "POST", scan, partner, payload, key=replay)["used_count"] == 1
    send(client, "POST", scan, partner, payload, 409, uuid4())
    done = send(client, "POST", action, partner, {"action": "finish"}, key=uuid4())
    assert done["booking_status"] == "completed" and done["used_count"] == 1
    send(client, "POST", f"{customer_order}/rating", customer, {"score": 5})
    send(client, "POST", f"{customer_order}/rating", customer, {"score": 1}, 409)
    assert send(client, "GET", customer_order, customer)["rating"]["score"] == 5
    candidate = send(client, "GET", f"{base}/youth-partners?sort=rating_desc", customer)["items"][0]
    assert (
        candidate["completed_service_count"],
        candidate["average_rating"],
        candidate["rating_count"],
    ) == (1, 5, 1)
    with contextmanager(app.dependency_overrides[get_db])() as db:
        assert len(list(db.scalars(select(YouthPartnerIdentityAccess)))) == 1
        events = list(
            db.scalars(
                select(BenefitOrderEvent)
                .where(BenefitOrderEvent.service_order_id == order_id)
                .order_by(BenefitOrderEvent.order_version)
            )
        )
        assert [(e.action, e.reserved_delta, e.used_delta) for e in events] == [
            ("create", 1, 0),
            ("confirm_booking", 0, 0),
            ("check_in", -1, 1),
            ("finish_partner_service", 0, 0),
        ]


def test_youth_partner_rejection_and_expiry_release_quota(benefits, monkeypatch):
    client, _, customer, partner, balance, location, _, partner_id, start = prepare(benefits)
    base = f"{USER}/benefit-balances/{balance}/youth-partner-bookings"
    request = {
        "partner_id": partner_id,
        "location_id": location["id"],
        "starts_at": (start + timedelta(minutes=15)).isoformat(),
    }
    order = send(client, "POST", base, customer, request, 201, uuid4())
    action = f"{FULFILL}/{order['id']}/youth-partner-actions"
    rejected = send(
        client,
        "POST",
        action,
        partner,
        {"action": "reject", "reason_code": "unavailable", "reason_text": "临时有事"},
        key=uuid4(),
    )
    assert rejected["booking_status"] == "rejected" and rejected["reserved_count"] == 0
    detail = send(client, "GET", f"{USER}/benefit-service-orders/{order['id']}", customer)
    assert detail["reason_text"] == "临时有事"
    second = send(client, "POST", base, customer, request, 201, uuid4())
    expired_at = datetime.fromisoformat(second["confirm_by"].replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc
    )
    monkeypatch.setattr(youth_partner_bookings, "now_utc", lambda: expired_at)
    with contextmanager(app.dependency_overrides[get_db])() as db:
        assert youth_partner_bookings.expire_bookings(db) == 1
        assert youth_partner_bookings.expire_bookings(db) == 0
        assert db.get(BenefitBalance, balance).reserved_count == 0
    detail = send(client, "GET", f"{USER}/benefit-service-orders/{second['id']}", customer)
    assert detail["booking_status"] == "confirmation_expired"


def test_youth_partner_shared_schedule_and_unattended_booking(benefits, monkeypatch):
    client, admin, customer, partner, balance, location, _, partner_id, start = prepare(
        benefits, mixed=True
    )
    base = f"{USER}/benefit-balances/{balance}/youth-partner-bookings"
    request = {
        "partner_id": partner_id,
        "location_id": location["id"],
        "starts_at": (start + timedelta(minutes=15)).isoformat(),
    }
    order = send(client, "POST", base, customer, request, 201, uuid4())
    with contextmanager(app.dependency_overrides[get_db])() as db:
        other = db.scalar(select(BenefitBalance.id).where(BenefitBalance.id != balance))
    regular = send(
        client,
        "POST",
        f"{USER}/benefit-balances/{other}/service-orders",
        customer,
        {},
        201,
        uuid4(),
    )
    generic_action = {
        "action": "confirm_booking",
        "expected_version": 1,
        "assignee_user_id": 3,
        "scheduled_start_at": start.isoformat(),
        "scheduled_end_at": (start + timedelta(minutes=15)).isoformat(),
    }
    # 普通派单与实际服务不重叠，但与预约前的通勤重叠。
    send(client, "POST", f"{FULFILL}/{regular['id']}/actions", admin, generic_action, 409, uuid4())
    cancel_path = f"{USER}/benefit-service-orders/{order['id']}/cancel"
    cancelled = send(
        client,
        "POST",
        cancel_path,
        customer,
        {"reason": "changed_mind", "expected_version": 1},
        key=uuid4(),
    )
    assert cancelled["booking_status"] == "cancelled" and cancelled["reserved_count"] == 0
    send(client, "POST", f"{FULFILL}/{regular['id']}/actions", admin, generic_action, key=uuid4())
    send(client, "POST", base, customer, request, 409, uuid4())
    request["starts_at"] = (start + timedelta(minutes=30)).isoformat()
    booked = send(client, "POST", base, customer, request, 201, uuid4())
    action = f"{FULFILL}/{booked['id']}/youth-partner-actions"
    send(client, "POST", action, partner, {"action": "confirm"}, key=uuid4())
    end = start.astimezone(timezone.utc) + timedelta(minutes=75)
    monkeypatch.setattr(youth_partner_bookings, "now_utc", lambda: end)
    with contextmanager(app.dependency_overrides[get_db])() as db:
        assert youth_partner_bookings.expire_bookings(db) == 1
    detail = send(client, "GET", f"{USER}/benefit-service-orders/{booked['id']}", customer)
    assert (detail["booking_status"], detail["reserved_count"], detail["used_count"]) == (
        "no_show",
        0,
        0,
    )
