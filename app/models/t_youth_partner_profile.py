"""[新增] 青年伙伴注册及公开资料。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY

from app.models.base import AuditFields, Base


class YouthPartnerProfile(AuditFields, Base):
    __tablename__ = "t_youth_partner_profile"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'rejected', 'disabled')",
            name="ck_youth_partner_profile_status",
        ),
        Index("idx_youth_partner_profile_status", "status", "id"),
        Index("idx_youth_partner_profile_tags", "tags", postgresql_using="gin"),
        Index("idx_youth_partner_profile_areas", "service_area_codes", postgresql_using="gin"),
        {"comment": "青年伙伴注册及公开资料"},
    )

    id = Column(BigInteger, primary_key=True, comment="主键")
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        comment="申请人账号",
    )
    display_name = Column(String(100), nullable=False, comment="公开展示名")
    major = Column(String(100), comment="专业")
    education_label = Column(String(50), comment="学历或在读状态")
    bio = Column(String(300), comment="公开简介")
    tags = Column(
        ARRAY(String(32)), nullable=False, server_default=text("'{}'"), comment="展示和筛选标签"
    )
    service_area_codes = Column(
        ARRAY(String(32)),
        nullable=False,
        server_default=text("'{}'"),
        comment="申请或获批的服务区域编码",
    )
    photo_asset_id = Column(
        BigInteger, ForeignKey("t_media_asset.id", ondelete="RESTRICT"), comment="公开展示照素材 ID"
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
        comment="状态：draft 草稿、pending_review 待审、approved 通过、rejected 驳回、disabled 停用",
    )
    reviewed_by_user_id = Column(
        BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT"), comment="最近审核人账号"
    )
    reviewed_at = Column(DateTime(timezone=True), comment="最近审核时间")
    review_notes = Column(String(500), comment="最近审核说明")
