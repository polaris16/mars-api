"""[新增] 企业采购所绑定的权益包版本。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class BenefitPackage(AuditFields, Base):
    __tablename__ = "t_benefit_package"
    __table_args__ = (
        UniqueConstraint("package_code", "version_no", name="uq_benefit_package_version"),
        CheckConstraint("version_no > 0", name="ck_benefit_package_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_benefit_package_status"
        ),
        CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="ck_benefit_package_published",
        ),
        Index("idx_benefit_package_status", "status", "id"),
        {"comment": "企业可采购的权益包版本"},
    )

    id = Column(BigInteger, primary_key=True, comment="权益包版本 ID")
    package_code = Column(String(50), nullable=False, comment="跨版本的稳定计划编码")
    version_no = Column(Integer, nullable=False, comment="同一计划内从 1 开始的版本号")
    title = Column(String(100), nullable=False, comment="面向用户的计划名称")
    description = Column(Text, comment="权益包计划说明")
    icon_url = Column(String(500), comment="权益包计划图标地址")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
        comment="版本状态：draft 草稿，published 已发布，retired 已退役",
    )
    published_at = Column(DateTime(timezone=True), comment="首次发布时间")
