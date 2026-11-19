"""Verify memory asset state transitions with autoflush disabled, as in production."""

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Integer, create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.db.database import get_db
from app.main import create_app
from app.models import (
    FragmentAttachment, InterestDomain, MediaAsset, MemoryContributor, MemoryFragment, MemorySession,
    Topic, TopicInterest, TopicParticipant, User, UserInterest,
)
from app.schemas.memory import FragmentCreate, SessionCreate, UploadCompleteRequest
from app.services.object_storage_service import ObjectStorageService, get_object_storage_service
from app.services.memory_service import MemoryService
from app.services.topic_service import TopicService


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'recording.db'}")

    @event.listens_for(engine, "connect")
    def add_btrim(connection, _):
        connection.create_function("btrim", 1, str.strip)

    for model in (MemorySession, MemoryContributor, MemoryFragment):
        column = model.__table__.c.id
        monkeypatch.setattr(column, "type", column.type.with_variant(Integer(), "sqlite"))
    tables = (
        MemorySession, MediaAsset, MemoryContributor, MemoryFragment, FragmentAttachment,
        Topic, TopicParticipant, TopicInterest, InterestDomain, UserInterest,
    )
    MemorySession.metadata.create_all(engine, tables=[model.__table__ for model in tables])
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def make_asset(asset_id, purpose, media_type):
    now = datetime.now(timezone.utc)
    return MediaAsset(
        id=asset_id, uploaded_by_user_id=1, purpose=purpose, media_type=media_type,
        storage_provider="mock", storage_region="local", bucket_name="test",
        object_key=f"object-{asset_id}", content_type=f"{media_type}/test", expected_size_bytes=1,
        upload_expires_at=now + timedelta(hours=1),
    )


def make_topic(topic_id, count=0):
    return Topic(
        id=topic_id, code=f"topic-{topic_id}", title=f"Topic {topic_id}", icon_key="book",
        summary="Summary", prompt="Prompt", category="life", is_general=True,
        participant_count=count,
    )


def test_audio_fragment_response_exposes_only_source_asset(sessions):
    asset = make_asset(25, "recording", "audio")
    asset.upload_status = "attached"
    asset.size_bytes = 1
    asset.content_sha256 = "a" * 64
    asset.uploaded_at = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add(MemorySession(id=11, owner_user_id=1))
        session.add(asset)
        session.add(MemoryFragment(
            id=10, session_id=11, sequence_no=1, contributor_id=6,
            source_type="audio", source_asset_id=25,
        ))

    def override_db():
        with sessions() as session:
            yield session

    issued_urls = []

    def create_read_url(key, _ttl):
        issued_urls.append(key)
        return "http://storage.localtest.me/audio"

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: User(id=1)
    app.dependency_overrides[get_object_storage_service] = lambda: SimpleNamespace(create_read_url=create_read_url)
    with TestClient(app) as client:
        page = client.get("/api/recording-sessions/11/fragments")

    assert page.status_code == 200, page.text
    item, = page.json()["items"]
    assert item["source_type"] == "audio"
    assert item["source_asset"]["id"] == 25
    assert item["source_asset"]["download_url"] == "http://storage.localtest.me/audio"
    assert "audio" not in item
    assert issued_urls == [asset.object_key]


def test_topic_counts_only_first_saved_fragment_per_user_across_sessions(sessions):
    with sessions.begin() as session:
        session.add_all([make_topic(1, count=5), make_topic(2, count=6), make_topic(3)])
    service = MemoryService()
    alice = User(id=1, username="alice")
    bob = User(id=2, username="bob")
    fragment = FragmentCreate(source_type="text", content_text="A memory")

    with sessions() as session:
        first = service.create_session(session, alice, SessionCreate(topic_id=1)).id
        second = service.create_session(session, alice, SessionCreate(topic_id=1)).id
        third = service.create_session(session, bob, SessionCreate(topic_id=1)).id
        free = service.create_session(session, bob, SessionCreate()).id
        disabled = service.create_session(session, bob, SessionCreate(topic_id=3)).id
    with sessions.begin() as session:
        assert session.get(Topic, 1).participant_count == 5
        assert session.scalars(select(TopicParticipant)).all() == []
        session.get(Topic, 3).is_enabled = False

    for owner, session_id in ((alice, first), (alice, first), (alice, second),
                              (bob, third), (bob, free), (bob, disabled)):
        with sessions() as session:
            service.create_fragment(session, owner, session_id, fragment)

    with sessions() as session:
        participants = session.scalars(select(TopicParticipant)).all()
        assert {(row.topic_id, row.user_id) for row in participants} == {(1, 1), (1, 2)}
        assert session.get(Topic, 3).participant_count == 0
        page = TopicService().get_recommended_topics(session, alice, page=1, page_size=2)
        assert [(item.id, item.participant_count) for item in page.items] == [(1, 7), (2, 6)]


def test_topic_participation_rolls_back_with_fragment(sessions, monkeypatch):
    with sessions.begin() as session:
        session.add(make_topic(1))
        session.add(MemorySession(id=1, owner_user_id=1, topic_id=1))

    record_participation = TopicService.record_participation

    def fail_after_recording(session, topic_id, user_id):
        record_participation(session, topic_id, user_id)
        raise RuntimeError("simulate transaction failure")

    monkeypatch.setattr(TopicService, "record_participation", staticmethod(fail_after_recording))
    with sessions() as session, pytest.raises(RuntimeError, match="simulate transaction failure"):
        MemoryService().create_fragment(
            session, User(id=1, username="alice"), 1,
            FragmentCreate(source_type="text", content_text="Unsaved memory"),
        )
    with sessions() as session:
        assert session.get(Topic, 1).participant_count == 0
        assert session.scalars(select(TopicParticipant)).all() == []
        assert session.scalars(select(MemoryFragment)).all() == []


def test_upload_confirmation_accepts_utf8_split_at_signature_boundary(sessions, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_mode", "mock")
    monkeypatch.setattr(settings, "object_storage_mock_directory", str(tmp_path / "media"))
    storage = ObjectStorageService("http://localhost")
    content = b"a" * 31 + "中文回忆".encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    asset = make_asset(1, "source", "text")
    asset.content_type = "text/plain"
    asset.expected_size_bytes = len(content)
    asset.expected_sha256 = digest
    file_path = storage.local_root / asset.object_key
    file_path.write_bytes(content)
    file_path.with_suffix(".meta.json").write_text('{"content_type":"text/plain"}', encoding="utf-8")

    with sessions.begin() as session:
        session.add(asset)
    with sessions() as session:
        completed = MemoryService().complete_upload(session, User(id=1), 1, UploadCompleteRequest(), storage)
        assert (completed.upload_status, completed.size_bytes, completed.content_sha256) == (
            "uploaded", len(content), digest,
        )


@pytest.mark.parametrize("remove", ["attachment", "fragment", "session"])
def test_asset_status_after_removing_one_then_last_reference(sessions, remove):
    is_attachment = remove == "attachment"
    asset = make_asset(1, "attachment" if is_attachment else "recording",
                      "image" if is_attachment else "audio")
    asset.upload_status = "attached"
    asset.size_bytes = 1
    asset.content_sha256 = "a" * 64
    asset.uploaded_at = datetime.now(timezone.utc)
    with sessions.begin() as session:
        session.add(asset)
        for record_id in (1, 2):
            session.add(MemorySession(id=record_id, owner_user_id=1))
            session.add(MemoryFragment(
                id=record_id, session_id=record_id, sequence_no=1, contributor_id=record_id,
                source_type="text" if is_attachment else "audio",
                content_text="memory" if is_attachment else None,
                source_asset_id=None if is_attachment else 1,
            ))
            if is_attachment:
                session.add(FragmentAttachment(
                    id=record_id, fragment_id=record_id, media_asset_id=1, contributor_id=record_id,
                ))

    service = MemoryService()
    user = User(id=1)
    for record_id, expected in ((1, "attached"), (2, "uploaded")):
        with sessions() as session:
            if remove == "attachment":
                service.delete_attachment(session, user, record_id, record_id)
            elif remove == "fragment":
                service.delete_fragment(session, user, record_id, record_id)
            else:
                service.delete_session(session, user, record_id)
        with sessions() as session:
            assert session.get(MediaAsset, 1).upload_status == expected


@pytest.mark.parametrize("intervening_status", ["uploaded", "attached", "expired"])
def test_upload_confirmation_rechecks_state_after_hashing(sessions, intervening_status):
    with sessions.begin() as session:
        session.add(make_asset(1, "recording", "audio"))

    def inspect_and_hash(key, expected_size, validate_utf8):
        assert (key, expected_size, validate_utf8) == ("object-1", 1, False)
        # Simulate another transaction completing, linking, or expiring the object during I/O.
        with sessions.begin() as competing_session:
            asset = competing_session.get(MediaAsset, 1)
            asset.upload_status = intervening_status
            if intervening_status != "expired":
                asset.size_bytes = 1
                asset.content_sha256 = "a" * 64
                asset.duration_ms = 50
                asset.sample_rate_hz = 8000
                asset.uploaded_at = datetime.now(timezone.utc)
        return 1, "audio/test", "a" * 64, b"ID3"

    storage = SimpleNamespace(inspect_and_hash=inspect_and_hash)
    service = MemoryService()
    body = UploadCompleteRequest(duration_ms=100, sample_rate_hz=16000)
    with sessions() as session:
        if intervening_status == "expired":
            with pytest.raises(HTTPException) as error:
                service.complete_upload(session, User(id=1), 1, body, storage)
            assert error.value.status_code == 409
        else:
            assert service.complete_upload(session, User(id=1), 1, body, storage).id == 1
    with sessions() as session:
        asset = session.get(MediaAsset, 1)
        assert asset.upload_status == intervening_status
        assert asset.duration_ms == (None if intervening_status == "expired" else 50)
        assert asset.sample_rate_hz == (None if intervening_status == "expired" else 8000)


def test_upload_confirmation_reuses_verified_duplicate(sessions):
    with sessions.begin() as session:
        session.add_all([make_asset(1, "recording", "audio"), make_asset(2, "recording", "audio")])
    storage = SimpleNamespace(inspect_and_hash=lambda *args, **kwargs: (1, "audio/test", "a" * 64, b"ID3"))
    service = MemoryService()
    with sessions() as session:
        first = service.complete_upload(session, User(id=1), 1, UploadCompleteRequest(duration_ms=50), storage)
        duplicate = service.complete_upload(session, User(id=1), 2, UploadCompleteRequest(duration_ms=100), storage)
        assert (first.id, first.upload_status, duplicate.id, duplicate.duration_ms) == (1, "uploaded", 1, 50)
    with sessions() as session:
        assert session.get(MediaAsset, 2).upload_status == "failed"
