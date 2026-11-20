"""所有数据库表模型共用的 SQLAlchemy 2.x 基类。"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AuditFields:
    """新增业务表共用的审计和软删除字段。"""

    created_by = Column(BigInteger, comment="创建人 ID")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(BigInteger, comment="更新人 ID")
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否软删除")
