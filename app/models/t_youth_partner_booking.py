"""[新增] 青年伙伴预约地点和占用扩展。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    text,
)

from app.models.base import AuditFields, Base


class YouthPartnerBooking(AuditFields, Base):
    __tablename__ = "t_youth_partner_booking"
    __table_args__ = (
        CheckConstraint(
            "blocking_start_at < blocking_end_at", name="ck_youth_partner_booking_interval"
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR confirmed_at <= confirm_by",
            name="ck_youth_partner_booking_confirm",
        ),
        CheckConstraint(
            "checked_in_at IS NULL OR (confirmed_at IS NOT NULL AND checked_in_at >= confirmed_at)",
            name="ck_youth_partner_booking_check_in",
        ),
        Index("idx_youth_partner_booking_schedule", "partner_id", "blocking_start_at"),
        Index(
            "idx_youth_partner_booking_confirmation",
            "confirm_by",
            "service_order_id",
            postgresql_where=text("confirmed_at IS NULL AND is_del = false"),
        ),
        {"comment": "青年伙伴预约地点和占用扩展"},
    )

    service_order_id = Column(
        BigInteger,
        ForeignKey("t_benefit_service_order.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="权益服务单 ID",
    )
    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        nullable=False,
        comment="获派伙伴资料 ID",
    )
    location_id = Column(
        BigInteger,
        ForeignKey("t_benefit_grant_service_location.id", ondelete="RESTRICT"),
        nullable=False,
        comment="预约地点 ID",
    )
    blocking_start_at = Column(
        DateTime(timezone=True), nullable=False, comment="含服务前通勤的占用开始时间"
    )
    blocking_end_at = Column(
        DateTime(timezone=True), nullable=False, comment="含服务后通勤的占用结束时间"
    )
    confirm_by = Column(DateTime(timezone=True), nullable=False, comment="伙伴确认截止时间")
    confirmed_at = Column(DateTime(timezone=True), nullable=True, comment="伙伴确认时间")
    checked_in_at = Column(DateTime(timezone=True), nullable=True, comment="到场核销时间")
