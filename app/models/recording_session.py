"""[新增] 可持续追加语音片段的录音会话。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, text

from app.models.base import Base


class RecordingSession(Base):
    __tablename__ = "t_recording_session"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_recording_session_pkey"),
        Index(
            "idx_t_recording_session_user_last_recorded",
            "user_id",
            "last_recorded_at",
            postgresql_where=text("is_del = false AND last_recorded_at IS NOT NULL"),
        ),
        {"comment": "可持续追加语音片段的录音会话"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="录音会话ID")
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_recording_session_user"),
        nullable=False,
        comment="所属用户ID",
    )
    topic_id = Column(
        BigInteger,
        ForeignKey("t_topic.id", ondelete="SET NULL", name="fk_t_recording_session_topic"),
        nullable=True,
        comment="关联话题ID",
    )
    title = Column(String(100), nullable=False, server_default=text("'未命名'"), comment="会话标题")
    is_favorite = Column(Boolean, nullable=False, server_default=text("false"), comment="是否收藏")
    last_recorded_at = Column(DateTime(timezone=True), nullable=True, comment="最近录音时间")
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
