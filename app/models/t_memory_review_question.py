"""[新增] 保存每次向所有者展示的核对问题版本。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryReviewQuestion(AuditFields, Base):
    __tablename__ = "t_memory_review_question"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_memory_review_question_revision"),
        CheckConstraint("length(btrim(question_text)) > 0", name="ck_memory_review_question_text"),
        CheckConstraint(
            "kind IN ('clarify_event', 'confirm_value', 'follow_up')",
            name="ck_memory_review_question_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'answered', 'skipped', 'superseded')",
            name="ck_memory_review_question_status",
        ),
        CheckConstraint(
            "jsonb_typeof(options) = 'array'", name="ck_memory_review_question_options"
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_ids) = 'array'", name="ck_memory_review_question_evidence"
        ),
        UniqueConstraint(
            "conflict_group_id", "revision", name="uq_memory_review_question_revision"
        ),
        Index(
            "uq_memory_review_question_pending",
            "conflict_group_id",
            unique=True,
            postgresql_where=text("is_del = false AND status = 'pending'"),
        ),
        Index("idx_memory_review_question_job", "issued_by_job_id"),
        {"comment": "面向所有者的冲突核对问题及其固定版本"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    conflict_group_id = Column(
        BigInteger,
        ForeignKey("t_memory_conflict_group.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属冲突组 ID",
    )
    revision = Column(Integer, nullable=False, comment="从 1 开始的组内问题版本")
    kind = Column(String(24), nullable=False, comment="澄清事件、核对取值或继续追问")
    status = Column(
        String(24),
        nullable=False,
        server_default=text("'pending'"),
        comment="待回答、已回答、已跳过或已被替代",
    )
    question_text = Column(Text, nullable=False, comment="向所有者展示的具体核对问题")
    options = Column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="当前版本的选项 ID、文字、候选结论和证据引用数组",
    )
    evidence_ids = Column(JSONB, nullable=False, comment="当前版本引用的同组证据 ID 数组")
    evidence_fingerprint = Column(String(64), nullable=False, comment="提问时的证据语义集合指纹")
    issued_by_job_id = Column(
        BigInteger,
        ForeignKey("t_memory_validation_job.id", ondelete="RESTRICT"),
        nullable=False,
        comment="生成该版本的复验任务 ID",
    )
