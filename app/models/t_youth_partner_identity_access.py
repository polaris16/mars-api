"""[新增] 青年伙伴证件查看链接签发记录。"""

from sqlalchemy import BigInteger, Column, ForeignKey, Index

from app.models.base import AuditFields, Base


class YouthPartnerIdentityAccess(AuditFields, Base):
    __tablename__ = "t_youth_partner_identity_access"
    __table_args__ = (
        Index("idx_youth_partner_identity_access", "partner_id", "created_at"),
        {"comment": "青年伙伴证件查看链接签发记录"},
    )

    id = Column(BigInteger, primary_key=True, comment="主键")
    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        nullable=False,
        comment="被审核伙伴资料 ID",
    )
    reviewer_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="获取查看链接的审核人账号",
    )
