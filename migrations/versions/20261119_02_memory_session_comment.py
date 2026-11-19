"""Align the renamed memory session primary key comment."""

from alembic import op

revision = "20261119_02"
down_revision = "20261119_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("COMMENT ON COLUMN t_memory_session.id IS '会话ID'")


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN t_memory_session.id IS '录音会话ID'")
