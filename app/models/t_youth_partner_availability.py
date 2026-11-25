"""[新增] 青年伙伴开放预约的时间区间。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
)

from app.models.base import AuditFields, Base


class YouthPartnerAvailability(AuditFields, Base):
    __tablename__ = "t_youth_partner_availability"
    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="ck_youth_partner_availability_interval"),
        Index("idx_youth_partner_availability_start", "partner_id", "starts_at"),
        {"comment": "青年伙伴开放预约的时间区间"},
    )

    id = Column(BigInteger, primary_key=True, comment="主键")
    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        nullable=False,
        comment="伙伴资料 ID",
    )
    starts_at = Column(DateTime(timezone=True), nullable=False, comment="开放开始时间")
    ends_at = Column(DateTime(timezone=True), nullable=False, comment="开放结束时间")
