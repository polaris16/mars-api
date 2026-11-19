"""系统维护的话题库模型。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)

from app.models.base import Base


class Topic(Base):
    __tablename__ = "t_topic"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_topic_pkey"),
        UniqueConstraint("code", name="t_topic_code_key"),
        {"comment": "系统话题库"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="话题ID")
    code = Column(String(64), nullable=False, comment="话题编码")
    title = Column(String(100), nullable=False, comment="话题标题")
    icon_key = Column(String(50), nullable=False, comment="前端图标标识")
    summary = Column(String(300), nullable=False, comment="话题简介")
    prompt = Column(Text, nullable=False, comment="用于引导用户讲述的核心问题")
    category = Column(String(50), nullable=False, comment="话题分类")
    life_stage = Column(String(50), nullable=True, comment="适用人生阶段")
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"), comment="是否启用")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="展示顺序")
    is_general = Column(
        Boolean, nullable=False, server_default=text("false"), comment="是否通用话题"
    )
    participant_count = Column(
        BigInteger, nullable=False, server_default=text("0"), comment="聊过该话题的去重用户数"
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
