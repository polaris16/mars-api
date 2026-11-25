"""[新增] 固定权益服务的说明、条款和交付口径。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class BenefitServiceVersion(AuditFields, Base):
    __tablename__ = "t_benefit_service_version"
    __table_args__ = (
        UniqueConstraint("service_id", "version_no", name="uq_benefit_service_version_number"),
        CheckConstraint("version_no > 0", name="ck_benefit_service_version_number"),
        CheckConstraint(
            "fulfillment_mode IN ('online', 'appointment')",
            name="ck_benefit_service_version_fulfillment",
        ),
        # 线上版本可以绑定已接入的履约能力。
        CheckConstraint(
            "online_handler IS NULL OR "
            "(fulfillment_mode = 'online' AND online_handler = 'product_experience')",
            name="ck_benefit_service_version_online_handler",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_benefit_service_version_status"
        ),
        CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="ck_benefit_service_version_published",
        ),
        Index("idx_benefit_service_version_status", "service_id", "status", "version_no"),
        {"comment": "权益服务说明、条款与履约规则版本"},
    )

    id = Column(BigInteger, primary_key=True, comment="服务版本 ID")
    service_id = Column(
        BigInteger,
        ForeignKey("t_benefit_service.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属权益服务主档 ID",
    )
    version_no = Column(Integer, nullable=False, comment="同一服务内从 1 开始的版本号")
    name = Column(String(100), nullable=False, comment="该版本的服务及权益卡片名称")
    summary = Column(String(300), comment="权益卡片短说明")
    description_ref = Column(String(500), nullable=False, comment="服务说明文档的固定版本引用")
    terms_ref = Column(String(500), nullable=False, comment="服务条款或合同的固定版本引用")
    delivery_terms = Column(Text, nullable=False, comment="单次交付及核销标准")
    fulfillment_mode = Column(
        String(20), nullable=False, comment="履约方式：online 线上，appointment 预约"
    )
    online_handler = Column(String(50), comment="线上履约能力编码，如 product_experience")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
        comment="版本状态：draft 草稿，published 已发布，retired 已退役",
    )
    published_at = Column(DateTime(timezone=True), comment="首次发布时间")
