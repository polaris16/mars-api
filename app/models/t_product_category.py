"""[新增] 产品目录分类。"""

from sqlalchemy import BigInteger, Boolean, Column, Integer, String, text

from app.models.base import AuditFields, Base


class ProductCategory(AuditFields, Base):
    __tablename__ = "t_product_category"
    __table_args__ = {"comment": "产品目录分类"}

    id = Column(BigInteger, primary_key=True, comment="分类 ID")
    code = Column(String(50), nullable=False, unique=True, comment="稳定分类编码")
    name = Column(String(100), nullable=False, comment="分类显示名称")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="显示顺序")
    is_enabled = Column(
        Boolean, nullable=False, server_default=text("true"), comment="是否展示并允许生成"
    )
