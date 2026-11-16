"""用户与兴趣领域的选择关系。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint, text

from app.models.base import Base


class UserInterest(Base):
    __tablename__ = "t_user_interest"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "interest_id", name="pk_t_user_interest"),
        Index("idx_t_user_interest_interest_id", "interest_id"),
    )

    user_id = Column(BigInteger, ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_interest_user"), nullable=False)
    interest_id = Column(BigInteger, ForeignKey("t_interest_domain.id", ondelete="RESTRICT", name="fk_t_user_interest_domain"), nullable=False)
    created_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_by = Column(BigInteger, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    is_del = Column(Boolean, nullable=False, server_default=text("false"))
