"""[新增] 企业权益从采购到 App 领取与线下核销的接口回归。"""

import hashlib
import re
from base64 import urlsafe_b64encode
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import JSON, Integer, text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.config import get_settings
from app.db.database import get_db
from app.main import app
from app.models import Base, BenefitBalance, BenefitGrant, BenefitServiceOrder
from app.services import benefit_orders
from app.workers import benefits as benefit_worker
from test_user_relationships import client, login

USER = "/api/users/me"
OPS = "/api/ops"
FULFILL = "/api/fulfillment/benefit-service-orders"


@pytest.fixture
def benefits(client, monkeypatch, tmp_path):
    """注册真实 Bearer 流程。以隔离 SQLite 跑业务，生产 DDL 由迁移测试另验。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "benefit_enabled", True)
    monkeypatch.setattr(settings, "benefit_bootstrap_admin_user_ids", {1})
    monkeypatch.setattr(
        settings, "benefit_crypto_keys", SecretStr("1:" + urlsafe_b64encode(b"x" * 32).decode())
    )
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path))
    with contextmanager(app.dependency_overrides[get_db])() as session:
        engine = session.get_bind()
    tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name.startswith(("t_benefit_", "t_enterprise"))
    ]
    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        raw.create_function("octet_length", 1, len)
        raw.create_function(
            "jsonb_typeof",
            1,
            lambda value: raw.execute("SELECT json_type(?)", (value,)).fetchone()[0],
        )
        raw.create_function("regexp", 2, lambda pattern, value: bool(re.search(pattern, value)))
        for table in tables:
            if "id" in table.c:
                monkeypatch.setattr(
                    table.c.id, "type", table.c.id.type.with_variant(Integer(), "sqlite")
                )
            for col in table.c:
                if isinstance(col.type, JSONB):
                    monkeypatch.setattr(col, "type", col.type.with_variant(JSON(), "sqlite"))
                    monkeypatch.setattr(col.server_default, "arg", text("'{}'"))
            for constraint in table.constraints:
                if constraint.name == "ck_benefit_grant_phone_last4":
                    monkeypatch.setattr(
                        constraint, "sqltext", text("recipient_phone_last4 REGEXP '^[0-9]{4}$'")
                    )
            for index in table.indexes:
                predicate = index.dialect_options["postgresql"].get("where")
                if predicate is not None:
                    monkeypatch.setitem(index.dialect_options["sqlite"], "where", predicate)
        Base.metadata.create_all(conn, tables=tables)
        conn.execute(text("PRAGMA foreign_keys = ON"))
    return client, tmp_path


def doc(root, content):
    key = "benefits/documents/" + hashlib.sha256(content.encode()).hexdigest() + ".txt"
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode())
    return key


def send(client, method, url, headers, body=None, expected=200, key=None):
    if key:
        headers = {**headers, "Idempotency-Key": str(key)}
    result = client.request(method, url, headers=headers, json=body)
    assert result.status_code == expected, result.text
    return result.json()


def configure(benefits, units=2, *, different_description=False):
    client, root = benefits
    platform = login(client, "13800000001")
    customer = login(client, "13800000002")
    provider = login(client, "13800000003")
    send(client, "PUT", f"{OPS}/benefit-staff/1", platform, {"roles": ["dispatcher"]})
    send(client, "PUT", f"{OPS}/benefit-staff/3", platform, {"roles": ["provider"]})
    org = send(
        client,
        "POST",
        f"{OPS}/enterprises",
        platform,
        {
            "code": "partner",
            "display_name": "合作企业",
            "admin_user_id": 1,
        },
        201,
    )
    service = send(
        client, "POST", f"{OPS}/benefit-services", platform, {"code": "offline_chat"}, 201
    )
    service_base = f"{OPS}/benefit-services/{service['id']}/versions"
    version_body = {
        "name": "陪聊",
        "summary": "线下陪伴",
        "description_ref": doc(root, "一对一线下陪伴"),
        "terms_ref": doc(root, "完成后扣次"),
        "delivery_terms": "一次陪聊完成即交付",
        "fulfillment_mode": "appointment",
    }
    version = send(client, "POST", service_base, platform, version_body, 201)
    version_path = f"{service_base}/{version['id']}"
    send(client, "POST", f"{version_path}/publish", platform)
    send(client, "PUT", version_path, platform, version_body, 409)
    version_ids = [version["id"]]
    if different_description:
        alternative = send(
            client,
            "POST",
            service_base,
            platform,
            {
                **version_body,
                "description_ref": doc(root, "包含一段口述录音的陪伴服务"),
            },
            201,
        )
        send(client, "POST", f"{service_base}/{alternative['id']}/publish", platform)
        version_ids.append(alternative["id"])
    package = send(
        client,
        "POST",
        f"{OPS}/benefit-packages",
        platform,
        {
            "package_code": "care",
            "title": "关怀计划",
            "items": [{"service_version_id": item, "quota_per_unit": 1} for item in version_ids],
        },
        201,
    )
    send(client, "POST", f"{OPS}/benefit-packages/{package['id']}/publish", platform)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    draft = send(
        client,
        "POST",
        f"{OPS}/benefit-purchases",
        platform,
        {
            "enterprise_id": org["id"],
            "package_id": package["id"],
            "purchase_reference": "SALE-01",
            "purchase_contract_ref": doc(root, "平台与企业的采购合同"),
            "purchased_units": units,
            "paid_amount_minor": 2500,
            "issue_until": (now + timedelta(days=7)).isoformat(),
            "claim_until": (now + timedelta(days=8)).isoformat(),
            "service_valid_from": (now - timedelta(days=1)).isoformat(),
            "service_valid_until": (now + timedelta(days=30)).isoformat(),
        },
        201,
    )
    send(
        client,
        "POST",
        f"{OPS}/benefit-purchases/{draft['id']}/activate",
        platform,
        {"paid_at": now.isoformat()},
    )
    return client, platform, customer, provider, org["id"], draft["id"], version["id"], now


def test_benefit_purchase_issuance_claim_merge_and_scope(benefits):
    client, platform, customer, provider, enterprise, batch, version, _ = configure(benefits)
    path = f"/api/enterprises/{enterprise}/benefit-purchases/{batch}"
    send(client, "GET", f"{OPS}/benefit-packages", customer, expected=403)
    send(client, "GET", path, provider, expected=404)
    assert send(client, "GET", f"{path}/contract", platform)["content"] == "平台与企业的采购合同"
    member = send(
        client,
        "POST",
        f"/api/enterprises/{enterprise}/members",
        platform,
        {
            "user_id": 3,
            "role": "issuer",
            "work_display_name": "企业联系人",
            "work_phone": "13800000003",
        },
        201,
    )
    send(client, "GET", path, provider)
    send(client, "GET", f"{path}/contract", provider, expected=403)
    send(client, "PUT", f"{path}/contacts", platform, {"member_ids": [member["id"]]})
    opts = {"items": [{"recipient_phone": "+8613800000002"}] * 2}
    key = uuid4()
    data = send(client, "POST", f"{path}/issues", platform, opts, 201, key)
    assert send(client, "POST", f"{path}/issues", platform, opts, key=key) == data
    assert send(client, "POST", f"{path}/issues", platform, opts, 409, uuid4())["code"] == (
        "purchase_quota_exhausted"
    )
    blocked = send(
        client,
        "POST",
        f"{path}/issues",
        platform,
        {
            "items": [{"recipient_phone": "13800000003"}],
        },
        409,
        key,
    )
    assert blocked["code"] == "idempotency_conflict"
    first, second = data["items"]
    assert len(send(client, "GET", f"{USER}/benefit-invitations", customer)["items"]) == 2
    assert send(client, "GET", f"{USER}/benefit-invitations", provider)["items"] == []
    assert (
        send(client, "GET", f"{USER}/benefit-service-versions/{version}/documents/terms", customer)[
            "content"
        ]
        == "完成后扣次"
    )
    send(
        client,
        "GET",
        f"{USER}/benefit-service-versions/{version}/documents/terms",
        provider,
        expected=404,
    )
    send(client, "POST", f"{USER}/benefit-claims", provider, {"code": first["code"]}, 409)
    claim = send(
        client, "POST", f"{USER}/benefit-claims", customer, {"grant_id": first["grant_id"]}, 201
    )
    assert claim["grant_id"] == first["grant_id"]
    assert (
        send(client, "POST", f"{USER}/benefit-claims", customer, {"code": first["code"]})[
            "grant_id"
        ]
        == first["grant_id"]
    )
    send(client, "POST", f"{USER}/benefit-claims", customer, {"code": second["code"]}, 201)
    plan = send(client, "GET", f"{USER}/benefit-plans", customer)["items"][0]
    assert len(plan["cards"]) == 1 and plan["cards"][0]["remaining_count"] == 2
    assert plan["contacts"][0]["work_display_name"] == "企业联系人"
    assert plan["cards"][0]["available_balance_id"] == claim["cards"][0]["balance_id"]
    assert (
        send(client, "GET", f"{USER}/benefit-enterprises", customer)["items"][0]["card_count"] == 1
    )
    assert send(client, "GET", path, platform)["claimed_units"] == 2
    assert all(
        "code" not in item for item in send(client, "GET", f"{path}/grants", platform)["items"]
    )
    with contextmanager(app.dependency_overrides[get_db])() as db:
        saved = db.get(BenefitGrant, first["grant_id"])
        assert saved.delivery_ciphertext is None and saved.recipient_user_id == 2


def test_benefit_booking_reschedule_complete_and_failed_reservation(benefits, monkeypatch):
    client, platform, customer, provider, enterprise, batch, _, now = configure(benefits, 1)
    record = send(
        client,
        "POST",
        f"/api/enterprises/{enterprise}/benefit-purchases/{batch}/issues",
        platform,
        {"items": [{"recipient_phone": "13800000002"}]},
        201,
        uuid4(),
    )
    claim = send(
        client,
        "POST",
        f"{USER}/benefit-claims",
        customer,
        {"grant_id": record["items"][0]["grant_id"]},
        201,
    )
    balance = claim["cards"][0]["balance_id"]
    create = f"{USER}/benefit-balances/{balance}/service-orders"
    first_key = uuid4()
    request = {"address": "测试地址", "contact_phone": "13800000002"}
    first = send(client, "POST", create, customer, request, 201, first_key)
    assert first["status"] == "requested" and first["remaining_count"] == 1
    assert send(client, "POST", create, customer, request, key=first_key)["id"] == first["id"]
    second = send(client, "POST", create, customer, {}, 201, uuid4())
    prefix = f"{FULFILL}/{first['id']}"
    send(client, "GET", prefix, provider, expected=404)
    booked = now + timedelta(hours=1)
    end = booked + timedelta(minutes=30)
    assignment = {
        "action": "confirm_booking",
        "expected_version": 1,
        "assignee_user_id": 3,
        "scheduled_start_at": booked.isoformat(),
        "scheduled_end_at": end.isoformat(),
    }
    key = uuid4()
    reserved = send(client, "POST", f"{prefix}/actions", platform, assignment, key=key)
    assert reserved["event"]["reserved_delta"] == 1 and reserved["order"]["remaining_count"] == 0
    assert (
        send(client, "POST", f"{prefix}/actions", platform, assignment, key=key)["event"]
        == reserved["event"]
    )
    send(client, "POST", f"{FULFILL}/{second['id']}/actions", platform, assignment, 409, uuid4())
    assert send(client, "GET", prefix, provider)["contact"]["address"] == "测试地址"
    moved = {
        **assignment,
        "action": "reschedule",
        "expected_version": 2,
        "scheduled_start_at": (booked + timedelta(minutes=10)).isoformat(),
    }
    updated = send(client, "POST", f"{prefix}/actions", platform, moved, key=uuid4())
    assert updated["event"]["reserved_delta"] == 0 and updated["order"]["version"] == 3
    monkeypatch.setattr(benefit_orders, "now_utc", lambda: end - timedelta(minutes=1))
    send(
        client,
        "POST",
        f"{prefix}/actions",
        provider,
        {
            "action": "start",
            "expected_version": 3,
        },
        key=uuid4(),
    )
    completion = {"action": "complete", "expected_version": 4, "result_ref": "chat/001"}
    completed_key = uuid4()
    done = send(client, "POST", f"{prefix}/actions", provider, completion, key=completed_key)
    assert done["order"]["status"] == "completed" and done["event"]["used_delta"] == 1
    assert (
        send(client, "POST", f"{prefix}/actions", provider, completion, key=completed_key)["event"]
        == done["event"]
    )
    with contextmanager(app.dependency_overrides[get_db])() as db:
        row = db.get(BenefitBalance, balance)
        assert row.reserved_count == 0 and row.used_count == 1
        order = db.get(BenefitServiceOrder, first["id"])
        assert b"13800000002" not in order.private_data_ciphertext


def test_unclaimed_void_releases_inventory_and_validation_hides_credentials(benefits):
    client, platform, customer, _, enterprise, batch, _, _ = configure(benefits, 1)
    root = f"/api/enterprises/{enterprise}"
    issue = f"{root}/benefit-purchases/{batch}/issues"
    body = {"items": [{"recipient_phone": "13800000002"}]}
    token = uuid4()
    first = send(client, "POST", issue, platform, body, 201, token)["items"][0]
    send(
        client,
        "POST",
        f"{root}/benefit-grants/{first['grant_id']}/void",
        platform,
        {"reason": "客户资料更新"},
    )
    assert send(client, "POST", issue, platform, body, key=token)["items"][0]["code"] is None
    send(client, "POST", f"{USER}/benefit-claims", customer, {"code": first["code"]}, 409)
    send(client, "POST", issue, platform, body, 201, uuid4())
    response = client.post(
        f"{USER}/benefit-claims", headers=customer, json={"grant_id": 123, "code": "secret-value"}
    )
    assert response.status_code == 422 and "secret-value" not in response.text


def test_benefit_cancel_releases_and_expiry_clears_pending_credentials(benefits, monkeypatch):
    client, platform, customer, provider, enterprise, batch, _, now = configure(benefits)
    path = f"/api/enterprises/{enterprise}/benefit-purchases/{batch}"
    items = send(
        client,
        "POST",
        f"{path}/issues",
        platform,
        {
            "items": [{"recipient_phone": "13800000002"}] * 2,
        },
        201,
        uuid4(),
    )["items"]
    claim = send(
        client, "POST", f"{USER}/benefit-claims", customer, {"grant_id": items[0]["grant_id"]}, 201
    )
    balance = claim["cards"][0]["balance_id"]
    path = f"{USER}/benefit-balances/{balance}/service-orders"
    first = send(client, "POST", path, customer, {}, 201, uuid4())
    appointment = {
        "action": "confirm_booking",
        "expected_version": 1,
        "assignee_user_id": 3,
        "scheduled_start_at": (now + timedelta(hours=1)).isoformat(),
        "scheduled_end_at": (now + timedelta(hours=2)).isoformat(),
    }
    send(client, "POST", f"{FULFILL}/{first['id']}/actions", platform, appointment, key=uuid4())
    cancel = f"{USER}/benefit-service-orders/{first['id']}/cancel"
    assert (
        send(
            client,
            "POST",
            cancel,
            customer,
            {"reason": "changed_mind", "expected_version": 1},
            409,
            uuid4(),
        )["code"]
        == "version_conflict"
    )
    result = send(
        client,
        "POST",
        cancel,
        customer,
        {"reason": "changed_mind", "expected_version": 2},
        key=uuid4(),
    )
    assert result["status"] == "cancelled" and result["remaining_count"] == 1
    queued = send(client, "POST", path, customer, {}, 201, uuid4())
    monkeypatch.setattr(benefit_worker, "now_utc", lambda: now + timedelta(days=31))
    with contextmanager(app.dependency_overrides[get_db])() as db:
        assert benefit_worker.expire_benefit_records(db) == (1, 1)
        assert benefit_worker.expire_benefit_records(db) == (0, 0)
        pending_grant = db.get(BenefitGrant, items[1]["grant_id"])
        assert pending_grant.status == "expired" and pending_grant.delivery_ciphertext is None
        assert db.get(BenefitServiceOrder, queued["id"]).terminal_reason == "benefit_expired"
        restored = db.get(BenefitBalance, balance)
        assert (restored.reserved_count, restored.used_count) == (0, 0)


def test_distinct_service_versions_stay_separate_and_provider_cannot_overlap(benefits):
    client, platform, customer, _, enterprise, batch, first_version, now = configure(
        benefits,
        1,
        different_description=True,
    )
    path = f"/api/enterprises/{enterprise}/benefit-purchases/{batch}"
    issued = send(
        client,
        "POST",
        f"{path}/issues",
        platform,
        {
            "items": [{"recipient_phone": "13800000002"}],
        },
        201,
        uuid4(),
    )["items"][0]
    send(client, "POST", f"{USER}/benefit-claims", customer, {"grant_id": issued["grant_id"]}, 201)
    cards = send(client, "GET", f"{USER}/benefit-plans", customer)["items"][0]["cards"]
    assert len(cards) == 2 and {card["name"] for card in cards} == {"陪聊"}
    assert len({card["description_ref"] for card in cards}) == 2
    assert (
        send(
            client,
            "GET",
            f"{USER}/benefit-service-versions/{first_version}/documents/description",
            customer,
        )["content"]
        == "一对一线下陪伴"
    )
    order_ids = []
    for card in cards:
        order = send(
            client,
            "POST",
            f"{USER}/benefit-balances/{card['available_balance_id']}/service-orders",
            customer,
            {},
            201,
            uuid4(),
        )
        order_ids.append(order["id"])
    appointment = {
        "action": "confirm_booking",
        "expected_version": 1,
        "assignee_user_id": 3,
        "scheduled_start_at": (now + timedelta(hours=1)).isoformat(),
        "scheduled_end_at": (now + timedelta(hours=2)).isoformat(),
    }
    send(client, "POST", f"{FULFILL}/{order_ids[0]}/actions", platform, appointment, key=uuid4())
    blocked = send(
        client, "POST", f"{FULFILL}/{order_ids[1]}/actions", platform, appointment, 409, uuid4()
    )
    assert blocked["code"] == "appointment_slot_unavailable"
