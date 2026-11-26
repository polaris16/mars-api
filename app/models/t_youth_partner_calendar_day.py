"""[新增] 记录青年伙伴已发布的日历日期和明确预约开始时间。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY

from app.models.base import AuditFields, Base


class YouthPartnerCalendarDay(AuditFields, Base):
    __tablename__ = "t_youth_partner_calendar_day"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_youth_partner_calendar_day_version"),
        {"comment": "青年伙伴已发布的每日预约开始时间"},
    )

    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="日历所属青年伙伴资料 ID",
    )
    schedule_date = Column(Date, primary_key=True, comment="伙伴日历时区下的发布日期")
    version = Column(Integer, nullable=False, server_default=text("1"), comment="日期的发布版本")
    booking_starts = Column(
        ARRAY(DateTime(timezone=True)),
        nullable=False,
        server_default=text("'{}'"),
        comment="当天发布的预约开始绝对时间，空数组表示无可约开始时间",
    )
