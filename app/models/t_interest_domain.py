"""可供用户选择的兴趣领域字典。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import Base


class InterestDomain(Base):
    __tablename__ = "t_interest_domain"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_interest_domain_pkey"),
        UniqueConstraint("code", name="t_interest_domain_code_key"),
        UniqueConstraint("name", name="t_interest_domain_name_key"),
        {"comment": "兴趣领域字典表"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="兴趣领域ID")
    code = Column(String(50), nullable=False, comment="兴趣领域编码")
    name = Column(String(50), nullable=False, comment="兴趣领域名称")
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"), comment="是否启用")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="显示顺序")
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
