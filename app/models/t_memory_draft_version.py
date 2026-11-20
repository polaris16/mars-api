"""[新增] 带段落来源的不可变记忆整理稿版本。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemoryDraftVersion(AuditFields, Base):
    __tablename__ = "t_memory_draft_version"
    __table_args__ = (
        UniqueConstraint("source_revision_id", "request_key", name="uq_memory_draft_request"),
        Index("idx_memory_draft_source", "source_revision_id", "id"),
        CheckConstraint("origin IN ('model', 'owner')", name="ck_memory_draft_origin"),
        CheckConstraint(
            "jsonb_typeof(paragraphs) = 'array' AND jsonb_array_length(paragraphs) > 0",
            name="ck_memory_draft_paragraphs",
        ),
        {"comment": "带段落来源的不可变记忆整理稿版本"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    source_revision_id = Column(
        BigInteger, ForeignKey("t_memory_source_revision.id", ondelete="CASCADE"),
        nullable=False, comment="所用的原文及定位版本 ID",
    )
    base_version_id = Column(
        BigInteger, ForeignKey("t_memory_draft_version.id", ondelete="SET NULL"),
        comment="人工编辑使用的基准版本 ID",
    )
    origin = Column(String(16), nullable=False, comment="model 自动整理或 owner 人工校订")
    paragraphs = Column(JSONB, nullable=False, comment="有序段落、来源位置和人工编辑标记")
    request_key = Column(Uuid, comment="人工保存幂等请求键")
    request_hash = Column(String(64), comment="人工保存请求内容指纹")
