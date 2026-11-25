"""[新增] 持久化联系人、亲属、代理等注册用户关系。"""

# [删除-旧] """Registered-user family, care, and delegation relationships."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class UserRelationship(Base):
    __tablename__ = "t_user_relationship"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_user_relationship_pkey"),
        CheckConstraint(
            "subject_user_id <> related_user_id", name="ck_t_user_relationship_distinct_users"
        ),
        CheckConstraint(
            "relationship_type IN ('family', 'agent', 'caregiver', 'other', 'contact')",
            name="ck_t_user_relationship_type",
        ),
        CheckConstraint(
            "(relationship_type = 'contact' AND relation_code = 'contact_of') OR "
            "(relationship_type <> 'contact' AND relation_code IN "
            "('parent_of', 'child_of', 'spouse_of', 'sibling_of', 'related_to', "
            "'agent_for', 'caregiver_for', 'other'))",
            name="ck_t_user_relationship_relation_code",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'revoked', 'expired')",
            name="ck_t_user_relationship_status",
        ),
        CheckConstraint(
            "authorization_method IN ('in_app', 'offline_agreement', 'admin_verified')",
            name="ck_t_user_relationship_auth_method",
        ),
        UniqueConstraint(
            "relationship_pair_id", "subject_user_id", name="uq_t_user_relationship_pair_subject"
        ),
        Index("idx_t_user_relationship_subject_status", "subject_user_id", "status"),
        Index("idx_t_user_relationship_related_status", "related_user_id", "status"),
        {"comment": "注册用户间的联系人、亲属、代理或其他关系"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 ID"
    )
    relationship_pair_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=text("gen_random_uuid()"),
        comment="关系组 ID，双向联系人或亲属记录共用",
    )
    subject_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_subject"),
        nullable=False,
        comment="定向关系的主体用户 ID",
    )
    related_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_user_relationship_related"),
        nullable=False,
        comment="定向关系指向的用户 ID",
    )
    relationship_type = Column(
        String(30),
        nullable=False,
        comment="关系类型：contact 联系人、family 亲属、agent 代理、caregiver 照护、other 其他",
    )
    relation_code = Column(
        String(30),
        nullable=False,
        comment="主体对目标的关系码，联系人双向均为 contact_of",
    )
    relationship_label = Column(
        String(100), comment="用户填写的自定义关系称谓"
    )
    # 留言保存在发起方向，历史申请和反向记录允许为空。
    request_message = Column(String(200), nullable=True, comment="发起人填写的好友申请留言")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="关系状态：pending 待确认、active 有效、rejected 已拒绝、revoked 已撤销、expired 已过期",
    )
    authorization_method = Column(
        String(30),
        nullable=False,
        comment="关系确认方式：in_app 应用内、offline_agreement 线下约定、admin_verified 人工核验",
    )
    authorization_reference = Column(
        String(255), comment="关系授权的凭据或外部记录引用"
    )
    initiated_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT", name="fk_t_user_relationship_initiator"),
        nullable=False,
        comment="发起关系邀请的用户 ID",
    )
    verified_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="SET NULL", name="fk_t_user_relationship_verifier"),
        comment="核验关系的用户 ID",
    )
    valid_from = Column(
        DateTime(timezone=True), comment="关系生效时间"
    )
    valid_until = Column(
        DateTime(timezone=True), comment="关系有效期截止时间，空值表示无单独期限"
    )
    invitation_expires_at = Column(
        DateTime(timezone=True), comment="待确认邀请的截止时间，独立于关系有效期"
    )
    accepted_at = Column(
        DateTime(timezone=True), comment="接收方确认关系的时间"
    )
    revoked_at = Column(
        DateTime(timezone=True), comment="撤回邀请或撤销关系的时间"
    )
    revocation_reason = Column(
        String(255), comment="撤回邀请或撤销关系的原因"
    )
    created_by = Column(BigInteger, comment="创建人 ID")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(BigInteger, comment="更新人 ID")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(
        Boolean, nullable=False, server_default=text("false"), comment="是否软删除"
    )
