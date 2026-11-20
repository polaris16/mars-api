"""Exercise memory extraction over HTTP and through the single-job Worker entry point."""

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import JSON, Integer, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.main import create_app
from app.models import (
    AIProcessingJob,
    FragmentAttachment,
    LifeEntity,
    LifeEntityAlias,
    MediaAsset,
    MemoryContributor,
    MemoryDraftVersion,
    MemoryElement,
    MemoryElementEntity,
    MemoryEntityCandidate,
    MemoryEntityEvidence,
    MemoryEntityResolution,
    MemoryFragment,
    MemorySession,
    MemorySessionMember,
    MemorySourceRevision,
    Topic,
    TopicParticipant,
    User,
    UserRelationship,
)
from app.schemas.memory_extraction import EntityMention, ExtractedElement, StructuredElement
from app.services.audio_transcription import AudioTranscriptionModel
from app.services.memory_extraction_service import MemoryExtractionService
from app.services.memory_pipeline import MemoryPipelineService
from app.services.narrative_organization import organize_narrative
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
    monkeypatch.setattr(settings, "ai_text_mock_scenario", "success")
    monkeypatch.setattr(settings, "ai_text_max_attempts", 3)
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path / "media"))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def add_functions(connection, _):
        connection.create_function("btrim", 1, str.strip)
        def jsonb_typeof(value):
            parsed = json.loads(value) if value is not None else None
            return "object" if isinstance(parsed, dict) else "array" if isinstance(parsed, list) else "null"

        connection.create_function("jsonb_typeof", 1, jsonb_typeof)
        connection.create_function("jsonb_array_length", 1, lambda value: len(json.loads(value)))

    version_models = (
        MemorySourceRevision, MemoryDraftVersion, LifeEntity, LifeEntityAlias,
        MemoryElementEntity, MemoryEntityCandidate, MemoryEntityResolution, MemoryEntityEvidence,
    )
    for model in (MemorySession, MemoryFragment, MemoryContributor, MemoryElement, AIProcessingJob, *version_models):
        column = model.__table__.c.id
        monkeypatch.setattr(column, "type", column.type.with_variant(Integer(), "sqlite"))
    for model, name in (
        (AIProcessingJob, "input_payload"),
        (AIProcessingJob, "result_payload"),
        (MemoryElement, "attribute_confidence"),
        (MemoryElement, "draft_references"),
        (LifeEntity, "attributes"),
        (MemorySourceRevision, "transcript_segments"),
        (MemoryDraftVersion, "paragraphs"),
        (MemoryEntityCandidate, "evidence"),
        (MemoryEntityCandidate, "candidate_entity_ids"),
        (MemoryEntityEvidence, "evidence"),
    ):
        column = model.__table__.columns[name]
        monkeypatch.setattr(column, "type", column.type.with_variant(JSON(none_as_null=True), "sqlite"))
        if column.server_default is not None:
            monkeypatch.setattr(column.server_default, "arg", text(str(column.server_default.arg).split("::")[0]))
    for index in MemoryElement.__table__.indexes:
        if index.name == "uq_t_memory_element_fragment_sequence_active":
            monkeypatch.setitem(index.dialect_options["sqlite"], "where", text("is_del = false"))
    with engine.begin() as db:
        db.execute(
            text("""CREATE TABLE t_user (
            id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT, phone TEXT,
            sex INTEGER, birth_date DATE, tags TEXT, user_role INTEGER, created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_by INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_del BOOLEAN DEFAULT 0,
            onboarding_completed BOOLEAN DEFAULT 0
        )""")
        )
        db.execute(
            text("""INSERT INTO t_user (id, username, phone)
            VALUES (1, 'owner', '13300000001'), (2, 'viewer', '13300000002'), (3, 'outsider', '13300000003')
        """)
        )
        models = (
            MemorySession,
            MemorySessionMember,
            UserRelationship,
            MemoryContributor,
            MediaAsset,
            MemoryFragment,
            FragmentAttachment,
            Topic,
            TopicParticipant,
            AIProcessingJob,
            MemoryElement,
            *version_models,
        )
        MemorySession.metadata.create_all(db, tables=[model.__table__ for model in models])
        db.execute(text("INSERT INTO t_memory_session (id, owner_user_id) VALUES (11, 1), (12, 1)"))
        db.execute(
            text("""INSERT INTO t_memory_session_member
            (id, session_id, user_id, role, granted_by_user_id) VALUES (21, 11, 2, 'viewer', 1)""")
        )
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    def override_user(request: Request, db: Session = Depends(get_db)):
        header = request.headers.get("Authorization")
        if header not in {
            OWNER["Authorization"],
            VIEWER["Authorization"],
            OUTSIDER["Authorization"],
        }:
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
    response = client.post(
        FRAGMENTS,
        headers=OWNER,
        json={
            "source_type": "text",
            "content_text": source,
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_versioned_memory_processing_edit_and_entity_resolution(extraction_api):
    client, factory = extraction_api
    patched = client.patch(SESSION, headers=OWNER, json={"visibility": "private"})
    assert patched.status_code == 200 and patched.json()["visibility"] == "private"
    fragment = save_text(client, "我跟陈师傅学手艺。\n后来去了上海。")
    root = f"{FRAGMENTS}/{fragment['id']}"
    initial = client.get(f"{root}/processing", headers=OWNER).json()
    assert initial["organization"]["status"] == "queued"
    assert initial["upload_processed_at"] is None
    assert client.post(f"{root}/extraction-jobs", headers=OWNER, json={}).status_code == 409
    assert client.get(f"{root}/processing", headers=VIEWER).status_code == 404

    pipeline = MemoryPipelineService()
    with factory() as db:
        assert pipeline.run(db, "organization")
        assert pipeline.extraction.run(db)
    progress = client.get(f"{root}/processing", headers=OWNER).json()
    assert progress["organization"]["status"] == progress["extraction"]["status"] == "succeeded"
    assert progress["upload_processed_at"]
    item = progress["extraction"]["elements"][0]
    assert item["verification_status"] == "pending"
    evidence = client.get(f"{ELEMENTS}/{item['id']}/provenance", headers=OWNER).json()
    (reference,) = evidence["draft_references"]
    assert reference["source_references"][0]["text_ranges"] == [{"start_offset": 0, "end_offset": 9}]

    candidates = f"{SESSION}/entity-candidates"
    (candidate,) = client.get(candidates, headers=OWNER).json()["items"]
    assert candidate["mention_text"] == "陈师傅"
    payload = {
        "expected_revision": candidate["revision"], "action": "create_new", "canonical_name": "陈师傅",
    }
    resolve = f"{candidates}/{candidate['id']}/resolutions"
    decision = client.post(resolve, headers=OWNER, json=payload)
    assert decision.status_code == 201, decision.text
    assert client.post(resolve, headers=OWNER, json=payload).json() == decision.json()
    assert client.post(resolve, headers=OWNER, json={**payload, "canonical_name": "老陈"}).status_code == 409
    entity_id = decision.json()["entity_id"]
    assert client.get(f"{SESSION}/memory-entities/{entity_id}", headers=OWNER).json()["aliases"] == ["陈师傅"]
    assert client.get(f"{SESSION}/memory-entities", headers=OWNER).json()["total"] == 1

    draft_id = progress["draft_version_id"]
    draft = client.get(f"{root}/draft-versions/{draft_id}", headers=OWNER).json()
    first, second = draft["paragraphs"]
    update = {
        "source_revision_id": progress["source_revision_id"],
        "base_version_id": draft_id,
        "paragraphs": [
            {"id": first["id"], "text": first["text"]},
            {"id": second["id"], "text": "后来去了北京。"},
        ],
    }
    headers = {**OWNER, "Idempotency-Key": str(uuid4())}
    revised = client.post(f"{root}/draft-versions", headers=headers, json=update)
    assert revised.status_code == 201, revised.text
    assert client.post(f"{root}/draft-versions", headers=headers, json=update).json() == revised.json()
    assert client.post(f"{root}/draft-versions", headers=OWNER, json=update).status_code == 409
    assert client.get(f"{ELEMENTS}/{item['id']}", headers=OWNER).status_code == 404
    with factory() as db:
        assert pipeline.extraction.run(db)
    new_items = client.get(ELEMENTS, headers=OWNER).json()["items"]
    assert new_items[0]["entities"][0]["id"] == entity_id
    assert new_items[1]["summary"] == "后来去了北京。"
    assert client.get(f"{root}/draft-versions", headers=OWNER).json()["total"] == 2


def test_versioned_org_failed_job_can_retry_then_extraction_can_fallback(extraction_api, monkeypatch):
    client, factory = extraction_api
    client.patch(SESSION, headers=OWNER, json={"visibility": "private"})
    fragment = save_text(client, "我写下了一段回忆。")
    root = f"{FRAGMENTS}/{fragment['id']}"
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_text_max_attempts", 1)
    with factory.begin() as db:
        job = db.scalar(select(AIProcessingJob).where(AIProcessingJob.source_fragment_id == fragment["id"]))
        job.max_attempts = 1

    def fail(*args, **kwargs):
        raise ExtractionError("ORGANIZATION_UNREACHABLE", retryable=True)

    pipeline = MemoryPipelineService()
    with monkeypatch.context() as context:
        context.setattr("app.services.memory_pipeline.organize_narrative", fail)
        with factory() as db:
            assert pipeline.run(db, "organization")
    failed = client.get(f"{root}/processing", headers=OWNER).json()
    assert failed["organization"]["status"] == "failed"
    assert failed["upload_processed_at"] is None
    body = {"stage": "organization", "retry_of_job_id": failed["organization"]["job_id"]}
    retry = client.post(f"{root}/processing-jobs", headers=OWNER, json=body)
    assert retry.status_code == 202, retry.text
    assert client.post(f"{root}/processing-jobs", headers=OWNER, json=body).json() == retry.json()
    monkeypatch.setattr(settings, "ai_text_mock_scenario", "fallback")
    with factory() as db:
        assert pipeline.run(db, "organization")
        assert pipeline.extraction.run(db)
    failed_extraction = client.get(f"{root}/processing", headers=OWNER).json()["extraction"]
    assert failed_extraction["status"] == "failed"
    fallback_url = f"{ELEMENTS}/{failed_extraction['fallback_element_id']}/provenance"
    fallback = client.get(fallback_url, headers=OWNER).json()
    assert fallback["source_text"] == "我写下了一段回忆。"
    assert fallback["draft_references"]


def _open_versioned_text(client, factory, source, **fields):
    assert client.patch(SESSION, headers=OWNER, json={"visibility": "private"}).status_code == 200
    fragment = save_text(client, source, **fields)
    pipeline = MemoryPipelineService()
    with factory() as db:
        assert pipeline.run(db, "organization")
        assert pipeline.extraction.run(db)
    path = f"{FRAGMENTS}/{fragment['id']}"
    progress = client.get(f"{path}/processing", headers=OWNER).json()
    draft = client.get(f"{path}/draft-versions/{progress['draft_version_id']}", headers=OWNER).json()
    return path, draft


def _revise_last_paragraph(client, path, draft, text):
    paragraphs = [{"id": item["id"], "text": item["text"]} for item in draft["paragraphs"]]
    paragraphs[-1]["text"] = text
    revised = client.post(f"{path}/draft-versions", headers=OWNER, json={
        "source_revision_id": draft["source_revision_id"], "base_version_id": draft["id"],
        "paragraphs": paragraphs,
    })
    assert revised.status_code == 201, revised.text
    return revised.json()


def test_automatic_entity_keeps_id_after_unrelated_paragraph_edit(extraction_api, monkeypatch):
    client, factory = extraction_api
    quote = "我跟陈师傅学车床，他是一车间的车工。"

    def extract(*_args, **_kwargs):
        return [StructuredElement(
            element_type="event", source_quote=quote, summary="我在一车间跟陈师傅学车床。",
            entity_mentions=[EntityMention(
                entity_type="person", mention_text="陈师傅", relation_type="participant",
                identity_quote="陈师傅学车床，他是一车间的车工",
            )],
        )]

    monkeypatch.setattr(TextExtractionModel, "extract_structured", extract)
    path, draft = _open_versioned_text(client, factory, f"{quote}\n后来我去了北京。")
    (first,) = client.get(ELEMENTS, headers=OWNER).json()["items"]
    (entity,) = first["entities"]
    revised = _revise_last_paragraph(client, path, draft, "后来我去了上海。")
    with factory() as db:
        assert MemoryExtractionService().run(db)
    (new,) = client.get(ELEMENTS, headers=OWNER).json()["items"]
    assert new["draft_version_id"] == revised["id"]
    assert new["entities"] == [entity]
    detail = client.get(f"{SESSION}/memory-entities/{entity['id']}", headers=OWNER).json()
    assert detail["evidence"][0]["draft_references"][0]["draft_version_id"] == revised["id"]


def _confirm_fixture_entity(client, action, **target):
    candidates = f"{SESSION}/entity-candidates"
    (candidate,) = client.get(candidates, headers=OWNER).json()["items"]
    response = client.post(f"{candidates}/{candidate['id']}/resolutions", headers=OWNER, json={
        "expected_revision": candidate["revision"], "action": action, **target,
    })
    assert response.status_code == 201, response.text
    return response.json()


def _changed_summary(quote):
    return [StructuredElement(
        element_type="event", source_quote=quote, summary="那段时间我跟陈师傅学习。",
        entity_mentions=[EntityMention(
            entity_type="person", mention_text="陈师傅", relation_type="participant",
        )],
    )]


def test_manual_entity_confirmation_survives_paraphrase_and_stays_in_fragment(extraction_api, monkeypatch):
    client, factory = extraction_api
    quote = "那阵子跟陈师傅学习。"
    path, draft = _open_versioned_text(client, factory, f"{quote}\n后来去了北京。")
    confirmed = _confirm_fixture_entity(client, "create_new", canonical_name="陈师傅")
    _revise_last_paragraph(client, path, draft, "后来去了上海。")
    monkeypatch.setattr(TextExtractionModel, "extract_structured", lambda *_a, **_kw: _changed_summary(quote))
    with factory() as db:
        assert MemoryExtractionService().run(db)
    (item,) = client.get(ELEMENTS, headers=OWNER).json()["items"]
    assert item["summary"] == _changed_summary(quote)[0].summary
    assert [entity["id"] for entity in item["entities"]] == [confirmed["entity_id"]]

    other = save_text(client, f"那是我去二厂以后。\n{quote}")
    pipeline = MemoryPipelineService()
    with factory() as db:
        assert pipeline.run(db, "organization")
        assert pipeline.extraction.run(db)
    (unconfirmed,) = client.get(
        ELEMENTS, params={"source_fragment_id": other["id"]}, headers=OWNER
    ).json()["items"]
    assert unconfirmed["entities"] == []
    (candidate,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
    assert candidate["element_id"] == unconfirmed["id"]


def test_rejected_entity_match_survives_paraphrased_summary(extraction_api, monkeypatch):
    client, factory = extraction_api
    _open_versioned_text(client, factory, "我跟陈师傅学手艺。")
    known = _confirm_fixture_entity(client, "create_new", canonical_name="陈师傅")
    quote = "那阵子跟陈师傅学习。"
    path, draft = _open_versioned_text(client, factory, f"{quote}\n后来去了北京。")
    rejection = _confirm_fixture_entity(client, "reject_match", entity_id=known["entity_id"])
    assert rejection["status"] == "pending"
    _revise_last_paragraph(client, path, draft, "后来去了上海。")
    monkeypatch.setattr(TextExtractionModel, "extract_structured", lambda *_a, **_kw: _changed_summary(quote))
    with factory() as db:
        assert MemoryExtractionService().run(db)
    (candidate,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
    assert candidate["candidate_entities"] == []


def test_repeated_entity_quote_keeps_candidate_and_requires_location(extraction_api):
    client, factory = extraction_api
    quote = "我跟陈师傅学手艺。"
    _, draft = _open_versioned_text(
        client, factory, f"{quote}\n{quote}", narrator_spans=[{
            "start_offset": len(quote) + 1, "end_offset": len(quote) * 2 + 1,
            "narrator_user_id": 2,
        }],
    )
    path = f"{SESSION}/entity-candidates"
    (candidate,) = client.get(path, headers=OWNER).json()["items"]
    evidence = candidate["evidence"]
    assert evidence["location_status"] == "ambiguous"
    assert candidate["narrator_user_id"] is None
    assert evidence["draft_references"] == []
    choices = evidence["possible_locations"]
    assert [choice["draft_references"][0]["paragraph_id"] for choice in choices] == [
        item["id"] for item in draft["paragraphs"]
    ]
    resolve = f"{path}/{candidate['id']}/resolutions"
    decision = {
        "expected_revision": candidate["revision"], "action": "create_new", "canonical_name": "陈师傅",
    }
    assert client.post(resolve, headers=OWNER, json=decision).status_code == 422
    assert client.post(resolve, headers=OWNER, json={
        **decision, "evidence_location_index": len(choices),
    }).status_code == 422
    decision["evidence_location_index"] = choices[1]["index"]
    confirmed = client.post(resolve, headers=OWNER, json=decision)
    assert confirmed.status_code == 201, confirmed.text
    assert client.post(resolve, headers=OWNER, json=decision).json() == confirmed.json()
    entity_id = confirmed.json()["entity_id"]
    results = client.get(f"{SESSION}/memory-entities/{entity_id}", headers=OWNER).json()
    (proof,) = results["evidence"]
    assert proof["draft_references"] == choices[1]["draft_references"]
    assert proof["narrator_user_id"] == 2
    assert "possible_locations" not in proof


def _save_versioned_text_file(client, factory, text):
    raw = text.encode()
    now = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.add(MediaAsset(
            id=25, uploaded_by_user_id=1, purpose="source", media_type="text",
            storage_provider="mock", storage_region="local", bucket_name="test",
            object_key="source.txt", content_type="text/plain", expected_size_bytes=len(raw),
            size_bytes=len(raw), content_sha256=sha256(raw).hexdigest(), upload_status="uploaded",
            uploaded_at=now, upload_expires_at=now,
        ))
    (ObjectStorageService("").local_root / "source.txt").write_bytes(raw)
    assert client.patch(SESSION, headers=OWNER, json={"visibility": "private"}).status_code == 200
    saved = client.post(
        FRAGMENTS, headers=OWNER, json={"source_type": "text", "source_asset_id": 25},
    )
    assert saved.status_code == 201, saved.text
    return f"{FRAGMENTS}/{saved.json()['id']}"


def _save_first_manual_draft(client, path):
    progress = client.get(f"{path}/processing", headers=OWNER).json()
    assert progress["source_revision_id"] is not None
    saved = client.post(f"{path}/draft-versions", headers=OWNER, json={
        "source_revision_id": progress["source_revision_id"], "base_version_id": None,
        "paragraphs": [{"text": "人工整理的正文。"}],
    })
    assert saved.status_code == 201, saved.text
    return saved.json()


def test_text_file_can_be_manually_organized_after_model_failure(extraction_api, monkeypatch):
    client, factory = extraction_api
    path = _save_versioned_text_file(client, factory, "我在北京工作。")

    def fail(*_args, **_kwargs):
        raise ExtractionError("ORGANIZATION_OUTPUT_INVALID")

    monkeypatch.setattr("app.services.memory_pipeline.organize_narrative", fail)
    pipeline = MemoryPipelineService()
    with factory() as db:
        assert pipeline.run(db, "organization")
    progress = client.get(f"{path}/processing", headers=OWNER).json()
    assert progress["organization"]["status"] == "failed"
    assert progress["upload_processed_at"] is None
    draft = _save_first_manual_draft(client, path)
    assert draft["origin"] == "owner"
    assert draft["source_revision_id"] == progress["source_revision_id"]
    with factory() as db:
        assert pipeline.extraction.run(db)
    assert client.get(f"{path}/processing", headers=OWNER).json()["extraction"]["status"] == "succeeded"


def test_automatic_organization_cannot_overwrite_concurrent_manual_draft(extraction_api, monkeypatch):
    client, factory = extraction_api
    path = _save_versioned_text_file(client, factory, "我在北京工作。")
    job_id = client.get(f"{path}/processing", headers=OWNER).json()["organization"]["job_id"]
    drafts = []

    def organize(model, source, timeout):
        assert source == "我在北京工作。"
        drafts.append(_save_first_manual_draft(client, path))
        return organize_narrative(model, source, timeout)

    monkeypatch.setattr("app.services.memory_pipeline.organize_narrative", organize)
    with factory() as db:
        assert MemoryPipelineService().run(db, "organization")
        assert db.get(AIProcessingJob, job_id).status == "cancelled"
    (manual,) = drafts
    listing = client.get(f"{path}/draft-versions", headers=OWNER).json()
    assert listing["active_version_id"] == manual["id"]
    assert listing["total"] == 1


def _finish_versioned_source(factory):
    pipeline = MemoryPipelineService()
    with factory() as db:
        assert pipeline.run(db, "organization")
        assert pipeline.extraction.run(db)


def _enable_versioned_session(client):
    reply = client.patch(SESSION, headers=OWNER, json={"visibility": "private"})
    assert reply.status_code == 200, reply.text


def test_app_transcript_persists_text_source_and_actual_narrator(extraction_api):
    client, factory = extraction_api
    _enable_versioned_session(client)
    raw = "我跟陈师傅学手艺。"
    fragment = save_text(client, raw, source_origin="app_asr", narrator_user_id=2)
    _finish_versioned_source(factory)
    path = f"{FRAGMENTS}/{fragment['id']}"
    progress = client.get(f"{path}/processing", headers=OWNER).json()
    assert progress["source_kind"] == "app_asr" and progress["asr"]["status"] == "skipped"
    (candidate,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
    assert candidate["narrator_user_id"] == 2
    evidence = candidate["evidence"]["draft_references"][0]["source_references"][0]
    assert evidence["source_kind"] == "app_asr"
    assert evidence["source_asset_id"] is None and evidence["audio_ranges"] == []
    assert evidence["text_ranges"] == [{"start_offset": 0, "end_offset": len(raw)}]
    with factory() as db:
        source = db.get(MemorySourceRevision, progress["source_revision_id"])
        assert source.source_text == raw
        assert source.created_by == 1 and source.transcript_segments[0]["narrator_user_id"] == 2
        assert db.get(MemoryFragment, fragment["id"]).source_asset_id is None


def test_narrator_span_overrides_default_and_scopes_relative_mentions(extraction_api, monkeypatch):
    client, factory = extraction_api
    _enable_versioned_session(client)
    quote = "前面是我姐姐讲的。接下来她说：我妈在厂里当老师。"
    relative = "我妈"

    def extract(*args, **kwargs):
        return [StructuredElement(
            element_type="fact", source_quote=quote, summary="转述母亲的工作。",
            entity_mentions=[EntityMention(
                entity_type="person", mention_text=relative, relation_type="participant",
            )],
        )]

    monkeypatch.setattr(TextExtractionModel, "extract_structured", extract)
    for speaker in (2, 3):
        save_text(client, quote, narrator_user_id=1, narrator_spans=[{
            "start_offset": quote.index(relative), "end_offset": len(quote),
            "narrator_user_id": speaker,
        }])
        _finish_versioned_source(factory)
        (candidate,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
        assert candidate["narrator_user_id"] == speaker
        assert candidate["candidate_entities"] == []
        if speaker == 2:
            _confirm_fixture_entity(client, "create_new", canonical_name="叙述者二的母亲")


def test_narrator_metadata_requires_valid_character_ranges_and_accounts(extraction_api):
    client, _ = extraction_api
    _enable_versioned_session(client)
    fields = {"source_type": "text", "content_text": "我的回忆。", "source_origin": "app_asr"}
    bad_range = client.post(FRAGMENTS, headers=OWNER, json={
        **fields, "narrator_spans": [{"start_offset": 0, "end_offset": 50, "narrator_user_id": 2}],
    })
    assert bad_range.status_code == 422 and bad_range.json()["detail"]["code"] == "NARRATOR_SPAN_OUT_OF_BOUNDS"
    unknown = client.post(FRAGMENTS, headers=OWNER, json={**fields, "narrator_user_id": 999})
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "NARRATOR_USER_INVALID"
    audio_asset = client.post(FRAGMENTS, headers=OWNER, json={**fields, "source_asset_id": 25})
    assert audio_asset.status_code == 422


def _store_mock_audio(factory):
    raw = b"RIFF" + b"\x00" * 4 + b"WAVE"
    now = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.add(MediaAsset(
            id=25, uploaded_by_user_id=1, purpose="recording", media_type="audio",
            storage_provider="mock", storage_region="local", bucket_name="test",
            object_key="audio.wav", content_type="audio/wav", expected_size_bytes=len(raw),
            size_bytes=len(raw), content_sha256=sha256(raw).hexdigest(), upload_status="uploaded",
            uploaded_at=now, upload_expires_at=now,
        ))
    (ObjectStorageService("").local_root / "audio.wav").write_bytes(raw)


@pytest.mark.parametrize("timestamps", [True, False])
def test_uploaded_audio_uses_backend_asr_with_optional_intervals(extraction_api, monkeypatch, timestamps):
    client, factory = extraction_api
    _enable_versioned_session(client)
    _store_mock_audio(factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_asr_enabled", True)
    monkeypatch.setattr(settings, "ai_asr_provider", "mock")
    monkeypatch.setattr(settings, "ai_asr_timestamps_enabled", timestamps)
    fields = {"source_type": "audio", "source_asset_id": 25, "narrator_user_id": 2}
    assert client.post(
        FRAGMENTS, headers=OWNER, json={**fields, "transcript_raw": "App 转写"},
    ).status_code == 422
    saved = client.post(FRAGMENTS, headers=OWNER, json=fields)
    assert saved.status_code == 201, saved.text
    path = f"{FRAGMENTS}/{saved.json()['id']}"
    assert client.put(
        f"{path}/transcript-raw", headers=OWNER, json={"transcript_raw": "App 转写"},
    ).status_code == 409

    with factory() as db:
        assert MemoryPipelineService().run(db, "asr")
    _finish_versioned_source(factory)
    progress = client.get(f"{path}/processing", headers=OWNER).json()
    assert progress["source_kind"] == "server_asr"
    assert progress["asr"]["status"] == "succeeded"
    (candidate,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
    assert candidate["narrator_user_id"] == 2
    evidence = candidate["evidence"]["draft_references"][0]["source_references"][0]
    assert evidence["source_asset_id"] == 25 and evidence["text_ranges"]
    assert evidence["audio_ranges"] == ([{"start_ms": 0, "end_ms": 2000}] if timestamps else [])

    transcript = client.get(path, headers=OWNER).json()["transcript_raw"]
    corrected = client.put(f"{path}/transcript-raw", headers=OWNER, json={
        "transcript_raw": transcript, "narrator_user_id": 3,
    })
    assert corrected.status_code == 200, corrected.text
    _finish_versioned_source(factory)
    (updated,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
    assert updated["narrator_user_id"] == 3
    proof = updated["evidence"]["draft_references"][0]["source_references"][0]
    assert proof["audio_ranges"] == evidence["audio_ranges"]
    assert proof["source_kind"] == "server_asr"


def test_audio_narrator_survives_blocked_asr_and_manual_submit(extraction_api, monkeypatch):
    client, factory = extraction_api
    _enable_versioned_session(client)
    _store_mock_audio(factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_asr_enabled", False)
    monkeypatch.setattr(settings, "ai_asr_provider", "mock")
    saved = client.post(FRAGMENTS, headers=OWNER, json={
        "source_type": "audio", "source_asset_id": 25, "narrator_user_id": 2,
    })
    assert saved.status_code == 201, saved.text
    path = f"{FRAGMENTS}/{saved.json()['id']}"
    status = client.get(f"{path}/processing", headers=OWNER).json()["asr"]
    assert status["status"] == "blocked" and status["reason_code"] == "PROCESSING_UNAVAILABLE"
    monkeypatch.setattr(settings, "ai_asr_enabled", True)
    submit = client.post(f"{path}/processing-jobs", headers=OWNER, json={"stage": "asr"})
    assert submit.status_code == 202, submit.text
    with factory() as db:
        assert MemoryPipelineService().run(db, "asr")
    _finish_versioned_source(factory)
    (candidate,) = client.get(f"{SESSION}/entity-candidates", headers=OWNER).json()["items"]
    assert candidate["narrator_user_id"] == 2


def test_compatible_asr_requests_timestamps_and_aligns_provider_segments(monkeypatch):
    settings = get_settings().model_copy(update={
        "ai_asr_enabled": True, "ai_asr_provider": "openai-compatible",
        "ai_asr_base_url": "https://asr.test/v1", "ai_asr_model": "asr-test",
        "ai_asr_api_key": SecretStr("fake-test-key"), "ai_asr_timestamps_enabled": True,
    })
    requests = []
    raw = {
        "text": "前一句。\n后一句。",
        "segments": [
            {"text": "前一句。", "start": 0.1, "end": 1.25},
            {"text": "后一句。", "start": 1.5, "end": 3},
            {"text": "无法匹配的分段", "start": 3.5, "end": 4},
        ],
    }

    class FakeOpener:
        def open(self, request, timeout):
            requests.append((request.full_url, request.data))
            return BytesIO(json.dumps(raw).encode())

    monkeypatch.setattr("app.services.audio_transcription.build_opener", lambda *_: FakeOpener())
    result = AudioTranscriptionModel(settings).transcribe(b"audio", "audio/wav", 1)
    assert result.text == raw["text"]
    assert [item.model_dump() for item in result.segments] == [
        {"start_offset": 0, "end_offset": 4, "start_ms": 100, "end_ms": 1250},
        {"start_offset": 5, "end_offset": 9, "start_ms": 1500, "end_ms": 3000},
    ]
    [(url, request)] = requests
    assert url == "https://asr.test/v1/audio/transcriptions"
    assert b'name="response_format"\r\n\r\nverbose_json' in request
    assert b'name="timestamp_granularities[]"\r\n\r\nsegment' in request
    raw.pop("segments")
    assert AudioTranscriptionModel(settings).transcribe(b"audio", "audio/wav", 1).segments == ()


def run_due_attempt(factory, job_id):
    # 用已到期的任务验证 Worker 流程，无需等待真实退避时间。
    with factory.begin() as db:
        db.get(AIProcessingJob, job_id).available_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
    with factory() as db:
        assert MemoryExtractionService().run(db)


def test_mock_scenario_settings_reject_unknown_values(monkeypatch):
    monkeypatch.delenv("AI_TEXT_MOCK_SCENARIO", raising=False)
    assert Settings(_env_file=None).ai_text_mock_scenario == "success"
    monkeypatch.setenv("AI_TEXT_MOCK_SCENARIO", "empty")
    assert Settings(_env_file=None).ai_text_mock_scenario == "empty"
    monkeypatch.setenv("AI_TEXT_MOCK_SCENARIO", "wrong")
    with pytest.raises(ValidationError, match="ai_text_mock_scenario"):
        Settings(_env_file=None)


def test_mock_success_returns_at_most_three_verifiable_quotes_offline(extraction_api, monkeypatch):
    client, _ = extraction_api
    monkeypatch.setattr(get_settings(), "ai_text_api_key", SecretStr(""))

    def unexpected_network(*_):
        pytest.fail("Mock 抽取无需连接外部服务")

    monkeypatch.setattr("app.services.text_extraction_model.build_opener", unexpected_network)
    first = "第一句。"
    second = "第二句🙂！？"
    long_sentence = "第三句" + "长" * 130 + "。"
    source = f"  {first}{first}\n{second}\n{long_sentence}第四句。"
    fragment = save_text(client, source)
    assert fragment["extraction_job"]["status"] == "succeeded"
    assert fragment["extraction_job"]["element_count"] == 3
    rows = client.get(ELEMENTS, headers=OWNER, params={"source_fragment_id": fragment["id"]})
    assert rows.status_code == 200
    items = rows.json()["items"]
    assert [item["summary"] for item in items] == [first, second, long_sentence[:120]]
    for item in items:
        assert (item["origin"], item["review_status"], item["element_type"]) == (
            "extracted",
            "pending",
            "other",
        )
        assert item["overall_confidence"] is None and item["location_description"] is None
    evidence = [
        client.get(f"{ELEMENTS}/{item['id']}/provenance", headers=OWNER).json() for item in items
    ]
    assert evidence[0]["source_quote"] == first
    assert evidence[0]["start_offset"] is None and evidence[0]["end_offset"] is None
    for proof in evidence[1:]:
        assert source[proof["start_offset"] : proof["end_offset"]] == proof["source_quote"]


def test_mock_retry_once_keeps_original_scenario_after_configuration_switch(
    extraction_api, monkeypatch
):
    client, factory = extraction_api
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_text_mock_scenario", "retry_once")
    fragment = save_text(client)
    queued = fragment["extraction_job"]
    assert (queued["status"], queued["attempt_count"], queued["error_code"]) == (
        "queued",
        1,
        "MODEL_TEMPORARY_ERROR",
    )
    assert queued["next_retry_at"] is not None and queued["fallback_element_id"] is None

    monkeypatch.setattr(settings, "ai_text_mock_scenario", "empty")
    empty = save_text(client, "另一段口述。")
    empty_job = empty["extraction_job"]
    assert (empty_job["status"], empty_job["element_count"], empty_job["fallback_element_id"]) == (
        "succeeded",
        0,
        None,
    )
    assert (
        client.get(ELEMENTS, headers=OWNER, params={"source_fragment_id": empty["id"]}).json()[
            "items"
        ]
        == []
    )

    run_due_attempt(factory, queued["id"])
    finished = client.get(f"{SESSION}/extraction-jobs/{queued['id']}", headers=OWNER).json()
    assert (
        finished["id"],
        finished["status"],
        finished["attempt_count"],
        finished["element_count"],
    ) == (
        queued["id"],
        "succeeded",
        2,
        1,
    )
    assert finished["error_code"] is None and finished["next_retry_at"] is None
    with factory() as db:
        old_job = db.get(AIProcessingJob, queued["id"])
        assert old_job.input_payload["mock_scenario"] == "retry_once"
        assert old_job.input_payload["model_provider"] == "mock"
        assert db.get(AIProcessingJob, empty_job["id"]).input_payload["mock_scenario"] == "empty"
        assert len(old_job.result_payload["failures"]) == 1


def test_legacy_mock_job_without_saved_scenario_uses_success(extraction_api, monkeypatch):
    client, factory = extraction_api
    monkeypatch.setattr(get_settings(), "ai_text_mock_scenario", "empty")
    with monkeypatch.context() as no_sync:
        no_sync.setattr(MemoryExtractionService, "run", lambda *_args, **_kwargs: False)
        job_id = save_text(client)["extraction_job"]["id"]
    with factory.begin() as db:
        job = db.get(AIProcessingJob, job_id)
        job.input_payload = {
            field: value
            for field, value in job.input_payload.items()
            if field not in {"model_provider", "mock_scenario"}
        }
    run_due_attempt(factory, job_id)
    result = client.get(f"{SESSION}/extraction-jobs/{job_id}", headers=OWNER).json()
    assert (result["status"], result["element_count"]) == ("succeeded", 1)


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
    rows = client.get(
        ELEMENTS, headers=VIEWER, params={"processing_job_id": job["id"], "origin": "extracted"}
    )
    (item,) = rows.json()["items"]
    url = f"{ELEMENTS}/{item['id']}"
    assert client.get(url, headers=OWNER).json() == item
    assert client.get(url.replace("/11/", "/12/"), headers=OWNER).status_code == 404
    evidence = client.get(f"{url}/provenance", headers=VIEWER).json()
    assert evidence["source_kind"] == "content_text"
    assert (
        source[evidence["start_offset"] : evidence["end_offset"]]
        == evidence["source_quote"]
        == source.strip()
    )
    with factory() as db:
        assert source not in json.dumps(
            db.get(AIProcessingJob, job["id"]).input_payload, ensure_ascii=False
        )
    assert client.delete(f"{FRAGMENTS}/{fragment['id']}", headers=OWNER).status_code == 200
    assert client.get(f"{url}/provenance", headers=VIEWER).status_code == 404


def test_fragment_save_idempotency_key_survives_retries_and_deletion(extraction_api):
    client, factory = extraction_api
    headers = {**OWNER, "Idempotency-Key": str(uuid4())}
    payload = {"source_type": "text", "content_text": "我在上海读书。"}
    first = client.post(FRAGMENTS, headers=headers, json=payload)
    second = client.post(FRAGMENTS, headers=headers, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["extraction_job"] == second.json()["extraction_job"]
    assert (
        client.post(
            FRAGMENTS, headers=headers, json={**payload, "content_text": "另一条记忆"}
        ).status_code
        == 409
    )
    with factory() as db:
        assert len(db.scalars(select(MemoryFragment)).all()) == 1
        assert len(db.scalars(select(AIProcessingJob)).all()) == 1
        assert len(db.scalars(select(MemoryElement)).all()) == 1

    fragment_url = f"{FRAGMENTS}/{first.json()['id']}"
    assert client.delete(fragment_url, headers=OWNER).status_code == 200
    assert client.post(FRAGMENTS, headers=headers, json=payload).status_code == 409
    assert (
        client.post(FRAGMENTS.replace("/11/", "/12/"), headers=headers, json=payload).status_code
        == 201
    )


def test_late_raw_transcript_starts_extraction_once_and_preserves_evidence(extraction_api):
    client, factory = extraction_api
    now = datetime.now(timezone.utc)
    with factory.begin() as db:
        db.add(
            MediaAsset(
                id=25,
                uploaded_by_user_id=1,
                purpose="recording",
                media_type="audio",
                storage_provider="mock",
                storage_region="local",
                bucket_name="test",
                object_key="audio.wav",
                content_type="audio/wav",
                expected_size_bytes=1,
                size_bytes=1,
                content_sha256="a" * 64,
                upload_status="uploaded",
                uploaded_at=now,
                upload_expires_at=now,
            )
        )
    created = client.post(FRAGMENTS, headers=OWNER, json={"audio_asset_id": 25})
    assert (
        created.status_code == 201
        and created.json()["extraction_skip_reason"] == "SOURCE_TEXT_REQUIRED"
    )
    url = f"{FRAGMENTS}/{created.json()['id']}"
    assert client.patch(url, headers=OWNER, json={"transcript_edited": "校订稿"}).status_code == 200
    raw_url = f"{url}/transcript-raw"
    payload = {"transcript_raw": "  我的录音原文。\n"}
    assert client.put(raw_url, headers=VIEWER, json=payload).status_code == 403
    updated = client.put(raw_url, headers=OWNER, json=payload)
    assert updated.status_code == 200, updated.text
    result = updated.json()
    assert (
        result["transcript_edited"] == "校订稿"
        and result["transcript_raw"] == payload["transcript_raw"]
    )
    assert result["extraction_job"]["status"] == "succeeded"
    repeat = client.put(raw_url, headers=OWNER, json=payload)
    assert repeat.json()["extraction_job"] == result["extraction_job"]
    assert (
        client.put(raw_url, headers=OWNER, json={"transcript_raw": "不同原文"}).status_code == 409
    )
    (element,) = client.get(ELEMENTS, headers=OWNER).json()["items"]
    proof = client.get(f"{ELEMENTS}/{element['id']}/provenance", headers=OWNER).json()
    assert (proof["source_kind"], proof["source_quote"], proof["start_offset"]) == (
        "transcript_raw",
        "我的录音原文。",
        2,
    )


def test_session_latest_recorded_at_tracks_live_fragments_after_backfill_and_delete(extraction_api):
    client, _ = extraction_api
    latest = save_text(client, "最新一段", recorded_at="2020-02-02T10:00:00Z")
    older = save_text(client, "补录旧内容", recorded_at="2020-01-01T10:00:00Z")

    def summary():
        reply = client.get(SESSION, headers=OWNER).json()
        return reply["last_recorded_at"], reply["transcript_excerpt"]

    recorded_at, excerpt = summary()
    assert recorded_at.startswith("2020-02-02") and excerpt == "最新一段"
    assert client.delete(f"{FRAGMENTS}/{latest['id']}", headers=OWNER).status_code == 200
    recorded_at, excerpt = summary()
    assert recorded_at.startswith("2020-01-01") and excerpt == "补录旧内容"
    assert client.delete(f"{FRAGMENTS}/{older['id']}", headers=OWNER).status_code == 200
    assert summary() == (None, None)


def test_retry_budget_fallback_and_manual_replacement(extraction_api, monkeypatch):
    client, factory = extraction_api
    monkeypatch.setattr(get_settings(), "ai_text_mock_scenario", "fallback")
    source = "  " + "上海往事。" * 50 + "  "
    fragment = save_text(client, source)
    job = fragment["extraction_job"]
    assert job["status"] == "queued" and job["error_code"] == "MODEL_TEMPORARY_ERROR"
    assert job["next_retry_at"] is not None
    assert client.get(ELEMENTS, headers=OWNER).json()["total"] == 0
    job_url = f"{SESSION}/extraction-jobs/{job['id']}"
    for attempts in (2, 3):
        run_due_attempt(factory, job["id"])
        status = client.get(job_url, headers=OWNER).json()
        assert status["attempt_count"] == attempts
        if attempts < 3:
            assert status["status"] == "queued" and status["fallback_element_id"] is None
    assert status["status"] == "failed" and status["element_count"] is None
    with factory() as db:
        assert len(db.get(AIProcessingJob, job["id"]).result_payload["failures"]) == 3
    fallback_id = status["fallback_element_id"]
    fallback = client.get(f"{ELEMENTS}/{fallback_id}", headers=VIEWER).json()
    assert fallback["origin"] == "fallback" and fallback["overall_confidence"] is None
    quote = client.get(f"{ELEMENTS}/{fallback_id}/provenance", headers=VIEWER).json()
    assert quote["source_text"] == source and quote["end_offset"] == len(source)

    jobs = f"{FRAGMENTS}/{fragment['id']}/extraction-jobs"
    retry = {"retry_of_job_id": job["id"]}
    result = client.post(jobs, headers=OWNER, json=retry)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded" and result.json()["retry_of_job_id"] == job["id"]
    assert result.json()["id"] != job["id"] and result.json()["attempt_count"] == 1
    assert client.post(jobs, headers=OWNER, json=retry).json() == result.json()
    assert client.get(f"{ELEMENTS}/{fallback_id}", headers=OWNER).status_code == 404
    old_job = client.get(job_url, headers=OWNER).json()
    assert old_job["status"] == "failed" and old_job["fallback_element_id"] is None
    with factory() as db:
        assert (
            db.get(AIProcessingJob, result.json()["id"]).input_payload["mock_scenario"] == "success"
        )


def test_configuration_and_external_opt_in(extraction_api, monkeypatch):
    client, factory = extraction_api
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_text_enabled", False)
    fragment = save_text(client)
    assert (
        fragment["extraction_job"] is None
        and fragment["extraction_skip_reason"] == "EXTRACTION_UNAVAILABLE"
    )
    jobs = f"{FRAGMENTS}/{fragment['id']}/extraction-jobs"
    assert client.post(jobs, headers=OWNER, json={}).status_code == 503
    monkeypatch.setattr(settings, "ai_text_enabled", True)
    monkeypatch.setattr(settings, "ai_text_provider", "openai-compatible")
    monkeypatch.setattr(settings, "ai_text_base_url", "https://models.test/v1")
    monkeypatch.setattr(settings, "ai_text_model", "test-model")
    monkeypatch.setattr(settings, "ai_text_api_key", SecretStr("fake-test-key"))
    monkeypatch.setattr(settings, "ai_text_mock_scenario", "fallback")
    seen = []

    class ExternalModelResponse:
        def open(self, request, timeout):
            seen.append((request.full_url, json.loads(request.data)))
            return BytesIO(b'{"choices":[{"message":{"content":"{\\"elements\\":[]}"}}]}')

    monkeypatch.setattr(
        "app.services.text_extraction_model.build_opener", lambda *_: ExternalModelResponse()
    )
    assert client.post(jobs, headers=OWNER, json={}).status_code == 403
    skipped = save_text(client)
    assert skipped["extraction_skip_reason"] == "SOURCE_AI_AUTHORIZATION_REQUIRED"
    assert seen == []
    submitted = client.post(jobs, headers=OWNER, json={"allow_external_processing": True})
    assert submitted.status_code == 200, submitted.text
    assert (submitted.json()["status"], submitted.json()["element_count"]) == ("succeeded", 0)
    [(url, payload)] = seen
    assert url == "https://models.test/v1/chat/completions" and payload["model"] == "test-model"
    assert json.loads(payload["messages"][1]["content"])["source_text"] == fragment["content_text"]
    with factory() as db:
        assert "mock_scenario" not in db.get(AIProcessingJob, submitted.json()["id"]).input_payload


def test_unverifiable_model_evidence_uses_original_text_fallback(extraction_api, monkeypatch):
    client, _ = extraction_api

    def fabricated_quote(*_args, **_kwargs):
        return [
            ExtractedElement(
                element_type="event", summary="编造的事件", source_quote="我在天津上学。"
            )
        ]

    monkeypatch.setattr(TextExtractionModel, "extract", fabricated_quote)
    source = "我在上海上学。"
    job = save_text(client, source)["extraction_job"]
    assert (job["status"], job["attempt_count"], job["error_code"]) == (
        "failed",
        1,
        "MODEL_EVIDENCE_INVALID",
    )
    (fallback,) = client.get(ELEMENTS, headers=OWNER).json()["items"]
    assert fallback["origin"] == "fallback" and fallback["summary"] == source
    quote = client.get(f"{ELEMENTS}/{fallback['id']}/provenance", headers=OWNER).json()
    assert quote["source_quote"] is None and quote["source_text"] == source


def test_text_file_is_loaded_using_verified_digest(extraction_api):
    client, factory = extraction_api
    raw = "我在北京工作。".encode()
    with factory.begin() as db:
        db.add(
            MediaAsset(
                id=25,
                uploaded_by_user_id=1,
                purpose="source",
                media_type="text",
                storage_provider="mock",
                storage_region="local",
                bucket_name="test",
                object_key="source.txt",
                content_type="text/plain",
                expected_size_bytes=len(raw),
                size_bytes=len(raw),
                content_sha256=sha256(raw).hexdigest(),
                upload_status="uploaded",
                uploaded_at=datetime.now(timezone.utc),
                upload_expires_at=datetime.now(timezone.utc),
            )
        )
    storage = ObjectStorageService("")
    (storage.local_root / "source.txt").write_bytes(raw)
    response = client.post(
        FRAGMENTS, headers=OWNER, json={"source_type": "text", "source_asset_id": 25}
    )
    assert response.status_code == 201, response.text
    assert response.json()["extraction_job"]["status"] == "succeeded"
    (item,) = client.get(ELEMENTS, headers=OWNER).json()["items"]
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
        db.get(AIProcessingJob, job_id).lease_expires_at = datetime.now(timezone.utc) - timedelta(
            seconds=1
        )
    with factory() as db:
        assert run(service, db)
        service._complete(db, first, None, [], ExtractionError("STALE"))
    with factory() as db:
        job = db.get(AIProcessingJob, job_id)
        assert job.status == "succeeded" and job.attempt_count == 2
        (failure,) = job.result_payload["failures"]
        assert failure["attempt"] == 1 and failure["code"] == "WORKER_LEASE_EXPIRED"
        assert len(db.scalars(select(MemoryElement)).all()) == 1
