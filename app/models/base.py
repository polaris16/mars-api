"""所有数据库表模型共用的 SQLAlchemy 2.x 基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
