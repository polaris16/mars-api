"""[新增] 记忆片段原文及转写版本。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class MemorySourceRevision(AuditFields, Base):
    __tablename__ = "t_memory_source_revision"
    __table_args__ = (
        UniqueConstraint("fragment_id", "source_fingerprint", name="uq_memory_source_fingerprint"),
        Index("idx_memory_source_fragment", "fragment_id", "id"),
        CheckConstraint(
            "source_kind IN ('content_text', 'text_file', 'app_asr', 'server_asr')",
            name="ck_memory_source_kind",
        ),
        CheckConstraint("length(trim(source_text)) > 0", name="ck_memory_source_nonblank"),
        CheckConstraint("jsonb_typeof(transcript_segments) = 'array'", name="ck_memory_source_segments"),
        {"comment": "记忆片段原文及转写版本"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键 ID")
    fragment_id = Column(
        BigInteger, ForeignKey("t_memory_fragment.id", ondelete="CASCADE"),
        nullable=False, comment="来源素材片段 ID",
    )
    source_kind = Column(String(24), nullable=False, comment="直接文字、文本文件或 App/服务端 ASR")
    source_asset_id = Column(
        BigInteger, ForeignKey("t_media_asset.id", ondelete="RESTRICT"), comment="原始文件对象 ID",
    )
    source_text = Column(Text, nullable=False, comment="原始文字或未整理的 ASR 全文")
    transcript_segments = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), comment="字符与原声区间的对应数组",
    )
    source_fingerprint = Column(String(64), nullable=False, comment="原文、定位及来源的 SHA-256")
