"""Add avatar URL to user profile."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_01"
down_revision = "20261115_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为现有用户表增加头像地址字段。"""
    op.add_column("t_user", sa.Column("avatar_url", sa.String(length=500), nullable=True, comment="用户头像地址"))


def downgrade() -> None:
    """回滚时删除头像地址字段。"""
    op.drop_column("t_user", "avatar_url")
