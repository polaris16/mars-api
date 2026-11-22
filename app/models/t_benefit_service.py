"""[新增] 保存跨版本不变的服务目录编码。"""

from sqlalchemy import BigInteger, CheckConstraint, Column, String, UniqueConstraint, text

from app.models.base import AuditFields, Base


class BenefitService(AuditFields, Base):
    __tablename__ = "t_benefit_service"
    __table_args__ = (
        UniqueConstraint("code", name="uq_benefit_service_code"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_benefit_service_status"),
        {"comment": "权益服务目录主档"},
    )

    id = Column(BigInteger, primary_key=True, comment="权益服务 ID")
    code = Column(String(50), nullable=False, comment="稳定服务编码，用于导航和处理器匹配")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="目录状态：active 启用，disabled 停止新增引用",
    )
