"""[新增] 来自记忆元素的实体提及和待确认匹配。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryEntityCandidate(AuditFields, Base):
    __tablename__ = "t_memory_entity_candidate"
    __table_args__ = (
        Index("idx_memory_entity_candidate_element", "element_id", "status", "id"),
        Index("idx_memory_entity_candidate_evidence", "evidence_key"),
        CheckConstraint("status IN ('pending', 'resolved')", name="ck_memory_candidate_status"),
        CheckConstraint("revision > 0", name="ck_memory_candidate_revision"),
        CheckConstraint("jsonb_typeof(evidence) = 'object'", name="ck_memory_candidate_evidence"),
        CheckConstraint("jsonb_typeof(candidate_entity_ids) = 'array'", name="ck_memory_candidate_ids"),
        {"comment": "来自记忆元素的实体提及和待确认匹配"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    element_id = Column(
        BigInteger, ForeignKey("t_memory_element.id", ondelete="CASCADE"),
        nullable=False, comment="所属记忆元素 ID",
    )
    entity_type = Column(String(24), nullable=False, comment="提及的人物、地点、机构或时间段")
    mention_text = Column(String(200), nullable=False, comment="叙述者所用的原文称谓")
    narrator_user_id = Column(
        BigInteger, ForeignKey("t_user.id", ondelete="SET NULL"), comment="已知的实际叙述者账号 ID",
    )
    relation_type = Column(String(24), nullable=False, comment="实体提及在元素中的角色")
    evidence = Column(JSONB, nullable=False, comment="提及的整理版本、段落与原文位置")
    evidence_key = Column(String(64), nullable=False, comment="来源、含义及叙述者的证据指纹")
    candidate_entity_ids = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), comment="可能关联的实体 ID",
    )
    status = Column(
        String(20), nullable=False, server_default=text("'pending'"), comment="候选待确认或已处理",
    )
    revision = Column(Integer, nullable=False, server_default=text("1"), comment="用于并发确认的版本")
