"""[新增] 仅允许记忆所有者查询、回答和重开核对问题。"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    MemoryConflictEvidence,
    MemoryConflictGroup,
    MemoryElement,
    MemoryReviewAnswer,
    MemoryReviewQuestion,
    MemorySession,
    MemoryValidationJob,
    User,
)
from app.schemas.list_sort import IdSort
from app.schemas.memory_review import ReviewAnswerCreate, ValidationJobCreate
from app.services.memory_validation_queue import enqueue, lock_owner

from app.services.memory_validation_rules import (
    active_conflict_groups,
    current_group_fingerprint,
    current_records,
    group_claims,
)
from app.services.memory_versions import fingerprint


class MemoryReviewService:
    @staticmethod
    def _questions(owner_id: int):
        return (
            select(MemoryReviewQuestion, MemoryConflictGroup)
            .join(
                MemoryConflictGroup,
                MemoryConflictGroup.id == MemoryReviewQuestion.conflict_group_id,
            )
            .where(
                MemoryConflictGroup.owner_user_id == owner_id,
                MemoryConflictGroup.is_del.is_(False),
                MemoryReviewQuestion.is_del.is_(False),
            )
        )

    @classmethod
    def _question(cls, db: Session, owner_id: int, question_id: int):
        result = db.execute(
            cls._questions(owner_id).where(MemoryReviewQuestion.id == question_id)
        ).one_or_none()
        if result is None:
            raise HTTPException(404, "核对问题不存在")
        return result

    @staticmethod
    def _lock(db: Session, user: User) -> int:
        owner_id = user.id
        if not lock_owner(db, owner_id):
            raise HTTPException(401, "用户不存在或已停用")
        return owner_id

    @staticmethod
    def _evidence(db: Session, questions: list, records: dict) -> dict[int, dict]:
        ids = {item for question, _ in questions for item in question.evidence_ids}
        if not ids:
            return {}
        query = (
            select(MemoryConflictEvidence, MemoryReviewAnswer, MemoryReviewQuestion)
            .outerjoin(
                MemoryReviewAnswer,
                MemoryReviewAnswer.id == MemoryConflictEvidence.answer_id,
            )
            .outerjoin(
                MemoryReviewQuestion,
                MemoryReviewQuestion.id == MemoryReviewAnswer.question_id,
            )
            .where(
                MemoryConflictEvidence.id.in_(ids),
                MemoryConflictEvidence.status == "active",
                MemoryConflictEvidence.is_del.is_(False),
            )
        )
        results = {}
        owners = {group.id: group.owner_user_id for _, group in questions}
        for item, answer, answered_question in db.execute(query):
            record = records.get(item.element_id)
            if record and (item.source_revision_id, item.draft_version_id) == (
                record.source.id,
                record.draft.id,
            ):
                results[item.id] = {
                    "id": item.id,
                    "session_id": record.element.session_id,
                    "element_id": item.element_id,
                    "source_revision_id": item.source_revision_id,
                    "draft_version_id": item.draft_version_id,
                    "excerpt": (record.element.source_quote or "")[:200],
                    "group_id": item.conflict_group_id,
                }
            elif (
                answer is not None
                and not answer.is_del
                and answer.action == "correct"
                and answer.answered_by_user_id == owners.get(item.conflict_group_id)
                and answered_question is not None
                and not answered_question.is_del
                and answered_question.conflict_group_id == item.conflict_group_id
            ):
                results[item.id] = {
                    "id": item.id,
                    "answer_id": answer.id,
                    "excerpt": answer.answer_text[:200],
                    "group_id": item.conflict_group_id,
                }
        return results

    @staticmethod
    def _visible(question, group, evidence):
        return [
            evidence[item]
            for item in question.evidence_ids
            if item in evidence and evidence[item]["group_id"] == group.id
        ]

    @staticmethod
    def _current_question(question, group, items, events, groups) -> bool:
        return bool(items) and (
            group.status == "open"
            and len(items) == len(question.evidence_ids)
            and question.evidence_fingerprint == group.evidence_fingerprint
            and question.evidence_fingerprint == current_group_fingerprint(group, events, groups)
        )

    def list_questions(
        self,
        db: Session,
        user: User,
        page: int,
        page_size: int,
        status: str,
        session_id: int | None,
        *,
        sort: IdSort = "default",
    ) -> dict:
        owner_id = user.id
        if (
            session_id is not None
            and db.scalar(
                select(MemorySession.id).where(
                    MemorySession.id == session_id,
                    MemorySession.owner_user_id == owner_id,
                    MemorySession.is_del.is_(False),
                )
            )
            is None
        ):
            raise HTTPException(404, "会话不存在")
        query = self._questions(owner_id).where(MemoryReviewQuestion.status == status)
        # 保留问题顺序，筛完可见证据后再切页。
        ordering = MemoryReviewQuestion.id if sort == "id_asc" else MemoryReviewQuestion.id.desc()
        questions = list(db.execute(query.order_by(ordering)))
        if not questions:
            return {"page": page, "page_size": page_size, "total": 0, "items": []}
        records = current_records(db, owner_id)
        evidence = self._evidence(db, questions, records)
        groups, events = {}, {}
        if status == "pending":
            groups = active_conflict_groups(db, owner_id)
            events, _, _ = group_claims(db, records, groups)
        visible = []
        for question, group in questions:
            items = self._visible(question, group, evidence)
            if status == "pending" and not self._current_question(
                question, group, items, events, groups
            ):
                continue
            if session_id is not None and not any(
                item.get("session_id") == session_id for item in items
            ):
                continue
            option_ids = {item["id"] for item in items}
            options = [
                option for option in question.options if set(option["evidence_ids"]) <= option_ids
            ]
            # 历史题面在失去来源授权后保留在数据库，响应只包含仍可核对的信息。
            if len(items) != len(question.evidence_ids):
                options = []
                text = "这道核对问题的部分依据已失效。"
            else:
                text = question.question_text
            visible.append(
                {
                    "id": question.id,
                    "revision": question.revision,
                    "conflict_group_id": group.id,
                    "kind": question.kind,
                    "status": question.status,
                    "question_text": text,
                    "options": options,
                    "evidence": items,
                }
            )
        offset = (page - 1) * page_size
        return {
            "page": page,
            "page_size": page_size,
            "total": len(visible),
            "items": visible[offset : offset + page_size],
        }

    def _require_current(self, db, owner_id, question, group):
        # 调用方持有所有者锁，新提交的提取结果也要纳入当前题的证据。
        records = current_records(db, owner_id)
        groups = active_conflict_groups(db, owner_id)
        events, _, _ = group_claims(db, records, groups)
        evidence = self._evidence(db, [(question, group)], records)
        items = self._visible(question, group, evidence)
        if not self._current_question(question, group, items, events, groups):
            raise HTTPException(409, "问题依据已变化，请刷新问题列表")

    def answer(self, db: Session, user: User, question_id: int, body: ReviewAnswerCreate) -> dict:
        with db.begin():
            owner_id = self._lock(db, user)
            question, group = self._question(db, owner_id, question_id)
            if question.revision != body.expected_revision:
                raise HTTPException(409, "问题版本已变化")
            digest = fingerprint(body.model_dump())
            existing = db.scalar(
                select(MemoryReviewAnswer).where(MemoryReviewAnswer.question_id == question_id)
            )
            if existing is not None:
                if existing.is_del or existing.answer_fingerprint != digest:
                    raise HTTPException(409, "这个问题版本已有回答")
                return {"answer_id": existing.id, "validation_job_id": existing.validation_job_id}
            if question.status != "pending":
                raise HTTPException(409, "问题版本已经失效")
            self._require_current(db, owner_id, question, group)
            if body.action == "confirm" and not any(
                option["id"] == body.selected_option_id for option in question.options
            ):
                raise HTTPException(422, "所选选项不属于该问题版本")
            job = enqueue(
                db,
                owner_id,
                "review_memoir",
                actor_id=owner_id,
                key=f"answer:{question.id}",
                trigger={"question_id": question.id},
            )
            answer = MemoryReviewAnswer(
                question_id=question.id,
                answered_by_user_id=owner_id,
                action=body.action,
                selected_option_id=body.selected_option_id,
                answer_text=body.answer_text,
                answer_fingerprint=digest,
                validation_job_id=job.id,
                created_by=owner_id,
                updated_by=owner_id,
            )
            db.add(answer)
            question.status = "skipped" if body.action == "skip" else "answered"
            question.updated_by = owner_id
            question.updated_at = datetime.now(timezone.utc)
            db.flush()
            return {"answer_id": answer.id, "validation_job_id": job.id}

    def reopen(self, db: Session, user: User, question_id: int, revision: int) -> dict:
        with db.begin():
            owner_id = self._lock(db, user)
            question, group = self._question(db, owner_id, question_id)
            if revision != question.revision:
                raise HTTPException(409, "问题版本已变化")
            self._require_current(db, owner_id, question, group)
            key = f"reopen:{question.id}"
            prior = db.scalar(
                self._jobs(owner_id).where(MemoryValidationJob.idempotency_key == key)
            )
            if prior is not None:
                return {"validation_job_id": prior.id}
            answer = db.scalar(
                select(MemoryReviewAnswer).where(
                    MemoryReviewAnswer.question_id == question_id,
                    MemoryReviewAnswer.is_del.is_(False),
                )
            )
            newer = db.scalar(
                select(MemoryReviewQuestion.id)
                .where(
                    MemoryReviewQuestion.conflict_group_id == group.id,
                    MemoryReviewQuestion.revision > revision,
                    MemoryReviewQuestion.is_del.is_(False),
                )
                .limit(1)
            )
            if (
                answer is None
                or answer.action not in {"skip", "cannot_remember"}
                or newer is not None
            ):
                raise HTTPException(409, "仅可重开该组最新跳过或记不清的问题")
            job = enqueue(
                db,
                owner_id,
                "review_memoir",
                actor_id=owner_id,
                key=key,
                trigger={"reopen_question_id": question.id},
            )
            return {"validation_job_id": job.id}

    @staticmethod
    def _jobs(owner_id):
        return select(MemoryValidationJob).where(
            MemoryValidationJob.owner_user_id == owner_id,
            MemoryValidationJob.is_del.is_(False),
        )

    def _job(self, db, owner_id, job_id):
        row = db.scalar(self._jobs(owner_id).where(MemoryValidationJob.id == job_id))
        if row is None:
            raise HTTPException(404, "复验任务不存在")
        return row

    def get_job(self, db: Session, user: User, job_id: int) -> MemoryValidationJob:
        return self._job(db, user.id, job_id)

    def submit_job(self, db: Session, user: User, body: ValidationJobCreate) -> dict:
        with db.begin():
            owner_id = self._lock(db, user)
            parent = None
            trigger = {"manual": True}
            element_id = body.element_id
            job_type = "verify_element" if body.action == "verify_element" else "rebuild_memoir"
            if body.action == "retry":
                parent = self._job(db, owner_id, body.retry_of_job_id)
                if parent.status not in {"failed", "cancelled"}:
                    raise HTTPException(409, "仅可重试失败或取消的任务")
                job_type, element_id = parent.job_type, parent.target_element_id
                trigger = parent.input_payload
                for name in ("reopen_question_id", "question_id"):
                    if name in trigger:
                        question, group = self._question(db, owner_id, trigger[name])
                        self._require_current(db, owner_id, question, group)
            if element_id is not None:
                exists = db.scalar(
                    select(MemoryElement.id)
                    .join(
                        MemorySession,
                        MemorySession.id == MemoryElement.session_id,
                    )
                    .where(MemoryElement.id == element_id, MemorySession.owner_user_id == owner_id)
                )
                if exists is None:
                    raise HTTPException(404, "记忆元素不存在")
                if not current_records(db, owner_id, element_id):
                    raise HTTPException(409, "记忆元素的来源已经失效或尚未版本化")
            job = enqueue(
                db,
                owner_id,
                job_type,
                actor_id=owner_id,
                element_id=element_id,
                retry_of_job_id=parent.id if parent else None,
                trigger=trigger,
                key=f"retry:{parent.id}" if parent else None,
            )
            return {"validation_job_id": job.id}
