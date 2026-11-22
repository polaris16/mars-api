"""[新增] 固定企业采购的计划版本、份数及业务时间窗口。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class BenefitPurchaseBatch(AuditFields, Base):
    __tablename__ = "t_benefit_purchase_batch"
    __table_args__ = (
        UniqueConstraint(
            "enterprise_id", "purchase_reference", name="uq_benefit_purchase_reference"
        ),
        CheckConstraint(
            "purchased_units > 0 AND paid_amount_minor > 0", name="ck_benefit_purchase_amounts"
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'closed')", name="ck_benefit_purchase_status"
        ),
        CheckConstraint(
            "issue_until <= claim_until AND claim_until <= service_valid_until "
            "AND service_valid_from < service_valid_until",
            name="ck_benefit_purchase_windows",
        ),
        CheckConstraint(
            "(status IN ('active', 'closed')) = (activated_at IS NOT NULL) AND "
            "(activated_at IS NULL OR (paid_at IS NOT NULL AND paid_at <= activated_at "
            "AND activated_at < issue_until)) AND "
            "(status = 'closed') = (closed_at IS NOT NULL) AND "
            "(closed_at IS NULL OR closed_at >= activated_at)",
            name="ck_benefit_purchase_status_times",
        ),
        Index("idx_benefit_purchase_enterprise_status", "enterprise_id", "status", "id"),
        Index("idx_benefit_purchase_package", "package_id"),
        {"comment": "企业权益包采购批次、付款及生效信息"},
    )

    id = Column(BigInteger, primary_key=True, comment="权益采购批次 ID")
    enterprise_id = Column(
        BigInteger,
        ForeignKey("t_enterprise.id", ondelete="RESTRICT"),
        nullable=False,
        comment="采购企业 ID",
    )
    package_id = Column(
        BigInteger,
        ForeignKey("t_benefit_package.id", ondelete="RESTRICT"),
        nullable=False,
        comment="采购时绑定的权益包版本 ID",
    )
    purchase_reference = Column(String(100), nullable=False, comment="企业内唯一采购参考号")
    purchase_contract_ref = Column(String(500), comment="企业签署的采购合同文档引用")
    purchased_units = Column(Integer, nullable=False, comment="采购的完整权益包份数")
    paid_amount_minor = Column(BigInteger, nullable=False, comment="以币种最小单位记录的实收金额")
    currency = Column(
        String(3), nullable=False, server_default=text("'CNY'"), comment="实收币种代码"
    )
    paid_at = Column(DateTime(timezone=True), comment="核验的实收时间")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
        comment="批次状态：draft 草稿，active 已生效，closed 已停止发放",
    )
    activated_at = Column(DateTime(timezone=True), comment="采购批次生效时间")
    closed_at = Column(DateTime(timezone=True), comment="采购批次关闭时间")
    issue_until = Column(DateTime(timezone=True), nullable=False, comment="企业发放截止时间")
    claim_until = Column(DateTime(timezone=True), nullable=False, comment="用户领取截止时间上限")
    service_valid_from = Column(
        DateTime(timezone=True), nullable=False, comment="服务可使用的起始时间"
    )
    service_valid_until = Column(
        DateTime(timezone=True), nullable=False, comment="服务可使用的截止时间"
    )
    display_timezone = Column(
        String(64),
        nullable=False,
        server_default=text("'Asia/Shanghai'"),
        comment="App 显示计划日期使用的时区",
    )
