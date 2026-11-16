"""Support phone-based registration and onboarding state."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_03"
down_revision = "20261116_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_user",
        sa.Column("onboarding_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False,
                  comment="是否完成首次引导"),
    )
    # 迁移前需确认未删除用户没有重复手机号，否则唯一索引创建会失败。
    op.drop_index("idx_user_phone", table_name="t_user")
    op.create_index(
        "uk_user_phone",
        "t_user",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("(is_del = false)"),
    )


def downgrade() -> None:
    op.drop_index("uk_user_phone", table_name="t_user")
    op.create_index(
        "idx_user_phone",
        "t_user",
        ["phone"],
        unique=False,
        postgresql_where=sa.text("(is_del = false)"),
    )
    op.drop_column("t_user", "onboarding_completed")
