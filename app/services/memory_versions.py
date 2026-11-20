"""[新增] 原文和整理稿版本的存取与证据序列化。"""

import json
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import MemoryDraftVersion, MemoryElement, MemoryFragment, MemorySession, MemorySourceRevision
from app.schemas.memory_processing import NarrationInput, TextRange, TranscriptSegment
from app.services.draft_provenance import DraftParagraph
from app.services.memory_validation_queue import lock_owner, schedule_material_validation
from app.services.narrative_organization import audio_ranges_for_text


def fingerprint(payload: object) -> str:
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def source_narration(source: MemorySourceRevision | None) -> NarrationInput:
    """存量定位数组只有音频区间，新增元数据用 kind 区分。"""
    if source is not None:
        for item in source.transcript_segments:
            if item.get("kind") == "narration":
                return NarrationInput.model_validate(item)
    return NarrationInput()


def source_audio_segments(source: MemorySourceRevision) -> list[TranscriptSegment]:
    return [
        TranscriptSegment.model_validate(item)
        for item in source.transcript_segments
        if item.get("kind") != "narration"
    ]


def _narrator_for_ranges(
    text_ranges: tuple[TextRange, ...], narration: NarrationInput
) -> int | None:
    speakers = set()
    for span in text_ranges:
        cursor = span.start_offset
        for override in narration.narrator_spans:
            if override.end_offset <= cursor:
                continue
            if override.start_offset >= span.end_offset:
                break
            if override.start_offset > cursor:
                speakers.add(narration.narrator_user_id)
            speakers.add(override.narrator_user_id)
            cursor = min(override.end_offset, span.end_offset)
        if cursor < span.end_offset:
            speakers.add(narration.narrator_user_id)
    return next(iter(speakers)) if len(speakers) == 1 else None


def current_source(db: Session, fragment_id: int) -> MemorySourceRevision | None:
    return db.scalar(
        select(MemorySourceRevision)
        .where(MemorySourceRevision.fragment_id == fragment_id, MemorySourceRevision.is_del.is_(False))
        .order_by(MemorySourceRevision.id.desc())
        .limit(1)
    )


def active_draft(db: Session, source_id: int) -> MemoryDraftVersion | None:
    return db.scalar(
        select(MemoryDraftVersion)
        .where(MemoryDraftVersion.source_revision_id == source_id, MemoryDraftVersion.is_del.is_(False))
        .order_by(MemoryDraftVersion.id.desc())
        .limit(1)
    )


def retire_elements(db: Session, fragment_id: int, owner_id: int) -> None:
    """当前结果失效，旧元素和确认记录继续用于审计与重跑比对。"""
    # [新增] 与核验 Worker 共用所有者锁，先锁所有者再更新元素。
    memory = db.execute(select(MemorySession.id, MemorySession.owner_user_id).join(
        MemoryFragment, MemoryFragment.session_id == MemorySession.id,
    ).where(MemoryFragment.id == fragment_id)).one_or_none()
    if memory is not None:
        lock_owner(db, memory.owner_user_id)
    db.execute(
        update(MemoryElement)
        .where(MemoryElement.source_fragment_id == fragment_id, MemoryElement.is_del.is_(False))
        .values(is_del=True, updated_by=owner_id)
    )
    # [新增] 失效引用与复验任务一同提交。
    if memory is not None:
        schedule_material_validation(
            db, memory.owner_user_id, memory.id, fragment_id=fragment_id,
            actor_id=owner_id, reason="source_revision_changed",
        )


def save_source(
    db: Session, *, fragment_id: int, actor_id: int, kind: str, asset_id: int | None,
    text: str, segments: list[dict], narration: NarrationInput | None = None,
) -> MemorySourceRevision:
    """在会话锁保护下复用已登记的原文版本。"""
    if narration and (narration.narrator_user_id is not None or narration.narrator_spans):
        segments = [*segments, {"kind": "narration", **narration.model_dump(mode="json")}]
    digest = fingerprint([kind, asset_id, text, segments])
    source = db.scalar(
        select(MemorySourceRevision).where(
            MemorySourceRevision.fragment_id == fragment_id,
            MemorySourceRevision.source_fingerprint == digest,
        )
    )
    if source is not None:
        latest = current_source(db, fragment_id)
        if latest is None or latest.id != source.id:
            if latest is not None and (
                latest.source_kind, latest.source_asset_id, latest.source_text, latest.transcript_segments
            ) == (kind, asset_id, text, segments):
                return latest
            # 重新采用旧标注时发布一份新版本，保留中间版本和历史引用。
            digest = fingerprint([digest, latest.id if latest else None])
            source = None
    if source is None:
        source = MemorySourceRevision(
            fragment_id=fragment_id, source_kind=kind, source_asset_id=asset_id, source_text=text,
            transcript_segments=segments, source_fingerprint=digest, created_by=actor_id,
            updated_by=actor_id,
        )
        db.add(source)
        db.flush()
        retire_elements(db, fragment_id, actor_id)
    return source


def decode_paragraphs(draft: MemoryDraftVersion) -> tuple[DraftParagraph, ...]:
    return tuple(
        DraftParagraph(
            UUID(item["id"]), item["text"],
            tuple(TextRange.model_validate(span) for span in item["source_ranges"]),
            item["owner_edited"],
        )
        for item in draft.paragraphs
    )


def encode_paragraphs(paragraphs: list[DraftParagraph]) -> list[dict]:
    return [
        {
            "id": str(item.id), "text": item.text, "owner_edited": item.owner_edited,
            "source_ranges": [span.model_dump() for span in item.source_ranges],
        }
        for item in paragraphs
    ]


def paragraph_reference(
    paragraph: DraftParagraph, source: MemorySourceRevision, draft_id: int,
    ranges: list[dict],
) -> dict:
    """两层定位均来自已保存的版本和实际转写区间。"""
    segments = source_audio_segments(source) if source.source_asset_id and source.source_kind in {
        "app_asr", "server_asr",
    } else []
    source_refs = []
    text_ranges = paragraph.source_ranges
    if len(text_ranges) == 1 and ranges:
        original = text_ranges[0]
        if source.source_text[original.start_offset : original.end_offset] == paragraph.text:
            # 逐字保留的整理段落可从引句精确换算原文字符位置。
            text_ranges = tuple(
                TextRange(
                    start_offset=original.start_offset + span["start_offset"],
                    end_offset=original.start_offset + span["end_offset"],
                )
                for span in ranges
            )
    if text_ranges:
        source_refs.append(
            {
                "fragment_id": source.fragment_id, "source_revision_id": source.id,
                "source_asset_id": source.source_asset_id,
                "source_kind": source.source_kind,
                "text_ranges": [span.model_dump() for span in text_ranges],
                "audio_ranges": [
                    item.model_dump() for item in audio_ranges_for_text(text_ranges, segments)
                ],
            }
        )
    reference = {
        "draft_version_id": draft_id, "paragraph_id": str(paragraph.id),
        "text_ranges": ranges, "source_references": source_refs,
        "owner_edited": paragraph.owner_edited,
    }
    narrator_id = _narrator_for_ranges(text_ranges, source_narration(source))
    if narrator_id is not None:
        reference["narrator_user_id"] = narrator_id
    return reference
