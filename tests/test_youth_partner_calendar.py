"""[新增] 覆盖青年伙伴日历发布、客户状态及重复预约拦截。"""

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.db.database import get_db
from app.main import app
from app.models import (
    BenefitBalance,
    BenefitGrant,
    BenefitIssueRequest,
    BenefitStaff,
    YouthPartnerAvailability,
    YouthPartnerProfile,
    YouthPartnerService,
)
from test_benefit_flows import FULFILL, USER, benefits, send
from test_user_relationships import client
from test_youth_partner_flows import prepare

CALENDAR = f"{USER}/youth-partner-calendar"
TEMPLATES = f"{USER}/youth-partner-availability-templates"
PLAN = {
    "windows": [{"start_local": "09:30", "end_local": "13:00"}],
    "booking_start_times": ["10:00", "11:10"],
}
CLOSED = {"windows": [], "booking_start_times": []}


def test_templates_copy_to_independent_dates_and_protect_manual_changes(benefits):
    client, _, customer, partner, _, _, _, _, start = prepare(benefits)
    legacy = start.date()
    first = legacy + timedelta(days=1)
    second = legacy + timedelta(days=2)
    query = f"{CALENDAR}?from_date={legacy}&to_date={second}"
    assert [d["status"] for d in send(client, "GET", query, partner)["days"]] == [
        "legacy",
        "unset",
        "unset",
    ]
    send(client, "GET", query, customer, expected=403)
    missing_templates = send(client, "GET", TEMPLATES, partner)["items"]
    assert all(not row["is_configured"] for row in missing_templates)
    send(client, "PUT", f"{TEMPLATES}/workday", partner, PLAN)
    copy = {
        "dates": [
            {"date": str(legacy), "template_kind": "workday"},
            {"date": str(first), "template_kind": "workday"},
            {"date": str(second), "template_kind": "restday"},
        ]
    }
    unavailable = send(client, "POST", f"{CALENDAR}/apply-templates", partner, copy, 422)
    assert unavailable["code"] == "calendar_template_missing"
    assert send(client, "GET", query, partner)["days"][1]["status"] == "unset"
    send(client, "PUT", f"{TEMPLATES}/restday", partner, CLOSED)
    issued = send(client, "POST", f"{CALENDAR}/apply-templates", partner, copy)
    assert issued["skipped_days"] == [{"date": str(legacy), "reason": "already_configured"}]
    first_day, second_day = issued["published_days"]
    assert first_day["version"] == 1 and first_day["booking_start_times"] == ["10:00", "11:10"]
    assert second_day["status"] == "closed"
    again = send(client, "POST", f"{CALENDAR}/apply-templates", partner, copy)
    assert not again["published_days"] and len(again["skipped_days"]) == 3

    new_plan = {**PLAN, "booking_start_times": ["11:20"]}
    send(client, "PUT", f"{TEMPLATES}/workday", partner, new_plan)
    assert send(client, "GET", query, partner)["days"][1] == first_day
    outdated = send(client, "PUT", f"{CALENDAR}/{first}", partner, {"version": 0, **CLOSED}, 409)
    assert outdated["code"] == "calendar_version_conflict"
    updated = send(
        client,
        "POST",
        f"{CALENDAR}/apply-templates",
        partner,
        {
            "mode": "replace",
            "dates": [{"date": str(first), "template_kind": "workday", "version": 1}],
        },
    )
    assert updated["published_days"][0]["booking_start_times"] == ["11:20"]
    assert updated["published_days"][0]["version"] == 2
    rolled_back = send(
        client,
        "POST",
        f"{CALENDAR}/apply-templates",
        partner,
        {
            "mode": "replace",
            "dates": [
                {"date": str(first), "template_kind": "restday", "version": 2},
                {"date": str(second), "template_kind": "workday", "version": 0},
            ],
        },
        409,
    )
    assert rolled_back["code"] == "calendar_version_conflict"
    assert send(client, "GET", query, partner)["days"][1] == updated["published_days"][0]
    malformed = {**PLAN, "booking_start_times": ["13:00"]}
    send(client, "PUT", f"{TEMPLATES}/workday", partner, malformed, 422)
    old_put = f"{USER}/youth-partner-availability?date={first}"
    assert (
        send(client, "PUT", old_put, partner, {"windows": []}, 409)["code"]
        == "calendar_day_managed"
    )


def test_booking_calendar_shows_exact_starts_occupancy_and_location_buffers(benefits):
    client, admin, customer, partner, balance, location, place_path, partner_id, start = prepare(
        benefits
    )
    # 让两场服务的通勤互不重叠，以便单独观察第二张锁定卡片。
    location = send(
        client,
        "PUT",
        place_path,
        admin,
        {
            "items": [
                {**location, "travel_before_minutes": 10, "travel_after_minutes": 10},
            ]
        },
    )[0]
    day = start.date().isoformat()
    send(client, "PUT", f"{CALENDAR}/{day}", partner, {"version": 0, **PLAN})
    base = f"{USER}/benefit-balances/{balance}"
    slots = f"{base}/youth-partners/{partner_id}/slots?location_id={location['id']}"
    daily = f"{base}/youth-partners/{partner_id}/booking-calendar"
    calendar = send(client, "GET", f"{daily}?location_id={location['id']}", customer)
    assert len(calendar["days"]) == 7 and not calendar["days"][0]["slots"]
    when = calendar["days"][1]
    assert when["date"] == day and when["available_count"] == 2
    by_start = [s["starts_at"][11:16] for s in when["slots"]]
    assert by_start == ["10:00", "11:10"]
    second = when["slots"][1]
    assert second["ends_at"][11:16] == "11:55"
    assert len(send(client, "GET", slots, customer)["slots"]) == 2
    send(client, "GET", f"{daily}?location_id={location['id']}", partner, expected=404)
    booking_path = f"{base}/youth-partner-bookings"
    body = {
        "partner_id": partner_id,
        "location_id": location["id"],
        "starts_at": second["starts_at"],
    }
    send(
        client,
        "POST",
        booking_path,
        customer,
        {
            **body,
            "starts_at": f"{day}T11:15:00+08:00",
        },
        409,
        uuid4(),
    )
    booked = send(client, "POST", booking_path, customer, body, 201, uuid4())
    updated = send(client, "GET", f"{daily}?location_id={location['id']}", customer)["days"][1]
    assert updated["available_count"] == 1
    assert updated["slots"][1] == {**second, "status": "occupied"}
    blocked = send(client, "PUT", f"{CALENDAR}/{day}", partner, {"version": 1, **CLOSED}, 409)
    assert blocked["code"] == "appointment_slot_in_use"
    place_input = {key: value for key, value in location.items() if key != "id"}
    places = send(
        client,
        "PUT",
        place_path,
        admin,
        {
            "items": [
                location,
                {**place_input, "name": "Farther location", "travel_before_minutes": 60},
            ]
        },
    )
    other = next(p for p in places if p["id"] != location["id"])
    farther = send(client, "GET", f"{daily}?location_id={other['id']}", customer)["days"][1]
    assert [s["starts_at"][11:16] for s in farther["slots"]] == ["11:10"]
    release = f"{FULFILL}/{booked['id']}/youth-partner-actions"
    send(
        client,
        "POST",
        release,
        partner,
        {
            "action": "reject",
            "reason_code": "unavailable",
            "reason_text": "临时有事",
        },
        key=uuid4(),
    )
    reopened = send(client, "GET", f"{daily}?location_id={location['id']}", customer)["days"][1]
    assert reopened["available_count"] == 2


def test_booking_calendar_groups_published_starts_in_location_time_zone(benefits):
    client, _, customer, partner, balance, location, _, partner_id, start = prepare(benefits)
    day = start.date().isoformat()
    published = send(
        client,
        "PUT",
        f"{CALENDAR}/{day}",
        partner,
        {
            "version": 0,
            "time_zone": "UTC",
            "windows": [{"start_local": "21:00", "end_local": "23:59"}],
            "booking_start_times": ["21:40"],
        },
    )
    assert published["booking_start_times"] == ["21:40"]
    url = f"{USER}/benefit-balances/{balance}/youth-partners/{partner_id}/booking-calendar?location_id={location['id']}"
    result = send(client, "GET", url, customer)["days"][2]
    assert result["date"] == (start.date() + timedelta(days=1)).isoformat()
    assert result["slots"][0]["starts_at"][11:16] == "05:40"
    send(
        client,
        "PUT",
        f"{CALENDAR}/{day}",
        partner,
        {
            "version": 1,
            "time_zone": "Asia/Shanghai",
            **CLOSED,
        },
        409,
    )


def test_recipient_overlap_is_blocked_across_partners_and_balances(benefits):
    client, admin, customer, _, balance, location, place_path, first, start = prepare(benefits)
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        version_id = db.scalar(
            select(YouthPartnerService.service_version_id).where(
                YouthPartnerService.partner_id == first,
            )
        )
        old_grant_id = db.get(BenefitBalance, balance).grant_id
        issue = db.get(BenefitIssueRequest, db.get(BenefitGrant, old_grant_id).issue_request_id)
        batch_id = issue.purchase_batch_id
        other = YouthPartnerProfile(
            user_id=1, display_name="Another", status="approved", service_area_codes=["A1"]
        )
        db.add_all([other, BenefitStaff(user_id=1, role="provider", status="active")])
        db.flush()
        second = other.id
        utc_start = start.astimezone(timezone.utc)
        db.add_all(
            [
                YouthPartnerService(
                    partner_id=second, status="approved", service_version_id=version_id
                ),
                YouthPartnerAvailability(
                    partner_id=second, starts_at=utc_start, ends_at=utc_start + timedelta(hours=5)
                ),
            ]
        )
    organization = place_path.split("/benefit-grants/", 1)[0]
    grant = send(
        client,
        "POST",
        f"{organization}/benefit-purchases/{batch_id}/issues",
        admin,
        {"items": [{"recipient_phone": "13800000002"}]},
        201,
        uuid4(),
    )["items"][0]["grant_id"]
    place_input = {key: value for key, value in location.items() if key != "id"}
    new_place = send(
        client,
        "PUT",
        f"{organization}/benefit-grants/{grant}/service-locations",
        admin,
        {"items": [place_input]},
    )[0]["id"]
    other_balance = send(
        client, "POST", f"{USER}/benefit-claims", customer, {"grant_id": grant}, 201
    )["cards"][0]["balance_id"]
    starts_at = (start + timedelta(minutes=15)).isoformat()
    send(
        client,
        "POST",
        f"{USER}/benefit-balances/{balance}/youth-partner-bookings",
        customer,
        {"partner_id": first, "location_id": location["id"], "starts_at": starts_at},
        201,
        uuid4(),
    )
    root = f"{USER}/benefit-balances/{other_balance}"
    schedule = send(
        client,
        "GET",
        f"{root}/youth-partners/{second}/booking-calendar?location_id={new_place}",
        customer,
    )
    conflicting = next(s for s in schedule["days"][1]["slots"] if s["starts_at"] == starts_at)
    assert (
        conflicting["status"] == "unavailable"
        and conflicting["reason"] == "recipient_schedule_conflict"
    )
    req = {"partner_id": second, "location_id": new_place, "starts_at": starts_at}
    blocked = send(client, "POST", f"{root}/youth-partner-bookings", customer, req, 409, uuid4())
    assert blocked["code"] == "recipient_schedule_conflict"
    req["starts_at"] = (start + timedelta(minutes=60)).isoformat()
    send(client, "POST", f"{root}/youth-partner-bookings", customer, req, 201, uuid4())


def test_calendar_migration_is_additive_and_protects_existing_publications():
    output = StringIO()
    command.upgrade(
        Config("alembic.ini", output_buffer=output), "20261125_03:20261126_01", sql=True
    )
    sql = output.getvalue()
    assert set(re.findall(r"^CREATE TABLE (\w+) \(", sql, re.M)) == {
        "t_youth_partner_availability_template",
        "t_youth_partner_calendar_day",
    }
    assert re.findall(r"^UPDATE (\w+)", sql, re.M) == ["alembic_version"]
    assert "booking_starts TIMESTAMP WITH TIME ZONE[]" in sql
    assert "idx_benefit_order_recipient_schedule" in sql
    output = StringIO()
    command.downgrade(
        Config("alembic.ini", output_buffer=output), "20261126_01:20261125_03", sql=True
    )
    sql = output.getvalue()
    assert sql.index("must be preserved before downgrade") < sql.index("DROP TABLE")
