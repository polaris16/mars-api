"""[新增] 通过两条批量接口和 Mock 直传验证附件保存及重试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import get_db
from app.main import app
from app.models import FragmentAttachment, MediaAsset, MemoryContributor
from app.services.object_storage_service import ObjectStorageService
from test_user_relationships import CONTRIBUTORS, client, login, register_material_contributor

PNG = b"\x89PNG\r\n\x1a\n"
ASSETS = "/api/media-assets"


@pytest.fixture
def memory_api(client, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_text_enabled", False)
    monkeypatch.setattr(settings, "memory_pipeline_enabled", False)
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path))
    owner = login(client, "13800000001")
    return client, owner, _new_fragment(client, owner)


def _new_fragment(client, owner):
    reply = client.post(
        "/api/recording-sessions/11/fragments",
        headers=owner,
        json={"source_type": "text", "content_text": "我搬家的那一年。"},
    )
    assert reply.status_code == 201, reply.text
    return f"/api/memory-fragments/{reply.json()['id']}/attachments"


def _image(content, **options):
    return {
        "client_file_id": str(uuid4()),
        "filename": "旧照片.png",
        "media_type": "image",
        "content_type": "image/png",
        "size_bytes": len(content),
        **options,
    }


def _batch(client, path, headers, items):
    reply = client.post(path, headers=headers, json={"items": items})
    assert reply.status_code == 200, reply.text
    results = reply.json()["items"]
    assert [result["client_file_id"] for result in results] == [
        item["client_file_id"] for item in items
    ]
    return results


def _put(client, grant, content):
    reply = client.put(grant["upload_url"], headers=grant["required_headers"], content=content)
    assert reply.status_code == 200, reply.text


def _completion(grant, **options):
    return {
        "client_file_id": grant["client_file_id"],
        "asset_id": grant["asset_id"],
        **options,
    }


def test_nine_files_complete_in_two_business_calls_and_replay_at_capacity(memory_api, monkeypatch):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    contents = [PNG + bytes([index]) for index in range(9)]
    items = [_image(content) for content in contents]
    grants = _batch(client, authorize, owner, items)
    assert all(grant["status"] == "upload_required" for grant in grants)

    # 响应丢失后使用同一 UUID 重试，继续使用原授权，不延长到期时间。
    replay = _batch(client, authorize, owner, [items[0]])[0]
    assert replay["asset_id"] == grants[0]["asset_id"]
    assert replay["expires_at"] == grants[0]["expires_at"]
    for grant, content in zip(grants, contents):
        _put(client, grant, content)
    request = [_completion(grant, sort_order=index) for index, grant in enumerate(grants)]
    linked = _batch(client, complete, owner, request)
    assert all(result["status"] == "attached" for result in linked)
    repeated = _batch(client, complete, owner, request)
    assert repeated == [{**result, "status": "already_attached"} for result in linked]

    extra_content = PNG + b"extra"
    extra = _batch(client, authorize, owner, [_image(extra_content)])[0]
    _put(client, extra, extra_content)
    full = _batch(client, complete, owner, [_completion(extra)])[0]
    assert full["error"]["code"] == "attachment_limit_reached"
    assert client.get(f"{ASSETS}/{extra['asset_id']}", headers=owner).json()["upload_status"] == (
        "uploaded"
    )
    active = client.get(attachments, headers=owner).json()
    assert [row["id"] for row in active] == [result["attachment_id"] for result in linked]
    assert (
        client.delete(f"{attachments}/{linked[0]['attachment_id']}", headers=owner).status_code
        == 200
    )

    # 文件校验期间其他写入占用最后一个名额，外层确认仍保存已验证素材。
    last_content = PNG + b"last"
    last = _batch(client, authorize, owner, [_image(last_content)])[0]
    _put(client, last, last_content)
    inspect = ObjectStorageService.inspect_and_hash

    def occupy_slot(storage, key, max_bytes, *, validate_utf8=False):
        verified = inspect(storage, key, max_bytes, validate_utf8)
        with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
            db.add(
                FragmentAttachment(
                    fragment_id=active[0]["fragment_id"],
                    media_asset_id=extra["asset_id"],
                    contributor_id=active[0]["contributor_id"],
                    created_by=1,
                    updated_by=1,
                )
            )
            db.get(MediaAsset, extra["asset_id"]).upload_status = "attached"
        return verified

    monkeypatch.setattr(ObjectStorageService, "inspect_and_hash", occupy_slot)
    final = _batch(client, complete, owner, [_completion(last)])[0]
    assert final["error"]["code"] == "attachment_limit_reached"
    assert len(client.get(attachments, headers=owner).json()) == 9
    assert (
        client.delete(f"{attachments}/{linked[1]['attachment_id']}", headers=owner).status_code
        == 200
    )
    assert _batch(client, complete, owner, [_completion(last)])[0]["status"] == "attached"


def test_batch_returns_file_errors_and_only_retries_failed_upload(memory_api, monkeypatch):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    monkeypatch.setattr(get_settings(), "object_storage_max_image_bytes", 100)
    good_bytes, later_bytes, wrong_bytes = PNG + b"a", PNG + b"b", PNG + b"c"
    good, later, wrong, too_big = _batch(
        client,
        authorize,
        owner,
        [
            _image(good_bytes),
            _image(later_bytes),
            _image(wrong_bytes, sha256="0" * 64),
            _image(b"x" * 101),
        ],
    )
    assert too_big["error"]["code"] == "file_too_large"
    _put(client, good, good_bytes)
    _put(client, wrong, wrong_bytes)
    results = _batch(client, complete, owner, [_completion(item) for item in (good, later, wrong)])
    assert results[0]["status"] == "attached"
    assert results[1]["error"]["code"] == "upload_not_found"
    assert results[1]["error"]["retryable"]
    assert results[2]["error"]["code"] == "file_mismatch"
    assert not results[2]["error"]["retryable"]
    _put(client, later, later_bytes)
    assert _batch(client, complete, owner, [_completion(later)])[0]["status"] == "attached"
    assert len(client.get(attachments, headers=owner).json()) == 2


def test_canonical_asset_and_attachment_are_recovered_after_deduplication(memory_api):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    picture = PNG + b"same photo"
    items = [_image(picture), _image(picture)]
    first, duplicate = _batch(client, authorize, owner, items)
    assert first["asset_id"] != duplicate["asset_id"]
    for grant in (first, duplicate):
        _put(client, grant, picture)
    applied, reused = _batch(
        client, complete, owner, [_completion(first, caption="原说明"), _completion(duplicate)]
    )
    assert applied["status"] == "attached" and reused["status"] == "already_attached"
    assert reused["asset_id"] == applied["asset_id"] == first["asset_id"]
    assert reused["attachment_id"] == applied["attachment_id"]
    assert _batch(client, complete, owner, [_completion(duplicate, caption="新说明")]) == [reused]
    assert client.get(attachments, headers=owner).json()[0]["caption"] == "原说明"
    grant = _batch(client, authorize, owner, [items[1]])[0]
    assert grant["status"] == "reused" and grant["asset_id"] == first["asset_id"]
    with contextmanager(app.dependency_overrides[get_db])() as db:
        discarded = db.get(MediaAsset, duplicate["asset_id"])
        assert discarded.upload_status == "failed" and discarded.purge_after is not None
        assert discarded.content_sha256 == sha256(picture).hexdigest()

    other = _new_fragment(client, owner)
    preverified = _batch(
        client,
        f"{other}/batch-upload-authorizations",
        owner,
        [_image(picture, sha256=sha256(picture).hexdigest())],
    )[0]
    assert preverified["status"] == "reused" and preverified["asset_id"] == first["asset_id"]
    assert (
        _batch(client, f"{other}/batch-complete-and-attach", owner, [_completion(preverified)])[0][
            "status"
        ]
        == "attached"
    )


def test_batch_validates_scope_file_id_and_legacy_recording_purpose(memory_api):
    client, owner, attachments = memory_api
    other = _new_fragment(client, owner)
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    content = PNG + b"old photo"
    item = _image(content)
    invalid_batches = (
        [],
        [item, item],
        [item] * 10,
        [{**item, "purpose": "recording"}],
        [{**item, "content_type": "image/png\r\ninvalid-header"}],
    )
    for invalid in invalid_batches:
        assert client.post(authorize, headers=owner, json={"items": invalid}).status_code == 422
    assert client.post(authorize, json={"items": [item]}).status_code == 401
    grant = _batch(client, authorize, owner, [item])[0]
    assert (
        client.post(
            complete, headers=owner, json={"items": [_completion(grant), _completion(grant)]}
        ).status_code
        == 422
    )
    conflict = _batch(client, authorize, owner, [{**item, "filename": "different.png"}])[0]
    assert conflict["error"]["code"] == "file_id_conflict"
    _put(client, grant, content)
    assert (
        _batch(client, f"{other}/batch-complete-and-attach", owner, [_completion(grant)])[0][
            "error"
        ]["code"]
        == "asset_unavailable"
    )
    assert (
        _batch(client, complete, owner, [_completion(grant, client_file_id=str(uuid4()))])[0][
            "error"
        ]["code"]
        == "asset_unavailable"
    )
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(MediaAsset, grant["asset_id"]).upload_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=10)
    assert _batch(client, authorize, owner, [item])[0]["error"]["code"] == "authorization_expired"
    assert _batch(client, complete, owner, [_completion(grant)])[0]["error"]["code"] == (
        "authorization_expired"
    )

    recording = b"RIFF" + b"\x00" * 4 + b"WAVE"
    reply = client.post(
        f"{ASSETS}/upload-authorizations",
        headers=owner,
        json={
            "purpose": "recording",
            "session_id": 11,
            "filename": "source.wav",
            "media_type": "audio",
            "content_type": "audio/wav",
            "size_bytes": len(recording),
        },
    )
    assert reply.status_code == 201, reply.text
    raw = reply.json()
    _put(client, raw, recording)
    assert (
        client.post(f"{ASSETS}/{raw['asset_id']}/complete", headers=owner, json={}).status_code
        == 200
    )
    wrong_purpose = {"asset_id": raw["asset_id"], "client_file_id": str(uuid4())}
    assert (
        _batch(client, complete, owner, [wrong_purpose])[0]["error"]["code"] == "asset_unavailable"
    )


def test_batch_checks_contributor_consent_again_after_hashing(memory_api, monkeypatch):
    client, owner, attachments = memory_api
    contributor = login(client, "13800000002")
    _, _, contributor_id = register_material_contributor(client, owner, contributor, "contributor")
    consent_url = f"{CONTRIBUTORS}/{contributor_id}/consent"
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    content = PNG + b"relative photo"
    item = _image(content)
    assert client.post(authorize, headers=contributor, json={"items": [item]}).status_code == 403
    assert (
        client.patch(
            consent_url, headers=contributor, json={"consent_status": "accepted"}
        ).status_code
        == 200
    )
    grant = _batch(client, authorize, contributor, [item])[0]
    _put(client, grant, content)
    assert _batch(client, complete, owner, [_completion(grant)])[0]["error"]["code"] == (
        "asset_unavailable"
    )
    inspect = ObjectStorageService.inspect_and_hash

    def withdraw_after_hashing(storage, key, max_bytes, *, validate_utf8=False):
        result = inspect(storage, key, max_bytes, validate_utf8)
        with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
            db.get(MemoryContributor, contributor_id).consent_status = "withdrawn"
        return result

    monkeypatch.setattr(ObjectStorageService, "inspect_and_hash", withdraw_after_hashing)
    result = _batch(client, complete, contributor, [_completion(grant)])[0]
    assert result["error"]["code"] == "contribution_not_allowed"
    assert (
        client.post(complete, headers=contributor, json={"items": [_completion(grant)]}).status_code
        == 403
    )
    with contextmanager(app.dependency_overrides[get_db])() as db:
        asset = db.scalar(select(MediaAsset).where(MediaAsset.id == grant["asset_id"]))
        assert asset.upload_status == "pending_upload"
    assert client.get(attachments, headers=owner).json() == []
