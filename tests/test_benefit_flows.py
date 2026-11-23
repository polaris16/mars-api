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
from app.models import Base, BenefitBalance, BenefitGrant, BenefitPurchaseBatch, BenefitServiceOrder
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


# [新增] 运营目录全量读取保留版本归属、发布状态与子项顺序。
def test_benefit_catalog_lists_sort_before_paging_and_allow_full_results(benefits):
    client, root = benefits
    platform = login(client, "13800000001")
    customer = login(client, "13800000002")
    services_path = f"{OPS}/benefit-services"
    packages_path = f"{OPS}/benefit-packages"
    empty = {"items": [], "page": 1, "page_size": 0, "total": 0}
    for path in (services_path, packages_path):
        assert send(client, "GET", f"{path}?paginate=false", platform) == empty

    zebra = send(client, "POST", services_path, platform, {"code": "zebra"}, 201)
    apple = send(client, "POST", services_path, platform, {"code": "apple"}, 201)
    versions_path = f"{services_path}/{zebra['id']}/versions"
    assert send(client, "GET", f"{versions_path}?paginate=false", platform) == empty
    body = {
        "name": "陪聊",
        "summary": "线下陪伴",
        "description_ref": doc(root, "一对一陪伴"),
        "terms_ref": doc(root, "完成后扣次"),
        "delivery_terms": "一次陪伴完成即交付",
        "fulfillment_mode": "appointment",
    }
    v1 = send(client, "POST", versions_path, platform, body, 201)
    v2 = send(client, "POST", versions_path, platform, body, 201)
    v3 = send(client, "POST", versions_path, platform, body, 201)
    for item in (v1, v2):
        send(client, "POST", f"{versions_path}/{item['id']}/publish", platform)
    apple_versions = f"{services_path}/{apple['id']}/versions"
    send(client, "POST", apple_versions, platform, body, 201)

    packages = [
        send(
            client,
            "POST",
            packages_path,
            platform,
            {
                "package_code": code,
                "title": title,
                "items": [
                    {"service_version_id": v1["id"], "quota_per_unit": 2, "sort_order": 9},
                    {"service_version_id": v2["id"], "quota_per_unit": 1, "sort_order": 1},
                ],
            },
            201,
        )
        for code, title in (("z", "Zebra"), ("a", "apple"), ("a2", "Apple"))
    ]
    first, second, third = (item["id"] for item in packages)

    for path, default_ids, ascending_sort, ascending_ids in (
        (services_path, [zebra["id"], apple["id"]], "code_asc", [apple["id"], zebra["id"]]),
        (
            versions_path,
            [v3["id"], v2["id"], v1["id"]],
            "version_asc",
            [v1["id"], v2["id"], v3["id"]],
        ),
        (packages_path, [third, second, first], "title_asc", [third, second, first]),
    ):
        default = send(client, "GET", path, platform)
        assert (default["page"], default["page_size"], default["total"]) == (
            1,
            20,
            len(default_ids),
        )
        assert [item["id"] for item in default["items"]] == default_ids
        sorted_page = send(
            client, "GET", f"{path}?sort={ascending_sort}&page=2&page_size=1", platform
        )
        assert [item["id"] for item in sorted_page["items"]] == ascending_ids[1:2]
        full = send(
            client,
            "GET",
            f"{path}?sort={ascending_sort}&paginate=false&page=9&page_size=1",
            platform,
        )
        assert [item["id"] for item in full["items"]] == ascending_ids
        assert (full["page"], full["page_size"], full["total"]) == (
            1,
            len(ascending_ids),
            len(ascending_ids),
        )
        send(client, "GET", f"{path}?paginate=false", customer, expected=403)
        send(client, "GET", f"{path}?sort=unknown", platform, expected=422)

    for path, sort, ordered_ids in (
        (services_path, "code_desc", [zebra["id"], apple["id"]]),
        (versions_path, "version_desc", [v3["id"], v2["id"], v1["id"]]),
        (packages_path, "title_desc", [first, third, second]),
    ):
        result = send(client, "GET", f"{path}?sort={sort}&paginate=false", platform)
        assert [item["id"] for item in result["items"]] == ordered_ids
        if path == services_path:
            assert result["items"][0]["latest_version"]["id"] == v2["id"]
            assert result["items"][1]["latest_version"] is None
        if path == packages_path:
            for package in result["items"]:
                assert [item["service_version_id"] for item in package["items"]] == [
                    v2["id"],
                    v1["id"],
                ]


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
    # [新增] 批次发放记录按领取期限切页，仍需有本企业权限。
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        earliest = db.get(BenefitGrant, first["grant_id"])
        earliest.claim_expires_at -= timedelta(hours=1)
    sorted_grants = f"{path}/grants?status=issued&sort=expires_at_asc&page_size=1"
    page = send(client, "GET", sorted_grants, platform)
    assert page["total"] == 2 and page["items"][0]["grant_id"] == first["grant_id"]
    send(client, "GET", sorted_grants, customer, expected=404)
    send(client, "GET", f"{path}/grants?paginate=false", platform, expected=422)
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


# [新增] 经营和用户列表在原有可见范围内使用排序及全量选项。
def test_benefit_business_lists_sort_and_apply_full_mode_by_endpoint(benefits):
    client, platform, customer, provider, org, batch, version, now = configure(benefits, 1)
    team = f"/api/enterprises/{org}"
    extra = send(
        client,
        "POST",
        f"{OPS}/enterprises",
        platform,
        {
            "code": "alternate",
            "display_name": "Alpha",
            "admin_user_id": 1,
        },
        201,
    )["id"]
    send(
        client,
        "POST",
        f"{team}/members",
        platform,
        {
            "user_id": 3,
            "role": "issuer",
            "work_display_name": "My Name",
        },
        201,
    )
    package = send(
        client,
        "POST",
        f"{OPS}/benefit-packages",
        platform,
        {
            "package_code": "alternate",
            "title": "Alpha Plan",
            "items": [{"service_version_id": version, "quota_per_unit": 1}],
        },
        201,
    )["id"]
    send(client, "POST", f"{OPS}/benefit-packages/{package}/publish", platform)
    alternate_batch = send(
        client,
        "POST",
        f"{OPS}/benefit-purchases",
        platform,
        {
            "enterprise_id": extra,
            "package_id": package,
            "purchase_reference": "SALE-02",
            "purchased_units": 1,
            "paid_amount_minor": 2500,
            "issue_until": (now + timedelta(days=4)).isoformat(),
            "claim_until": (now + timedelta(days=5)).isoformat(),
            "service_valid_from": (now - timedelta(days=1)).isoformat(),
            "service_valid_until": (now + timedelta(days=30)).isoformat(),
        },
        201,
    )["id"]
    send(
        client,
        "POST",
        f"{OPS}/benefit-purchases/{alternate_batch}/activate",
        platform,
        {"paid_at": now.isoformat()},
    )
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(BenefitPurchaseBatch, batch).created_at = now - timedelta(days=1)
        db.get(BenefitPurchaseBatch, alternate_batch).created_at = now

    opts = {"items": [{"recipient_phone": "13800000002"}]}
    grant_ids = [
        send(
            client,
            "POST",
            f"/api/enterprises/{enterprise}/benefit-purchases/{purchase}/issues",
            platform,
            opts,
            201,
            uuid4(),
        )["items"][0]["grant_id"]
        for enterprise, purchase in ((org, batch), (extra, alternate_batch))
    ]
    invitations = f"{USER}/benefit-invitations"
    for path, sort, token in (
        (f"{OPS}/enterprises", "name_asc", platform),
        (f"{OPS}/benefit-staff", "role_desc", platform),
        (f"{OPS}/benefit-purchases", "created_at_asc", platform),
        (f"{team}/members", "name_asc", platform),
        (f"{team}/benefit-purchases", "created_at_asc", provider),
        (invitations, "expires_at_asc", customer),
        (f"{USER}/benefit-plans", "title_asc", customer),
    ):
        result = client.get(f"{path}?paginate=false", headers=token)
        assert result.status_code == 422, result.text
        assert result.json()["detail"][0]["loc"] == ["query", "paginate"]
        send(client, "GET", f"{path}?sort={sort}&page_size=1", token)
        send(client, "GET", f"{path}?sort=unknown", token, expected=422)

    assert (
        send(client, "GET", f"{OPS}/enterprises?sort=name_asc&page_size=1", platform)["items"][0][
            "id"
        ]
        == extra
    )
    assert (
        send(client, "GET", f"{OPS}/benefit-staff?sort=role_desc&page_size=1", platform)["items"][
            0
        ]["role"]
        == "provider"
    )
    assert (
        send(client, "GET", f"{team}/members?sort=name_asc&page_size=1", platform)["items"][0][
            "user_id"
        ]
        == 3
    )
    assert (
        send(client, "GET", f"{OPS}/benefit-purchases?sort=created_at_asc&page_size=1", platform)[
            "items"
        ][0]["id"]
        == batch
    )
    assert (
        send(client, "GET", f"{team}/benefit-purchases?sort=created_at_desc", provider)["items"][0][
            "id"
        ]
        == batch
    )
    send(
        client,
        "GET",
        f"/api/enterprises/{extra}/benefit-purchases?sort=created_at_desc",
        provider,
        expected=404,
    )
    assert (
        send(client, "GET", f"{invitations}?sort=expires_at_desc&page_size=1", customer)["items"][
            0
        ]["grant_id"]
        == grant_ids[0]
    )
    for grant_id in grant_ids:
        send(client, "POST", f"{USER}/benefit-claims", customer, {"grant_id": grant_id}, 201)
    sorted_plans = send(client, "GET", f"{USER}/benefit-plans?sort=title_asc&page_size=1", customer)
    assert sorted_plans["total"] == 2
    assert sorted_plans["items"][0]["package_id"] == package
    filtered = send(
        client, "GET", f"{USER}/benefit-plans?sort=title_desc&enterprise_id={org}", customer
    )
    assert [plan["enterprise"]["id"] for plan in filtered["items"]] == [org]

    for path, token, empty_token in (
        (f"{USER}/enterprises", platform, customer),
        (f"{USER}/benefit-enterprises", customer, provider),
    ):
        full = send(client, "GET", f"{path}?sort=name_asc&paginate=false&page=3&page_size=1", token)
        assert (full["page"], full["page_size"], full["total"]) == (1, 2, 2)
        assert [row["id"] for row in full["items"]] == [extra, org]
        assert send(client, "GET", f"{path}?paginate=false", empty_token) == {
            "items": [],
            "page": 1,
            "page_size": 0,
            "total": 0,
        }


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
    # [新增] 已预约服务优先于缺少预约时间的服务，履约人员仍只看本人的分配。
    user_orders = f"{USER}/benefit-service-orders"
    for path, token in ((user_orders, customer), (FULFILL, platform)):
        page = send(client, "GET", f"{path}?sort=scheduled_start_at_desc&page_size=1", token)
        assert page["total"] == 2 and page["items"][0]["id"] == first["id"]
        send(client, "GET", f"{path}?paginate=false", token, expected=422)
    assigned = send(client, "GET", f"{FULFILL}?sort=created_at_asc&status=reserved", provider)
    assert [order["id"] for order in assigned["items"]] == [first["id"]]
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
