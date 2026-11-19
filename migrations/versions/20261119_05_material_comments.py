"""Align material table and column comments with current ORM metadata."""

from alembic import op
import sqlalchemy as sa


revision = "20261119_05"
down_revision = "20261119_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 20261119_04 was applied before comment drift was detected by alembic check.
    # Keep this follow-up so already-upgraded databases and fresh installs match.
    op.alter_column("t_media_asset", "uploaded_by_user_id", existing_type=sa.BigInteger(), comment="上传账户ID")
    op.alter_column("t_media_asset", "purpose", existing_type=sa.String(length=30), comment="用途：recording/source/attachment")
    op.alter_column("t_media_asset", "media_type", existing_type=sa.String(length=20), comment="媒体类型：audio/image/video/text")
    op.alter_column("t_memory_fragment", "source_type", existing_type=sa.String(length=20), comment="来源类型：text/audio/video")
    op.alter_column("t_memory_fragment", "source_asset_id", existing_type=sa.BigInteger(), comment="来源文件对象ID")
    op.execute("COMMENT ON TABLE t_memory_fragment IS '记忆会话中的独立素材片段'")
    op.alter_column("t_fragment_attachment", "contributor_id", existing_type=sa.BigInteger(), comment="添加附件的提供者ID")


def downgrade() -> None:
    # Comments are descriptive only; retaining the current descriptions while
    # downgrading the structural migration is safe and avoids reintroducing
    # legacy, encoding-corrupted labels.
    pass
