"""[新增] 选择某个采购批次向客户展示的企业联系人。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, Integer, text

from app.models.base import AuditFields, Base


class BenefitBatchContact(AuditFields, Base):
    __tablename__ = "t_benefit_batch_contact"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_benefit_batch_contact_sort_order"),
        Index("idx_benefit_batch_contact_member", "enterprise_member_id"),
        {"comment": "权益采购批次展示的客户经理"},
    )

    purchase_batch_id = Column(
        BigInteger,
        ForeignKey("t_benefit_purchase_batch.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="配置联系人的权益采购批次 ID",
    )
    enterprise_member_id = Column(
        BigInteger,
        ForeignKey("t_enterprise_member.id", ondelete="RESTRICT"),
        primary_key=True,
        comment="企业联系人对应的成员登记 ID",
    )
    sort_order = Column(
        Integer, nullable=False, server_default=text("0"), comment="联系人的展示顺序"
    )
