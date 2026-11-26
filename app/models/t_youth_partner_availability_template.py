"""[新增] 青年伙伴工作日及休息日排班模板。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.models.base import AuditFields, Base


class YouthPartnerAvailabilityTemplate(AuditFields, Base):
    __tablename__ = "t_youth_partner_availability_template"
    __table_args__ = (
        CheckConstraint("kind IN ('workday', 'restday')", name="ck_youth_partner_template_kind"),
        CheckConstraint(
            "jsonb_typeof(windows) = 'array'", name="ck_youth_partner_template_windows"
        ),
        {"comment": "青年伙伴的工作日与休息日预约模板"},
    )

    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="模板所属青年伙伴资料 ID",
    )
    kind = Column(String(20), primary_key=True, comment="模板类别：workday 工作日、restday 休息日")
    windows = Column(
        JSONB,
        nullable=False,
        server_default=text("'[]'"),
        comment="模板开放区间，起止为当地午夜起的分钟数",
    )
    booking_start_minutes = Column(
        ARRAY(SmallInteger),
        nullable=False,
        server_default=text("'{}'"),
        comment="模板预约开始时间，当地午夜起的分钟数",
    )
