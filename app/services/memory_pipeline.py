"""[新增] 版本化素材的 ASR、整理、提取登记和进度。"""

from datetime import timedelta
from hashlib import sha256
from time import monotonic
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import (
    AIProcessingJob, MediaAsset, MemoryContributor, MemoryDraftVersion, MemoryElement,
    MemoryFragment, MemorySourceRevision, User,
)
from app.schemas.memory_extraction import ExtractionRequest
from app.schemas.memory_processing import (
    DraftVersionCreate, NarrationInput, ProcessingJobCreate, TranscriptSegment,
)
from app.services.audio_transcription import AudioTranscriptionModel, TranscriptionResult
from app.services.draft_provenance import apply_owner_edit, create_model_paragraphs
from app.services.memory_extraction_service import (
    ACTIVE_STATUSES, JOB_TYPE, Claim, MemoryExtractionService, Source, _bounded_io, _now, _problem,
    _record_failure, _utc,
)
from app.services.memory_versions import (
    active_draft, current_source, decode_paragraphs, encode_paragraphs, fingerprint,
    paragraph_reference, retire_elements, save_source, source_audio_segments, source_narration,
)
from app.services.memory_validation_rules import invalidated_verification_ids
from app.services.narrative_organization import organize_narrative, validate_transcript_segments
from app.services.object_storage_service import ObjectStorageService
from app.services.text_extraction_model import ExtractionError

JOB_TYPES = {"asr": "transcribe_memory_audio", "organization": "organize_memory", "extraction": JOB_TYPE}


class MemoryPipelineService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.extraction = MemoryExtractionService(self.settings)
        self.memories = self.extraction.memories
        self.asr = AudioTranscriptionModel(self.settings)

    def _model(self, stage: str):
        return self.asr if stage == "asr" else self.extraction.model

    def _model_scope(self, stage: str) -> str:
        if stage == "asr":
            configuration = [
                self.settings.ai_asr_provider, self.settings.ai_asr_base_url, self.settings.ai_asr_model,
            ]
            if self.settings.ai_asr_timestamps_enabled:
                configuration.append("segment_timestamps")
            return fingerprint(configuration)
        return self.extraction._model_scope()

    def _fragment(
        self, db: Session, user: User, session_id: int, fragment_id: int, *, write: bool = False,
    ):
        fragment, memory, role = self.memories._fragment_access(
            db, user.id, fragment_id, lock=write, expected_session_id=session_id
        )
        if memory.visibility is None:
            raise _problem(409, "VERSIONED_MEMORY_REQUIRED")
        if write:
            self.memories._require_source_owner(memory, role)
        return fragment, memory

    @staticmethod
    def _audio_source(db: Session, fragment: MemoryFragment) -> tuple[Source, str]:
        asset = db.get(MediaAsset, fragment.source_asset_id) if fragment.source_asset_id else None
        if (
            fragment.source_type != "audio" or asset is None or asset.is_del
            or asset.media_type != "audio" or asset.upload_status not in {"uploaded", "attached"}
            or not asset.size_bytes or not asset.content_sha256
        ):
            raise _problem(409, "AUDIO_SOURCE_REQUIRED")
        return Source(
            "audio_file", asset.content_sha256, asset_id=asset.id, key=asset.object_key,
            size_bytes=asset.size_bytes,
        ), asset.content_type

    @staticmethod
    def _jobs(fragment_id: int, stage: str):
        return select(AIProcessingJob).where(
            AIProcessingJob.source_fragment_id == fragment_id,
            AIProcessingJob.job_type == JOB_TYPES[stage], AIProcessingJob.is_del.is_(False),
        )

    @staticmethod
    def _validate_narration(
        db: Session, narration: NarrationInput, raw_text: str | None
    ) -> None:
        if narration.narrator_spans and (
            raw_text is None or narration.narrator_spans[-1].end_offset > len(raw_text)
        ):
            raise _problem(422, "NARRATOR_SPAN_OUT_OF_BOUNDS")
        narrator_ids = {
            user_id for user_id in (
                narration.narrator_user_id, *(span.narrator_user_id for span in narration.narrator_spans),
            ) if user_id is not None
        }
        if narrator_ids:
            active_ids = set(db.scalars(select(User.id).where(
                User.id.in_(narrator_ids), User.is_del.is_(False),
            )))
            if narrator_ids != active_ids:
                raise _problem(422, "NARRATOR_USER_INVALID")

    def prepare(
        self, db: Session, user: User, fragment: MemoryFragment, allow_external: bool,
        segments: list[TranscriptSegment], *, source_origin: str | None = None,
        narration_changes: dict | None = None,
    ) -> str | None:
        """在片段保存事务内登记版本和首个阶段任务。"""
        try:
            raw = self.extraction._source(db, fragment)
        except HTTPException as exc:
            if fragment.source_type != "audio":
                if narration_changes:
                    raise _problem(422, "NARRATION_SOURCE_REQUIRED") from None
                return "SOURCE_TEXT_REQUIRED" if exc.status_code == 409 else "SOURCE_UNAVAILABLE"
            raw, _ = self._audio_source(db, fragment)
            narration = NarrationInput.model_validate(narration_changes or {})
            self._validate_narration(db, narration, None)
            return self._prepare_job(
                db, user, fragment, "asr", raw, None, allow_external, narration,
            )

        source = current_source(db, fragment.id)
        previous_narration = source_narration(source)
        narration = NarrationInput.model_validate({
            **previous_narration.model_dump(mode="json"), **(narration_changes or {}),
        })
        if narration_changes:
            self._validate_narration(db, narration, raw.text)
        if raw.text is not None:
            if "\x00" in raw.text:
                raise _problem(422, "SOURCE_TEXT_INVALID")
            try:
                validate_transcript_segments(raw.text, segments)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            if source is None or source.source_text != raw.text or segments or narration != previous_narration:
                kind = source.source_kind if source is not None else (
                    source_origin or ("app_asr" if raw.kind == "transcript_raw" else raw.kind)
                )
                if not segments and source is not None and source.source_text == raw.text:
                    segments = source_audio_segments(source)
                source = save_source(
                    db, fragment_id=fragment.id, actor_id=user.id, kind=kind,
                    asset_id=fragment.source_asset_id, text=raw.text,
                    segments=[item.model_dump() for item in segments],
                    narration=narration,
                )
        return self._prepare_job(
            db, user, fragment, "organization", raw, source, allow_external,
            narration if source is None else None,
        )

    def _prepare_job(
        self, db, user, fragment, stage, raw, revision, allow_external,
        narration: NarrationInput | None = None,
    ) -> str | None:
        try:
            job = self._enqueue(
                db, user, fragment, stage, raw, revision, allow_external,
                allow_followup_external=allow_external, narration=narration,
            )
        except HTTPException as exc:
            if exc.status_code in {403, 503}:
                return exc.detail["code"]
            raise
        return job.error_code if job.status == "cancelled" else None

    def _enqueue(
        self, db: Session, user: User, fragment: MemoryFragment, stage: str, raw: Source,
        revision: MemorySourceRevision | None, allow_external: bool, *,
        retry_of_job_id: int | None = None, manual: bool = False,
        allow_followup_external: bool = False,
        narration: NarrationInput | None = None,
    ) -> AIProcessingJob:
        revision_id = revision.id if revision is not None else None
        jobs = self._jobs(fragment.id, stage).where(
            AIProcessingJob.input_payload["raw_fingerprint"].as_string() == raw.fingerprint,
        )
        if stage == "organization" and revision_id is not None:
            jobs = jobs.where(AIProcessingJob.input_payload["source_revision_id"].as_integer() == revision_id)
        parent = db.scalar(jobs.where(AIProcessingJob.id == retry_of_job_id)) if retry_of_job_id else None
        if retry_of_job_id and parent is None:
            raise _problem(409, "PROCESSING_JOB_STALE")
        latest = db.scalar(jobs.order_by(AIProcessingJob.id.desc()).limit(1))
        if latest is not None and (
            latest.status in (*ACTIVE_STATUSES, "succeeded") or not manual
            or (parent is not None and latest.id != parent.id)
        ):
            return latest
        model = self._model(stage)
        scope = self._model_scope(stage)
        provider = db.get(MemoryContributor, fragment.contributor_id)
        author_id = provider.account_user_id if provider is not None and not provider.is_del else None
        blocked = None
        if not model.available:
            blocked = (503, "PROCESSING_UNAVAILABLE")
        elif model.external and (
            author_id != user.id or (
                not allow_external and not (
                    latest is not None and latest.input_payload.get("authorized_author_id") == author_id
                    and latest.input_payload.get("model_scope") == scope
                )
            )
        ):
            blocked = (403, "SOURCE_AI_AUTHORIZATION_REQUIRED")
        narration_payload = (
            narration.model_dump(mode="json")
            if narration is not None and (narration.narrator_user_id is not None or narration.narrator_spans)
            else latest.input_payload.get("narration") if latest else None
        )
        # 预条件阻塞也保留待转写文件的已确认叙述者，供手工提交时继承。
        if blocked and (manual or not narration_payload):
            raise _problem(*blocked)
        job = AIProcessingJob(
            session_id=fragment.session_id, source_fragment_id=fragment.id,
            requested_by_user_id=user.id, job_type=JOB_TYPES[stage],
            status="cancelled" if blocked else "queued",
            error_code=blocked[1] if blocked else None, finished_at=_now() if blocked else None,
            input_payload={
                "raw_fingerprint": raw.fingerprint, "asset_id": raw.asset_id,
                "source_revision_id": revision_id, "model_scope": scope,
                **({"narration": narration_payload} if narration_payload else {}),
                "authorized_author_id": author_id if model.external and allow_external else (
                    latest.input_payload.get("authorized_author_id") if latest and model.external else None
                ),
                # 片段保存可授权整条链。单阶段重试只授权本次模型调用。
                "followup_text_scope": (
                    self.extraction._model_scope()
                    if allow_followup_external and author_id == user.id
                    else None
                ),
            },
            idempotency_key=fingerprint([
                fragment.id, stage, raw.fingerprint, revision_id, latest.id if latest else None,
            ]),
            max_attempts=self.settings.ai_text_max_attempts,
            created_by=user.id, updated_by=user.id,
        )
        db.add(job)
        db.flush()
        return job

    def submit(
        self, db: Session, user: User, session_id: int, fragment_id: int, body: ProcessingJobCreate
    ) -> dict:
        with db.begin():
            fragment, _ = self._fragment(db, user, session_id, fragment_id, write=True)
            revision = current_source(db, fragment.id)
            if body.stage == "asr":
                if fragment.transcript_raw:
                    if revision is not None and revision.source_kind == "server_asr":
                        finished = db.scalar(self._jobs(fragment_id, "asr").where(
                            AIProcessingJob.status == "succeeded",
                            AIProcessingJob.input_payload["source_revision_id"].as_integer() == revision.id,
                        ).order_by(AIProcessingJob.id.desc()).limit(1))
                        if finished is not None:
                            return {"job_id": finished.id, "stage": body.stage, "status": finished.status}
                    raise _problem(409, "TRANSCRIPT_ALREADY_AVAILABLE")
                raw, _ = self._audio_source(db, fragment)
            else:
                raw = self.extraction._source(db, fragment)
            job = self._enqueue(
                db, user, fragment, body.stage, raw, revision, body.allow_external_processing,
                retry_of_job_id=body.retry_of_job_id, manual=True,
            )
            return {"job_id": job.id, "stage": body.stage, "status": job.status}

    def _stage_inputs(self, db: Session, job: AIProcessingJob, stage: str, *, lock: bool):
        user = db.scalar(select(User).where(
            User.id == job.requested_by_user_id, User.is_del.is_(False),
        ))
        if user is None:
            raise ExtractionError("SOURCE_ACCESS_LOST")
        try:
            fragment, memory = self._fragment(db, user, job.session_id, job.source_fragment_id, write=lock)
            if memory.owner_user_id != user.id:
                raise ExtractionError("SOURCE_ACCESS_LOST")
            revision = current_source(db, fragment.id)
            content_type = None
            if stage == "asr":
                if fragment.transcript_raw:
                    raise ExtractionError("SOURCE_CHANGED")
                raw, content_type = self._audio_source(db, fragment)
            else:
                raw = self.extraction._source(db, fragment)
                if revision is not None and active_draft(db, revision.id) is not None:
                    raise ExtractionError("DRAFT_ALREADY_AVAILABLE")
        except HTTPException:
            raise ExtractionError("SOURCE_ACCESS_LOST") from None
        payload = job.input_payload
        if (
            raw.fingerprint != payload["raw_fingerprint"] or raw.asset_id != payload.get("asset_id")
            or (revision.id if revision else None) != payload.get("source_revision_id")
        ):
            raise ExtractionError("SOURCE_CHANGED")
        model = self._model(stage)
        if not model.available or payload["model_scope"] != self._model_scope(stage):
            raise ExtractionError("MODEL_CONFIGURATION_CHANGED")
        if model.external:
            provider = db.get(MemoryContributor, fragment.contributor_id)
            if provider is None or provider.is_del or provider.account_user_id != user.id or (
                payload.get("authorized_author_id") != user.id
            ):
                raise ExtractionError("SOURCE_AI_AUTHORIZATION_REQUIRED")
        return user, fragment, raw, revision, content_type

    @staticmethod
    def _lock_claimed_job(db: Session, claim: Claim) -> AIProcessingJob | None:
        return db.scalar(
            select(AIProcessingJob)
            .where(
                AIProcessingJob.id == claim.job_id,
                AIProcessingJob.worker_id == claim.token,
                AIProcessingJob.status == "processing",
                AIProcessingJob.lease_expires_at > _now(),
                AIProcessingJob.is_del.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _snapshot_organization_source(self, db: Session, claim: Claim, raw_text: str) -> None:
        """先提交已校验原文，使模型失败时仍有可供人工整理的版本。"""
        if not raw_text.strip():
            raise ExtractionError("SOURCE_TEXT_REQUIRED")
        with db.begin():
            job = self._lock_claimed_job(db, claim)
            if job is None:
                raise ExtractionError("WORKER_LEASE_EXPIRED")
            actor, fragment, raw, revision, _ = self._stage_inputs(
                db, job, "organization", lock=True
            )
            if _utc(job.lease_expires_at) <= _now():
                raise ExtractionError("WORKER_LEASE_EXPIRED")
            if revision is None:
                revision = save_source(
                    db, fragment_id=fragment.id, actor_id=actor.id,
                    kind="app_asr" if raw.kind == "transcript_raw" else raw.kind,
                    asset_id=fragment.source_asset_id, text=raw_text, segments=[],
                    narration=NarrationInput.model_validate(job.input_payload.get("narration") or {}),
                )
                job.input_payload = {**job.input_payload, "source_revision_id": revision.id}

    def run(self, db: Session, stage: str, storage: ObjectStorageService | None = None) -> bool:
        claim = self.extraction._claim(db, None, JOB_TYPES[stage])
        if claim is None:
            return False
        deadline = monotonic() + self.settings.ai_text_timeout_seconds
        error, raw_text, organized, transcription = None, None, None, None
        try:
            with db.begin():
                job = self.extraction._claimed_job(db, claim)
                _, _, raw, revision, content_type = self._stage_inputs(db, job, stage, lock=False)
                saved_text = (
                    revision.source_text
                    if revision is not None and raw.kind == "text_file"
                    else None
                )
            if claim.exhausted:
                raise ExtractionError("WORKER_LEASE_EXPIRED")
            storage = storage or ObjectStorageService("")
            if stage == "asr":
                if raw.size_bytes > self.settings.ai_asr_max_audio_bytes:
                    raise ExtractionError("ASR_SOURCE_TOO_LARGE")
                try:
                    audio = _bounded_io(
                        lambda timeout: storage.read_text_source(raw.key, raw.size_bytes, timeout),
                        deadline, "ASR_SOURCE_TIMEOUT",
                    )
                except HTTPException as exc:
                    raise ExtractionError("SOURCE_UNAVAILABLE", retryable=exc.status_code >= 500) from None
                if len(audio) != raw.size_bytes or sha256(audio).hexdigest() != raw.fingerprint:
                    raise ExtractionError("SOURCE_CHANGED")
                with db.begin():
                    self._stage_inputs(db, self.extraction._claimed_job(db, claim), stage, lock=False)
                transcription = _bounded_io(
                    lambda timeout: self.asr.transcribe(audio, content_type, timeout),
                    deadline, "ASR_TIMEOUT",
                )
            else:
                if raw.size_bytes and raw.size_bytes > self.settings.ai_text_max_input_chars * 4:
                    raise ExtractionError("SOURCE_TEXT_TOO_LONG")
                raw_text = (
                    saved_text if saved_text is not None
                    else self.extraction._read_source(raw, storage, deadline)
                )
                if revision is None:
                    # [新增] 在提交给整理模型前固定原文，后续重跑可复用已验证正文。
                    self._snapshot_organization_source(db, claim, raw_text)
                with db.begin():
                    self._stage_inputs(db, self.extraction._claimed_job(db, claim), stage, lock=False)
                organized = _bounded_io(
                    lambda timeout: organize_narrative(self.extraction.model, raw_text, timeout),
                    deadline, "ORGANIZATION_TIMEOUT",
                )
        except ExtractionError as exc:
            error = exc
        with db.begin():
            job = self._lock_claimed_job(db, claim)
            if job is None:
                return True
            result = dict(job.result_payload or {})
            try:
                actor, fragment, _, revision, _ = self._stage_inputs(db, job, stage, lock=True)
            except ExtractionError as exc:
                error = exc
                job.status = "cancelled"
            now = _now()
            if _utc(job.lease_expires_at) <= now:
                return True
            if error is None:
                result.update(
                    self._publish(db, job, actor, fragment, revision, organized, transcription)
                )
                job.status = "succeeded"
            else:
                _record_failure(result, job.attempt_count, now, error.code)
                if job.status != "cancelled":
                    if error.retryable and job.attempt_count < job.max_attempts:
                        job.status = "queued"
                        job.available_at = now + timedelta(seconds=min(60, 5 * 2 ** (job.attempt_count - 1)))
                    else:
                        job.status = "failed"
            job.result_payload = result
            job.error_code = error.code if error else None
            job.finished_at = now if job.status != "queued" else None
            job.lease_expires_at = job.worker_id = None
            job.updated_at = now
        return True

    def _publish(
        self, db, job, actor, fragment, revision, organized,
        transcription: TranscriptionResult | None,
    ) -> dict:
        allow_external = job.input_payload.get("followup_text_scope") == self.extraction._model_scope()
        # [删除-旧] 整理成功后才补存缺失的原文版本。
        if transcription is not None:
            revision = save_source(
                db, fragment_id=fragment.id, actor_id=actor.id,
                kind="server_asr",
                asset_id=fragment.source_asset_id, text=transcription.text,
                segments=[segment.model_dump() for segment in transcription.segments],
                narration=NarrationInput.model_validate(job.input_payload.get("narration") or {}),
            )
            job.input_payload = {**job.input_payload, "source_revision_id": revision.id}
            fragment.transcript_raw = transcription.text
            fragment.updated_by, fragment.updated_at = actor.id, _now()
            self.prepare(db, actor, fragment, allow_external, [])
            return {"source_revision_id": revision.id}

        draft = MemoryDraftVersion(
            source_revision_id=revision.id, origin="model",
            paragraphs=encode_paragraphs(create_model_paragraphs(organized)),
            created_by=actor.id, updated_by=actor.id,
        )
        db.add(draft)
        db.flush()
        retire_elements(db, fragment.id, actor.id)
        self._enqueue_extraction(db, actor, fragment, allow_external)
        return {"draft_version_id": draft.id, "source_revision_id": revision.id}

    def _enqueue_extraction(self, db, user, fragment, allow_external: bool) -> None:
        try:
            job_id, created = self.extraction.enqueue(
                db, user, fragment.session_id, fragment.id,
                ExtractionRequest(allow_external_processing=allow_external),
            )
            if created:
                # 整理在 Worker 内完成，提取可由下一轮立即领取。
                db.get(AIProcessingJob, job_id).available_at = _now()
        except HTTPException as exc:
            if exc.status_code not in {403, 409, 422, 503}:
                raise

    @staticmethod
    def _draft_response(draft, source, *, include_body=False) -> dict:
        result = {
            "id": draft.id, "source_revision_id": source.id, "base_version_id": draft.base_version_id,
            "origin": draft.origin, "created_at": draft.created_at,
        }
        if include_body:
            result["paragraphs"] = [
                {
                    "id": str(item.id), "text": item.text, "owner_edited": item.owner_edited,
                    "source_references": paragraph_reference(item, source, draft.id, [])["source_references"],
                }
                for item in decode_paragraphs(draft)
            ]
        return result

    def list_drafts(self, db, user, session_id, fragment_id, page, page_size) -> dict:
        self._fragment(db, user, session_id, fragment_id)
        query = select(MemoryDraftVersion, MemorySourceRevision).join(MemorySourceRevision).where(
            MemorySourceRevision.fragment_id == fragment_id, MemorySourceRevision.is_del.is_(False),
            MemoryDraftVersion.is_del.is_(False),
        )
        current = current_source(db, fragment_id)
        active = active_draft(db, current.id) if current else None
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(query.order_by(MemoryDraftVersion.id.desc()).offset((page - 1) * page_size).limit(page_size))
        return {
            "items": [self._draft_response(draft, source) for draft, source in rows],
            "page": page, "page_size": page_size, "total": total,
            "active_version_id": active.id if active else None,
        }

    def get_draft(self, db, user, session_id, fragment_id, version_id) -> dict:
        self._fragment(db, user, session_id, fragment_id)
        draft = db.get(MemoryDraftVersion, version_id)
        source = db.get(MemorySourceRevision, draft.source_revision_id) if draft and not draft.is_del else None
        if source is None or source.is_del or source.fragment_id != fragment_id:
            raise _problem(404, "DRAFT_NOT_FOUND")
        return self._draft_response(draft, source, include_body=True)

    def save_draft(
        self, db, user, session_id, fragment_id, body: DraftVersionCreate, request_key: UUID | None,
    ) -> dict:
        with db.begin():
            fragment, _ = self._fragment(db, user, session_id, fragment_id, write=True)
            source = current_source(db, fragment.id)
            if source is None or body.source_revision_id != source.id:
                raise _problem(409, "SOURCE_CHANGED")
            digest = fingerprint(body.model_dump(mode="json"))
            if request_key is not None:
                prior = db.scalar(select(MemoryDraftVersion).where(
                    MemoryDraftVersion.source_revision_id == source.id,
                    MemoryDraftVersion.request_key == request_key,
                ))
                if prior is not None:
                    if prior.request_hash != digest or prior.is_del:
                        raise _problem(409, "DRAFT_REQUEST_KEY_CONFLICT")
                    return self._draft_response(prior, source, include_body=True)
            active = active_draft(db, source.id)
            if body.base_version_id != (active.id if active else None):
                raise _problem(409, "DRAFT_VERSION_CHANGED")
            try:
                paragraphs = apply_owner_edit(
                    decode_paragraphs(active) if active else (), body.paragraphs
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            draft = MemoryDraftVersion(
                source_revision_id=source.id, base_version_id=body.base_version_id, origin="owner",
                paragraphs=encode_paragraphs(paragraphs), request_key=request_key, request_hash=digest,
                created_by=user.id, updated_by=user.id,
            )
            db.add(draft)
            db.flush()
            retire_elements(db, fragment.id, user.id)
            self._enqueue_extraction(db, user, fragment, False)
            return self._draft_response(draft, source, include_body=True)

    @staticmethod
    def _status(job: AIProcessingJob | None, missing: str, reason: str | None = None) -> dict:
        if job is None:
            return {"status": missing, "reason_code": reason}
        if job.status == "cancelled" and job.attempt_count == 0:
            return {"status": "blocked", "job_id": job.id, "reason_code": job.error_code}
        return {
            "status": job.status, "job_id": job.id, "started_at": job.started_at,
            "finished_at": job.finished_at, "error_code": job.error_code,
            "next_retry_at": job.available_at if job.status == "queued" and job.attempt_count else None,
        }

    def progress(self, db, user, session_id, fragment_id) -> dict:
        # [删除-旧] fragment, _ = self._fragment(db, user, session_id, fragment_id)
        # [新增] 处理进度同样收窄跨会话核验状态。
        fragment, memory = self._fragment(db, user, session_id, fragment_id)
        source = current_source(db, fragment_id)
        draft = active_draft(db, source.id) if source else None
        asr_job = db.scalar(self._jobs(fragment_id, "asr").order_by(AIProcessingJob.id.desc()).limit(1))
        if source and source.source_kind == "app_asr":
            asr_job = None
        org = self._jobs(fragment_id, "organization")
        if source is not None:
            org = org.where(AIProcessingJob.input_payload["source_revision_id"].as_integer() == source.id)
        org_job = db.scalar(org.order_by(AIProcessingJob.id.desc()).limit(1))
        if draft is not None and draft.origin == "owner":
            org_job = None
        ext_job = db.scalar(self._jobs(fragment_id, "extraction").where(
            AIProcessingJob.input_payload["draft_version_id"].as_integer() == draft.id,
        ).order_by(AIProcessingJob.id.desc()).limit(1)) if draft else None
        asr_waits = fragment.source_type == "audio" and not fragment.transcript_raw
        asr_status = self._status(asr_job, "blocked" if asr_waits else "skipped", (
            "ASR_INPUT_OR_AUTHORIZATION_REQUIRED" if asr_waits else None
        ))
        org_status = self._status(org_job, "skipped" if draft else "blocked", (
            None if draft else "ORGANIZATION_INPUT_OR_AUTHORIZATION_REQUIRED"
        ))
        extraction = self._status(ext_job, "blocked" if draft else "not_started", (
            "EXTRACTION_INPUT_OR_AUTHORIZATION_REQUIRED" if draft else None
        ))
        if ext_job and ext_job.status == "succeeded":
            rows = db.scalars(self.extraction._elements(session_id).where(
                MemoryElement.processing_job_id == ext_job.id,
            ).order_by(MemoryElement.sequence_no))
            # [删除-旧] extraction["elements"] = [self.extraction.element_response(db, row) for row in rows]
            shared = memory.owner_user_id != user.id
            invalidated = set() if shared else invalidated_verification_ids(db, user.id)
            extraction["elements"] = [
                self.extraction.element_response(db, row, shared=shared, invalidated=row.id in invalidated)
                for row in rows
            ]
        if ext_job and ext_job.status in {"succeeded", "failed"}:
            extraction["fallback_element_id"] = (ext_job.result_payload or {}).get("fallback_element_id")
        return {
            "fragment_id": fragment_id, "source_revision_id": source.id if source else None,
            "source_kind": source.source_kind if source else None,
            "draft_version_id": draft.id if draft else None,
            "upload_processed_at": draft.created_at if draft else None, "asr": asr_status,
            "organization": org_status, "extraction": extraction,
        }
