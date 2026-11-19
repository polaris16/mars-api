"""Exercise memory extraction over HTTP and through the single-job Worker entry point."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import JSON, Integer, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.database import get_db
from app.api.dependencies import get_current_user
from app.main import create_app
from app.models import (
    AIProcessingJob, FragmentAttachment, MediaAsset, MemoryContributor, MemoryElement, MemoryFragment,
    MemorySession, MemorySessionMember, Topic, TopicParticipant, User, UserRelationship,
)
from app.schemas.memory_extraction import ExtractedElement
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.object_storage_service import ObjectStorageService
from app.services.text_extraction_model import ExtractionError, TextExtractionModel


SESSION = "/api/recording-sessions/11"
FRAGMENTS = f"{SESSION}/fragments"
ELEMENTS = f"{SESSION}/memory-elements"
OWNER = {"Authorization": "Bearer 1"}
VIEWER = {"Authorization": "Bearer 2"}
OUTSIDER = {"Authorization": "Bearer 3"}


@pytest.fixture
def extraction_api(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_text_provider", "mock")
    monkeypatch.setattr(settings, "ai_text_enabled", True)
    monkeypatch.setattr(settings, "ai_text_max_attempts", 3)
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path / "media"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def add_functions(connection, _):
        connection.create_function("btrim", 1, str.strip)
        connection.create_function("jsonb_typeof", 1, lambda value: "object" if isinstance(json.loads(value), dict) else "null")

    for model in (MemoryFragment, MemoryContributor, MemoryElement, AIProcessingJob):
        column = model.__table__.c.id
        monkeypatch.setattr(column, "type", column.type.with_variant(Integer(), "sqlite"))
    for model, name in (
        (AIProcessingJob, "input_payload"), (AIProcessingJob, "result_payload"), (MemoryElement, "attribute_confidence"),
    ):
        column = model.__table__.columns[name]
        monkeypatch.setattr(column, "type", column.type.with_variant(JSON(), "sqlite"))
        if column.server_default is not None:
            monkeypatch.setattr(column.server_default, "arg", text("'{}'"))
    for index in MemoryElement.__table__.indexes:
        if index.name == "uq_t_memory_element_fragment_sequence_active":
            monkeypatch.setitem(index.dialect_options["sqlite"], "where", text("is_del = false"))
    with engine.begin() as db:
        db.execute(text("""CREATE TABLE t_user (
            id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT, phone TEXT,
            sex INTEGER, birth_date DATE, tags TEXT, user_role INTEGER, created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_by INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_del BOOLEAN DEFAULT 0,
            onboarding_completed BOOLEAN DEFAULT 0
        )"""))
        db.execute(text("""INSERT INTO t_user (id, username, phone)
            VALUES (1, 'owner', '13300000001'), (2, 'viewer', '13300000002'), (3, 'outsider', '13300000003')
        """))
        models = (MemorySession, MemorySessionMember, UserRelationship, MemoryContributor, MediaAsset,
                  MemoryFragment, FragmentAttachment, Topic, TopicParticipant, AIProcessingJob, MemoryElement)
        MemorySession.metadata.create_all(db, tables=[model.__table__ for model in models])
        db.execute(text("INSERT INTO t_memory_session (id, owner_user_id) VALUES (11, 1), (12, 1)"))
        db.execute(text("""INSERT INTO t_memory_session_member
            (id, session_id, user_id, role, granted_by_user_id) VALUES (21, 11, 2, 'viewer', 1)"""))
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    def override_user(request: Request, db: Session = Depends(get_db)):
        header = request.headers.get("Authorization")
        if header not in {OWNER["Authorization"], VIEWER["Authorization"], OUTSIDER["Authorization"]}:
            raise HTTPException(status_code=401)
        user = db.get(User, int(header.split()[-1]))
        db.rollback()
        return user

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        engine.dispose()


def save_text(client, source="我在上海读书。", **fields):
    response = client.post(FRAGMENTS, headers=OWNER, json={
        "source_type": "text", "content_text": source, **fields,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_synchronous_extraction_is_idempotent_and_provenance_is_scoped(extraction_api):
    client, factory = extraction_api
    source = "  我在上海读书。"
    fragment = save_text(client, source)
    job = fragment["extraction_job"]
    assert (job["status"], job["attempt_count"], job["element_count"]) == ("succeeded", 1, 1)
    jobs = f"{FRAGMENTS}/{fragment['id']}/extraction-jobs"
    assert client.post(jobs, headers=OWNER, json={}).json() == job
    assert client.get(jobs, headers=VIEWER).json()["total"] == 1
    assert client.post(jobs, headers=VIEWER, json={}).status_code == 403
    assert client.get(f"{SESSION}/extraction-jobs/{job['id']}", headers=OUTSIDER).status_code == 404
    rows = client.get(ELEMENTS, headers=VIEWER, params={"processing_job_id": job["id"], "origin": "extracted"})
    item, = rows.json()["items"]
    url = f"{ELEMENTS}/{item['id']}"
    assert client.get(url, headers=OWNER).json() == item
    assert client.get(url.replace("/11/", "/12/"), headers=OWNER).status_code == 404
    evidence = client.get(f"{url}/provenance", headers=VIEWER).json()
    assert evidence["source_kind"] == "content_text"
    assert source[evidence["start_offset"]:evidence["end_offset"]] == evidence["source_quote"] == source.strip()
    with factory() as db:
        assert source not in json.dumps(db.get(AIProcessingJob, job["id"]).input_payload, ensure_ascii=False)
    assert client.delete(f"{FRAGMENTS}/{fragment['id']}", headers=OWNER).status_code == 200
    assert client.get(f"{url}/provenance", headers=VIEWER).status_code == 404


def test_retry_budget_fallback_and_manual_replacement(extraction_api, monkeypatch):
    client, factory = extraction_api
    original_extract = TextExtractionModel.extract

    def unavailable(*_):
        raise ExtractionError("MODEL_UNREACHABLE", retryable=True)

    monkeypatch.setattr(TextExtractionModel, "extract", unavailable)
    source = "  " + "上海往事。" * 50 + "  "
    fragment = save_text(client, source)
    job = fragment["extraction_job"]
    assert job["status"] == "queued" and job["error_code"] == "MODEL_UNREACHABLE"
    assert job["next_retry_at"] is not None
    assert client.get(ELEMENTS, headers=OWNER).json()["total"] == 0
    job_url = f"{SESSION}/extraction-jobs/{job['id']}"
    service = MemoryExtractionService()
    for attempts in (2, 3):
        with factory.begin() as db:
            db.get(AIProcessingJob, job["id"]).available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        with factory() as db:
            assert service.run(db)
        status = client.get(job_url, headers=OWNER).json()
        assert status["attempt_count"] == attempts
    assert status["status"] == "failed" and status["element_count"] is None
    with factory() as db:
        assert len(db.get(AIProcessingJob, job["id"]).result_payload["failures"]) == 3
    fallback_id = status["fallback_element_id"]
    fallback = client.get(f"{ELEMENTS}/{fallback_id}", headers=VIEWER).json()
    assert fallback["origin"] == "fallback" and fallback["overall_confidence"] is None
    quote = client.get(f"{ELEMENTS}/{fallback_id}/provenance", headers=VIEWER).json()
    assert quote["source_text"] == source and quote["end_offset"] == len(source)

    monkeypatch.setattr(TextExtractionModel, "extract", original_extract)
    jobs = f"{FRAGMENTS}/{fragment['id']}/extraction-jobs"
    retry = {"retry_of_job_id": job["id"]}
    result = client.post(jobs, headers=OWNER, json=retry)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded" and result.json()["retry_of_job_id"] == job["id"]
    assert client.post(jobs, headers=OWNER, json=retry).json() == result.json()
    assert client.get(f"{ELEMENTS}/{fallback_id}", headers=OWNER).status_code == 404
    assert client.get(job_url, headers=OWNER).json()["fallback_element_id"] is None


def test_configuration_and_external_opt_in(extraction_api, monkeypatch):
    client, _ = extraction_api
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_text_enabled", False)
    fragment = save_text(client)
    assert fragment["extraction_job"] is None and fragment["extraction_skip_reason"] == "EXTRACTION_UNAVAILABLE"
    jobs = f"{FRAGMENTS}/{fragment['id']}/extraction-jobs"
    assert client.post(jobs, headers=OWNER, json={}).status_code == 503
    monkeypatch.setattr(settings, "ai_text_enabled", True)
    monkeypatch.setattr(settings, "ai_text_provider", "openai-compatible")
    monkeypatch.setattr(settings, "ai_text_base_url", "https://models.test/v1")
    monkeypatch.setattr(settings, "ai_text_model", "test-model")
    monkeypatch.setattr(settings, "ai_text_api_key", SecretStr("fake-test-key"))
    seen = []

    def extract(_self, source, _timeout):
        seen.append(source)
        return []

    monkeypatch.setattr(TextExtractionModel, "extract", extract)
    assert client.post(jobs, headers=OWNER, json={}).status_code == 403
    skipped = save_text(client)
    assert skipped["extraction_skip_reason"] == "SOURCE_AI_AUTHORIZATION_REQUIRED"
    assert seen == []
    submitted = client.post(jobs, headers=OWNER, json={"allow_external_processing": True})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["element_count"] == 0 and len(seen) == 1


def test_unverifiable_model_evidence_uses_original_text_fallback(extraction_api, monkeypatch):
    client, _ = extraction_api

    def fabricated_quote(*_):
        return [ExtractedElement(element_type="event", summary="编造的事件", source_quote="我在天津上学。")]

    monkeypatch.setattr(TextExtractionModel, "extract", fabricated_quote)
    source = "我在上海上学。"
    job = save_text(client, source)["extraction_job"]
    assert (job["status"], job["attempt_count"], job["error_code"]) == ("failed", 1, "MODEL_EVIDENCE_INVALID")
    fallback, = client.get(ELEMENTS, headers=OWNER).json()["items"]
    assert fallback["origin"] == "fallback" and fallback["summary"] == source
    quote = client.get(f"{ELEMENTS}/{fallback['id']}/provenance", headers=OWNER).json()
    assert quote["source_quote"] is None and quote["source_text"] == source


def test_text_file_is_loaded_using_verified_digest(extraction_api):
    client, factory = extraction_api
    raw = "我在北京工作。".encode()
    with factory.begin() as db:
        db.add(MediaAsset(
            id=25, uploaded_by_user_id=1, purpose="source", media_type="text",
            storage_provider="mock", storage_region="local", bucket_name="test", object_key="source.txt",
            content_type="text/plain", expected_size_bytes=len(raw), size_bytes=len(raw),
            content_sha256=sha256(raw).hexdigest(), upload_status="uploaded", uploaded_at=datetime.now(timezone.utc),
            upload_expires_at=datetime.now(timezone.utc),
        ))
    storage = ObjectStorageService("")
    (storage.local_root / "source.txt").write_bytes(raw)
    response = client.post(FRAGMENTS, headers=OWNER, json={"source_type": "text", "source_asset_id": 25})
    assert response.status_code == 201, response.text
    assert response.json()["extraction_job"]["status"] == "succeeded"
    item, = client.get(ELEMENTS, headers=OWNER).json()["items"]
    evidence = client.get(f"{ELEMENTS}/{item['id']}/provenance", headers=OWNER).json()
    assert evidence["source_kind"] == "text_file" and evidence["source_asset_id"] == 25


def test_stale_lease_cannot_publish(extraction_api, monkeypatch):
    client, factory = extraction_api
    run = MemoryExtractionService.run
    monkeypatch.setattr(MemoryExtractionService, "run", lambda *args, **kwargs: False)
    job_id = save_text(client)["extraction_job"]["id"]
    service = MemoryExtractionService()
    with factory() as db:
        first = service._claim(db, job_id)
    with factory.begin() as db:
        db.get(AIProcessingJob, job_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with factory() as db:
        assert run(service, db)
        service._complete(db, first, None, [], ExtractionError("STALE"))
    with factory() as db:
        job = db.get(AIProcessingJob, job_id)
        assert job.status == "succeeded" and job.attempt_count == 2
        failure, = job.result_payload["failures"]
        assert failure["attempt"] == 1 and failure["code"] == "WORKER_LEASE_EXPIRED"
        assert len(db.scalars(select(MemoryElement)).all()) == 1
