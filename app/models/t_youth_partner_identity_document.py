"""[新增] 青年伙伴私有身份证照片引用。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey

from app.models.base import AuditFields, Base


class YouthPartnerIdentityDocument(AuditFields, Base):
    __tablename__ = "t_youth_partner_identity_document"
    __table_args__ = (
        CheckConstraint(
            "portrait_side_asset_id <> emblem_side_asset_id",
            name="ck_youth_partner_identity_distinct",
        ),
        {"comment": "青年伙伴私有身份证照片引用"},
    )

    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="伙伴资料 ID",
    )
    portrait_side_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="RESTRICT"),
        comment="身份证人像面素材 ID",
    )
    emblem_side_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="RESTRICT"),
        comment="身份证国徽面素材 ID",
    )
