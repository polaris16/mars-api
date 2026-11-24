"""[新增] 正式作品在好友之间的站内分享和撤回。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    text,
)

from app.models.base import AuditFields, Base


class ProductExperienceShare(AuditFields, Base):
    __tablename__ = "t_product_experience_share"
    __table_args__ = (
        UniqueConstraint("sender_user_id", "idempotency_key", name="uq_product_share_request"),
        CheckConstraint(
            "sender_user_id <> recipient_user_id", name="ck_product_share_distinct_users"
        ),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_product_share_status"),
        CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)", name="ck_product_share_revoked"
        ),
        Index(
            "uq_product_share_active",
            "result_id",
            "recipient_user_id",
            "relationship_pair_id",
            unique=True,
            postgresql_where=text("status = 'active' AND is_del = false"),
        ),
        Index("idx_product_share_sender", "sender_user_id", "created_at", "id"),
        Index("idx_product_share_recipient", "recipient_user_id", "created_at", "id"),
        {"comment": "产品体验作品的站内好友分享"},
    )

    id = Column(BigInteger, primary_key=True, comment="分享记录 ID")
    result_id = Column(
        BigInteger,
        ForeignKey("t_product_experience_result.id", ondelete="RESTRICT"),
        nullable=False,
        comment="分享的正式作品 ID",
    )
    sender_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="发送人及作品所有人 ID",
    )
    recipient_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="收件好友 ID",
    )
    relationship_pair_id = Column(Uuid, nullable=False, comment="分享时绑定的双向好友关系组 ID")
    idempotency_key = Column(Uuid, nullable=False, comment="发送人请求的幂等键")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
        comment="分享状态：active 已分享、revoked 已撤回",
    )
    revoked_at = Column(DateTime(timezone=True), comment="分享撤回时间")
