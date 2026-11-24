"""[新增] 一次作品权益的体验轮次和预览计数。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)

from app.models.base import AuditFields, Base


class ProductExperienceRound(AuditFields, Base):
    __tablename__ = "t_product_experience_round"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'completed', 'expired')", name="ck_product_experience_round_status"
        ),
        CheckConstraint(
            "preview_limit > 0 AND preview_used >= 0 AND preview_reserved >= 0 "
            "AND preview_used + preview_reserved <= preview_limit",
            name="ck_product_experience_round_counts",
        ),
        CheckConstraint(
            "(status = 'open') = (closed_at IS NULL) "
            "AND (closed_at IS NULL OR preview_reserved = 0)",
            name="ck_product_experience_round_closed",
        ),
        # 开放轮次只在业务关闭后释放唯一位置，软删除不能重置额度。
        Index(
            "uq_product_experience_round_open",
            "balance_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("idx_product_experience_round_owner", "owner_user_id", "id"),
        Index(
            "idx_product_experience_round_expiry",
            "expires_at",
            "id",
            postgresql_where=text("status = 'open'"),
        ),
        {"comment": "产品体验轮次和预览额度"},
    )

    id = Column(BigInteger, primary_key=True, comment="体验轮次 ID")
    owner_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="权益领取人和轮次所有人 ID",
    )
    balance_id = Column(
        BigInteger,
        ForeignKey("t_benefit_balance.id", ondelete="RESTRICT"),
        nullable=False,
        comment="本轮关联的权益余额 ID",
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'open'"),
        comment="轮次状态：open 待确认、completed 已确认、expired 已到期",
    )
    preview_limit = Column(
        SmallInteger, nullable=False, server_default=text("20"), comment="本轮预览上限"
    )
    preview_used = Column(
        SmallInteger, nullable=False, server_default=text("0"), comment="成功生成的预览次数"
    )
    preview_reserved = Column(
        SmallInteger, nullable=False, server_default=text("0"), comment="在途任务占用的预览次数"
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="本轮截止时间")
    closed_at = Column(DateTime(timezone=True), comment="确认成功或到期关闭时间")
