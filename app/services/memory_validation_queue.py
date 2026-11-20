"""[新增] 在业务事务内登记核验触发，并为同一所有者领取任务。"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import MemoryElement, MemoryValidationJob, User

LEASE_DURATION = timedelta(minutes=2)


def lock_owner(db: Session, owner_id: int, *, skip_locked: bool = False) -> bool:
    """素材更新先持有会话锁，核验持有所有者锁且只读会话行。"""
    return (
        db.scalar(
            select(User.id)
            .where(User.id == owner_id, User.is_del.is_(False))
            .with_for_update(skip_locked=skip_locked)
        )
        is not None
    )


def enqueue(
    db: Session,
    owner_id: int,
    job_type: str,
    *,
    actor_id: int | None = None,
    element_id: int | None = None,
    retry_of_job_id: int | None = None,
    trigger: dict | None = None,
    key: str | None = None,
) -> MemoryValidationJob:
    """调用方持有所有者锁；终态任务保留以处理精确重放。"""
    jobs = select(MemoryValidationJob).where(MemoryValidationJob.owner_user_id == owner_id)
    if key is not None:
        current = db.scalar(jobs.where(MemoryValidationJob.idempotency_key == key))
    else:
        current = db.scalar(
            jobs.where(
                MemoryValidationJob.job_type == job_type,
                MemoryValidationJob.target_element_id == element_id,
                MemoryValidationJob.is_del.is_(False),
                MemoryValidationJob.status == "queued",
            )
            .order_by(MemoryValidationJob.id)
            .limit(1)
        )
    if current is not None:
        return current
    job = MemoryValidationJob(
        owner_user_id=owner_id,
        requested_by_user_id=actor_id,
        job_type=job_type,
        target_element_id=element_id,
        retry_of_job_id=retry_of_job_id,
        idempotency_key=key or str(uuid4()),
        input_payload=trigger or {},
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(job)
    db.flush()
    return job


def schedule_material_validation(
    db: Session,
    owner_id: int,
    session_id: int,
    *,
    fragment_id: int | None = None,
    reason: str,
    actor_id: int | None = None,
) -> None:
    """只为已有版本化元素的素材投递，旧流程仍可独立运行。"""
    query = select(MemoryElement.id).where(
        MemoryElement.session_id == session_id,
        MemoryElement.draft_version_id.is_not(None),
    )
    if fragment_id is not None:
        query = query.where(MemoryElement.source_fragment_id == fragment_id)
    if db.scalar(query.limit(1)) is None or not lock_owner(db, owner_id):
        return
    # 队列中的全量复验会读取所有提交后的变化，合并时追加触发引用。
    job = enqueue(db, owner_id, "review_memoir")
    trigger = {
        "session_id": session_id,
        "fragment_id": fragment_id,
        "reason": reason,
        "actor_id": actor_id,
    }
    triggers = job.input_payload.get("material_changes", [])
    if trigger not in triggers:
        job.input_payload = {**job.input_payload, "material_changes": [*triggers, trigger]}


def claim(db: Session) -> tuple[int, int, UUID] | None:
    """过期任务先回收，行锁和部分唯一索引阻止同一所有者并行领取。"""
    now = datetime.now(timezone.utc)
    running = aliased(MemoryValidationJob)
    running_for_owner = select(running.id).where(
        running.owner_user_id == MemoryValidationJob.owner_user_id,
        running.status == "processing",
        running.is_del.is_(False),
    )
    candidates = (
        select(MemoryValidationJob.owner_user_id)
        .join(
            User,
            User.id == MemoryValidationJob.owner_user_id,
        )
        .where(
            User.is_del.is_(False),
            MemoryValidationJob.is_del.is_(False),
            or_(
                and_(
                    MemoryValidationJob.status == "processing",
                    MemoryValidationJob.lease_expires_at <= now,
                ),
                and_(
                    MemoryValidationJob.status == "queued",
                    MemoryValidationJob.available_at <= now,
                    ~running_for_owner.exists(),
                ),
            ),
        )
        .order_by(MemoryValidationJob.available_at, MemoryValidationJob.id)
        .limit(64)
    )
    with db.begin():
        for owner_id in dict.fromkeys(db.scalars(candidates)):
            if not lock_owner(db, owner_id, skip_locked=True):
                continue
            jobs = select(MemoryValidationJob).where(
                MemoryValidationJob.owner_user_id == owner_id,
                MemoryValidationJob.is_del.is_(False),
            )
            job = db.scalar(jobs.where(MemoryValidationJob.status == "processing"))
            if job is not None and lease_end(job) > now:
                continue
            if job is None:
                job = db.scalar(
                    jobs.where(
                        MemoryValidationJob.status == "queued",
                        MemoryValidationJob.available_at <= now,
                    )
                    .order_by(MemoryValidationJob.available_at, MemoryValidationJob.id)
                    .limit(1)
                )
            if job is None:
                continue
            if job.attempt_count >= job.max_attempts:
                finish(job, "failed", "VALIDATION_ATTEMPTS_EXHAUSTED", now)
                continue
            token = uuid4()
            job.attempt_count += 1
            job.status = "processing"
            job.started_at = now
            job.worker_token = token
            job.lease_expires_at = now + LEASE_DURATION
            job.updated_at = now
            return job.id, owner_id, token
    return None


def lease_end(job: MemoryValidationJob) -> datetime:
    value = job.lease_expires_at
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def owned_claim(db: Session, job_id: int, owner_id: int, token: UUID) -> MemoryValidationJob | None:
    if not lock_owner(db, owner_id):
        return None
    return db.scalar(
        select(MemoryValidationJob)
        .where(
            MemoryValidationJob.id == job_id,
            MemoryValidationJob.owner_user_id == owner_id,
            MemoryValidationJob.worker_token == token,
            MemoryValidationJob.status == "processing",
            MemoryValidationJob.lease_expires_at > datetime.now(timezone.utc),
            MemoryValidationJob.is_del.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def finish(job: MemoryValidationJob, status: str, error: str | None, now: datetime) -> None:
    job.status = status
    job.finished_at = now if status in {"succeeded", "failed", "cancelled"} else None
    job.worker_token = None
    job.lease_expires_at = None
    job.error_code = error
    job.updated_at = now
