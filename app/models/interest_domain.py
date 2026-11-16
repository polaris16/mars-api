"""可供用户选择的兴趣领域字典。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, PrimaryKeyConstraint, String, UniqueConstraint, text

from app.models.base import Base


class InterestDomain(Base):
    __tablename__ = "t_interest_domain"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_t_interest_domain"),
        UniqueConstraint("code", name="uk_t_interest_domain_code"),
        UniqueConstraint("name", name="uk_t_interest_domain_name"),
        {"comment": "兴趣领域字典表"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(50), nullable=False, comment="兴趣领域编码")
    name = Column(String(50), nullable=False, comment="兴趣领域名称")
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"), comment="是否启用")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="显示顺序")
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
