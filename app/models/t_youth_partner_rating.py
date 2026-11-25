"""[新增] 领取人对已完成青年伙伴服务的评价。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    SmallInteger,
    String,
)

from app.models.base import AuditFields, Base


class YouthPartnerRating(AuditFields, Base):
    __tablename__ = "t_youth_partner_rating"
    __table_args__ = (
        CheckConstraint("score BETWEEN 1 AND 5", name="ck_youth_partner_rating_score"),
        {"comment": "领取人对已完成青年伙伴服务的评价"},
    )

    service_order_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_booking.service_order_id", ondelete="RESTRICT"),
        primary_key=True,
        comment="青年伙伴预约的服务单 ID",
    )
    score = Column(SmallInteger, nullable=False, comment="服务星级，范围 1 至 5")
    comment = Column(String(500), nullable=True, comment="可选评价内容")
