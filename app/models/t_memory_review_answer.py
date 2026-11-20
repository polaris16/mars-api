"""[新增] 记录所有者对固定问题版本的回答及复验结果。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryReviewAnswer(AuditFields, Base):
    __tablename__ = "t_memory_review_answer"
    __table_args__ = (
        CheckConstraint(
            "(action = 'confirm' AND selected_option_id IS NOT NULL "
            "AND length(btrim(selected_option_id)) > 0 AND answer_text IS NULL) OR "
            "(action = 'correct' AND selected_option_id IS NULL "
            "AND answer_text IS NOT NULL AND length(btrim(answer_text)) > 0) OR "
            "(action IN ('cannot_remember', 'skip') "
            "AND selected_option_id IS NULL AND answer_text IS NULL)",
            name="ck_memory_review_answer_action_payload",
        ),
        CheckConstraint(
            "result_status IN ('pending', 'applied', 'unresolved', 'awaiting_follow_up', 'stale')",
            name="ck_memory_review_answer_result_status",
        ),
        CheckConstraint(
            "jsonb_typeof(result_payload) = 'object'", name="ck_memory_review_answer_result"
        ),
        UniqueConstraint("question_id", name="uq_memory_review_answer_question"),
        Index("idx_memory_review_answer_job", "validation_job_id"),
        {"comment": "核对问题的用户回答及后续复验结果"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    question_id = Column(
        BigInteger,
        ForeignKey("t_memory_review_question.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所回答的具体问题版本 ID",
    )
    answered_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="提交回答的所有者账号 ID",
    )
    action = Column(String(24), nullable=False, comment="确认选项、补述纠正、记不清或跳过")
    selected_option_id = Column(String(100), comment="所选的当前问题版本选项 ID")
    answer_text = Column(Text, comment="所有者补充或纠正的原始文字")
    answer_fingerprint = Column(String(64), nullable=False, comment="回答动作及载荷的 SHA-256")
    validation_job_id = Column(
        BigInteger,
        ForeignKey("t_memory_validation_job.id", ondelete="RESTRICT"),
        nullable=False,
        comment="受理回答时登记的首个复验任务 ID",
    )
    result_status = Column(
        String(24),
        nullable=False,
        server_default=text("'pending'"),
        comment="待复验、已采纳、仍未确认、需再问或失去当前依据",
    )
    result_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="复验结论、后续问题及有效证据引用",
    )
