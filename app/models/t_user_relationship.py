"""Registered-user family, care, and delegation relationships."""

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
            "relationship_type IN ('family', 'agent', 'caregiver', 'other')",
            name="ck_t_user_relationship_type",
        ),
        CheckConstraint(
            "relation_code IN ('parent_of', 'child_of', 'spouse_of', 'sibling_of', 'related_to', 'agent_for', 'caregiver_for', 'other')",
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
        {"comment": "注册用户间的亲友或代理关系"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 ID"
    )  # [UPDATED] Added column comment.
    relationship_pair_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=text("gen_random_uuid()"),
        comment="关系组 ID，双向亲属记录共用",
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
        comment="关系类型：family 亲属、agent 代理、caregiver 照护、other 其他",
    )  # [UPDATED] Added column comment.
    relation_code = Column(
        String(30), nullable=False, comment="主体对目标的关系码，如 parent_of 的反向为 child_of"
    )
    relationship_label = Column(
        String(100), comment="用户填写的自定义关系称谓"
    )  # [UPDATED] Added column comment.
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
        comment="关系状态：pending 待确认、active 有效、rejected 已拒绝、revoked 已撤销、expired 已过期",
    )  # [UPDATED] Added column comment.
    authorization_method = Column(
        String(30),
        nullable=False,
        comment="关系确认方式：in_app 应用内、offline_agreement 线下约定、admin_verified 人工核验",
    )  # [UPDATED] Added column comment.
    authorization_reference = Column(
        String(255), comment="关系授权的凭据或外部记录引用"
    )  # [UPDATED] Added column comment.
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
    )  # [UPDATED] Added column comment.
    valid_from = Column(
        DateTime(timezone=True), comment="关系生效时间"
    )  # [UPDATED] Added column comment.
    valid_until = Column(
        DateTime(timezone=True), comment="关系有效期截止时间，空值表示无单独期限"
    )  # [UPDATED] Added column comment.
    invitation_expires_at = Column(
        DateTime(timezone=True), comment="待确认邀请的截止时间，独立于关系有效期"
    )
    accepted_at = Column(
        DateTime(timezone=True), comment="接收方确认关系的时间"
    )  # [UPDATED] Added column comment.
    revoked_at = Column(
        DateTime(timezone=True), comment="撤回邀请或撤销关系的时间"
    )  # [UPDATED] Added column comment.
    revocation_reason = Column(
        String(255), comment="撤回邀请或撤销关系的原因"
    )  # [UPDATED] Added column comment.
    created_by = Column(BigInteger, comment="创建人 ID")  # [UPDATED] Added column comment.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )  # [UPDATED] Added column comment.
    updated_by = Column(BigInteger, comment="更新人 ID")  # [UPDATED] Added column comment.
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )  # [UPDATED] Added column comment.
    is_del = Column(
        Boolean, nullable=False, server_default=text("false"), comment="是否软删除"
    )  # [UPDATED] Added column comment.
