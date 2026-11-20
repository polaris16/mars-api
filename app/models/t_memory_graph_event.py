"""[新增] 保存回忆录图谱事件的稳定标识及发布状态。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class MemoryGraphEvent(AuditFields, Base):
    __tablename__ = "t_memory_graph_event"
    __table_args__ = (
        CheckConstraint(
            "status IN ('published', 'retired')", name="ck_memory_graph_event_status"
        ),
        CheckConstraint(
            "anchor_key ~ '^[0-9a-f]{64}$'", name="ck_memory_graph_event_anchor_key"
        ),
        UniqueConstraint("owner_user_id", "anchor_key", name="uq_memory_graph_event_owner_anchor"),
        Index(
            "idx_memory_graph_event_published",
            "owner_user_id",
            "id",
            postgresql_where=text("is_del = false AND status = 'published'"),
        ),
        Index("idx_memory_graph_event_validation_job", "last_validation_job_id"),
        {"comment": "整本回忆录的稳定图谱事件节点"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="图谱事件主键 ID")
    owner_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", name="fk_memory_graph_event_owner", ondelete="RESTRICT"),
        nullable=False,
        comment="回忆录所有者 ID",
    )
    anchor_key = Column(String(64), nullable=False, comment="用来恢复同一事件节点的语义锚点 SHA-256")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'published'"),
        comment="published 当前发布，retired 已退役或暂无有效依据",
    )
    last_validation_job_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_validation_job.id", name="fk_memory_graph_event_job", ondelete="RESTRICT"
        ),
        nullable=False,
        comment="最近发布或退役此节点的复验任务 ID",
    )
