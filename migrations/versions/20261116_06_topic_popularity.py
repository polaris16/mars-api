"""Add general topic fallback and unique participant counts."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_06"
down_revision = "20261116_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 推荐兜底标记和去重讨论人数缓存。
    op.add_column(
        "t_topic",
        sa.Column(
            "is_general",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否通用话题",
        ),
    )
    op.add_column(
        "t_topic",
        sa.Column(
            "participant_count",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="聊过该话题的去重用户数",
        ),
    )

    # 通过复合主键保证同一用户只计入同一话题一次。
    op.create_table(
        "t_topic_participant",
        sa.Column("topic_id", sa.BigInteger(), nullable=False, comment="话题ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="用户ID"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="首次参与时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["t_topic.id"], ondelete="CASCADE", name="fk_t_topic_participant_topic"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["t_user.id"], ondelete="CASCADE", name="fk_t_topic_participant_user"
        ),
        sa.PrimaryKeyConstraint("topic_id", "user_id", name="pk_t_topic_participant"),
        comment="话题去重参与用户记录",
    )


def downgrade() -> None:
    op.drop_table("t_topic_participant")
    op.drop_column("t_topic", "participant_count")
    op.drop_column("t_topic", "is_general")
