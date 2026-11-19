"""话题与兴趣领域的多对多关联。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    text,
)

from app.models.base import Base


class TopicInterest(Base):
    __tablename__ = "t_topic_interest"
    __table_args__ = (
        PrimaryKeyConstraint("topic_id", "interest_id", name="pk_t_topic_interest"),
        Index("idx_t_topic_interest_interest_id", "interest_id"),
        {"comment": "话题与兴趣领域关联表"},
    )

    topic_id = Column(
        BigInteger,
        ForeignKey("t_topic.id", ondelete="CASCADE", name="fk_t_topic_interest_topic"),
        nullable=False,
        comment="话题ID",
    )
    interest_id = Column(
        BigInteger,
        ForeignKey("t_interest_domain.id", ondelete="RESTRICT", name="fk_t_topic_interest_domain"),
        nullable=False,
        comment="兴趣领域ID",
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
