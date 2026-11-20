"""[新增] 从有效来源生成可核验的时间、地点和事件线索。"""

import calendar
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from itertools import combinations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    MediaAsset,
    MemoryConflictEvidence,
    MemoryConflictGroup,
    MemoryContributor,
    MemoryDraftVersion,
    MemoryElement,
    MemoryEntityCandidate,
    MemoryFragment,
    MemoryReviewAnswer,
    MemoryReviewQuestion,
    MemorySession,
    MemorySourceRevision,
    User,
)
from app.services.draft_provenance import ExtractionDraft
from app.services.memory_contributor_service import MemoryContributorService
from app.services.memory_entities import MemoryEntityService
from app.services.memory_versions import decode_paragraphs, fingerprint

# [删除-旧] DATE_WORD = re.compile(
#     r"(?<!\d)(?P<year>\d{4})(?:年(?:(?P<month>\d{1,2})月(?:(?P<day>\d{1,2})[日号])?)?"
#     r"|-(?P<iso_month>\d{2})-(?P<iso_day>\d{2}))(?!\d)"
# )
# [修改] ASR 可能在数字和年月日之间留空格。
DATE_WORD = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?:[ \t]*年(?:[ \t]*(?P<month>\d{1,2})[ \t]*月"
    r"(?:[ \t]*(?P<day>\d{1,2})[ \t]*[日号])?)?"
    r"|-(?P<iso_month>\d{2})-(?P<iso_day>\d{2}))(?!\d)"
)
DATE_CONNECTOR = re.compile(r"\s*(?:至|到|[—–~～-])\s*")
NEGATED_DATE = re.compile(r"(?:不是|并非|不在|不可能是|排除|不是在)\s*$")
# [新增] 以日期粒度解释明确的单边边界，含糊的「前后」交回待核对。
AFTER_DATE = re.compile(r"\s*(?:结束\s*(?:以后|之后|后)|以后|之后|过后)")
BEFORE_DATE = re.compile(r"\s*(?:以前|之前|前(?![后往去进]))")
SINCE_DATE = re.compile(r"\s*(?:以来|起(?![草身步诉飞火楼]))")
AMBIGUOUS_DATE = re.compile(r"\s*(?:前后|左右|上下|前或后|以前或以后|之后或之前)")
EXPLICIT_TIMESTAMP = re.compile(
    r"(?<!\d)\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})(?!\d)"
)
DEPARTURE = re.compile(r"(?:从|由)\s*$")
DESTINATION = re.compile(r"(?:搬到|搬进|迁往|迁入|迁到|抵达|来到|前往|到了)\s*$")
EVENT_DISTINCTIONS = re.compile(
    r"第[一二三四五六七八九十\d]+次|又一次|再一次|后来|未曾|从未|没有|并未|不是|不再"
)


@dataclass(frozen=True)
class Record:
    element: MemoryElement
    source: MemorySourceRevision
    draft: MemoryDraftVersion


@dataclass(frozen=True)
class EventAnchor:
    context: str
    narrator_id: int | None
    people: tuple[int, ...]
    element_type: str


@dataclass(frozen=True)
class Claim:
    record: Record
    event_key: str
    field: str
    value: dict
    key: str
    strong_identity: bool
    anchor: EventAnchor


# [删除-旧] def current_records(db: Session, owner_id: int, element_id: int | None = None) -> dict[int, Record]:
def current_records(
    db: Session, owner_id: int, element_id: int | None = None, *, session_id: int | None = None,
) -> dict[int, Record]:
    """只返回仍属于该所有者、引用最新文章和可用原文的元素。"""
    newer_source = aliased(MemorySourceRevision)
    newer_draft = aliased(MemoryDraftVersion)
    provider = aliased(User)
    query = (
        select(
            MemoryElement,
            MemorySourceRevision,
            MemoryDraftVersion,
            MemorySession,
            MemoryContributor,
            provider,
        )
        .join(MemorySession, MemorySession.id == MemoryElement.session_id)
        .join(MemoryFragment, MemoryFragment.id == MemoryElement.source_fragment_id)
        .join(MemoryDraftVersion, MemoryDraftVersion.id == MemoryElement.draft_version_id)
        .join(
            MemorySourceRevision, MemorySourceRevision.id == MemoryDraftVersion.source_revision_id
        )
        .join(MemoryContributor, MemoryContributor.id == MemoryFragment.contributor_id)
        .join(provider, provider.id == MemoryContributor.account_user_id)
        .outerjoin(MediaAsset, MediaAsset.id == MemorySourceRevision.source_asset_id)
        .where(
            MemorySession.owner_user_id == owner_id,
            MemorySession.is_del.is_(False),
            MemoryFragment.session_id == MemorySession.id,
            MemoryFragment.is_del.is_(False),
            MemoryElement.is_del.is_(False),
            MemoryElement.review_status != "rejected",
            MemorySourceRevision.fragment_id == MemoryFragment.id,
            MemorySourceRevision.is_del.is_(False),
            MemoryDraftVersion.is_del.is_(False),
            MemoryContributor.session_id == MemorySession.id,
            MemoryContributor.is_del.is_(False),
            provider.is_del.is_(False),
            or_(
                (MemoryContributor.contributor_type == "owner") & (provider.id == owner_id),
                (MemoryContributor.contributor_type == "registered_user")
                & (MemoryContributor.consent_status == "accepted"),
            ),
            or_(MemorySourceRevision.source_asset_id.is_(None), MediaAsset.is_del.is_(False)),
            ~select(newer_source.id)
            .where(
                newer_source.fragment_id == MemorySourceRevision.fragment_id,
                newer_source.id > MemorySourceRevision.id,
                newer_source.is_del.is_(False),
            )
            .exists(),
            ~select(newer_draft.id)
            .where(
                newer_draft.source_revision_id == MemoryDraftVersion.source_revision_id,
                newer_draft.id > MemoryDraftVersion.id,
                newer_draft.is_del.is_(False),
            )
            .exists(),
        )
        .order_by(MemoryElement.id)
    )
    if element_id is not None:
        query = query.where(MemoryElement.id == element_id)
    # [新增] 单会话图谱仅查询指定会话的现行版本。
    if session_id is not None:
        query = query.where(MemorySession.id == session_id)
    contributors = MemoryContributorService()
    return {
        element.id: Record(element, source, draft)
        for element, source, draft, memory, contributor, account in db.execute(query)
        if contributor.contributor_type == "owner"
        or contributors.is_consented_contributor(db, memory, contributor, account)
    }


def time_value(quote: str) -> dict | None:
    # [删除-旧] """把明确说出的日期或年份范围转为闭区间，模糊区间不能升级为精确日期。"""
    """[修改] 用包含端点的日期区间表示时间，未给出的一端保持开放。"""
    matches = list(DATE_WORD.finditer(quote))
    if not matches or any(NEGATED_DATE.search(quote[: item.start()]) for item in matches):
        return None
    intervals = []
    boundaries = set()
    try:
        for item in matches:
            tail = quote[item.end() :]
            if AMBIGUOUS_DATE.match(tail):
                return None
            boundary = (
                "after"
                if AFTER_DATE.match(tail)
                else "before"
                if BEFORE_DATE.match(tail)
                else "since"
                if SINCE_DATE.match(tail)
                else None
            )
            year = int(item["year"])
            month = int(item["month"] or item["iso_month"] or 0)
            day = int(item["day"] or item["iso_day"] or 0)
            if day:
                start = end = date(year, month, day)
                precision = "day"
            elif month:
                start = date(year, month, 1)
                end = date(year, month, calendar.monthrange(year, month)[1])
                precision = "month"
            else:
                start, end = date(year, 1, 1), date(year, 12, 31)
                precision = "year"
            intervals.append((start, end, precision))
            boundaries.add(boundary)
        if boundaries != {None}:
            if len(boundaries) != 1 or len(set(intervals)) != 1:
                return None
            start, end, _ = intervals[0]
            if "before" in boundaries:
                return {
                    "time_start": None,
                    "time_end": (start - timedelta(days=1)).isoformat(),
                    "precision": "open_range",
                }
            if "after" in boundaries:
                start = end + timedelta(days=1)
            return {"time_start": start.isoformat(), "time_end": None, "precision": "open_range"}
    except (ValueError, OverflowError):
        return None
    if len(set(intervals)) == 1:
        start, end, precision = intervals[0]
    elif len(matches) == 2 and DATE_CONNECTOR.fullmatch(
        quote[matches[0].end() : matches[1].start()]
    ):
        start, end, precision = intervals[0][0], intervals[1][1], "range"
        if start > end:
            return None
    else:
        return None
    return {"time_start": start.isoformat(), "time_end": end.isoformat(), "precision": precision}


def place_values(quote: str, refs: list[dict]) -> list[tuple[str, dict]]:
    positions = []
    for ref in refs:
        if ref["entity_type"] != "place" or ref["relation_type"] not in {"location", "mentioned"}:
            continue
        name = ref["display_name"]
        start = quote.find(name)
        if not name or start < 0 or quote.find(name, start + len(name)) >= 0:
            continue
        before = quote[:start]
        if DEPARTURE.search(before):
            role = "departure_location"
        elif DESTINATION.search(before):
            role = "destination_location"
        elif ref["relation_type"] == "location":
            role = "event_location"
        else:
            continue
        positions.append((role, {"entity_id": ref["id"], "name": name}))
    # 单条原话只接受每个角色唯一的地点，防止把途经地或不同角色当作冲突。
    return [
        (role, value)
        for role, value in positions
        if len({other["entity_id"] for key, other in positions if key == role}) == 1
    ]


def value_key(value: dict) -> dict:
    if "entity_id" in value:
        return {"entity_id": value["entity_id"]}
    return value


def compatible(left: dict, right: dict) -> bool:
    # [删除-旧] if "entity_id" in left:
    #     return left["entity_id"] == right.get("entity_id")
    # return left["time_start"] <= right["time_end"] and right["time_start"] <= left["time_end"]
    # [修改] 只有可比较的边界能证明两个时间范围互斥。
    if "entity_id" in left or "entity_id" in right:
        return left.get("entity_id") == right.get("entity_id")
    left_start, left_end = left["time_start"], left["time_end"]
    right_start, right_end = right["time_start"], right["time_end"]
    return not (
        (left_end is not None and right_start is not None and left_end < right_start)
        or (right_end is not None and left_start is not None and right_end < left_start)
    )


def value_label(value: dict) -> str:
    if "entity_id" in value:
        return value["name"]
    start, end = value["time_start"], value["time_end"]
    # [新增] 保留选项的开放方向，不把边界显示成事件发生日。
    if start is None:
        return f"截至 {end}" if end is not None else "时间待核对"
    if end is None:
        return f"{start} 起"
    if value["precision"] == "year":
        return f"{start[:4]} 年"
    if value["precision"] == "month":
        return f"{start[:4]} 年 {int(start[5:7])} 月"
    return start if start == end else f"{start} 至 {end}"


def _time_source_mismatch(element: MemoryElement, source_time: dict, quote: str) -> bool:
    """[新增] 抽取范围要覆盖原话允许的日期，具体时刻还需要可核对的时分秒和时区。"""
    for extracted, bound, lower in (
        (element.time_start, source_time["time_start"], True),
        (element.time_end, source_time["time_end"], False),
    ):
        if extracted is None:
            continue
        if bound is None:
            return True
        if (lower and extracted.isoformat() > bound) or (
            not lower and extracted.isoformat() < bound
        ):
            return True
    if element.exact_at is None:
        return False
    matches = EXPLICIT_TIMESTAMP.findall(quote)
    if (
        len(set(matches)) != 1
        or element.exact_at.tzinfo is None
        or source_time["precision"] != "day"
    ):
        return True
    try:
        supported = datetime.fromisoformat(matches[0].replace("Z", "+00:00"))
    except ValueError:
        return True
    return (
        supported != element.exact_at or supported.date().isoformat() != source_time["time_start"]
    )


def _context_without_dates(quote: str) -> str:
    """[新增] 年份和它的已识别边界属于待核对的取值，归组时一起排除。"""
    parts = []
    end = 0
    for item in DATE_WORD.finditer(quote):
        between = quote[end : item.start()]
        if end == 0 or not DATE_CONNECTOR.fullmatch(between):
            parts.append(between)
        end = item.end()
        tail = quote[end:]
        if not AMBIGUOUS_DATE.match(tail):
            boundary = AFTER_DATE.match(tail) or BEFORE_DATE.match(tail) or SINCE_DATE.match(tail)
            if boundary is not None:
                end += boundary.end()
    parts.append(quote[end:])
    return "".join(parts)


def collect_claims(
    db: Session, record: Record, pending: set[int], *, visible_entities: list[dict] | None = None,
) -> tuple[list[Claim], str | None]:
    row = record.element
    if row.origin == "fallback":
        return [], "fallback"
    quote = row.source_quote or ""
    refs = row.draft_references or []
    if (
        not quote
        or not refs
        or ExtractionDraft(decode_paragraphs(record.draft)).text.count(quote) != 1
    ):
        return [], "source_unconfirmed"
    if row.id in pending:
        return [], "entity_unconfirmed"
    # [删除-旧] entities = MemoryEntityService.element_refs(db, row.id)
    # [新增] 单会话候选由当前会话引句提供，避免使用跨会话归一后的身份。
    entities = visible_entities if visible_entities is not None else MemoryEntityService.element_refs(db, row.id)
    locations = place_values(quote, entities)
    values = list(locations)
    parsed_time = time_value(quote)
    reason = None
    if parsed_time is not None:
        values.append(("event_time", parsed_time))
        # [新增] 引句仍可作证，抽错或推断过细的元素保持待核对。
        if _time_source_mismatch(row, parsed_time, quote):
            reason = "time_source_mismatch"
    if (
        any((row.exact_at, row.time_start, row.time_end, row.time_description))
        and parsed_time is None
    ):
        return [], "time_unconfirmed"
    if row.location_description and not locations:
        return [], "entity_unconfirmed"
    if not values:
        return [], "fact_unconfirmed"
    people = sorted({ref["id"] for ref in entities if ref["entity_type"] == "person"})
    speakers = {ref.get("narrator_user_id") for ref in refs}
    narrator = next(iter(speakers)) if len(speakers) == 1 else None
    # [删除-旧] context = DATE_WORD.sub("", quote)
    context = _context_without_dates(quote)
    for _, place in sorted(locations, key=lambda pair: len(pair[1]["name"]), reverse=True):
        context = context.replace(place["name"], "")
    context = "".join(char for char in unicodedata.normalize("NFKC", context) if char.isalnum())
    event_key = (
        fingerprint([people, narrator, context])
        if len(context) >= 2
        else fingerprint(
            [
                record.source.fragment_id,
                row.sequence_no,
            ]
        )
    )
    strong_identity = len(context) >= 12 and bool(people or narrator)
    anchor = EventAnchor(context, narrator, tuple(people), row.element_type)
    claims = [
        Claim(
            record,
            event_key,
            field,
            value,
            fingerprint([record.source.fragment_id, narrator, field, value_key(value), context]),
            strong_identity,
            anchor,
        )
        for field, value in values
    ]
    # [删除-旧] return claims, None
    return claims, reason


def pending_entities(db: Session, element_ids: list[int]) -> set[int]:
    if not element_ids:
        return set()
    return set(
        db.scalars(
            select(MemoryEntityCandidate.element_id).where(
                MemoryEntityCandidate.element_id.in_(element_ids),
                MemoryEntityCandidate.is_del.is_(False),
                MemoryEntityCandidate.status == "pending",
            )
        )
    )


def active_conflict_groups(
    db: Session, owner_id: int
) -> dict[tuple[str, str], MemoryConflictGroup]:
    return {
        (row.event_key, row.field_key): row
        for row in db.scalars(
            select(MemoryConflictGroup).where(
                MemoryConflictGroup.owner_user_id == owner_id,
                MemoryConflictGroup.is_del.is_(False),
                MemoryConflictGroup.status != "superseded",
            )
        )
    }


def _conflicting_pairs(claims: list[Claim]) -> set[tuple[int, int]]:
    return {
        tuple(sorted((left.record.element.id, right.record.element.id)))
        for left, right in combinations(claims, 2)
        if left.record.element.id != right.record.element.id
        and not compatible(left.value, right.value)
    }


def _related_anchors(left: EventAnchor, right: EventAnchor) -> bool:
    """[新增] 同一叙述主体及共享的独立上下文只构成提问线索。"""
    if left.element_type != right.element_type or not (
        (left.narrator_id is not None and left.narrator_id == right.narrator_id)
        or set(left.people).intersection(right.people)
    ):
        return False
    if min(len(left.context), len(right.context)) < 12 or EVENT_DISTINCTIONS.findall(
        left.context
    ) != EVENT_DISTINCTIONS.findall(right.context):
        return False
    match = SequenceMatcher(None, left.context, right.context, autojunk=False)
    return match.ratio() >= 0.85 and match.find_longest_match().size >= 8


def _prior_event_keys(
    db: Session, groups: dict[tuple[str, str], MemoryConflictGroup]
) -> dict[str, set[str]]:
    if not groups:
        return {}
    keys = {group.id: group.event_key for group in groups.values()}
    known = defaultdict(set)
    for group_id, evidence_key in db.execute(
        select(
            MemoryConflictEvidence.conflict_group_id,
            MemoryConflictEvidence.evidence_key,
        ).where(
            MemoryConflictEvidence.conflict_group_id.in_(keys),
            MemoryConflictEvidence.element_id.is_not(None),
            MemoryConflictEvidence.is_del.is_(False),
            MemoryConflictEvidence.status == "active",
        )
    ):
        known[evidence_key].add(keys[group_id])
    return known


@dataclass(frozen=True)
class EventClaims:
    fields: dict[str, list[Claim]]
    disputed: dict[str, set[tuple[int, int]]]
    clarification: list[Claim]

    def for_group(self, field: str) -> list[Claim]:
        return self.clarification if field == "event_identity" else self.fields.get(field, [])


def group_claims(
    db: Session,
    records: dict[int, Record],
    groups: dict[tuple[str, str], MemoryConflictGroup],
    *, visible_entities: dict[int, list[dict]] | None = None,
) -> tuple[dict[str, EventClaims], dict[int, str | None], set[int]]:
    """[新增] 同一组的各描述直接相似才加入，按当前证据尽量保留原归组键。"""
    # [删除-旧] pending = pending_entities(db, list(records))
    pending = pending_entities(db, list(records)) if visible_entities is None else set()
    reasons = {}
    exact = defaultdict(list)
    for element_id, record in records.items():
        # [删除-旧] claims, reasons[element_id] = collect_claims(db, record, pending)
        local_refs = visible_entities.get(element_id, []) if visible_entities is not None else None
        claims, reasons[element_id] = collect_claims(db, record, pending, visible_entities=local_refs)
        for claim in claims:
            exact[claim.event_key].append(claim)

    known = _prior_event_keys(db, groups)
    grouped = defaultdict(list)
    prior = {}
    active_keys = {key for key, _ in groups}
    for key, claims in exact.items():
        possible = {event for claim in claims for event in known.get(claim.key, ())}
        if key in active_keys:
            prior[key] = key
        elif len(possible) == 1:
            prior[key] = next(iter(possible))

    # 先放已有组的描述，再处理新描述，避免加入一条新线索改变旧题所属组。
    for key in sorted(
        exact, key=lambda item: (item not in prior, exact[item][0].record.element.id)
    ):
        anchor = exact[key][0].anchor
        matching = [
            event
            for event, members in grouped.items()
            if all(
                other == key or _related_anchors(anchor, exact[other][0].anchor)
                for other in members
            )
        ]
        pinned = prior.get(key)
        if pinned is not None and (pinned not in grouped or pinned in matching):
            target = pinned
        elif pinned is None and len(matching) == 1:
            target = matching[0]
        else:
            target = key if key not in grouped else fingerprint(["separate_event", key])
            # 能对应多个组时先挂起该条，不因桥接描述合并已有事件。
            if len(matching) > 1:
                for claim in exact[key]:
                    element_id = claim.record.element.id
                    reasons[element_id] = reasons[element_id] or "event_unconfirmed"
        grouped[target].append(key)

    events = {}
    for key, members in grouped.items():
        fields = defaultdict(list)
        for member in members:
            for claim in exact[member]:
                fields[claim.field].append(claim)
        disputed = {field: _conflicting_pairs(claims) for field, claims in fields.items()}
        uncertain = [
            claim for field, claims in fields.items() if disputed[field] for claim in claims
        ]
        clarification = (
            uncertain
            if len(members) > 1 or any(not claim.strong_identity for claim in uncertain)
            else []
        )
        events[key] = EventClaims(dict(fields), disputed, clarification)
    return events, reasons, pending


def claims_fingerprint(claims: list[Claim]) -> str:
    return fingerprint(sorted({claim.key for claim in claims}))


def current_group_fingerprint(
    group: MemoryConflictGroup,
    events: dict[str, EventClaims],
    groups: dict[tuple[str, str], MemoryConflictGroup],
) -> str | None:
    """[新增] 旧题必须对应现行证据，而且事件归属已足以核对具体属性。"""
    event = events.get(group.event_key)
    if event is None or groups.get((group.event_key, group.field_key)) is None:
        return None
    if group.field_key != "event_identity" and event.clarification:
        identity = groups.get((group.event_key, "event_identity"))
        if (
            identity is None
            or identity.status != "resolved"
            or identity.resolution_payload.get("decision") != "same_event"
            or identity.evidence_fingerprint != claims_fingerprint(event.clarification)
        ):
            return None
    evidence = event.for_group(group.field_key)
    return claims_fingerprint(evidence) if evidence else None


def _blocked_decisions(db: Session, owner_id: int, records: dict[int, Record]) -> set[int]:
    """找出裁定所依赖的来源已失效的元素。"""
    groups = list(
        db.scalars(
            select(MemoryConflictGroup).where(
                MemoryConflictGroup.owner_user_id == owner_id,
                MemoryConflictGroup.is_del.is_(False),
                MemoryConflictGroup.status == "resolved",
            )
        )
    )
    if not groups:
        return set()
    evidence = {}
    for item, answer, question in db.execute(
        select(
            MemoryConflictEvidence,
            MemoryReviewAnswer,
            MemoryReviewQuestion,
        )
        .outerjoin(MemoryReviewAnswer, MemoryReviewAnswer.id == MemoryConflictEvidence.answer_id)
        .outerjoin(MemoryReviewQuestion, MemoryReviewQuestion.id == MemoryReviewAnswer.question_id)
        .where(
            MemoryConflictEvidence.conflict_group_id.in_([group.id for group in groups]),
            MemoryConflictEvidence.status == "active",
            MemoryConflictEvidence.is_del.is_(False),
        )
    ):
        record = records.get(item.element_id)
        valid = record is not None and (item.source_revision_id, item.draft_version_id) == (
            record.source.id,
            record.draft.id,
        )
        if item.answer_id is not None:
            valid = (
                answer is not None
                and not answer.is_del
                and answer.answered_by_user_id == owner_id
                and question is not None
                and not question.is_del
                and question.conflict_group_id == item.conflict_group_id
            )
        evidence[item.id] = (item, valid)
    blocked = set()
    for group in groups:
        conclusion = group.resolution_payload
        supports = conclusion.get("support_evidence_ids", [])
        affected = set(conclusion.get("applies_to_element_ids", []))
        if group.field_key == "event_identity":
            affected.update(
                item.element_id
                for item, _ in evidence.values()
                if item.conflict_group_id == group.id and item.element_id is not None
            )
        if not supports or not all(
            item in evidence
            and evidence[item][1]
            and evidence[item][0].conflict_group_id == group.id
            for item in supports
        ):
            blocked.update(affected)
    return blocked


def invalidated_verification_ids(db: Session, owner_id: int) -> set[int]:
    """素材失效到 Worker 复验之间，读取先挂起它所支撑的核验结论。"""
    verified = set(
        db.scalars(
            select(MemoryElement.id)
            .join(
                MemorySession,
                MemorySession.id == MemoryElement.session_id,
            )
            .where(
                MemorySession.owner_user_id == owner_id,
                MemoryElement.is_del.is_(False),
                MemoryElement.verification_status == "verified",
            )
        )
    )
    if not verified:
        return set()
    records = current_records(db, owner_id)
    blocked = _blocked_decisions(db, owner_id, records)
    return (verified - records.keys()) | (verified & blocked)
