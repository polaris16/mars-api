"""[新增] 根据当前证据发布核验、冲突及问答状态。"""

# [删除-旧] from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import (
    MemoryConflict,
    MemoryConflictEvidence,
    MemoryConflictGroup,
    MemoryElement,
    MemoryReviewAnswer,
    MemoryReviewQuestion,
    MemorySession,
    MemoryValidationJob,
)
from app.services import memory_validation_queue as queue
from app.services.memory_validation_rules import (
    Claim,
    # [删除-旧] collect_claims,
    active_conflict_groups,
    claims_fingerprint,
    compatible,
    current_records,
    # [删除-旧] pending_entities,
    group_claims,
    time_value,
    value_key,
    value_label,
)
from app.services.memory_versions import fingerprint

FIELD_LABELS = {
    "event_time": "时间",
    "event_location": "地点",
    "departure_location": "出发地",
    "destination_location": "目的地",
}


def _current_evidence(db: Session, group_id: int) -> dict[str, MemoryConflictEvidence]:
    rows = db.scalars(
        select(MemoryConflictEvidence).where(
            MemoryConflictEvidence.conflict_group_id == group_id,
            MemoryConflictEvidence.is_del.is_(False),
            MemoryConflictEvidence.status == "active",
        )
    )
    return {row.evidence_key: row for row in rows}


def _sync_evidence(db: Session, group: MemoryConflictGroup, claims: list[Claim]) -> list:
    active = _current_evidence(db, group.id)
    keep = set()
    missing = {}
    for claim in claims:
        element, source, draft = claim.record.element, claim.record.source, claim.record.draft
        old = active.get(claim.key)
        if old is not None and (old.element_id, old.source_revision_id, old.draft_version_id) == (
            element.id,
            source.id,
            draft.id,
        ):
            keep.add(claim.key)
        else:
            missing.setdefault(claim.key, claim)
    for key, old in active.items():
        if old.answer_id is None and key not in keep:
            old.status = "superseded"
            old.updated_at = datetime.now(timezone.utc)
    db.flush()
    for key, claim in missing.items():
        if key in keep:
            continue
        element, source, draft = claim.record.element, claim.record.source, claim.record.draft
        row = MemoryConflictEvidence(
            conflict_group_id=group.id,
            element_id=element.id,
            source_revision_id=source.id,
            draft_version_id=draft.id,
            evidence_key=key,
            normalized_value=claim.value,
            evidence_payload={"draft_references": element.draft_references},
        )
        db.add(row)
        active[key] = row
    db.flush()
    return sorted((active[key] for key in keep | missing.keys()), key=lambda row: row.id)


def _questions(db: Session, group_id: int) -> list[MemoryReviewQuestion]:
    return list(
        db.scalars(
            select(MemoryReviewQuestion)
            .where(
                MemoryReviewQuestion.conflict_group_id == group_id,
            )
            .order_by(MemoryReviewQuestion.revision.desc())
        )
    )


def _close_question(question: MemoryReviewQuestion | None) -> None:
    if question is not None and question.status == "pending":
        question.status = "superseded"
        question.updated_at = datetime.now(timezone.utc)


def _answer_history(
    db: Session, group_id: int
) -> list[tuple[MemoryReviewQuestion, MemoryReviewAnswer]]:
    return list(
        db.execute(
            select(MemoryReviewQuestion, MemoryReviewAnswer)
            .join(
                MemoryReviewAnswer,
                MemoryReviewAnswer.question_id == MemoryReviewQuestion.id,
            )
            .where(
                MemoryReviewQuestion.conflict_group_id == group_id,
                MemoryReviewAnswer.is_del.is_(False),
                MemoryReviewQuestion.is_del.is_(False),
            )
            .order_by(MemoryReviewQuestion.revision.desc())
        )
    )


def _set_answer(answer: MemoryReviewAnswer, status: str, **details) -> None:
    answer.result_status = status
    answer.result_payload = details
    answer.updated_at = datetime.now(timezone.utc)


def _options(evidence: list[MemoryConflictEvidence]) -> list[dict]:
    unique = {}
    for item in evidence:
        if not _selectable(item.normalized_value, evidence):
            continue
        key = fingerprint(value_key(item.normalized_value))
        option = unique.setdefault(
            key,
            {
                "id": f"option-{len(unique) + 1}",
                "label": value_label(item.normalized_value),
                "evidence_ids": [],
                "value": item.normalized_value,
            },
        )
        option["evidence_ids"].append(item.id)
    return list(unique.values())


def _selectable(value: dict, evidence: list[MemoryConflictEvidence]) -> bool:
    """一个过宽区间可能同时覆盖互斥的说法，不能据此宣告冲突解决。"""
    matches = [
        item.normalized_value for item in evidence if compatible(value, item.normalized_value)
    ]
    return all(compatible(left, right) for left, right in combinations(matches, 2))


def _issue_question(
    db: Session,
    job: MemoryValidationJob,
    group: MemoryConflictGroup,
    history: list[MemoryReviewQuestion],
    kind: str,
    text: str,
    options: list[dict],
    evidence: list[MemoryConflictEvidence],
) -> MemoryReviewQuestion:
    previous = next((row for row in history if row.status == "pending" and not row.is_del), None)
    evidence_ids = sorted(row.id for row in evidence)
    if previous is not None and (
        previous.kind,
        previous.question_text,
        previous.options,
        previous.evidence_ids,
        previous.evidence_fingerprint,
    ) == (kind, text, options, evidence_ids, group.evidence_fingerprint):
        return previous
    _close_question(previous)
    db.flush()
    question = MemoryReviewQuestion(
        conflict_group_id=group.id,
        issued_by_job_id=job.id,
        revision=(history[0].revision if history else 0) + 1,
        kind=kind,
        question_text=text,
        options=options,
        evidence_ids=evidence_ids,
        evidence_fingerprint=group.evidence_fingerprint,
    )
    db.add(question)
    db.flush()
    return question


def _question_text(field: str, follow_up: bool) -> str:
    label = FIELD_LABELS[field]
    if follow_up:
        return f"刚才的补充和已有记录还对不上。您愿意再核对一下这件事的{label}吗？"
    return f"这件事的{label}有几种说法。您更记得是哪一个？"


def _apply_value(group, answer, selected, evidence) -> set[int]:
    matching = [row for row in evidence if compatible(selected, row.normalized_value)]
    accepted = {row.element_id for row in matching if row.element_id is not None}
    if not accepted and matching:
        # 新值来自已确认的补述，保留原始元素作为事件锚点。
        accepted.add(next(row.element_id for row in evidence if row.element_id is not None))
    group.status = "resolved"
    group.resolution_answer_id = answer.id if answer is not None else None
    group.resolution_payload = {
        "adopted_value": selected,
        "support_evidence_ids": [row.id for row in matching],
        "applies_to_element_ids": sorted(accepted),
    }
    if answer is not None:
        _set_answer(
            answer, "applied", evidence_ids=group.resolution_payload["support_evidence_ids"]
        )
    return accepted


def _supplement(db: Session, group, history, base_evidence) -> MemoryConflictEvidence | None:
    answer = next((answer for _, answer in history if answer.action == "correct"), None)
    value = None
    if answer is not None and group.field_key == "event_time":
        value = time_value(answer.answer_text)
    if answer is not None and group.field_key in FIELD_LABELS and group.field_key != "event_time":
        names = {
            item.normalized_value["entity_id"]: item.normalized_value
            for item in base_evidence
            if item.normalized_value["name"] in answer.answer_text
        }
        if len(names) == 1:
            value = next(iter(names.values()))
    active = _current_evidence(db, group.id)
    key = fingerprint([answer.id, value_key(value)]) if value is not None else None
    for item in active.values():
        if item.answer_id is not None and item.evidence_key != key:
            item.status = "superseded"
    if key is None or key in active:
        return active.get(key)
    record = MemoryConflictEvidence(
        conflict_group_id=group.id,
        answer_id=answer.id,
        evidence_key=key,
        normalized_value=value,
        evidence_payload={"start_offset": 0, "end_offset": len(answer.answer_text)},
    )
    db.add(record)
    db.flush()
    return record


def _sync_pairs(db: Session, job, group, pairs: set[tuple[int, int]]) -> None:
    active = select(MemoryConflict).where(MemoryConflict.is_del.is_(False))
    existing = list(db.scalars(active.where(MemoryConflict.conflict_group_id == group.id)))
    known = {(row.element_a_id, row.element_b_id): row for row in existing}
    for key, row in known.items():
        if key not in pairs:
            row.status = "dismissed"
        elif group.status == "resolved":
            row.status = "resolved"
        else:
            row.status = "awaiting_user"
        row.updated_at = datetime.now(timezone.utc)
    for left, right in sorted(pairs - known.keys()):
        previous = db.scalar(
            active.where(
                MemoryConflict.element_a_id == left,
                MemoryConflict.element_b_id == right,
                MemoryConflict.conflict_type == group.field_key,
            )
        )
        if previous is not None:
            previous.is_del = True
            previous.updated_at = datetime.now(timezone.utc)
            db.flush()
        db.add(
            MemoryConflict(
                element_a_id=left,
                element_b_id=right,
                conflict_group_id=group.id,
                validation_job_id=job.id,
                conflict_type=group.field_key,
                description=f"同一事件的{FIELD_LABELS[group.field_key]}存在分歧",
                status="resolved" if group.status == "resolved" else "awaiting_user",
            )
        )
    db.flush()


# [删除-旧] def _pairs(claims: list[Claim]) -> set[tuple[int, int]]:
#     return {
#         tuple(sorted((left.record.element.id, right.record.element.id)))
#         for left, right in combinations(claims, 2)
#         if left.record.element.id != right.record.element.id
#         and not compatible(left.value, right.value)
#     }


class MemoryValidationService:
    """使用来源中的明确证据核验，复验只在所有者自己的范围发布。"""

    def run(self, db: Session) -> bool:
        claimed = queue.claim(db)
        if claimed is None:
            return False
        try:
            with db.begin():
                job = queue.owned_claim(db, *claimed)
                if job is None:
                    return True
                records = current_records(db, job.owner_user_id)
                if job.target_element_id is not None and job.target_element_id not in records:
                    queue.finish(
                        job, "cancelled", "VALIDATION_TARGET_CHANGED", datetime.now(timezone.utc)
                    )
                    return True
                self._publish(db, job, records)
                now = datetime.now(timezone.utc)
                if queue.lease_end(job) <= now:
                    # 回滚计算结果，让下一次领取者从当前快照重跑。
                    raise TimeoutError
                queue.finish(job, "succeeded", None, now)
        except TimeoutError:
            return True
        except (SQLAlchemyError, ValueError):
            with db.begin():
                job = queue.owned_claim(db, *claimed)
                if job is not None:
                    now = datetime.now(timezone.utc)
                    retry = job.attempt_count < job.max_attempts
                    queue.finish(job, "queued" if retry else "failed", "VALIDATION_FAILED", now)
                    if retry:
                        job.available_at = now + timedelta(seconds=5 * job.attempt_count)
        return True

    def _publish(self, db: Session, job: MemoryValidationJob, records: dict) -> None:
        # 本地核验只读来源，持有所有者锁直到状态、证据和任务结果一起提交。
        # [删除-旧] active_groups = {
        #     (row.event_key, row.field_key): row
        #     for row in db.scalars(
        #         select(MemoryConflictGroup).where(
        #             MemoryConflictGroup.owner_user_id == job.owner_user_id,
        #             MemoryConflictGroup.is_del.is_(False),
        #             MemoryConflictGroup.status != "superseded",
        #         )
        #     )
        # }
        # pending = pending_entities(db, list(records))
        # reasons = {}
        # contexts = defaultdict(lambda: defaultdict(list))
        # for element_id, record in records.items():
        #     claims, reasons[element_id] = collect_claims(db, record, pending)
        #     for claim in claims:
        #         contexts[claim.event_key][claim.field].append(claim)
        # [新增] 归组结果覆盖原文一致和需要澄清的近似叙述。
        active_groups = active_conflict_groups(db, job.owner_user_id)
        events, reasons, pending = group_claims(db, records, active_groups)
        reopen_id = job.input_payload.get("reopen_question_id")
        source_versions = [[key, item.source.id, item.draft.id] for key, item in records.items()]
        answer_ids = sorted(
            db.scalars(
                select(MemoryReviewAnswer.id).where(
                    MemoryReviewAnswer.answered_by_user_id == job.owner_user_id,
                    MemoryReviewAnswer.is_del.is_(False),
                )
            )
        )
        claim_keys = sorted(
            claim.key
            # [删除-旧] for fields in contexts.values()
            # for claims in fields.values()
            for event in events.values()
            for claims in event.fields.values()
            for claim in claims
        )
        job.input_fingerprint = fingerprint(
            [source_versions, claim_keys, sorted(pending), answer_ids]
        )
        job.input_payload = {
            **job.input_payload,
            "source_versions": source_versions,
            "answer_ids": answer_ids,
        }
        # [删除-旧] for event_key, fields in contexts.items():
        #     disputed = {field: _pairs(claims) for field, claims in fields.items()}
        #     uncertain = [
        #         claim for field, claims in fields.items() if disputed[field] for claim in claims
        #     ]
        #     clarify = bool(uncertain) and not all(claim.strong_identity for claim in uncertain)
        #     if clarify:
        for event_key, event in events.items():
            uncertain = event.clarification
            if uncertain:
                key = (event_key, "event_identity")
                group = self._group(db, job, key, active_groups.pop(key, None), uncertain)
                decision = self._resolve(db, job, group, uncertain, set(), reopen_id)
                if decision != "same_event":
                    if decision != "different_events":
                        for claim in uncertain:
                            # [删除-旧] reasons[claim.record.element.id] = "event_unconfirmed"
                            element_id = claim.record.element.id
                            reasons[element_id] = reasons[element_id] or "event_unconfirmed"
                    continue
            # [删除-旧] for field, claims in fields.items():
            for field, claims in event.fields.items():
                key = (event_key, field)
                current = active_groups.pop(key, None)
                # [删除-旧] if current is None and not disputed[field]:
                pairs = event.disputed[field]
                if current is None and not pairs:
                    continue
                group = self._group(db, job, key, current, claims)
                # [删除-旧] accepted = self._resolve(db, job, group, claims, disputed[field], reopen_id)
                accepted = self._resolve(db, job, group, claims, pairs, reopen_id)
                for claim in claims:
                    if claim.record.element.id not in accepted:
                        # [删除-旧] reasons[claim.record.element.id] = "evidence_conflict"
                        element_id = claim.record.element.id
                        reasons[element_id] = reasons[element_id] or "evidence_conflict"
        for group in active_groups.values():
            self._supersede(db, job, group)
        now = datetime.now(timezone.utc)
        outdated = db.scalars(
            select(MemoryElement)
            .join(
                MemorySession,
                MemorySession.id == MemoryElement.session_id,
            )
            .where(
                MemorySession.owner_user_id == job.owner_user_id,
                MemoryElement.is_del.is_(False),
                MemoryElement.draft_version_id.is_not(None),
                MemoryElement.verification_status.in_(("verified", "conflict")),
            )
        )
        for element in outdated:
            if element.id not in records:
                element.verification_status = "pending"
                element.verification_reason = "source_inactive"
                element.last_validation_job_id = job.id
                element.updated_at = now
        newly_verified = 0
        for element_id, record in records.items():
            element = record.element
            reason = reasons[element_id]
            status = (
                "conflict" if reason == "evidence_conflict" else "pending" if reason else "verified"
            )
            newly_verified += int(
                status == "verified" and element.verification_status != "verified"
            )
            if (
                element.verification_status != status
                or element.verification_reason != reason
                or not element.last_validation_job_id
            ):
                element.verification_status = status
                element.verification_reason = reason
                element.last_validation_job_id = job.id
                element.updated_at = now
        db.flush()
        job.result_payload = {
            "newly_verified_element_count": newly_verified,
            "conflict_element_count": sum(
                reason == "evidence_conflict" for reason in reasons.values()
            ),
            "pending_question_count": len(
                list(
                    db.scalars(
                        select(MemoryReviewQuestion.id)
                        .join(
                            MemoryConflictGroup,
                            MemoryConflictGroup.id == MemoryReviewQuestion.conflict_group_id,
                        )
                        .where(
                            MemoryConflictGroup.owner_user_id == job.owner_user_id,
                            MemoryConflictGroup.is_del.is_(False),
                            MemoryReviewQuestion.is_del.is_(False),
                            MemoryReviewQuestion.status == "pending",
                        )
                    )
                )
            ),
        }

    @staticmethod
    def _group(db, job, key, group, claims):
        # [删除-旧] evidence_fingerprint = fingerprint(sorted({item.key for item in claims}))
        evidence_fingerprint = claims_fingerprint(claims)
        if group is None:
            group = MemoryConflictGroup(
                owner_user_id=job.owner_user_id,
                event_key=key[0],
                field_key=key[1],
                group_type="clarification" if key[1] == "event_identity" else "conflict",
                evidence_fingerprint=evidence_fingerprint,
            )
            db.add(group)
            db.flush()
        group.evidence_fingerprint = evidence_fingerprint
        group.updated_at = datetime.now(timezone.utc)
        return group

    def _resolve(self, db, job, group, claims, pairs, reopen_id):
        evidence = _sync_evidence(db, group, claims)
        questions = _questions(db, group.id)
        pending = next(
            (row for row in questions if row.status == "pending" and not row.is_del), None
        )
        history = []
        current_keys = {item.evidence_key for item in evidence}
        for question, answer in _answer_history(db, group.id):
            if question.evidence_fingerprint == group.evidence_fingerprint or (
                answer.action in {"confirm", "correct"}
                and answer.result_status in {"applied", "awaiting_follow_up"}
                and current_keys
                <= set(
                    db.scalars(
                        select(MemoryConflictEvidence.evidence_key).where(
                            MemoryConflictEvidence.id.in_(question.evidence_ids),
                            MemoryConflictEvidence.conflict_group_id == group.id,
                            MemoryConflictEvidence.element_id.is_not(None),
                        )
                    )
                )
            ):
                history.append((question, answer))
            elif answer.result_status in {"pending", "applied", "awaiting_follow_up"}:
                _set_answer(answer, "stale")
        clarification = group.field_key == "event_identity"
        supplement = _supplement(db, group, history, evidence)
        if supplement is not None:
            evidence.append(supplement)
        group.status = "open"
        group.resolution_payload = {}
        group.resolution_answer_id = None
        latest_question, latest_answer = history[0] if history else (None, None)
        # 已重开的问题仍允许依据相同证据作答，不会被上一次跳过立即关闭。
        reopening = latest_question is not None and (
            reopen_id == latest_question.id
            or (pending and pending.revision > latest_question.revision)
        )
        result = None if clarification else set()
        if latest_answer is not None and latest_answer.action == "confirm":
            selected = next(
                (
                    option
                    for option in latest_question.options
                    if option["id"] == latest_answer.selected_option_id
                ),
                None,
            )
            if clarification and selected and selected.get("decision"):
                result = selected["decision"]
                group.status = "resolved"
                group.resolution_answer_id = latest_answer.id
                group.resolution_payload = {
                    "decision": result,
                    "support_evidence_ids": [row.id for row in evidence],
                }
                _set_answer(latest_answer, "applied")
            elif (
                selected
                and _selectable(selected["value"], evidence)
                and any(
                    value_key(item.normalized_value) == value_key(selected["value"])
                    for item in evidence
                )
            ):
                result = _apply_value(group, latest_answer, selected["value"], evidence)
                if (
                    supplement is not None
                    and supplement.id in group.resolution_payload["support_evidence_ids"]
                ):
                    source_answer = next(
                        answer for _, answer in history if answer.id == supplement.answer_id
                    )
                    _set_answer(source_answer, "applied", confirmed_by_answer_id=latest_answer.id)
        if (
            not clarification
            and not pairs
            and group.status == "open"
            and (
                supplement is None
                or all(
                    compatible(supplement.normalized_value, row.normalized_value)
                    for row in evidence
                )
            )
        ):
            result = _apply_value(group, None, evidence[0].normalized_value, evidence)
        if group.status == "resolved":
            _close_question(pending)
        elif (
            latest_answer and latest_answer.action in {"cannot_remember", "skip"} and not reopening
        ):
            _set_answer(latest_answer, "unresolved")
            _close_question(pending)
        else:
            self._pending(db, job, group, evidence, questions, latest_answer, clarification)
        if not clarification:
            _sync_pairs(db, job, group, pairs)
        return result

    @staticmethod
    def _pending(db, job, group, evidence, questions, answer, clarification):
        if clarification:
            ids = [row.id for row in evidence]
            options = [
                {
                    "id": "same-event",
                    "label": "是同一次经历",
                    "decision": "same_event",
                    "evidence_ids": ids,
                },
                {
                    "id": "different-events",
                    "label": "是不同的经历，分别保留",
                    "decision": "different_events",
                    "evidence_ids": ids,
                },
            ]
            kind, text = (
                "clarify_event",
                "这几段记录提到了相似的情形。您记得它们是在说同一次经历吗？",
            )
        else:
            follow_up = answer is not None and answer.action == "correct"
            kind = "follow_up" if follow_up else "confirm_value"
            text, options = _question_text(group.field_key, follow_up), _options(evidence)
        question = _issue_question(db, job, group, questions, kind, text, options, evidence)
        if answer is not None and answer.action == "correct":
            _set_answer(answer, "awaiting_follow_up", question_id=question.id)

    @staticmethod
    def _supersede(db, job, group):
        group.status = "superseded"
        group.resolution_answer_id = None
        group.resolution_payload = {}
        group.updated_at = datetime.now(timezone.utc)
        for evidence in _current_evidence(db, group.id).values():
            evidence.status = "superseded"
        for question in _questions(db, group.id):
            _close_question(question)
        for _, answer in _answer_history(db, group.id):
            if answer.result_status in {"pending", "applied", "awaiting_follow_up"}:
                _set_answer(answer, "stale")
        _sync_pairs(db, job, group, set())
