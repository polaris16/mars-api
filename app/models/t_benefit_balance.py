"""[新增] 每份已领取权益中某个服务的独立次数字段。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class BenefitBalance(AuditFields, Base):
    __tablename__ = "t_benefit_balance"
    __table_args__ = (
        UniqueConstraint("grant_id", "package_item_id", name="uq_benefit_balance_grant_item"),
        CheckConstraint(
            "granted_count > 0 AND reserved_count >= 0 AND used_count >= 0 "
            "AND used_count <= granted_count AND reserved_count <= granted_count - used_count",
            name="ck_benefit_balance_counts",
        ),
        {"comment": "用户一份权益内单项服务的发放、占用及核销次数"},
    )

    id = Column(BigInteger, primary_key=True, comment="服务余额 ID")
    grant_id = Column(
        BigInteger,
        ForeignKey("t_benefit_grant.id", ondelete="RESTRICT"),
        nullable=False,
        comment="已领取的权益 ID",
    )
    package_item_id = Column(
        BigInteger,
        ForeignKey("t_benefit_package_item.id", ondelete="RESTRICT"),
        nullable=False,
        comment="领取时适用的权益包服务明细 ID",
    )
    granted_count = Column(Integer, nullable=False, comment="领取时发放的服务总次数")
    reserved_count = Column(
        Integer, nullable=False, server_default=text("0"), comment="当前占用次数"
    )
    used_count = Column(Integer, nullable=False, server_default=text("0"), comment="累计已核销次数")
