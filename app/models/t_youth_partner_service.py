"""[新增] 青年伙伴获准提供的权益服务版本。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
)

from app.models.base import AuditFields, Base


class YouthPartnerService(AuditFields, Base):
    __tablename__ = "t_youth_partner_service"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'disabled')", name="ck_youth_partner_service_status"
        ),
        Index("idx_youth_partner_service_version", "service_version_id", "status", "partner_id"),
        {"comment": "青年伙伴获准提供的权益服务版本"},
    )

    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="伙伴资料 ID",
    )
    service_version_id = Column(
        BigInteger,
        ForeignKey("t_benefit_service_version.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="权益服务版本 ID",
    )
    status = Column(
        String(20), nullable=False, comment="状态：pending 申请、approved 核准、disabled 停用"
    )
