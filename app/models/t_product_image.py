"""[新增] 产品版本使用的封面和模型参考图。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)

from app.models.base import AuditFields, Base


class ProductImage(AuditFields, Base):
    __tablename__ = "t_product_image"
    __table_args__ = (
        CheckConstraint(
            "angle IN ('unspecified', 'front', 'side', 'back', 'detail')",
            name="ck_product_image_angle",
        ),
        CheckConstraint("is_cover OR is_reference", name="ck_product_image_usage"),
        Index(
            "uq_product_image_version_asset",
            "product_version_id",
            "media_asset_id",
            unique=True,
            postgresql_where=text("is_del = false"),
        ),
        Index(
            "uq_product_image_version_cover",
            "product_version_id",
            unique=True,
            postgresql_where=text("is_cover = true AND is_del = false"),
        ),
        {"comment": "产品版本的封面和模型参考图"},
    )

    id = Column(BigInteger, primary_key=True, comment="图片关联 ID")
    product_version_id = Column(
        BigInteger,
        ForeignKey("t_product_version.id", ondelete="RESTRICT"),
        nullable=False,
        comment="图片所属产品版本 ID",
    )
    media_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="RESTRICT"),
        nullable=False,
        comment="已确认的产品参考图媒体 ID",
    )
    angle = Column(
        String(20),
        nullable=False,
        server_default=text("'unspecified'"),
        comment="图片角度：未指定、正面、侧面、背面或细节",
    )
    is_cover = Column(
        Boolean, nullable=False, server_default=text("false"), comment="是否用于产品封面"
    )
    is_reference = Column(
        Boolean, nullable=False, server_default=text("true"), comment="是否可供模型参考"
    )
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="图片展示顺序")
