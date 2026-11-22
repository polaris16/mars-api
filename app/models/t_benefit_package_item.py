"""[新增] 权益包版本引用的服务版本及每份次数。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class BenefitPackageItem(AuditFields, Base):
    __tablename__ = "t_benefit_package_item"
    __table_args__ = (
        UniqueConstraint(
            "package_id", "service_version_id", name="uq_benefit_package_item_version"
        ),
        CheckConstraint("quota_per_unit > 0", name="ck_benefit_package_item_quota"),
        CheckConstraint("sort_order >= 0", name="ck_benefit_package_item_sort_order"),
        Index("idx_benefit_package_item_order", "package_id", "sort_order", "id"),
        Index("idx_benefit_package_item_version", "service_version_id"),
        {"comment": "权益包版本中的服务及次数"},
    )

    id = Column(BigInteger, primary_key=True, comment="权益包明细 ID")
    package_id = Column(
        BigInteger,
        ForeignKey("t_benefit_package.id", ondelete="RESTRICT"),
        nullable=False,
        comment="权益包版本 ID",
    )
    service_version_id = Column(
        BigInteger,
        ForeignKey("t_benefit_service_version.id", ondelete="RESTRICT"),
        nullable=False,
        comment="固定的服务说明、条款及履约版本 ID",
    )
    icon_key = Column(String(50), comment="权益卡片图标映射标识")
    quota_per_unit = Column(Integer, nullable=False, comment="每份权益包含的该服务次数")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="卡片展示顺序")
