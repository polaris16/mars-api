"""[新增] 图谱发布与读取共用的证据定位和裁定有效性检查。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LifeEntity, MemoryConflictEvidence, MemoryConflictGroup, MemoryReviewAnswer, MemoryReviewQuestion
from app.schemas.memory_graph import GraphEvidence
from app.services.draft_provenance import DraftParagraph
from app.services.memory_validation_rules import (
    Record, active_conflict_groups, current_group_fingerprint, group_claims, value_key,
)
from app.services.memory_versions import decode_paragraphs, fingerprint, paragraph_reference


def _checked_ranges(value: object, length: int) -> list[dict] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    ranges = []
    for span in value:
        if not isinstance(span, dict):
            return None
        start, end = span.get("start_offset"), span.get("end_offset")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
            return None
        ranges.append({"start_offset": start, "end_offset": end})
    return ranges


@dataclass(frozen=True)
class LocatedRecord:
    record: Record
    paragraphs: dict[str, DraftParagraph]
    evidence: GraphEvidence
    anchor_key: str


def locate_references(record: Record, paragraphs: dict, references, quote: str) -> list[dict]:
    """根据当前保存的两层版本重建位置，输出仅含该条来源信息。"""
    if not quote or not isinstance(references, list) or not references:
        return []
    items = []
    fragments = []
    for reference in references:
        if not isinstance(reference, dict) or reference.get("draft_version_id") != record.draft.id:
            return []
        paragraph = paragraphs.get(reference.get("paragraph_id"))
        if paragraph is None or not (paragraph.source_ranges or paragraph.owner_edited):
            return []
        ranges = _checked_ranges(reference.get("text_ranges"), len(paragraph.text))
        raw_ranges = [span.model_dump() for span in paragraph.source_ranges]
        if ranges is None or (raw_ranges and not _checked_ranges(raw_ranges, len(record.source.source_text))):
            return []
        fragments.append("".join(paragraph.text[r["start_offset"]:r["end_offset"]] for r in ranges))
        item = paragraph_reference(paragraph, record.source, record.draft.id, ranges)
        # 图谱定位保留字符和原声位置，叙述者账号仅用于内部归组。
        item.pop("narrator_user_id", None)
        items.append(item)
    return items if quote in "\n\n".join(fragments) else []


def source_evidence(record, evidence_id, kind, quote, references) -> GraphEvidence:
    return GraphEvidence(
        evidence_id=evidence_id, source_type=kind, excerpt=quote[:200],
        session_id=record.element.session_id, fragment_id=record.source.fragment_id,
        element_id=record.element.id, source_revision_id=record.source.id,
        draft_version_id=record.draft.id, draft_references=references,
    )


def locate_records(records: dict[int, Record]) -> dict[int, LocatedRecord]:
    located = {}
    for element_id, record in records.items():
        element = record.element
        if element.origin == "fallback" or not element.source_quote:
            continue
        paragraphs = {str(paragraph.id): paragraph for paragraph in decode_paragraphs(record.draft)}
        refs = locate_references(record, paragraphs, element.draft_references, element.source_quote)
        if not refs:
            continue
        positions = []
        for ref in refs:
            paragraph = paragraphs[ref["paragraph_id"]]
            raw = [
                [span.start_offset, span.end_offset, record.source.source_text[span.start_offset:span.end_offset]]
                for span in paragraph.source_ranges
            ]
            # 对齐原文位置和叙事上下文，忽略重抽取生成的元素及版本 ID。
            location = raw or [ref["paragraph_id"]]
            positions.append([location, paragraph.text, ref["text_ranges"]])
        anchor = fingerprint([
            record.source.fragment_id, element.element_type, element.source_quote, positions,
            [item.get("narrator_user_id") for item in element.draft_references],
        ])
        located[element_id] = LocatedRecord(
            record, paragraphs, source_evidence(record, f"element:{element_id}", "element", element.source_quote, refs), anchor,
        )
    return located


@dataclass(frozen=True)
class Decision:
    group: MemoryConflictGroup
    element_ids: frozenset[int]
    evidence: tuple[GraphEvidence, ...]


@dataclass
class GraphValidation:
    eligible: set[int]
    identities: list[Decision]
    overrides: dict[int, dict[str, Decision]]


def _review_evidence(answer: MemoryReviewAnswer) -> GraphEvidence:
    return GraphEvidence(
        evidence_id=f"review_answer:{answer.id}", source_type="review_answer",
        answer_id=answer.id, excerpt=answer.answer_text[:200] if answer.answer_text else None,
    )


def _load_resolutions(db, owner_id, groups, events, located):
    resolved = {group.id: group for group in groups.values() if group.status == "resolved"}
    if not resolved:
        return {}, set()
    rows = list(db.scalars(select(MemoryConflictEvidence).where(
        MemoryConflictEvidence.conflict_group_id.in_(resolved),
        MemoryConflictEvidence.is_del.is_(False), MemoryConflictEvidence.status == "active",
    )))
    evidence = {row.id: row for row in rows}
    members = {group_id: set() for group_id in resolved}
    for row in rows:
        if row.element_id is not None:
            members[row.conflict_group_id].add(row.element_id)
    ids = {row.answer_id for row in rows if row.answer_id is not None}
    ids.update(group.resolution_answer_id for group in resolved.values() if group.resolution_answer_id is not None)
    answers = {
        (group_id, answer.id): answer
        for answer, group_id in db.execute(select(
            MemoryReviewAnswer, MemoryReviewQuestion.conflict_group_id,
        ).join(MemoryReviewQuestion, MemoryReviewQuestion.id == MemoryReviewAnswer.question_id).where(
            MemoryReviewAnswer.id.in_(ids), MemoryReviewAnswer.answered_by_user_id == owner_id,
            MemoryReviewAnswer.is_del.is_(False), MemoryReviewQuestion.is_del.is_(False),
        ))
    } if ids else {}
    place_ids = {
        value["entity_id"]
        for group in resolved.values()
        if isinstance(value := group.resolution_payload.get("adopted_value"), dict)
        and "entity_id" in value
    }
    visible_places = set(db.scalars(select(LifeEntity.id).where(
        LifeEntity.id.in_(place_ids), LifeEntity.owner_user_id == owner_id,
        LifeEntity.entity_type == "place", LifeEntity.is_del.is_(False),
    ))) if place_ids else set()
    decisions = {}
    blocked = set()
    for group in resolved.values():
        payload = group.resolution_payload
        affected = (members[group.id] if group.field_key == "event_identity"
                    else set(payload.get("applies_to_element_ids", [])))
        result = _resolve_graph_proof(group, evidence, answers, located)
        place_id = (payload.get("adopted_value") or {}).get("entity_id")
        if (not result or (place_id is not None and place_id not in visible_places)
                or group.evidence_fingerprint != current_group_fingerprint(group, events, groups)):
            blocked.update(affected)
            continue
        decisions[(group.event_key, group.field_key)] = Decision(group, frozenset(affected), result)
    return decisions, blocked


def _resolve_graph_proof(group, evidence, answers, located):
    ids = group.resolution_payload.get("support_evidence_ids", [])
    if not ids:
        return ()
    proofs = []
    adopted = group.resolution_payload.get("adopted_value")
    if group.field_key == "event_identity" and group.resolution_answer_id is None:
        return ()
    supports_adoption = adopted is None
    for evidence_id in ids:
        row = evidence.get(evidence_id)
        if row is None or row.conflict_group_id != group.id:
            return ()
        if isinstance(adopted, dict) and value_key(row.normalized_value) == value_key(adopted):
            supports_adoption = True
        if row.answer_id is not None:
            answer = answers.get((group.id, row.answer_id))
            if answer is None or answer.action != "correct" or answer.result_status != "applied":
                return ()
            proofs.append(_review_evidence(answer))
            continue
        source = located.get(row.element_id)
        if source is None or (source.record.source.id, source.record.draft.id) != (
            row.source_revision_id, row.draft_version_id,
        ):
            return ()
        proofs.append(source.evidence)
    if group.resolution_answer_id is not None:
        answer = answers.get((group.id, group.resolution_answer_id))
        if answer is None or answer.result_status != "applied":
            return ()
        proofs.append(_review_evidence(answer))
    return tuple(proofs) if supports_adoption else ()


def validated_graph(db: Session, owner_id: int, located: dict[int, LocatedRecord]) -> GraphValidation:
    """读取最新依据；新增冲突在 Worker 重建前也可挡住受影响的旧结果。"""
    records = {key: value.record for key, value in located.items()}
    groups = active_conflict_groups(db, owner_id)
    events, reasons, _ = group_claims(db, records, groups)
    decisions, blocked = _load_resolutions(db, owner_id, groups, events, located)
    identities = []
    overrides = {}
    for event_key, event in events.items():
        identity = decisions.get((event_key, "event_identity"))
        if event.clarification:
            if identity is None:
                blocked.update(claim.record.element.id for claim in event.clarification)
                continue
            if identity.group.resolution_payload.get("decision") != "same_event":
                continue
            identities.append(identity)
        for field, claims in event.fields.items():
            decision = decisions.get((event_key, field))
            if decision is not None and "adopted_value" in decision.group.resolution_payload:
                for element_id in decision.element_ids:
                    overrides.setdefault(element_id, {})[field] = decision
            if event.disputed[field]:
                adopted = decision.element_ids if decision is not None else set()
                blocked.update(claim.record.element.id for claim in claims if claim.record.element.id not in adopted)
    eligible = {
        key for key, record in records.items()
        if record.element.verification_status == "verified" and not reasons[key] and key not in blocked
    }
    return GraphValidation(eligible, identities, overrides)
