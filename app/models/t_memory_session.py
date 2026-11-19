"""可持续收集记忆素材的会话。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, String, text

from app.models.base import Base


class MemorySession(Base):
    __tablename__ = "t_memory_session"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_memory_session_pkey"),
        Index(
            "idx_t_memory_session_owner_last_material",
            "owner_user_id",
            "last_material_at",
            postgresql_where=text("is_del = false AND last_material_at IS NOT NULL"),
        ),
        {"comment": "记忆素材会话"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="会话ID")
    owner_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_memory_session_owner"),
        nullable=False,
        comment="回忆录所属用户ID",
    )
    topic_id = Column(
        BigInteger,
        ForeignKey("t_topic.id", ondelete="SET NULL", name="fk_t_memory_session_topic"),
        nullable=True,
        comment="关联话题ID",
    )
    title = Column(String(100), nullable=False, server_default=text("'未命名'"), comment="会话标题")
    is_favorite = Column(Boolean, nullable=False, server_default=text("false"), comment="是否收藏")
    last_material_at = Column(DateTime(timezone=True), nullable=True, comment="未删除片段中最晚的记录时间，无片段时为空")
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
