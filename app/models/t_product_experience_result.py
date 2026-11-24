"""[新增] 用户确认并完成权益扣次的产品体验作品。"""

from sqlalchemy import BigInteger, Column, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint

from app.models.base import AuditFields, Base


class ProductExperienceResult(AuditFields, Base):
    __tablename__ = "t_product_experience_result"
    __table_args__ = (
        UniqueConstraint("round_id", name="uq_product_result_round"),
        UniqueConstraint("service_order_id", name="uq_product_result_order"),
        # 将作品选中的预览锁定到同一轮次。
        ForeignKeyConstraint(
            ["round_id", "preview_id"],
            ["t_product_experience_preview.round_id", "t_product_experience_preview.id"],
            name="fk_product_result_round_preview",
            ondelete="RESTRICT",
        ),
        Index("idx_product_result_image", "image_asset_id"),
        {"comment": "已确认并完成扣次的产品体验作品"},
    )

    id = Column(BigInteger, primary_key=True, comment="正式作品 ID")
    round_id = Column(
        BigInteger,
        ForeignKey("t_product_experience_round.id", ondelete="RESTRICT"),
        nullable=False,
        comment="已确认的轮次 ID，作品所有人由轮次确定",
    )
    preview_id = Column(BigInteger, nullable=False, comment="同一轮次内选中的预览 ID")
    service_order_id = Column(
        BigInteger,
        ForeignKey("t_benefit_service_order.id", ondelete="RESTRICT"),
        nullable=False,
        comment="本次扣次对应的权益服务单 ID",
    )
    image_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="RESTRICT"),
        nullable=False,
        comment="用户选中预览对应的原成图 ID",
    )
