"""[新增] 在复验事务内同步稳定的事件及其当前成员。"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MemoryGraphEvent, MemoryGraphEventElement, MemoryValidationJob
from app.services.memory_graph_evidence import Decision, locate_records, validated_graph
from app.services.memory_validation_rules import Record


def publish_graph(db: Session, job: MemoryValidationJob, records: dict[int, Record]) -> None:
    """所有者锁由复验任务持有，事件、成员和任务结果在同一事务发布。"""
    located = locate_records(records)
    current = validated_graph(db, job.owner_user_id, located)
    events = {row.anchor_key: row for row in db.scalars(select(MemoryGraphEvent).where(
        MemoryGraphEvent.owner_user_id == job.owner_user_id,
    ))}
    now = datetime.now(timezone.utc)

    def event_for(anchor: str) -> MemoryGraphEvent | None:
        event = events.get(anchor)
        if event is None:
            event = MemoryGraphEvent(
                owner_user_id=job.owner_user_id, anchor_key=anchor, status="published",
                last_validation_job_id=job.id,
            )
            db.add(event)
            db.flush()
            events[anchor] = event
        return None if event.is_del else event

    desired: dict[int, tuple[MemoryGraphEvent, Decision | None]] = {}
    remaining = current.eligible.copy()
    for decision in sorted(current.identities, key=lambda item: item.group.id):
        eligible = remaining & decision.element_ids
        if not eligible:
            continue
        anchors = {located[key].anchor_key for key in decision.element_ids if key in located}
        candidates = [events[key] for key in anchors if key in events and not events[key].is_del]
        if candidates:
            event = min(candidates, key=lambda item: item.id)
        else:
            event = event_for(located[min(eligible)].anchor_key)
        if event is None:
            continue
        for key in eligible:
            desired[key] = (event, decision)
        remaining -= eligible
    for key in sorted(remaining):
        event = event_for(located[key].anchor_key)
        if event is not None:
            desired[key] = (event, None)

    wanted = {}
    for key, (event, decision) in desired.items():
        proof = located[key]
        group = decision.group if decision else None
        wanted[key] = {
            "event_id": event.id, "element_id": key, "source_revision_id": proof.record.source.id,
            "draft_version_id": proof.record.draft.id, "element_anchor_key": proof.anchor_key,
            "membership_basis": "confirmed_same_event" if group else "source",
            "identity_group_id": group.id if group else None,
            "identity_answer_id": group.resolution_answer_id if group else None,
            "identity_evidence_fingerprint": group.evidence_fingerprint if group else None,
        }
    changed = set()
    active = db.scalars(select(MemoryGraphEventElement).join(MemoryGraphEvent).where(
        MemoryGraphEvent.owner_user_id == job.owner_user_id,
        MemoryGraphEventElement.status == "active", MemoryGraphEventElement.is_del.is_(False),
    ))
    for member in active:
        values = wanted.get(member.element_id)
        if values and all(getattr(member, field) == value for field, value in values.items()):
            wanted.pop(member.element_id)
        else:
            member.status = "superseded"
            member.updated_at = now
            changed.add(member.event_id)
    # 活跃成员唯一索引要求旧行先退役，历史证明继续保留。
    db.flush()
    for values in wanted.values():
        db.add(MemoryGraphEventElement(**values, published_by_job_id=job.id, status="active"))
        changed.add(values["event_id"])
    visible = {event.id for event, _ in desired.values()}
    for event in events.values():
        if event.is_del:
            continue
        status = "published" if event.id in visible else "retired"
        if event.status != status or event.id in changed:
            event.status = status
            event.last_validation_job_id = job.id
            event.updated_at = now
