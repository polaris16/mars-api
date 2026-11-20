"""记忆元素抽取、任务租约及原文证据。"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import BoundedSemaphore
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import MockExtractionScenario, Settings, get_settings
from app.models import (
    AIProcessingJob,
    MediaAsset,
    MemoryContributor,
    MemoryDraftVersion,
    MemoryElement,
    MemoryFragment,
    MemorySession,
    MemorySourceRevision,
    User,
)
from app.schemas.memory_extraction import (
    ExtractedElement,
    ExtractionJobPage,
    ExtractionJobResponse,
    ExtractionRequest,
    MemoryElementPage,
    MemoryElementResponse,
    MemoryProvenanceResponse,
    SourceKind,
    StructuredElement,
)
from app.schemas.memory_processing import TextRange
from app.services.draft_provenance import ExtractionDraft
from app.services.memory_entities import MemoryEntityService
from app.services.memory_service import MemoryService
from app.services.memory_validation_queue import lock_owner, schedule_material_validation
from app.services.memory_validation_rules import invalidated_verification_ids
from app.services.memory_versions import active_draft, current_source, decode_paragraphs, paragraph_reference
from app.services.object_storage_service import ObjectStorageService
from app.services.text_extraction_model import ExtractionError, TextExtractionModel

JOB_TYPE = "extract_memory_elements"
EXTRACTOR_VERSION = "v1"
ACTIVE_STATUSES = ("queued", "processing")
MAX_ENTITY_QUOTE_LOCATIONS = 30
_io_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="memory-extraction")
_io_slots = BoundedSemaphore(8)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _problem(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _record_failure(result: dict, attempt: int, at: datetime, code: str) -> None:
    failures = list(result.get("failures", []))
    failures.append({"attempt": attempt, "at": at.isoformat(), "code": code})
    result["failures"] = failures


def _bounded_io(call: Callable, deadline: float, timeout_code: str):
    """Bound the caller's wait. A timed-out I/O task has no access to the database."""
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ExtractionError(timeout_code, retryable=True)
    if not _io_slots.acquire(blocking=False):
        raise ExtractionError("EXTRACTION_BUSY", retryable=True)
    try:
        future = _io_pool.submit(call, remaining)
    except RuntimeError:
        _io_slots.release()
        raise ExtractionError("EXTRACTION_BUSY", retryable=True) from None
    # 请求可能先超时返回。模型调用实际结束后才回收并发名额。
    future.add_done_callback(lambda _: _io_slots.release())
    try:
        return future.result(timeout=max(0, deadline - monotonic()))
    except TimeoutError:
        future.cancel()
        raise ExtractionError(timeout_code, retryable=True) from None


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    fingerprint: str
    text: str | None = None
    asset_id: int | None = None
    key: str | None = None
    size_bytes: int | None = None
    draft_version_id: int | None = None


@dataclass(frozen=True)
class Claim:
    job_id: int
    token: str
    exhausted: bool


class MemoryExtractionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = TextExtractionModel(self.settings)
        self.memories = MemoryService()

    def _model_scope(self) -> str:
        return _digest(
            "|".join(
                (
                    self.settings.ai_text_provider,
                    self.settings.ai_text_base_url.rstrip("/"),
                    self.settings.ai_text_model,
                )
            )
        )

    def _mock_scenario(self, parent: AIProcessingJob | None) -> MockExtractionScenario:
        scenario = self.settings.ai_text_mock_scenario
        # 真实、取消或未生成兜底的旧任务仍按当前场景处理。
        if (
            scenario == "fallback"
            and parent is not None
            and parent.status == "failed"
            and parent.input_payload.get("model_provider") == "mock"
            and parent.input_payload.get("mock_scenario") == "fallback"
            and (parent.result_payload or {}).get("fallback_element_id") is not None
        ):
            return "success"
        return scenario

    @staticmethod
    def _source(db: Session, fragment: MemoryFragment) -> Source:
        if fragment.source_type == "text":
            if fragment.content_text and fragment.content_text.strip():
                return Source(
                    "content_text", _digest(fragment.content_text), text=fragment.content_text
                )
            asset = (
                db.get(MediaAsset, fragment.source_asset_id)
                if fragment.source_asset_id is not None
                else None
            )
            if (
                asset is not None
                and not asset.is_del
                and asset.media_type == "text"
                and asset.upload_status in {"uploaded", "attached"}
                and asset.content_sha256
                and asset.size_bytes
            ):
                return Source(
                    "text_file",
                    asset.content_sha256,
                    asset_id=asset.id,
                    key=asset.object_key,
                    size_bytes=asset.size_bytes,
                )
        elif fragment.transcript_raw and fragment.transcript_raw.strip():
            return Source(
                "transcript_raw", _digest(fragment.transcript_raw), text=fragment.transcript_raw
            )
        raise _problem(409, "SOURCE_TEXT_REQUIRED")

    def _read_source(
        self, source: Source, storage: ObjectStorageService | None, deadline: float
    ) -> str:
        if source.text is not None:
            return source.text
        try:

            def read(timeout: float) -> bytes:
                adapter = storage or ObjectStorageService("")
                return adapter.read_text_source(source.key, source.size_bytes, timeout)

            data = _bounded_io(read, deadline, "SOURCE_READ_TIMEOUT")
        except HTTPException as exc:
            raise ExtractionError("SOURCE_UNAVAILABLE", retryable=exc.status_code >= 500) from None
        if len(data) != source.size_bytes or sha256(data).hexdigest() != source.fingerprint:
            raise ExtractionError("SOURCE_CHANGED")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ExtractionError("SOURCE_TEXT_INVALID") from None
        if "\x00" in text:
            raise ExtractionError("SOURCE_TEXT_INVALID")
        return text

    def _authorized_source(
        self,
        db: Session,
        requester: User,
        session_id: int,
        fragment_id: int,
        *,
        lock: bool,
    ) -> tuple[Source, int]:
        fragment, memory, role = self.memories._fragment_access(
            db,
            requester.id,
            fragment_id,
            lock=lock,
            expected_session_id=session_id,
        )
        self.memories._require_material_edit(db, memory, requester, role, fragment.contributor_id)
        self.memories._require_source_owner(memory, role)
        provider = db.get(MemoryContributor, fragment.contributor_id)
        if (
            provider is None
            or provider.is_del
            or provider.session_id != session_id
            or provider.account_user_id is None
        ):
            raise _problem(403, "SOURCE_PROVIDER_INELIGIBLE")
        if provider.contributor_type != "owner" or provider.account_user_id != memory.owner_user_id:
            author = db.get(User, provider.account_user_id)
            if (
                provider.contributor_type != "registered_user"
                or author is None
                or not self.memories.contributors.is_consented_contributor(
                    db, memory, provider, author
                )
            ):
                raise _problem(403, "SOURCE_PROVIDER_INELIGIBLE")
        if memory.visibility is None:
            return self._source(db, fragment), provider.account_user_id
        revision = current_source(db, fragment.id)
        draft = active_draft(db, revision.id) if revision is not None else None
        if draft is None:
            raise _problem(409, "DRAFT_REQUIRED")
        text = ExtractionDraft(decode_paragraphs(draft)).text
        return Source("draft_version", _digest(text), text=text, draft_version_id=draft.id), provider.account_user_id

    @staticmethod
    def _jobs(session_id: int):
        return select(AIProcessingJob).where(
            AIProcessingJob.session_id == session_id,
            AIProcessingJob.job_type == JOB_TYPE,
            AIProcessingJob.is_del.is_(False),
        )

    def enqueue(
        self,
        db: Session,
        requester: User,
        session_id: int,
        fragment_id: int,
        body: ExtractionRequest,
    ) -> tuple[int, bool]:
        """Insert the job inside the caller's transaction, serialized by the source session lock."""
        source, author_id = self._authorized_source(
            db, requester, session_id, fragment_id, lock=True
        )
        query = self._jobs(session_id).where(AIProcessingJob.source_fragment_id == fragment_id)
        if source.draft_version_id is not None:
            query = query.where(
                AIProcessingJob.input_payload["draft_version_id"].as_integer() == source.draft_version_id
            )
        jobs = query.order_by(AIProcessingJob.id.desc())
        parent = None
        if body.retry_of_job_id is not None:
            parent = db.scalar(query.where(AIProcessingJob.id == body.retry_of_job_id))
            if parent is None:
                raise _problem(404, "EXTRACTION_JOB_NOT_FOUND")
            if parent.status not in {"failed", "cancelled"}:
                raise _problem(409, "EXTRACTION_JOB_NOT_RETRYABLE")
        preferred = db.scalar(
            jobs.where(AIProcessingJob.status.in_((*ACTIVE_STATUSES, "succeeded"))).limit(1)
        )
        if preferred is not None:
            return preferred.id, False
        previous = db.scalar(jobs.limit(1))
        if parent is not None:
            if previous is not None and previous.id != parent.id:
                # Retrying an older job returns the latest submitted retry, including after an HTTP retry.
                return previous.id, False
        elif previous is not None:
            return previous.id, False
        if (
            db.scalar(
                select(MemoryElement.id)
                .where(
                    MemoryElement.session_id == session_id,
                    MemoryElement.source_fragment_id == fragment_id,
                    MemoryElement.origin == "extracted",
                    MemoryElement.is_del.is_(False),
                )
                .limit(1)
            )
            is not None
        ):
            raise _problem(409, "EXTRACTION_RESULT_EXISTS")

        if not self.model.available:
            raise _problem(503, "EXTRACTION_UNAVAILABLE")
        limit = self.settings.ai_text_max_input_chars
        if (source.text is not None and len(source.text) > limit) or (
            source.size_bytes is not None and source.size_bytes > limit * 4
        ):
            raise _problem(422, "SOURCE_TEXT_TOO_LONG")
        if body.allow_external_processing and requester.id != author_id:
            raise _problem(403, "SOURCE_AI_AUTHORIZATION_REQUIRED")
        model_scope = self._model_scope()
        authorized_author = None
        if self.model.external:
            # 同一原文交给同一模型配置时才可沿用作者的授权。
            already_authorized = (
                previous is not None
                and previous.input_payload.get("authorized_source_user_id") == author_id
                and previous.input_payload.get("model_scope") == model_scope
                and previous.input_payload.get("source_fingerprint") == source.fingerprint
                and previous.input_payload.get("source_kind") == source.kind
                and previous.input_payload.get("source_asset_id") == source.asset_id
            )
            if not body.allow_external_processing and not already_authorized:
                raise _problem(403, "SOURCE_AI_AUTHORIZATION_REQUIRED")
            authorized_author = author_id
        input_payload = {
            "source_kind": source.kind,
            "source_asset_id": source.asset_id,
            "source_fingerprint": source.fingerprint,
            "extractor_version": EXTRACTOR_VERSION,
            "retry_of_job_id": body.retry_of_job_id,
            "model_scope": model_scope,
            "authorized_source_user_id": authorized_author,
        }
        if source.draft_version_id is not None:
            input_payload["draft_version_id"] = source.draft_version_id
        if not self.model.external:
            # 配置切换只影响新任务，Worker 按任务自身的场景重试。
            input_payload.update(model_provider="mock", mock_scenario=self._mock_scenario(parent))
        now = _now()
        job = AIProcessingJob(
            session_id=session_id,
            source_fragment_id=fragment_id,
            requested_by_user_id=requester.id,
            job_type=JOB_TYPE,
            status="queued",
            attempt_count=0,
            max_attempts=self.settings.ai_text_max_attempts,
            # Give the request its first attempt before an independent worker claims the task.
            available_at=now + timedelta(seconds=self.settings.ai_text_sync_wait_seconds),
            idempotency_key=_digest(
                f"{fragment_id}:{source.kind}:{source.fingerprint}:{EXTRACTOR_VERSION}:"
                f"{source.draft_version_id or ''}:{body.retry_of_job_id}"
            ),
            input_payload=input_payload,
            created_by=requester.id,
            updated_by=requester.id,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.flush()
        return job.id, True

    def _claim(self, db: Session, job_id: int | None, job_type: str = JOB_TYPE) -> Claim | None:
        now = _now()
        with db.begin():
            claimable = or_(
                and_(AIProcessingJob.status == "queued", AIProcessingJob.available_at <= now),
                and_(
                    AIProcessingJob.status == "processing", AIProcessingJob.lease_expires_at <= now
                ),
            )
            if job_id is not None:
                # Only the request that created the job can bypass its initial scheduling delay.
                claimable = and_(
                    AIProcessingJob.status == "queued", AIProcessingJob.attempt_count == 0
                )
            query = select(AIProcessingJob).where(
                AIProcessingJob.job_type == job_type,
                AIProcessingJob.is_del.is_(False),
                claimable,
            )
            if job_id is not None:
                query = query.where(AIProcessingJob.id == job_id)
            job = db.scalar(
                query.order_by(
                    AIProcessingJob.priority.desc(),
                    AIProcessingJob.available_at,
                    AIProcessingJob.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if job is None:
                return None
            exhausted = job.attempt_count >= job.max_attempts
            if not exhausted:
                if job.status == "processing":
                    result = dict(job.result_payload or {})
                    _record_failure(result, job.attempt_count, now, "WORKER_LEASE_EXPIRED")
                    job.result_payload = result
                    job.error_code = "WORKER_LEASE_EXPIRED"
                job.attempt_count += 1
            token = uuid4().hex
            job.status = "processing"
            job.worker_id = token
            job.started_at = job.started_at or now
            job.lease_expires_at = now + timedelta(
                seconds=max(
                    self.settings.ai_text_timeout_seconds, self.settings.ai_text_sync_wait_seconds
                )
                + 15,
            )
            job.updated_at = now
            return Claim(job.id, token, exhausted)

    def _recheck(self, db: Session, job: AIProcessingJob, *, lock: bool) -> Source:
        actor = db.scalar(
            select(User).where(
                User.id == job.requested_by_user_id,
                User.is_del.is_(False),
            )
        )
        if actor is None or job.source_fragment_id is None:
            raise ExtractionError("SOURCE_ACCESS_LOST")
        try:
            source, author_id = self._authorized_source(
                db,
                actor,
                job.session_id,
                job.source_fragment_id,
                lock=lock,
            )
        except HTTPException:
            raise ExtractionError("SOURCE_ACCESS_LOST") from None
        payload = job.input_payload
        if source.draft_version_id != payload.get("draft_version_id"):
            raise ExtractionError("SOURCE_CHANGED")
        if (source.kind, source.asset_id, source.fingerprint) != (
            payload.get("source_kind"),
            payload.get("source_asset_id"),
            payload.get("source_fingerprint"),
        ):
            raise ExtractionError("SOURCE_CHANGED")
        if (
            not self.model.available
            or payload.get("model_scope") != self._model_scope()
            or payload.get("extractor_version") != EXTRACTOR_VERSION
        ):
            raise ExtractionError("MODEL_CONFIGURATION_CHANGED")
        if self.model.external and payload.get("authorized_source_user_id") != author_id:
            raise ExtractionError("SOURCE_AI_AUTHORIZATION_REQUIRED")
        return source

    @staticmethod
    def _verify_elements(
        extracted: list[ExtractedElement],
        source_text: str,
    ) -> list[tuple[ExtractedElement, int | None]]:
        verified = []
        seen = set()
        for row in extracted:
            start = source_text.find(row.source_quote)
            key = (row.element_type, row.summary, row.source_quote)
            if start < 0 or not row.source_quote.strip() or key in seen:
                continue
            seen.add(key)
            if source_text.find(row.source_quote, start + 1) >= 0:
                start = None
            verified.append((row, start))
        if extracted and not verified:
            raise ExtractionError("MODEL_EVIDENCE_INVALID")
        return verified

    def _store_elements(
        self,
        db: Session,
        job: AIProcessingJob,
        verified: list[tuple[ExtractedElement, int | None]],
    ) -> dict:
        # [新增] Worker 可能正在核验旧兜底元素，先锁所有者再更改元素记录。
        memory = db.get(MemorySession, job.session_id) if job.input_payload.get("draft_version_id") else None
        if memory is not None:
            lock_owner(db, memory.owner_user_id)
        active = select(MemoryElement).where(
            MemoryElement.source_fragment_id == job.source_fragment_id,
            MemoryElement.is_del.is_(False),
        )
        current = list(db.scalars(active.order_by(MemoryElement.sequence_no)))
        extracted = [row for row in current if row.origin == "extracted"]
        if extracted:
            # Protect an existing publication, including elements created by older versions.
            raise ExtractionError("EXTRACTION_RESULT_EXISTS")
        fallback = next((row for row in current if row.origin == "fallback"), None)
        if not verified:
            return {"element_count": 0, "evidence": {}}
        if fallback is not None:
            fallback.is_del = True
            fallback.updated_at = _now()
            db.flush()
        evidence = {}
        rows = []
        draft_id = job.input_payload.get("draft_version_id")
        draft = db.get(MemoryDraftVersion, draft_id) if draft_id is not None else None
        references = ExtractionDraft(decode_paragraphs(draft)) if draft is not None else None
        source = db.get(MemorySourceRevision, draft.source_revision_id) if draft is not None else None

        for sequence, (result, start) in enumerate(verified, start=1):
            draft_refs = None
            if references is not None:
                draft_refs = self._draft_references(
                    references, source, draft.id, start, len(result.source_quote)
                )
            row = MemoryElement(
                **result.model_dump(exclude={"entity_mentions"}),
                session_id=job.session_id,
                source_fragment_id=job.source_fragment_id,
                processing_job_id=job.id,
                sequence_no=sequence,
                origin="extracted",
                review_status="pending",
                verification_status="pending" if draft is not None else None,
                draft_version_id=draft_id,
                draft_references=draft_refs,
                created_by=job.requested_by_user_id,
                updated_by=job.requested_by_user_id,
            )
            db.add(row)
            rows.append((row, start, result))
        db.flush()
        for row, start, result in rows:
            evidence[str(row.id)] = {
                "start_offset": start,
                "end_offset": start + len(row.source_quote) if start is not None else None,
            }
            if isinstance(result, StructuredElement) and source is not None:
                possible_locations, truncated = [], False
                narrator_by_mention = {}
                if start is None and result.entity_mentions:
                    possible_locations, truncated = self._possible_draft_locations(
                        references, source, draft.id, result.source_quote
                    )
                elif result.entity_mentions:
                    for mention in result.entity_mentions:
                        name = mention.mention_text
                        offset = result.source_quote.find(name)
                        if offset >= 0 and result.source_quote.find(name, offset + 1) < 0:
                            proof = self._draft_references(
                                references, source, draft.id, start + offset, len(name)
                            )
                            narrator_by_mention[name] = MemoryEntityService.reference_narrator(proof)
                MemoryEntityService().store_mentions(
                    db, row, result, possible_locations=possible_locations,
                    locations_truncated=truncated, narrator_by_mention=narrator_by_mention,
                )
        # [新增] 版本化元素和它们的首次核验任务一同发布。
        if memory is not None:
            schedule_material_validation(
                db, memory.owner_user_id, memory.id, fragment_id=job.source_fragment_id,
                actor_id=job.requested_by_user_id, reason="elements_extracted",
            )
        return {"element_count": len(rows), "evidence": evidence}

    @staticmethod
    def _draft_references(content, source, draft_id, start, length) -> list[dict]:
        if start is None:
            return []
        span = TextRange(start_offset=start, end_offset=start + length)
        paragraphs = {item.id: item for item in content.paragraphs}
        return [
            paragraph_reference(
                paragraphs[ref.paragraph_id], source, draft_id, [ref.text_range.model_dump()]
            )
            for ref in content.references(span)
        ]

    @classmethod
    def _possible_draft_locations(cls, content, source, draft_id, quote) -> tuple[list[dict], bool]:
        """重复引句的多个位置供本人选择，发布前不将其中一个当成确定来源。"""
        text = content.text
        offset = text.find(quote)
        locations = []
        while offset >= 0 and len(locations) < MAX_ENTITY_QUOTE_LOCATIONS:
            locations.append({
                "index": len(locations),
                "draft_references": cls._draft_references(content, source, draft_id, offset, len(quote)),
            })
            offset = text.find(quote, offset + 1)
        return locations, offset >= 0

    @staticmethod
    def _fallback(db: Session, job: AIProcessingJob, source_text: str) -> int | None:
        rows = list(
            db.scalars(
                select(MemoryElement).where(
                    MemoryElement.source_fragment_id == job.source_fragment_id,
                    MemoryElement.is_del.is_(False),
                )
            )
        )
        if any(row.origin == "extracted" for row in rows):
            return None
        existing = next((row for row in rows if row.origin == "fallback"), None)
        if existing is not None:
            return existing.id
        fallback = MemoryElement(
            session_id=job.session_id,
            source_fragment_id=job.source_fragment_id,
            processing_job_id=job.id,
            sequence_no=1,
            origin="fallback",
            draft_version_id=job.input_payload.get("draft_version_id"),
            verification_status="pending" if job.input_payload.get("draft_version_id") else None,
            element_type="other",
            review_status="pending",
            summary=source_text.strip()[:160],
            attribute_confidence={},
            created_by=job.requested_by_user_id,
            updated_by=job.requested_by_user_id,
        )
        if fallback.draft_version_id is not None:
            draft = db.get(MemoryDraftVersion, fallback.draft_version_id)
            source = db.get(MemorySourceRevision, draft.source_revision_id)
            fallback.draft_references = MemoryExtractionService._draft_references(
                ExtractionDraft(decode_paragraphs(draft)), source, draft.id, 0, len(source_text)
            )
        db.add(fallback)
        db.flush()
        # [新增] 兜底保留待核对原因并进入同一复验流程。
        if fallback.draft_version_id is not None:
            fallback.verification_reason = "fallback"
            memory = db.get(MemorySession, job.session_id)
            schedule_material_validation(
                db, memory.owner_user_id, memory.id, fragment_id=job.source_fragment_id,
                actor_id=job.requested_by_user_id, reason="fallback_recorded",
            )
        return fallback.id

    def _complete(
        self,
        db: Session,
        claim: Claim,
        text: str | None,
        verified: list[tuple[ExtractedElement, int | None]],
        error: ExtractionError | None,
    ) -> None:
        with db.begin():
            now = _now()
            job = db.scalar(
                select(AIProcessingJob)
                .where(
                    AIProcessingJob.id == claim.job_id,
                    AIProcessingJob.status == "processing",
                    AIProcessingJob.worker_id == claim.token,
                    AIProcessingJob.lease_expires_at > now,
                    AIProcessingJob.is_del.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if job is None:
                return
            cancelled = False
            try:
                self._recheck(db, job, lock=True)
            except ExtractionError as exc:
                error, cancelled = exc, True
            # A worker waiting on the source lock can lose its lease before it may publish.
            now = _now()
            if _utc(job.lease_expires_at) <= now:
                return
            result = dict(job.result_payload or {})
            if error is None:
                try:
                    result.update(self._store_elements(db, job, verified))
                except ExtractionError as exc:
                    error, cancelled = exc, True
            if error is None:
                job.status = "succeeded"
                job.error_code = None
            else:
                _record_failure(result, job.attempt_count, now, error.code)
                job.error_code = error.code
                if cancelled:
                    job.status = "cancelled"
                elif error.retryable and job.attempt_count < job.max_attempts:
                    job.status = "queued"
                    job.available_at = now + timedelta(
                        seconds=min(60, 5 * 2 ** (job.attempt_count - 1))
                    )
                else:
                    job.status = "failed"
                    if (
                        text
                        and text.strip()
                        and error.code not in {"SOURCE_TEXT_TOO_LONG", "SOURCE_TEXT_REQUIRED"}
                    ):
                        result["fallback_element_id"] = self._fallback(db, job, text)
            job.result_payload = result
            job.error_message = None
            job.finished_at = now if job.status != "queued" else None
            job.lease_expires_at = None
            job.worker_id = None
            job.updated_at = now

    @staticmethod
    def _claimed_job(db: Session, claim: Claim) -> AIProcessingJob:
        job = db.scalar(
            select(AIProcessingJob)
            .where(
                AIProcessingJob.id == claim.job_id,
                AIProcessingJob.status == "processing",
                AIProcessingJob.worker_id == claim.token,
                AIProcessingJob.lease_expires_at > _now(),
                AIProcessingJob.is_del.is_(False),
            )
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise ExtractionError("WORKER_LEASE_EXPIRED")
        return job

    def run(
        self,
        db: Session,
        *,
        job_id: int | None = None,
        storage: ObjectStorageService | None = None,
    ) -> bool:
        """Run one job. Passing an ID is reserved for the request starting its first attempt."""
        claim = self._claim(db, job_id)
        if claim is None:
            return False
        budget = self.settings.ai_text_timeout_seconds
        if job_id is not None:
            budget = min(self.settings.ai_text_sync_wait_seconds, budget)
        deadline = monotonic() + budget
        text = None
        verified = []
        error = None
        try:
            mock_options = {}
            with db.begin():
                job = self._claimed_job(db, claim)
                source = self._recheck(db, job, lock=False)
                if not self.model.external:
                    # 早期 Mock 任务没有场景字段，仍生成正常结果。
                    mock_options = {
                        "mock_scenario": job.input_payload.get("mock_scenario", "success"),
                        "attempt_count": job.attempt_count,
                    }
            text = self._read_source(source, storage, deadline)
            if not text.strip():
                raise ExtractionError("SOURCE_TEXT_REQUIRED")
            if len(text) > self.settings.ai_text_max_input_chars:
                raise ExtractionError("SOURCE_TEXT_TOO_LONG")
            if claim.exhausted:
                raise ExtractionError("WORKER_LEASE_EXPIRED")
            if self.model.external:
                # Before sending text, confirm the claim and authorization are still valid.
                with db.begin():
                    self._recheck(db, self._claimed_job(db, claim), lock=False)
            extracted = _bounded_io(
                lambda timeout: (
                    self.model.extract_structured(text, timeout, **mock_options)
                    if source.draft_version_id
                    else self.model.extract(text, timeout, **mock_options)
                ),
                deadline,
                "MODEL_TIMEOUT",
            )
            verified = self._verify_elements(extracted, text)
        except ExtractionError as exc:
            error = exc
        self._complete(db, claim, text, verified, error)
        return True

    @staticmethod
    def _job_response(db: Session, job: AIProcessingJob) -> ExtractionJobResponse:
        result = job.result_payload or {}
        fallback_id = result.get("fallback_element_id")
        if fallback_id is not None:
            fallback_id = db.scalar(
                select(MemoryElement.id).where(
                    MemoryElement.id == fallback_id,
                    MemoryElement.session_id == job.session_id,
                    MemoryElement.source_fragment_id == job.source_fragment_id,
                    MemoryElement.origin == "fallback",
                    MemoryElement.is_del.is_(False),
                )
            )
        return ExtractionJobResponse(
            id=job.id,
            session_id=job.session_id,
            source_fragment_id=job.source_fragment_id,
            status=job.status,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            created_at=_utc(job.created_at),
            started_at=_utc(job.started_at),
            finished_at=_utc(job.finished_at),
            next_retry_at=_utc(job.available_at)
            if job.status == "queued" and job.attempt_count
            else None,
            element_count=result.get("element_count") if job.status == "succeeded" else None,
            error_code=job.error_code,
            retry_of_job_id=job.input_payload.get("retry_of_job_id"),
            fallback_element_id=fallback_id,
        )

    def get_job(
        self, db: Session, user: User, session_id: int, job_id: int
    ) -> ExtractionJobResponse:
        self.memories._session_access(db, user.id, session_id)
        job = db.scalar(
            self._jobs(session_id)
            .join(
                MemoryFragment,
                MemoryFragment.id == AIProcessingJob.source_fragment_id,
            )
            .where(
                AIProcessingJob.id == job_id,
                MemoryFragment.session_id == session_id,
                MemoryFragment.is_del.is_(False),
            )
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise _problem(404, "EXTRACTION_JOB_NOT_FOUND")
        return self._job_response(db, job)

    def list_jobs(
        self,
        db: Session,
        user: User,
        session_id: int,
        fragment_id: int,
        page: int,
        page_size: int,
    ) -> ExtractionJobPage:
        self.memories._fragment_access(db, user.id, fragment_id, expected_session_id=session_id)
        query = self._jobs(session_id).where(AIProcessingJob.source_fragment_id == fragment_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(AIProcessingJob.created_at.desc(), AIProcessingJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return ExtractionJobPage(
            items=[self._job_response(db, row) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    @staticmethod
    def _elements(session_id: int):
        return (
            select(MemoryElement)
            .join(
                MemoryFragment,
                MemoryFragment.id == MemoryElement.source_fragment_id,
            )
            .where(
                MemoryElement.session_id == session_id,
                MemoryElement.is_del.is_(False),
                MemoryFragment.session_id == session_id,
                MemoryFragment.is_del.is_(False),
            )
        )

    def list_elements(
        self,
        db: Session,
        user: User,
        session_id: int,
        page: int,
        page_size: int,
        *,
        source_fragment_id: int | None = None,
        processing_job_id: int | None = None,
        element_type: str | None = None,
        review_status: str | None = None,
        origin: str | None = None,
        verification_status: str | None = None,
    ) -> MemoryElementPage:
        # [删除-旧] self.memories._session_access(db, user.id, session_id)
        # [新增] 共享读路径及其过滤条件均隐藏跨会话验证结论。
        memory, _ = self.memories._session_access(db, user.id, session_id)
        shared = memory.owner_user_id != user.id
        invalidated = invalidated_verification_ids(db, user.id) if not shared and memory.visibility else set()
        effective_status = MemoryElement.verification_status
        if shared:
            effective_status = case(
                (MemoryElement.last_validation_job_id.is_not(None), "pending"),
                else_=MemoryElement.verification_status,
            )
        elif invalidated:
            effective_status = case(
                (MemoryElement.id.in_(invalidated), "pending"), else_=MemoryElement.verification_status,
            )
        query = self._elements(session_id)
        for column, value in (
            (MemoryElement.source_fragment_id, source_fragment_id),
            (MemoryElement.processing_job_id, processing_job_id),
            (MemoryElement.element_type, element_type),
            (MemoryElement.review_status, review_status),
            (MemoryElement.origin, origin),
            # [删除-旧] (MemoryElement.verification_status, verification_status),
            # [新增] 过滤和序列化采用同一可见状态。
            (effective_status, verification_status),
        ):
            if value is not None:
                query = query.where(column == value)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.scalars(
            query.order_by(MemoryElement.id).offset((page - 1) * page_size).limit(page_size)
        )
        return MemoryElementPage(
            # [删除-旧] items=[self.element_response(db, row) for row in rows],
            # [新增] 共享读路径使用保守的核验状态。
            items=[self.element_response(db, row, shared=shared, invalidated=row.id in invalidated) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _element(
        self, db: Session, user_id: int, session_id: int, element_id: int
    ) -> MemoryElement:
        self.memories._session_access(db, user_id, session_id)
        row = db.scalar(self._elements(session_id).where(MemoryElement.id == element_id))
        if row is None:
            raise _problem(404, "MEMORY_ELEMENT_NOT_FOUND")
        return row

    def get_element(
        self, db: Session, user: User, session_id: int, element_id: int
    ) -> MemoryElementResponse:
        # [删除-旧] return self.element_response(db, self._element(db, user.id, session_id, element_id))
        # [新增] 只让所有者看到可能由其其他会话产生的核验结果。
        row = self._element(db, user.id, session_id, element_id)
        shared = db.get(MemorySession, session_id).owner_user_id != user.id
        invalidated = (not shared and row.verification_status == "verified"
                       and row.id in invalidated_verification_ids(db, user.id))
        return self.element_response(db, row, shared=shared, invalidated=invalidated)

    @staticmethod
    # [删除-旧] def element_response(db: Session, row: MemoryElement) -> MemoryElementResponse:
    # [新增] 单个元素的序列化与列表保持相同的跨会话隐私边界。
    def element_response(
        db: Session, row: MemoryElement, *, shared: bool = False, invalidated: bool = False,
    ) -> MemoryElementResponse:
        response = MemoryElementResponse.model_validate(row)
        if invalidated or (shared and row.last_validation_job_id is not None):
            response.verification_status = "pending"
        if row.draft_version_id is not None:
            response.entities = MemoryEntityService.element_refs(db, row.id)
        return response

    def get_provenance(
        self,
        db: Session,
        user: User,
        session_id: int,
        element_id: int,
    ) -> MemoryProvenanceResponse:
        user_id = user.id
        row = self._element(db, user_id, session_id, element_id)
        job = db.scalar(
            self._jobs(session_id).where(
                AIProcessingJob.id == row.processing_job_id,
                AIProcessingJob.source_fragment_id == row.source_fragment_id,
            )
        )
        payload = job.input_payload if job is not None else {}
        evidence = (
            (job.result_payload or {}).get("evidence", {}).get(str(row.id), {}) if job else {}
        )
        response = MemoryProvenanceResponse(
            element_id=row.id,
            source_fragment_id=row.source_fragment_id,
            processing_job_id=row.processing_job_id,
            source_kind=payload.get("source_kind"),
            source_asset_id=payload.get("source_asset_id"),
            source_quote=row.source_quote,
            start_offset=evidence.get("start_offset"),
            end_offset=evidence.get("end_offset"),
            draft_references=row.draft_references or [],
        )
        if row.draft_version_id is not None:
            if row.origin == "fallback":
                draft = db.get(MemoryDraftVersion, row.draft_version_id)
                full_text = ExtractionDraft(decode_paragraphs(draft)).text
                response.source_text = full_text
                response.start_offset, response.end_offset = 0, len(full_text)
            return response
        if row.origin == "fallback":
            source = self._source(db, db.get(MemoryFragment, row.source_fragment_id))
            if (source.kind, source.asset_id, source.fingerprint) != (
                payload.get("source_kind"),
                payload.get("source_asset_id"),
                payload.get("source_fingerprint"),
            ):
                raise _problem(409, "SOURCE_CHANGED")
            db.rollback()
            try:
                text = self._read_source(
                    source, None, monotonic() + self.settings.ai_text_timeout_seconds
                )
            except ExtractionError as exc:
                raise _problem(503 if exc.retryable else 409, exc.code) from None
            self._element(db, user_id, session_id, element_id)
            response = response.model_copy(
                update={"source_text": text, "start_offset": 0, "end_offset": len(text)}
            )
        return response


def get_memory_extraction_service() -> MemoryExtractionService:
    return MemoryExtractionService()
