from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from fastapi import HTTPException
import json

from app.core.config import get_settings
from app.models import FragmentAttachment, MediaAsset, MemoryFragment
from app.schemas.memory import AttachmentInput, AttachmentPatch, FragmentCreate, SessionSummary, UploadAuthorizationRequest, UploadCompleteRequest
from app.services.object_storage_service import ObjectStorageService


@pytest.mark.parametrize(("schema", "field", "maximum"), [
    (UploadCompleteRequest, "duration_ms", (1 << 63) - 1),
    (UploadCompleteRequest, "sample_rate_hz", (1 << 31) - 1),
    (AttachmentInput, "sort_order", (1 << 31) - 1),
    (AttachmentPatch, "sort_order", (1 << 31) - 1),
])
def test_media_metadata_fits_postgresql_integer_range(schema, field, maximum):
    payload = {"asset_id": 1} if schema is AttachmentInput else {}
    assert getattr(schema.model_validate({**payload, field: maximum}), field) == maximum
    with pytest.raises(ValidationError):
        schema.model_validate({**payload, field: maximum + 1})


def test_session_summary_maps_persisted_last_material_at_to_api_field():
    created_at = datetime.now(timezone.utc)
    summary = SessionSummary.model_validate(
        SimpleNamespace(
            id=11,
            topic_id=None,
            title="未命名",
            is_favorite=False,
            last_material_at=created_at,
            created_at=created_at,
        )
    )

    assert summary.last_recorded_at == created_at
    assert summary.model_dump()["last_recorded_at"] == created_at


def test_fragment_create_accepts_legacy_audio_asset_id():
    request = FragmentCreate(audio_asset_id=42, transcript_raw="spoken words")

    assert request.source_type == "audio"
    assert request.audio_asset_id == 42
    assert request.source_asset_id is None


def test_fragment_create_accepts_text_and_uploaded_text_asset():
    typed_text = FragmentCreate(source_type="text", content_text="A written memory")
    uploaded_text = FragmentCreate(source_type="text", source_asset_id=19)

    assert typed_text.content_text == "A written memory"
    assert uploaded_text.source_asset_id == 19


@pytest.mark.parametrize(
    "payload",
    [
        {"source_type": "audio"},
        {"source_type": "video"},
        {"source_type": "text"},
        {"source_type": "text", "content_text": "  "},
        {"source_type": "video", "source_asset_id": 4, "audio_asset_id": 5},
    ],
)
def test_fragment_create_rejects_missing_or_conflicting_source(payload):
    with pytest.raises(ValidationError):
        FragmentCreate(**payload)


def test_upload_authorization_accepts_text_source():
    request = UploadAuthorizationRequest(
        purpose="source",
        media_type="text",
        filename="memo.txt",
        content_type="text/plain; charset=utf-8",
        size_bytes=12,
        session_id=8,
    )

    assert request.purpose == "source"
    assert request.media_type == "text"


def test_upload_authorization_rejects_text_attachment():
    with pytest.raises(ValidationError):
        UploadAuthorizationRequest(
            purpose="attachment",
            media_type="text",
            filename="memo.txt",
            content_type="text/plain",
            size_bytes=12,
            fragment_id=4,
        )


@pytest.mark.parametrize("invalid_tail", [b"\xff", b"\x00", b"\xe4\xb8"])
def test_text_asset_upload_rejects_invalid_data_beyond_signature_prefix(tmp_path, monkeypatch, invalid_tail):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path))
    storage = ObjectStorageService("http://localhost")
    key = "text/invalid.txt"
    file_path = storage.local_root / key
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"a" * 64 + invalid_tail)
    file_path.with_suffix(file_path.suffix + ".meta.json").write_text(
        json.dumps({"content_type": "text/plain"}), encoding="utf-8"
    )

    with pytest.raises(HTTPException) as error:
        storage.inspect_and_hash(key, 128, validate_utf8=True)

    assert error.value.status_code == 422


def test_material_models_have_source_and_contributor_fields():
    assert {"uploaded_by_user_id", "media_type", "purpose"} <= set(MediaAsset.__table__.columns.keys())
    assert {"source_type", "source_asset_id", "content_text", "contributor_id"} <= set(MemoryFragment.__table__.columns.keys())
    assert "contributor_id" in FragmentAttachment.__table__.columns
    assert not MemoryFragment.__table__.c.contributor_id.nullable
    assert not FragmentAttachment.__table__.c.contributor_id.nullable
