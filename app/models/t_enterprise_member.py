"""[新增] 企业成员的工作台权限及工作联系方式。"""

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


class EnterpriseMember(AuditFields, Base):
    __tablename__ = "t_enterprise_member"
    __table_args__ = (
        UniqueConstraint("enterprise_id", "user_id", name="uq_enterprise_member_user"),
        CheckConstraint("role IN ('admin', 'issuer', 'support')", name="ck_enterprise_member_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_enterprise_member_status"),
        Index("idx_enterprise_member_user_status", "user_id", "status", "enterprise_id"),
        {"comment": "企业成员、组织权限及工作联系资料"},
    )

    id = Column(BigInteger, primary_key=True, comment="企业成员 ID")
    enterprise_id = Column(
        BigInteger,
        ForeignKey("t_enterprise.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属合作企业 ID",
    )
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="成员的用户账号 ID",
    )
    role = Column(
        String(20), nullable=False, comment="企业角色：admin 管理，issuer 发放，support 咨询"
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="成员状态：active 启用，disabled 停用",
    )
    work_display_name = Column(String(100), comment="工作展示称呼")
    work_title = Column(String(100), comment="对外展示的工作职位或简介")
    work_phone = Column(String(32), comment="企业提供的工作联系电话")
