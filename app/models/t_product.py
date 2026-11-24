"""[新增] 独立于发布版本的产品主档。"""

from sqlalchemy import BigInteger, Boolean, Column, Integer, String, text

from app.models.base import AuditFields, Base


class Product(AuditFields, Base):
    __tablename__ = "t_product"
    __table_args__ = {"comment": "产品目录主档"}

    id = Column(BigInteger, primary_key=True, comment="稳定产品 ID")
    code = Column(String(50), nullable=False, unique=True, comment="运营产品编码")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="目录展示顺序")
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"), comment="是否上架")
