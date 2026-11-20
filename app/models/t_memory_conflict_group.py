"""[新增] 按事件与属性聚合冲突和待澄清事项。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryConflictGroup(AuditFields, Base):
    __tablename__ = "t_memory_conflict_group"
    __table_args__ = (
        CheckConstraint("length(btrim(event_key)) > 0", name="ck_memory_group_event_key"),
        CheckConstraint(
            "field_key IN ('event_identity', 'event_time', 'event_location', "
            "'departure_location', 'destination_location')",
            name="ck_memory_group_field_key",
        ),
        CheckConstraint("group_type IN ('conflict', 'clarification')", name="ck_memory_group_type"),
        CheckConstraint(
            "status IN ('open', 'resolved', 'superseded')", name="ck_memory_group_status"
        ),
        CheckConstraint(
            "jsonb_typeof(resolution_payload) = 'object'", name="ck_memory_group_resolution"
        ),
        Index(
            "uq_memory_group_active_fact",
            "owner_user_id",
            "event_key",
            "field_key",
            unique=True,
            postgresql_where=text("is_del = false AND status <> 'superseded'"),
        ),
        Index("idx_memory_group_owner_status", "owner_user_id", "status", "id"),
        Index("idx_memory_group_resolution_answer", "resolution_answer_id"),
        {"comment": "同一事件属性的冲突或澄清分组"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    owner_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="回忆录所有者 ID",
    )
    event_key = Column(String(64), nullable=False, comment="依据独立上下文生成的事件归组键")
    field_key = Column(String(40), nullable=False, comment="被核对的事件属性或地点角色")
    group_type = Column(String(24), nullable=False, comment="冲突或需要澄清的事件归属")
    status = Column(
        String(24),
        nullable=False,
        server_default=text("'open'"),
        comment="待处理、已得出结论或已被替代",
    )
    evidence_fingerprint = Column(String(64), nullable=False, comment="当前证据语义集合的 SHA-256")
    resolution_answer_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_review_answer.id",
            name="fk_memory_group_resolution_answer",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        comment="当前结论采纳的同组回答 ID，自动解决时为空",
    )
    resolution_payload = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="采纳值、支持证据及适用元素，或确认的事件划分",
    )
