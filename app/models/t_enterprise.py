"""[新增] 权益采购及发放的合作企业。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, String, UniqueConstraint, text

from app.models.base import AuditFields, Base


class Enterprise(AuditFields, Base):
    __tablename__ = "t_enterprise"
    __table_args__ = (
        UniqueConstraint("code", name="uq_enterprise_code"),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_enterprise_status"),
        {"comment": "权益合作企业"},
    )

    id = Column(BigInteger, primary_key=True, comment="合作企业 ID")
    code = Column(String(50), nullable=False, comment="稳定机构编码")
    display_name = Column(String(100), nullable=False, comment="面向用户的企业名称")
    icon_url = Column(String(500), comment="企业展示图标地址")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="机构状态：active 启用，suspended 停止企业工作台操作",
    )
