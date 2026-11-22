"""[新增] 平台运营、派单及履约岗位。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import AuditFields, Base


class BenefitStaff(AuditFields, Base):
    __tablename__ = "t_benefit_staff"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_benefit_staff_user_role"),
        CheckConstraint(
            "role IN ('admin', 'ops', 'dispatcher', 'provider')", name="ck_benefit_staff_role"
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_benefit_staff_status"),
        Index("idx_benefit_staff_role_status", "role", "status", "user_id"),
        {"comment": "权益平台运营与履约岗位"},
    )

    id = Column(BigInteger, primary_key=True, comment="平台岗位登记 ID")
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="平台工作人员账号 ID",
    )
    role = Column(
        String(20),
        nullable=False,
        comment="岗位：admin 管理，ops 运营，dispatcher 派单，provider 履约",
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="岗位状态：active 启用，disabled 停用",
    )
