"""SQLAlchemy model for the existing PostgreSQL t_user table."""

from sqlalchemy import Column, Index, PrimaryKeyConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, BOOLEAN, DATE, SMALLINT, TEXT, TIMESTAMP, VARCHAR

from app.models.base import Base


class TUser(Base):
    __tablename__ = 't_user'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='t_user_pkey'),
        Index('idx_user_phone', 'phone', unique=False, postgresql_where=text('(is_del = false)')),
        Index('idx_user_tags', 'tags', unique=False, postgresql_using='gin'),
        Index('uk_user_username', 'username', unique=True, postgresql_where=text('(is_del = false)')),
        {'comment': '用户信息表'},
    )

    id = Column(BIGINT(), nullable=False, server_default=text('nextval(\'"public".t_user_id_seq\'::regclass)'), comment='主键ID')
    username = Column(VARCHAR(length=20), nullable=False, comment='登录用户名/账号')
    display_name = Column(VARCHAR(length=100), nullable=True, comment='显示名称/昵称')
    phone = Column(VARCHAR(length=20), nullable=False, comment='手机号码')
    sex = Column(SMALLINT(), nullable=True, server_default=text('0'), comment='性别 (0:未知, 1:男, 2:女)')
    birth_date = Column(DATE(), nullable=True, comment='出生日期')
    tags = Column(ARRAY(TEXT()), nullable=True, server_default=text("'{}'::text[]"), comment='用户标签数组')
    user_role = Column(SMALLINT(), nullable=True, server_default=text('0'), comment='用户角色 (0:普通用户, 1:企业用户)')
    created_by = Column(BIGINT(), nullable=True, comment='创建人ID')
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='创建时间')
    updated_by = Column(BIGINT(), nullable=True, comment='更新人ID')
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='更新时间')
    is_del = Column(BOOLEAN(), nullable=False, server_default=text('false'), comment='是否删除 (false:未删除, true:已删除)')
