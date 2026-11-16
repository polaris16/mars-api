"""[新增] 录音会话中的单段语音及其 ASR 转写。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, text

from app.models.base import Base


class MemoryFragment(Base):
    __tablename__ = "t_memory_fragment"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_fragment_pkey"),
        UniqueConstraint("session_id", "sequence_no", name="uq_t_memory_fragment_session_sequence"),
        UniqueConstraint("audio_asset_id", name="uq_t_memory_fragment_audio_asset"),
        {"comment": "录音会话中的语音记忆片段"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="记忆片段ID")
    session_id = Column(
        BigInteger,
        ForeignKey("t_recording_session.id", ondelete="CASCADE", name="fk_t_memory_fragment_session"),
        nullable=False,
        comment="所属录音会话ID",
    )
    sequence_no = Column(Integer, nullable=False, comment="会话内片段顺序")
    audio_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="CASCADE", name="fk_t_memory_fragment_audio_asset"),
        nullable=False,
        comment="语音媒体对象ID",
    )
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
