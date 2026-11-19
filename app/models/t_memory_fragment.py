"""[新增] 录音会话中的单段语音及其 ASR 转写。"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint, text

from app.models.base import Base


class MemoryFragment(Base):
    __tablename__ = "t_memory_fragment"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_fragment_pkey"),
        UniqueConstraint("session_id", "sequence_no", name="uq_t_memory_fragment_session_sequence"),
        # [删除-旧] audio_asset_id 曾唯一，限制一个媒体对象只能对应一个片段；媒体对象现可复用。
        # UniqueConstraint("audio_asset_id", name="uq_t_memory_fragment_audio_asset"),
        CheckConstraint("source_type IN ('text', 'audio', 'video')", name="ck_t_memory_fragment_source_type"),
        CheckConstraint(
            "source_type <> 'text' OR (content_text IS NOT NULL AND length(btrim(content_text)) > 0) OR source_asset_id IS NOT NULL",
            name="ck_t_memory_fragment_text_has_content",
        ),
        {"comment": "记忆会话中的独立素材片段"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="记忆片段ID")
    session_id = Column(
        BigInteger,
        ForeignKey("t_memory_session.id", ondelete="CASCADE", name="fk_t_memory_fragment_memory_session"),
        nullable=False,
        comment="所属录音会话ID",
    )
    sequence_no = Column(Integer, nullable=False, comment="会话内片段顺序")
    # [新增] 素材片段改为区分来源类型、提供者、文件对象和直接文本。
    contributor_id = Column(BigInteger, ForeignKey("t_memory_contributor.id", ondelete="RESTRICT", name="fk_t_memory_fragment_contributor"), nullable=False, comment="素材提供者ID")
    source_type = Column(String(20), nullable=False, server_default=text("'audio'"), comment="来源类型：text/audio/video")
    # [删除-旧] audio_asset_id = Column(BigInteger, ForeignKey("t_media_asset.id", ondelete="CASCADE", name="fk_t_memory_fragment_audio_asset"), nullable=False, comment="语音媒体对象ID")
    # [新增] 文件引用可空，以支持直接输入文本。
    source_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="RESTRICT", name="fk_t_memory_fragment_source_asset"),
        nullable=True,
        comment="来源文件对象ID",
    )
    content_text = Column(Text, nullable=True, comment="直接输入或文本文件内容")
    transcript_raw = Column(Text, nullable=True, comment="ASR原始转写文本")
    transcript_edited = Column(Text, nullable=True, comment="用户修订后的转写文本")
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="录音发生时间",
    )
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
