"""[新增] 通过两条批量接口和 Mock 直传验证附件保存及重试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


def _batch(client, path, headers, items, **options):
    reply = client.post(path, headers=headers, json={"items": items, **options})
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


def test_attachment_schema_allows_copies_and_reserves_operation_id_after_deletion(memory_api):
    """[新增] 使用现有接口创建附件，再直接验证关联表的约束。"""
    client, owner, attachments = memory_api
    picture = PNG + b"photo with copies"
    authorize = f"{attachments}/batch-upload-authorizations"
    grant = _batch(client, authorize, owner, [_image(picture)])[0]
    _put(client, grant, picture)
    attached = _batch(
        client, f"{attachments}/batch-complete-and-attach", owner, [_completion(grant)]
    )[0]
    assert attached["status"] == "attached"

    with contextmanager(app.dependency_overrides[get_db])() as db:
        with db.begin():
            original = db.get(FragmentAttachment, attached["attachment_id"])
            assert original.client_file_id is None
            shared = {
                "fragment_id": original.fragment_id,
                "media_asset_id": original.media_asset_id,
                "contributor_id": original.contributor_id,
            }
            creator = original.created_by
            assert creator is not None
            first_operation = uuid4()
            first_copy = FragmentAttachment(
                **shared, created_by=creator, client_file_id=first_operation
            )
            second_copy = FragmentAttachment(**shared, created_by=creator, client_file_id=uuid4())
            db.add_all([first_copy, second_copy])
            db.flush()
            assert len({original.id, first_copy.id, second_copy.id}) == 3
            first_copy.is_del = True

        with pytest.raises(IntegrityError), db.begin():
            db.add(FragmentAttachment(**shared, created_by=creator, client_file_id=first_operation))

        with pytest.raises(IntegrityError), db.begin():
            db.add(FragmentAttachment(**shared, client_file_id=uuid4()))


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


def test_ask_batch_uploads_once_and_links_only_the_selected_copies(memory_api, monkeypatch):
    """[新增] 跨过重复项只新增原关联，明确保留的关联有独立说明和顺序。"""
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    photo = PNG + b"original photo"
    digest = sha256(photo).hexdigest()
    items = [_image(photo, sha256=digest) for _ in range(2)]
    items.append(_image(photo, sha256=digest.upper(), filename="副本.png"))
    first, duplicate_to_skip, duplicate_to_keep = _batch(
        client, authorize, owner, items, duplicate_mode="ask"
    )
    assert first["status"] == "upload_required"
    for duplicate in (duplicate_to_skip, duplicate_to_keep):
        assert duplicate["status"] == "duplicate" and duplicate["reason"] == "same_batch"
        assert duplicate["representative_client_file_id"] == first["client_file_id"]
        assert duplicate["asset_id"] == first["asset_id"]
        assert "upload_url" not in duplicate
    _put(client, first, photo)

    inspect = ObjectStorageService.inspect_and_hash
    verified_keys = []

    def track_verification(storage, key, max_bytes, *, validate_utf8=False):
        verified_keys.append(key)
        return inspect(storage, key, max_bytes, validate_utf8=validate_utf8)

    monkeypatch.setattr(ObjectStorageService, "inspect_and_hash", track_verification)
    keep = _completion(
        duplicate_to_keep,
        duplicate_action="keep",
        representative_client_file_id=first["client_file_id"],
        caption="副本说明",
        sort_order=1,
    )
    skip = _completion(
        duplicate_to_skip,
        duplicate_action="skip",
        representative_client_file_id=first["client_file_id"],
    )
    # App 即使先传入依赖项，也会先校验并关联代表项，响应顺序仍与请求一致。
    saved, skipped, original = _batch(
        client, complete, owner, [keep, skip, _completion(first)], duplicate_mode="ask"
    )
    assert len(verified_keys) == 1
    assert saved["status"] == original["status"] == "attached"
    assert saved["attachment_id"] != original["attachment_id"]
    assert skipped["status"] == "skipped" and skipped["attachment_id"] == original["attachment_id"]
    active = client.get(attachments, headers=owner).json()
    assert [row["id"] for row in active] == [original["attachment_id"], saved["attachment_id"]]
    assert active[1]["caption"] == "副本说明"
    with contextmanager(app.dependency_overrides[get_db])() as db:
        assert db.scalars(select(MediaAsset.id)).all() == [first["asset_id"]]


def test_ask_split_batch_reuses_attachment_and_soft_deleted_operation_stays_deleted(memory_api):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    picture = PNG + b"same file across batches"
    item = _image(picture, sha256=sha256(picture).hexdigest())
    original = _batch(client, authorize, owner, [item], duplicate_mode="ask")[0]
    _put(client, original, picture)
    original_link = _batch(client, complete, owner, [_completion(original)], duplicate_mode="ask")[
        0
    ]

    new_item = _image(picture, sha256=item["sha256"])
    candidate = _batch(client, authorize, owner, [new_item], duplicate_mode="ask")[0]
    assert candidate["reason"] == "existing_attachment"
    assert candidate["attachment_id"] == original_link["attachment_id"]
    pending_choice = _batch(
        client, complete, owner, [_completion(candidate)], duplicate_mode="ask"
    )[0]
    assert pending_choice["status"] == "duplicate"
    assert (
        _batch(
            client,
            complete,
            owner,
            [_completion(candidate, duplicate_action="skip")],
            duplicate_mode="ask",
        )[0]["status"]
        == "skipped"
    )
    assert len(client.get(attachments, headers=owner).json()) == 1

    keep = _completion(candidate, duplicate_action="keep")
    saved = _batch(client, complete, owner, [keep], duplicate_mode="ask")[0]
    assert saved["status"] == "attached"
    assert _batch(client, complete, owner, [keep], duplicate_mode="ask")[0] == {
        **saved,
        "status": "already_attached",
    }
    assert (
        _batch(client, authorize, owner, [new_item], duplicate_mode="ask")[0]["attachment_id"]
        == (saved["attachment_id"])
    )
    assert (
        _batch(client, authorize, owner, [{**new_item, "sha256": "a" * 64}], duplicate_mode="ask")[
            0
        ]["error"]["code"]
        == "file_id_conflict"
    )

    assert (
        client.delete(f"{attachments}/{saved['attachment_id']}", headers=owner).status_code == 200
    )
    remaining = client.get(attachments, headers=owner).json()
    assert [row["id"] for row in remaining] == [original_link["attachment_id"]]
    assert (
        client.get(f"{ASSETS}/{candidate['asset_id']}", headers=owner).json()["upload_status"]
        == "attached"
    )
    for path, request in ((authorize, new_item), (complete, keep)):
        gone = _batch(client, path, owner, [request], duplicate_mode="ask")[0]
        assert gone["error"]["code"] == "attachment_deleted" and not gone["error"]["retryable"]


def test_ask_split_pending_reuses_only_same_fragment_and_can_recover_after_expiry(memory_api):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    photo = PNG + b"one pending upload"
    digest = sha256(photo).hexdigest()
    original = _image(photo, sha256=digest)
    first = _batch(client, authorize, owner, [original], duplicate_mode="ask")[0]
    later_item = _image(photo, sha256=digest)
    later = _batch(client, authorize, owner, [later_item], duplicate_mode="ask")[0]
    assert later["status"] == "duplicate" and later["reason"] == "pending_upload"
    assert later["representative_client_file_id"] == first["client_file_id"]
    assert later["expires_at"] == first["expires_at"]

    other = _new_fragment(client, owner)
    other_grant = _batch(
        client, f"{other}/batch-upload-authorizations", owner, [later_item], duplicate_mode="ask"
    )[0]
    assert (
        other_grant["status"] == "upload_required" and other_grant["asset_id"] != first["asset_id"]
    )
    wrong_scope = _completion(
        later, duplicate_action="keep", representative_client_file_id=first["client_file_id"]
    )
    assert (
        _batch(
            client, f"{other}/batch-complete-and-attach", owner, [wrong_scope], duplicate_mode="ask"
        )[0]["error"]["code"]
        == "asset_unavailable"
    )
    with contextmanager(app.dependency_overrides[get_db])() as db, db.begin():
        db.get(MediaAsset, first["asset_id"]).upload_expires_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=5)
    assert _batch(client, complete, owner, [wrong_scope], duplicate_mode="ask")[0]["error"][
        "code"
    ] == ("authorization_expired")
    replacement = _batch(client, authorize, owner, [later_item], duplicate_mode="ask")[0]
    assert (
        replacement["status"] == "upload_required" and replacement["asset_id"] != first["asset_id"]
    )


def test_ask_duplicate_found_during_verification_waits_for_decision(memory_api):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    photo = PNG + b"duplicate without declared digest"
    first, second = _batch(
        client, authorize, owner, [_image(photo), _image(photo)], duplicate_mode="ask"
    )
    assert first["asset_id"] != second["asset_id"]
    _put(client, first, photo)
    _put(client, second, photo)
    original, decision = _batch(
        client, complete, owner, [_completion(first), _completion(second)], duplicate_mode="ask"
    )
    assert decision["status"] == "duplicate"
    assert decision["attachment_id"] == original["attachment_id"]
    assert decision["asset_id"] == original["asset_id"]
    assert len(client.get(attachments, headers=owner).json()) == 1
    # App 可沿用原授权 ID，服务端会找到校验后保留的素材。
    keep = _completion(second, duplicate_action="keep")
    saved = _batch(client, complete, owner, [keep], duplicate_mode="ask")[0]
    assert saved["status"] == "attached" and saved["asset_id"] == original["asset_id"]
    assert _batch(client, complete, owner, [keep], duplicate_mode="ask")[0] == {
        **saved,
        "status": "already_attached",
    }


def test_ask_representative_failure_is_shared_and_unrelated_file_succeeds(memory_api, monkeypatch):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    photo, other = PNG + b"photo", PNG + b"other"
    items = [_image(photo, sha256=sha256(photo).hexdigest()) for _ in range(2)] + [_image(other)]
    first, duplicate, independent = _batch(client, authorize, owner, items, duplicate_mode="ask")
    _put(client, first, other)
    _put(client, independent, other)
    inspect = ObjectStorageService.inspect_and_hash
    attempts = []

    def track_verification(storage, key, max_bytes, *, validate_utf8=False):
        attempts.append(key)
        return inspect(storage, key, max_bytes, validate_utf8=validate_utf8)

    monkeypatch.setattr(ObjectStorageService, "inspect_and_hash", track_verification)
    failed, dependent, saved = _batch(
        client,
        complete,
        owner,
        [
            _completion(first),
            _completion(
                duplicate,
                duplicate_action="keep",
                representative_client_file_id=first["client_file_id"],
            ),
            _completion(independent),
        ],
        duplicate_mode="ask",
    )
    assert failed["error"] == dependent["error"] and failed["error"]["code"] == "file_mismatch"
    assert saved["status"] == "attached" and len(attempts) == 2


def test_ask_kept_copies_each_use_one_slot_and_existing_operations_replay_at_limit(memory_api):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    photo = PNG + b"photo"
    items = [_image(photo, sha256=sha256(photo).hexdigest()) for _ in range(9)]
    representative, *copies = _batch(client, authorize, owner, items, duplicate_mode="ask")
    _put(client, representative, photo)
    request = [_completion(representative)] + [
        _completion(
            copy,
            duplicate_action="keep",
            representative_client_file_id=representative["client_file_id"],
        )
        for copy in copies
    ]
    saved = _batch(client, complete, owner, request, duplicate_mode="ask")
    assert {row["status"] for row in saved} == {"attached"}
    assert len({row["attachment_id"] for row in saved}) == 9
    assert _batch(client, complete, owner, request, duplicate_mode="ask") == [
        {**row, "status": "already_attached"} for row in saved
    ]
    extra = _batch(
        client, authorize, owner, [_image(photo, sha256=items[0]["sha256"])], duplicate_mode="ask"
    )[0]
    assert (
        _batch(
            client,
            complete,
            owner,
            [_completion(extra, duplicate_action="keep")],
            duplicate_mode="ask",
        )[0]["error"]["code"]
        == "attachment_limit_reached"
    )
    assert (
        _batch(
            client,
            complete,
            owner,
            [_completion(extra, duplicate_action="skip")],
            duplicate_mode="ask",
        )[0]["status"]
        == "skipped"
    )


def test_ask_requires_direct_representative_for_same_asset(memory_api):
    client, owner, attachments = memory_api
    complete = f"{attachments}/batch-complete-and-attach"
    first_id, later_id = str(uuid4()), str(uuid4())
    first = {"client_file_id": first_id, "asset_id": 1}
    later = {"client_file_id": later_id, "asset_id": 2, "representative_client_file_id": first_id}
    for payload in (
        {"items": [{**first, "duplicate_action": "keep"}]},
        {"duplicate_mode": "ask", "items": [first, later]},
        {"duplicate_mode": "ask", "items": [{**first, "representative_client_file_id": first_id}]},
    ):
        assert client.post(complete, headers=owner, json=payload).status_code == 422


def test_ask_reuses_verified_file_across_fragments_but_not_across_accounts(memory_api):
    client, owner, attachments = memory_api
    authorize = f"{attachments}/batch-upload-authorizations"
    complete = f"{attachments}/batch-complete-and-attach"
    picture = PNG + b"photo"
    digest = sha256(picture).hexdigest()
    first = _batch(client, authorize, owner, [_image(picture, sha256=digest)])[0]
    _put(client, first, picture)
    _batch(client, complete, owner, [_completion(first)])

    relative = login(client, "13800000002")
    _, _, contributor_id = register_material_contributor(client, owner, relative, "contributor")
    consent = client.patch(
        f"{CONTRIBUTORS}/{contributor_id}/consent",
        headers=relative,
        json={"consent_status": "accepted"},
    )
    assert consent.status_code == 200
    foreign = _batch(
        client, authorize, relative, [_image(picture, sha256=digest)], duplicate_mode="ask"
    )[0]
    assert foreign["status"] == "upload_required" and foreign["asset_id"] != first["asset_id"]

    other_fragment = _new_fragment(client, owner)
    different_picture = PNG + b"other"
    grants = _batch(
        client,
        f"{other_fragment}/batch-upload-authorizations",
        owner,
        [
            _image(picture, sha256=digest),
            _image(picture, sha256=digest),
            _image(different_picture, sha256=sha256(different_picture).hexdigest()),
        ],
        duplicate_mode="ask",
    )
    reused, duplicate, distinct = grants
    assert reused["status"] == "reused" and reused["asset_id"] == first["asset_id"]
    assert duplicate["reason"] == "same_batch"
    assert duplicate["representative_client_file_id"] == reused["client_file_id"]
    # 文件名相同，摘要不同，仍然单独上传。
    assert distinct["status"] == "upload_required" and distinct["asset_id"] != reused["asset_id"]
