"""[新增] 按已授权范围组装当前图谱节点及证据。"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    LifeEntity, LifeEntityAlias, MemoryElementEntity, MemoryEntityCandidate, MemoryEntityEvidence,
    MemoryGraphEvent, MemoryGraphEventElement, MemoryValidationJob,
)
from app.schemas.memory_graph import EntityNode, EventNode, GraphEvidence, GraphNode, GraphPlace, GraphTime
from app.services.memory_graph_evidence import (
    Decision, LocatedRecord, locate_references, source_evidence, validated_graph,
)
from app.services.memory_validation_rules import (
    EXPLICIT_TIMESTAMP, _context_without_dates, _time_source_mismatch, group_claims, place_values, time_value,
)

PLACE_ROLES = {"location", "departure_location", "destination_location"}
ENTITY_TYPES = {"person", "place", "organization", "time_period"}
EDGE_ROLES = {"subject", "participant", "mentioned", "time_period"} | PLACE_ROLES
EdgeKey = tuple[str, str, str]
Proofs = dict[str, GraphEvidence]


@dataclass
class GraphView:
    session_id: int | None
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    proofs: dict[str, Proofs] = field(default_factory=lambda: defaultdict(dict))
    time_proofs: dict[str, Proofs] = field(default_factory=dict)
    edges: dict[EdgeKey, Proofs] = field(default_factory=lambda: defaultdict(dict))

    @property
    def scope(self) -> dict:
        return {"view_scope": "memoir" if self.session_id is None else "session", "session_id": self.session_id}


@dataclass(frozen=True)
class Mention:
    node_id: str
    name: str
    alias: str
    entity_type: str
    role: str
    proofs: tuple[GraphEvidence, ...]


def _role(quote: str, name: str, kind: str, role: str) -> str:
    if kind == "place":
        refs = [{"id": 1, "entity_type": kind, "display_name": name, "relation_type": role}]
        places = place_values(quote, refs)
        if places:
            result = places[0][0]
            return "location" if result == "event_location" else result
        # 仅出现名称时展示为提及，不推定具体地点角色。
        return "mentioned"
    return role if role in EDGE_ROLES - PLACE_ROLES else "mentioned"


def _owner_mentions(db: Session, owner_id: int, located: dict[int, LocatedRecord]) -> dict[int, list[Mention]]:
    if not located:
        return {}
    query = select(MemoryElementEntity, LifeEntity, MemoryEntityEvidence, LifeEntityAlias).join(
        LifeEntity, LifeEntity.id == MemoryElementEntity.entity_id,
    ).join(MemoryEntityEvidence, MemoryEntityEvidence.element_entity_id == MemoryElementEntity.id).join(
        LifeEntityAlias, LifeEntityAlias.id == MemoryEntityEvidence.alias_id,
    ).where(
        MemoryElementEntity.memory_element_id.in_(located), LifeEntity.owner_user_id == owner_id,
        MemoryElementEntity.is_del.is_(False), LifeEntity.is_del.is_(False),
        MemoryEntityEvidence.is_del.is_(False), LifeEntityAlias.is_del.is_(False),
        LifeEntityAlias.entity_id == LifeEntity.id,
    ).order_by(MemoryEntityEvidence.id)
    mentions = defaultdict(list)
    for link, entity, evidence, alias in db.execute(query):
        if entity.entity_type not in ENTITY_TYPES:
            continue
        source = located[link.memory_element_id]
        refs = locate_references(source.record, source.paragraphs, evidence.evidence.get("draft_references"), alias.alias_name)
        if not refs:
            continue
        proof = source_evidence(source.record, f"entity_evidence:{evidence.id}", "entity_mention", alias.alias_name, refs)
        role = _role(source.record.element.source_quote, alias.alias_name, entity.entity_type, link.relation_type)
        mentions[link.memory_element_id].append(Mention(
            f"entity:{entity.id}", entity.canonical_name, alias.alias_name, entity.entity_type, role, (proof,),
        ))
    return mentions


def _session_mentions(db: Session, located: dict[int, LocatedRecord]) -> tuple[dict, dict]:
    if not located:
        return {}, {}
    candidates = db.scalars(select(MemoryEntityCandidate).where(
        MemoryEntityCandidate.element_id.in_(located), MemoryEntityCandidate.is_del.is_(False),
    ).order_by(MemoryEntityCandidate.id))
    mentions, refs = defaultdict(list), defaultdict(list)
    labels = {}
    for row in candidates:
        if row.entity_type not in ENTITY_TYPES:
            continue
        source = located[row.element_id]
        references = locate_references(source.record, source.paragraphs, row.evidence.get("draft_references"), row.mention_text)
        if not references:
            continue
        node_id = f"mention:{row.id}"
        proof = source_evidence(source.record, node_id, "entity_mention", row.mention_text, references)
        role = _role(source.record.element.source_quote, row.mention_text, row.entity_type, row.relation_type)
        mentions[row.element_id].append(Mention(
            node_id, row.mention_text, row.mention_text, row.entity_type, role, (proof,),
        ))
        # 只比较本会话可见称谓，内部比较键不作为节点标识或身份结论返回。
        label_id = labels.setdefault((row.entity_type, row.mention_text.casefold()), len(labels) + 1)
        refs[row.element_id].append({
            "id": label_id, "entity_type": row.entity_type, "display_name": row.mention_text,
            "relation_type": row.relation_type,
        })
    return mentions, refs


def _source_time(source: LocatedRecord) -> GraphTime:
    row = source.record.element
    declared = time_value(row.source_quote)
    if declared is None or _time_source_mismatch(row, declared, row.source_quote):
        return GraphTime()
    return GraphTime(
        start_date=declared["time_start"], end_date=declared["time_end"], exact_at=row.exact_at,
        precision="instant" if row.exact_at is not None else declared["precision"], origin="source",
    )


def _time_contains(outer: GraphTime, inner: GraphTime) -> bool:
    if outer.exact_at is not None and outer.exact_at != inner.exact_at:
        return False
    return (
        (outer.start_date is None or (inner.start_date is not None and outer.start_date <= inner.start_date))
        and (outer.end_date is None or (inner.end_date is not None and outer.end_date >= inner.end_date))
    )


def _select_time(values: list[tuple[GraphTime, tuple[GraphEvidence, ...]]]) -> tuple[GraphTime, Proofs]:
    # 裁定已经在证据层核验，展示精度按裁定口径保留。
    matching = [item for item in values if item[0].origin == "review_answer"] or values
    for selected, evidence in matching:
        if all(_time_contains(other, selected) for other, _ in matching):
            return selected, {item.evidence_id: item for item in evidence}
    return GraphTime(), {}


def _title(source: LocatedRecord, replaced_places: set[str]) -> str:
    quote = _context_without_dates(EXPLICIT_TIMESTAMP.sub("", source.record.element.source_quote))
    for old_place in sorted(replaced_places, key=len, reverse=True):
        quote = quote.replace(old_place, "")
    return " ".join(quote.split()).strip("，。；、,. ")[:100] or "一段经历"


def _add_mention(view: GraphView, event: str, mention: Mention) -> None:
    existing = view.nodes.get(mention.node_id)
    if existing is None:
        existing = EntityNode(
            node_id=mention.node_id, entity_type=mention.entity_type,
            display_name=mention.name, related_event_count=0,
        )
        view.nodes[mention.node_id] = existing
    if mention.alias not in existing.aliases:
        existing.aliases.append(mention.alias)
    edge = (event, mention.node_id, mention.role)
    for evidence in mention.proofs:
        view.edges[edge][evidence.evidence_id] = evidence
        view.proofs[event][evidence.evidence_id] = evidence
        view.proofs[mention.node_id][evidence.evidence_id] = evidence


def _add_event(view, node_id, members, located, mentions, overrides, withheld=None):
    places = {}
    dates = []
    titles = {}
    withheld = withheld or {}
    for element_id, identity in members:
        source = located[element_id]
        proof = source.evidence
        view.proofs[node_id][proof.evidence_id] = proof
        if identity:
            view.proofs[node_id].update((item.evidence_id, item) for item in identity.evidence)
        local_mentions = list(mentions.get(element_id, ()))
        replaced = set()
        decisions = overrides.get(element_id, {})
        excluded = withheld.get(element_id, set())
        when = _source_time(source) if "event_time" not in excluded else GraphTime()
        time_proof = (proof,)
        for role, decision in decisions.items():
            value = decision.group.resolution_payload["adopted_value"]
            view.proofs[node_id].update((item.evidence_id, item) for item in decision.evidence)
            if role == "event_time":
                when = GraphTime(
                    start_date=value["time_start"], end_date=value["time_end"], precision=value["precision"],
                    origin="review_answer" if decision.group.resolution_answer_id else "source",
                )
                time_proof = decision.evidence
                continue
            actual_role = "location" if role == "event_location" else role
            previous = [item for item in local_mentions if item.role == actual_role]
            replaced.update(item.alias for item in previous if item.node_id != f"entity:{value['entity_id']}")
            local_mentions = [item for item in local_mentions if item.role != actual_role]
            local_mentions.append(Mention(
                f"entity:{value['entity_id']}", value["name"], value["name"], "place", actual_role, decision.evidence,
            ))
        if when.precision != "unknown":
            dates.append((when, time_proof))
        for item in local_mentions:
            property_key = "event_location" if item.role == "location" else item.role
            if property_key in excluded:
                replaced.add(item.alias)
                continue
            _add_mention(view, node_id, item)
            if item.role in PLACE_ROLES:
                places[(item.node_id, item.role)] = GraphPlace(node_id=item.node_id, display_name=item.name, role=item.role)
        titles[element_id] = _title(source, replaced)
    when, time_proof = _select_time(dates)
    view.time_proofs[node_id] = time_proof
    primary = members[0][0]
    view.nodes[node_id] = EventNode(
        node_id=node_id, element_type=located[primary].record.element.element_type, title=titles[primary],
        time=when, evidence_count=len(view.proofs[node_id]), places=list(places.values()),
    )


def _finish_entities(view: GraphView) -> GraphView:
    events = defaultdict(set)
    for source, target, _ in view.edges:
        events[target].add(source)
    for key, node in view.nodes.items():
        if isinstance(node, EntityNode):
            node.related_event_count = len(events[key])
            node.aliases.sort()
    return view


def memoir_view(db: Session, owner_id: int, located: dict[int, LocatedRecord]) -> GraphView:
    view = GraphView(None)
    current = validated_graph(db, owner_id, located)
    if not current.eligible:
        return view
    event_job = MemoryValidationJob.__table__.alias("graph_event_job")
    member_job = MemoryValidationJob.__table__.alias("graph_member_job")
    query = select(MemoryGraphEventElement).join(MemoryGraphEvent).join(
        event_job, event_job.c.id == MemoryGraphEvent.last_validation_job_id,
    ).join(member_job, member_job.c.id == MemoryGraphEventElement.published_by_job_id).where(
        MemoryGraphEvent.owner_user_id == owner_id, MemoryGraphEvent.is_del.is_(False),
        MemoryGraphEvent.status == "published", MemoryGraphEventElement.is_del.is_(False),
        MemoryGraphEventElement.status == "active", MemoryGraphEventElement.element_id.in_(current.eligible),
        event_job.c.owner_user_id == owner_id, event_job.c.status == "succeeded", event_job.c.is_del.is_(False),
        member_job.c.owner_user_id == owner_id, member_job.c.status == "succeeded", member_job.c.is_del.is_(False),
    ).order_by(MemoryGraphEventElement.event_id, MemoryGraphEventElement.element_id)
    identities = {decision.group.id: decision for decision in current.identities}
    grouped = defaultdict(list)
    for member in db.scalars(query):
        source = located[member.element_id]
        record = source.record
        if (member.source_revision_id, member.draft_version_id) != (record.source.id, record.draft.id):
            continue
        if member.element_anchor_key != source.anchor_key:
            continue
        decision = identities.get(member.identity_group_id)
        if member.membership_basis == "confirmed_same_event" and (
            decision is None or member.element_id not in decision.element_ids
            or member.identity_answer_id != decision.group.resolution_answer_id
            or member.identity_evidence_fingerprint != decision.group.evidence_fingerprint
        ):
            continue
        grouped[member.event_id].append((member.element_id, decision))
    shown = {key: located[key] for members in grouped.values() for key, _ in members}
    mentions = _owner_mentions(db, owner_id, shown)
    for event_id, members in grouped.items():
        _add_event(view, f"event:{event_id}", members, shown, mentions, current.overrides)
    return _finish_entities(view)


def session_view(db: Session, session_id: int, located: dict[int, LocatedRecord]) -> GraphView:
    view = GraphView(session_id)
    mentions, refs = _session_mentions(db, located)
    events, _, _ = group_claims(
        db, {key: item.record for key, item in located.items()}, {}, visible_entities=refs,
    )
    withheld = defaultdict(set)
    for event in events.values():
        for field, claims in event.fields.items():
            if event.disputed[field]:
                for claim in claims:
                    withheld[claim.record.element.id].add(field)
    for element_id in located:
        _add_event(view, f"element:{element_id}", [(element_id, None)], located, mentions, {}, withheld)
    return _finish_entities(view)
