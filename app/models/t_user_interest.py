"""用户与兴趣领域的选择关系。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, text

from app.models.base import Base


class UserInterest(Base):
    __tablename__ = "t_user_interest"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "interest_id", name="pk_t_user_interest"),
        Index("idx_t_user_interest_interest_id", "interest_id"),
        {"comment": "用户兴趣领域关联表"},
    )

    user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_interest_user"), nullable=False, comment="用户ID")
    interest_id = Column(BigInteger, ForeignKey("t_interest_domain.id", ondelete="RESTRICT", name="fk_t_user_interest_domain"), nullable=False, comment="兴趣领域ID")
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
