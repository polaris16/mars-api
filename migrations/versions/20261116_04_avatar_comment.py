"""Align the existing avatar column comment with ORM metadata."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_04"
down_revision = "20261116_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "t_user",
        "avatar_url",
        existing_type=sa.String(length=500),
        existing_nullable=True,
        comment="用户头像地址",
    )


def downgrade() -> None:
    op.alter_column(
        "t_user",
        "avatar_url",
        existing_type=sa.String(length=500),
        existing_nullable=True,
        comment=None,
        existing_comment="用户头像地址",
    )
