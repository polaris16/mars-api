"""记录首次参与话题的用户，用于统计去重讨论人数。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, PrimaryKeyConstraint, text

from app.models.base import Base


class TopicParticipant(Base):
    __tablename__ = "t_topic_participant"
    __table_args__ = (
        PrimaryKeyConstraint("topic_id", "user_id", name="pk_t_topic_participant"),
        {"comment": "话题去重参与用户记录"},
    )

    topic_id = Column(
        BigInteger,
        ForeignKey("t_topic.id", ondelete="CASCADE", name="fk_t_topic_participant_topic"),
        nullable=False,
        comment="话题ID",
    )
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_topic_participant_user"),
        nullable=False,
        comment="用户ID",
    )
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="首次参与时间",
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
