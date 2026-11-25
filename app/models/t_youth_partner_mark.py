"""[新增] 登录用户的个人青年伙伴收藏。"""

from sqlalchemy import BigInteger, Column, ForeignKey, Index, text

from app.models.base import AuditFields, Base


class YouthPartnerMark(AuditFields, Base):
    __tablename__ = "t_youth_partner_mark"
    __table_args__ = (
        Index(
            "idx_youth_partner_mark_active_user",
            "user_id",
            "updated_at",
            "partner_id",
            postgresql_where=text("is_del = false"),
        ),
        {"comment": "用户个人青年伙伴收藏"},
    )

    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="收藏所属用户账号 ID",
    )
    partner_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="收藏的青年伙伴资料 ID",
    )
