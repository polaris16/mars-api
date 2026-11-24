"""[新增] 使用隔离数据库验证产品体验的完整调用链。"""

from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import JSON, Integer, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.core.config import get_settings
from app.db.database import get_db
from app.main import app
from app.models import Base, BenefitBalance, BenefitOrderEvent, MediaAsset, ProductExperienceRound
from app.services import product_experience_worker
from app.services.benefit_common import BenefitError
from app.services.benefit_common import now_utc
from app.services.object_storage_service import ObjectStorageService
from test_benefit_flows import OPS, USER, benefits, configure, send
from test_user_relationships import RELATIONSHIPS, client, confirm_relationship

EXPERIENCES = "/api/product-experiences"


@pytest.fixture
def product_setup(benefits, monkeypatch):
    monkeypatch.setattr(get_settings(), "product_experience_provider", "mock")
    with _database() as db:
        engine = db.get_bind()
    tables = [table for table in Base.metadata.sorted_tables if table.name.startswith("t_product")]
    for table in tables:
        pk = table.c.id
        monkeypatch.setattr(pk, "type", pk.type.with_variant(Integer(), "sqlite"))
        for col in table.c:
            if isinstance(col.type, (ARRAY, JSONB)):
                default = "'[]'" if isinstance(col.type, ARRAY) else "'{}'"
                monkeypatch.setattr(col, "type", col.type.with_variant(JSON(), "sqlite"))
                monkeypatch.setattr(col.server_default, "arg", text(default))
        for check in table.constraints:
            sqlite_rules = {
                "ck_product_version_modes": "status = 'draft' OR json_array_length(generation_modes) > 0",
                "ck_product_preview_fingerprint": "request_fingerprint REGEXP '^[a-f0-9]{64}$'",
            }
            if check.name in sqlite_rules:
                monkeypatch.setattr(check, "sqltext", text(sqlite_rules[check.name]))
        for index in table.indexes:
            predicate = index.dialect_options["postgresql"]["where"]
            if predicate is not None:
                monkeypatch.setitem(index.dialect_options["sqlite"], "where", predicate)
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=tables)
    return benefits


def _database():
    return contextmanager(app.dependency_overrides[get_db])()


def _upload(client, headers, auth_path, complete_prefix, data, *, round_id=None):
    body = {"filename": "example.png", "content_type": "image/png", "size_bytes": len(data)}
    key = "asset_id"
    if round_id is not None:
        body["round_id"] = round_id
        key = "photo_id"
    auth = send(client, "POST", auth_path, headers, body, expected=201)
    response = client.put(auth["upload_url"], headers=auth["required_headers"], content=data)
    assert response.status_code == 200, response.text
    path = f"{complete_prefix}/{auth[key]}/complete"
    confirmed = send(client, "POST", path, headers)
    assert confirmed[key] == send(client, "POST", path, headers)[key]
    return confirmed[key]


def _png(color, size):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _catalog(client, admin):
    category = send(
        client,
        "POST",
        f"{OPS}/product-categories",
        admin,
        {"code": "clothing", "name": "服装"},
        expected=201,
    )["id"]
    send(client, "PATCH", f"{OPS}/product-categories/{category}", admin, {"sort_order": 3})
    product = send(client, "POST", f"{OPS}/products", admin, {"code": "red-shirt"}, expected=201)[
        "id"
    ]
    image = _upload(
        client,
        admin,
        f"{OPS}/product-images/upload-authorizations",
        f"{OPS}/product-images",
        _png("red", (80, 90)),
    )
    versions = f"{OPS}/products/{product}/versions"
    draft = {
        "category_id": category,
        "name": "红衬衫",
        "generation_modes": ["try_on", "scene_composite"],
        "images": [{"media_asset_id": image, "is_cover": True}],
    }
    row = send(client, "POST", versions, admin, draft, expected=201)
    version = row["product_version_id"]
    send(client, "PUT", f"{versions}/{version}", admin, draft)
    send(client, "POST", f"{versions}/{version}/publish", admin)
    assert send(client, "GET", versions, admin)["total"] == 1
    assert (
        send(client, "GET", f"{OPS}/products", admin)["items"][0]["current_version"][
            "product_version_id"
        ]
        == version
    )
    return product, version, draft, versions


def _round_setup(benefits):
    client, admin, owner, friend, enterprise, batch, _, _ = configure(benefits, 1, online=True)
    path = f"/api/enterprises/{enterprise}/benefit-purchases/{batch}/issues"
    issued = send(
        client,
        "POST",
        path,
        admin,
        {"items": [{"recipient_phone": "13800000002"}]},
        expected=201,
        key=uuid4(),
    )
    grant = send(
        client,
        "POST",
        f"{USER}/benefit-claims",
        owner,
        {"grant_id": issued["items"][0]["grant_id"]},
        expected=201,
    )
    assert grant["cards"][0]["online_handler"] == "product_experience"
    balance = grant["cards"][0]["balance_id"]
    opening = f"{USER}/benefit-balances/{balance}/product-experience-rounds"
    row = send(client, "POST", opening, owner, expected=201)
    assert row == send(client, "POST", opening, owner)
    assert row["preview_remaining"] == 20
    photo = _upload(
        client,
        owner,
        f"{EXPERIENCES}/photo-upload-authorizations",
        f"{EXPERIENCES}/photos",
        _png("green", (720, 960)),
        round_id=row["round_id"],
    )
    product, version, draft, versions = _catalog(client, admin)
    return (
        client,
        admin,
        owner,
        friend,
        row,
        balance,
        photo,
        product,
        version,
        draft,
        versions,
        opening,
    )


def _run_worker():
    with _database() as db:
        assert product_experience_worker.run_next_preview(
            db, ObjectStorageService("http://testserver")
        )


def test_online_purchase_preview_confirm_and_share_product_experience(product_setup):
    (
        client,
        admin,
        owner,
        friend,
        row,
        balance,
        photo,
        product,
        version,
        draft,
        versions,
        opening,
    ) = _round_setup(product_setup)
    route = f"{EXPERIENCES}/rounds/{row['round_id']}"
    assert send(client, "GET", "/api/products/categories", owner)["items"][0]["sort_order"] == 3
    assert send(client, "GET", "/api/products?keyword=红", owner)["total"] == 1
    assert send(client, "GET", f"/api/products/{product}", owner)["cover"]["width_px"] == 80
    send(client, "GET", f"{OPS}/products", owner, expected=403)
    send(client, "GET", f"/api/media-assets/{photo}", owner, expected=404)
    assert send(client, "GET", "/api/media-assets", owner)["items"] == []
    payload = {
        "photo_id": photo,
        "product_id": product,
        "product_version_id": version,
        "mode": "try_on",
    }
    key = uuid4()
    pending = send(client, "POST", f"{route}/previews", owner, payload, expected=202, key=key)
    assert pending["preview_remaining"] == 19
    assert send(client, "POST", f"{route}/previews", owner, payload, key=key) == pending
    assert (
        send(
            client,
            "POST",
            f"{route}/previews",
            owner,
            {**payload, "mode": "scene_composite"},
            expected=409,
            key=key,
        )["code"]
        == "idempotency_conflict"
    )
    send(client, "DELETE", f"{EXPERIENCES}/photos/{photo}", owner, expected=409)
    assert (
        send(
            client,
            "POST",
            f"{route}/confirm",
            owner,
            {"preview_id": pending["preview_id"]},
            expected=409,
            key=uuid4(),
        )["code"]
        == "preview_not_ready"
    )
    send(client, "GET", route, friend, expected=404)
    revised = send(client, "POST", versions, admin, {**draft, "name": "第二版"}, expected=201)
    new_version = revised["product_version_id"]
    send(client, "POST", f"{versions}/{new_version}/publish", admin)
    assert (
        send(client, "POST", f"{route}/previews", owner, payload, expected=409, key=uuid4())["code"]
        == "product_version_changed"
    )
    _run_worker()
    complete = send(client, "GET", f"{route}/previews/{pending['preview_id']}", owner)
    assert (complete["status"], complete["width_px"], complete["height_px"]) == (
        "succeeded",
        540,
        720,
    )
    assert client.get(complete["preview_url"]).status_code == 200
    assert send(client, "GET", route, owner)["previews"][0]["preview_id"] == pending["preview_id"]
    with _database() as db:
        assert db.get(BenefitBalance, balance).used_count == 0
    body = {"preview_id": pending["preview_id"]}
    idem = uuid4()
    receipt = send(client, "POST", f"{route}/confirm", owner, body, expected=201, key=idem)
    assert send(client, "POST", f"{route}/confirm", owner, body, key=idem) == receipt
    assert send(client, "POST", f"{route}/confirm", owner, body, key=uuid4()) == receipt
    assert receipt["remaining_benefit_count"] == 0
    assert send(client, "POST", opening, owner, expected=409)["code"] == "benefit_quota_exhausted"
    with _database() as db:
        events = db.scalars(
            select(BenefitOrderEvent).order_by(BenefitOrderEvent.order_version)
        ).all()
        assert [(event.action, event.reserved_delta, event.used_delta) for event in events] == [
            ("create", 1, 0),
            ("start", 0, 0),
            ("complete", -1, 1),
        ]
    result = f"{EXPERIENCES}/results/{receipt['result_id']}"
    saved = send(client, "GET", result, owner)
    assert saved["product_version_id"] == version
    assert saved["image"]["width_px"] == 720
    assert send(client, "GET", f"{EXPERIENCES}/results", owner)["total"] == 1
    send(client, "GET", result, friend, expected=404)
    target = {"recipient_user_id": 3}
    send(client, "POST", f"{result}/shares", owner, target, expected=404, key=uuid4())
    relationship = confirm_relationship(
        client, owner, friend, user_id=3, kind="contact", code="contact_of"
    )
    share_key = uuid4()
    sent = send(client, "POST", f"{result}/shares", owner, target, expected=201, key=share_key)
    assert send(client, "POST", f"{result}/shares", owner, target, key=share_key) == sent
    direct = f"{EXPERIENCES}/shares/{sent['share_id']}"
    detail = send(client, "GET", direct, friend)
    assert detail["image"]["width_px"] == 720
    assert "source_asset_id" not in detail
    assert send(client, "GET", f"{EXPERIENCES}/shares?direction=incoming", friend)["total"] == 1
    send(client, "POST", f"{RELATIONSHIPS}/{relationship}/revoke", owner)
    send(client, "GET", direct, friend, expected=404)
    assert send(client, "GET", f"{EXPERIENCES}/shares?direction=incoming", friend)["total"] == 0
    confirm_relationship(client, owner, friend, user_id=3, kind="contact", code="contact_of")
    send(client, "GET", direct, friend, expected=404)
    again = send(client, "POST", f"{result}/shares", owner, target, expected=201, key=uuid4())
    new_share = f"{EXPERIENCES}/shares/{again['share_id']}"
    assert client.delete(new_share, headers=owner).status_code == 204
    send(client, "GET", new_share, friend, expected=404)
    assert client.delete(result, headers=owner).status_code == 204
    send(client, "GET", result, owner, expected=404)
    with _database() as db:
        assert db.get(BenefitBalance, balance).used_count == 1


def test_preview_limit_and_failure_return_the_reserved_slot(product_setup, monkeypatch):
    client, _, owner, _, row, balance, photo, product, version, _, _, _ = _round_setup(
        product_setup
    )
    route = f"{EXPERIENCES}/rounds/{row['round_id']}"
    body = {
        "photo_id": photo,
        "product_id": product,
        "product_version_id": version,
        "mode": "scene_composite",
    }
    submitted = []
    for remaining in range(19, -1, -1):
        item = send(client, "POST", f"{route}/previews", owner, body, expected=202, key=uuid4())
        assert item["preview_remaining"] == remaining
        submitted.append(item)
    rejected = send(client, "POST", f"{route}/previews", owner, body, expected=409, key=uuid4())
    assert (rejected["code"], rejected["preview_remaining"]) == ("preview_quota_exhausted", 0)
    with monkeypatch.context() as patch:

        def fail(*_args):
            raise BenefitError(422, "invalid_image", "模拟输入失败")

        patch.setattr(product_experience_worker, "_mock_images", fail)
        _run_worker()
    first = send(client, "GET", f"{route}/previews/{submitted[0]['preview_id']}", owner)
    assert (first["status"], first["preview_remaining"]) == ("failed", 1)
    _run_worker()
    second = send(client, "GET", f"{route}/previews/{submitted[1]['preview_id']}", owner)
    assert second["status"] == "succeeded"
    send(client, "POST", f"{route}/previews", owner, body, expected=202, key=uuid4())
    receipt = send(
        client,
        "POST",
        f"{route}/confirm",
        owner,
        {"preview_id": second["preview_id"]},
        expected=201,
        key=uuid4(),
    )
    cancelled = send(client, "GET", f"{route}/previews/{submitted[2]['preview_id']}", owner)
    assert (cancelled["status"], cancelled["preview_remaining"]) == ("cancelled", 0)
    assert receipt["remaining_benefit_count"] == 0
    with _database() as db:
        assert db.get(BenefitBalance, balance).used_count == 1


def test_image_validation_and_expired_round_cleanup(product_setup, monkeypatch):
    client, _, owner, _, row, balance, photo, product, version, _, _, _ = _round_setup(
        product_setup
    )
    path = f"{EXPERIENCES}/rounds/{row['round_id']}"
    request = {
        "round_id": row["round_id"],
        "filename": "fake.png",
        "content_type": "image/png",
        "size_bytes": 8,
    }
    auth = send(
        client, "POST", f"{EXPERIENCES}/photo-upload-authorizations", owner, request, expected=201
    )
    assert (
        client.put(
            auth["upload_url"], headers=auth["required_headers"], content=b"notimage"
        ).status_code
        == 200
    )
    rejected = send(
        client, "POST", f"{EXPERIENCES}/photos/{auth['photo_id']}/complete", owner, expected=422
    )
    assert rejected["code"] == "invalid_image"
    data = _png("green", (720, 960))
    duplicate = _upload(
        client,
        owner,
        f"{EXPERIENCES}/photo-upload-authorizations",
        f"{EXPERIENCES}/photos",
        data,
        round_id=row["round_id"],
    )
    assert duplicate == photo
    body = {
        "photo_id": photo,
        "product_id": product,
        "product_version_id": version,
        "mode": "try_on",
    }
    job = send(client, "POST", f"{path}/previews", owner, body, expected=202, key=uuid4())
    _run_worker()
    queued = send(client, "POST", f"{path}/previews", owner, body, expected=202, key=uuid4())
    storage = ObjectStorageService("http://testserver")
    deadline = now_utc() - timedelta(minutes=1)
    with _database() as db, db.begin():
        db.get(ProductExperienceRound, row["round_id"]).expires_at = deadline
    confirm = send(
        client,
        "POST",
        f"{path}/confirm",
        owner,
        {"preview_id": job["preview_id"]},
        expected=409,
        key=uuid4(),
    )
    assert confirm["code"] == "benefit_expired"
    with _database() as db:
        closed, _ = product_experience_worker.cleanup_product_experiences(db, storage)
    assert closed == 1
    assert (
        send(client, "GET", f"{path}/previews/{queued['preview_id']}", owner)["status"]
        == "cancelled"
    )
    closed_at = now_utc()
    monkeypatch.setattr(
        product_experience_worker, "now_utc", lambda: closed_at + timedelta(hours=3)
    )
    with _database() as db:
        _, removed = product_experience_worker.cleanup_product_experiences(db, storage)
        assert removed >= 2
        generated = db.scalars(
            select(MediaAsset).where(MediaAsset.purpose == "experience_generated")
        ).all()
        assert len(generated) == 2 and all(asset.is_del for asset in generated)
        assert db.get(BenefitBalance, balance).used_count == 0
