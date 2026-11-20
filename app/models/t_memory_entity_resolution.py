"""[新增] 记忆所有者处理实体候选的历史记录。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Integer, String, UniqueConstraint

from app.models.base import AuditFields, Base


class MemoryEntityResolution(AuditFields, Base):
    __tablename__ = "t_memory_entity_resolution"
    __table_args__ = (
        UniqueConstraint("candidate_id", "candidate_revision", name="uq_memory_entity_resolution"),
        CheckConstraint(
            "action IN ('link_existing', 'create_new', 'reject_match')",
            name="ck_memory_resolution_action",
        ),
        {"comment": "记忆所有者处理实体候选的历史记录"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    candidate_id = Column(
        BigInteger, ForeignKey("t_memory_entity_candidate.id", ondelete="CASCADE"),
        nullable=False, comment="被确认的实体候选 ID",
    )
    candidate_revision = Column(Integer, nullable=False, comment="提交确认时的候选版本")
    action = Column(String(24), nullable=False, comment="关联已有实体、新建实体或排除匹配")
    entity_id = Column(
        BigInteger, ForeignKey("t_life_entity.id", ondelete="RESTRICT"),
        nullable=False, comment="确认或排除的实体 ID",
    )
    request_hash = Column(String(64), nullable=False, comment="同一候选版本的幂等请求指纹")
