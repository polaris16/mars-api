"""[新增] 录音会话中的单段语音及其 ASR 转写。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)

from app.models.base import Base


class MemoryFragment(Base):
    __tablename__ = "t_memory_fragment"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_fragment_pkey"),
        UniqueConstraint("session_id", "sequence_no", name="uq_t_memory_fragment_session_sequence"),
        # [新增] 软删除的片段仍保留请求键，防止旧请求重新创建素材。
        UniqueConstraint(
            "session_id",
            "created_by",
            "client_request_key",
            name="uq_t_memory_fragment_client_request",
        ),
        Index(
            "idx_t_memory_fragment_session_recorded_active",
            "session_id",
            "recorded_at",
            "sequence_no",
            postgresql_where=text("is_del = false"),
        ),
        # [删除-旧] audio_asset_id 曾唯一，限制一个媒体对象只能对应一个片段；媒体对象现可复用。
        # UniqueConstraint("audio_asset_id", name="uq_t_memory_fragment_audio_asset"),
        CheckConstraint(
            "source_type IN ('text', 'audio', 'video')", name="ck_t_memory_fragment_source_type"
        ),
        CheckConstraint(
            "source_type <> 'text' OR (content_text IS NOT NULL AND length(btrim(content_text)) > 0) OR source_asset_id IS NOT NULL",
            name="ck_t_memory_fragment_text_has_content",
        ),
        {"comment": "记忆会话中的独立素材片段"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="记忆片段ID")
    session_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_fragment_memory_session"
        ),
        nullable=False,
        comment="所属记忆会话 ID",
    )
    sequence_no = Column(Integer, nullable=False, comment="会话内片段顺序")
    # [新增] 素材片段改为区分来源类型、提供者、文件对象和直接文本。
    contributor_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_contributor.id", ondelete="RESTRICT", name="fk_t_memory_fragment_contributor"
        ),
        nullable=False,
        comment="素材提供者ID",
    )
    source_type = Column(
        String(20),
        nullable=False,
        server_default=text("'audio'"),
        comment="来源类型：text/audio/video",
    )
    # [删除-旧] audio_asset_id = Column(BigInteger, ForeignKey("t_media_asset.id", ondelete="CASCADE", name="fk_t_memory_fragment_audio_asset"), nullable=False, comment="语音媒体对象ID")
    # [新增] 文件引用可空，以支持直接输入文本。
    source_asset_id = Column(
        BigInteger,
        ForeignKey(
            "t_media_asset.id", ondelete="RESTRICT", name="fk_t_memory_fragment_source_asset"
        ),
        nullable=True,
        comment="来源文件对象ID",
    )
    content_text = Column(
        Text, nullable=True, comment="用户直接提供的文字内容，文件对象通过来源字段关联"
    )
    transcript_raw = Column(Text, nullable=True, comment="原始 ASR 转写文本，可由原素材提供者补交")
    transcript_edited = Column(Text, nullable=True, comment="用户修订后的转写文本")
    # [新增] 没有提交幂等请求头的片段保留空值。
    client_request_key = Column(Uuid, nullable=True, comment="客户端保存请求的幂等键")
    client_request_hash = Column(String(64), nullable=True, comment="保存请求内容的 SHA-256 摘要")
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="素材记录时间，支持客户端补录历史时间",
    )
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
