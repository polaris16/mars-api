"""[新增] 固定产品资料和生成能力的发布版本。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.models.base import AuditFields, Base


class ProductVersion(AuditFields, Base):
    __tablename__ = "t_product_version"
    __table_args__ = (
        UniqueConstraint("product_id", "version_no", name="uq_product_version_number"),
        CheckConstraint("version_no > 0", name="ck_product_version_number"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_product_version_status"
        ),
        CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="ck_product_version_published",
        ),
        CheckConstraint(
            "jsonb_typeof(attributes) = 'object'", name="ck_product_version_attributes"
        ),
        CheckConstraint(
            "generation_modes <@ ARRAY['try_on', 'scene_composite']::text[] "
            "AND (status = 'draft' OR cardinality(generation_modes) > 0)",
            name="ck_product_version_modes",
        ),
        Index(
            "uq_product_version_published",
            "product_id",
            unique=True,
            postgresql_where=text("status = 'published' AND is_del = false"),
        ),
        Index("idx_product_version_category", "category_id", "status", "product_id"),
        {"comment": "产品目录资料和生成能力版本"},
    )

    id = Column(BigInteger, primary_key=True, comment="产品版本 ID")
    product_id = Column(
        BigInteger,
        ForeignKey("t_product.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属产品 ID",
    )
    version_no = Column(Integer, nullable=False, comment="同产品内递增版本号")
    category_id = Column(
        BigInteger,
        ForeignKey("t_product_category.id", ondelete="RESTRICT"),
        nullable=False,
        comment="该版本所属分类 ID",
    )
    name = Column(String(150), nullable=False, comment="该版本产品名称")
    description = Column(Text, comment="产品说明")
    attributes = Column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment="品类属性 JSON 对象"
    )
    generation_modes = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
        comment="可用生成方式：try_on 试穿、scene_composite 场景合成",
    )
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
        comment="版本状态：draft 草稿、published 已发布、retired 已退役",
    )
    published_at = Column(DateTime(timezone=True), comment="首次发布时间")
