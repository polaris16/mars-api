"""Create the curated topic library and interest mappings."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_05"
down_revision = "20261116_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_topic",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="话题ID"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="话题编码"),
        sa.Column("title", sa.String(length=100), nullable=False, comment="话题标题"),
        sa.Column("icon_key", sa.String(length=50), nullable=False, comment="前端图标标识"),
        sa.Column("summary", sa.String(length=300), nullable=False, comment="话题简介"),
        sa.Column("prompt", sa.Text(), nullable=False, comment="用于引导用户讲述的核心问题"),
        sa.Column("category", sa.String(length=50), nullable=False, comment="话题分类"),
        sa.Column("life_stage", sa.String(length=50), nullable=True, comment="适用人生阶段"),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False, comment="是否启用"),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="展示顺序"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.PrimaryKeyConstraint("id", name="t_topic_pkey"),
        sa.UniqueConstraint("code", name="t_topic_code_key"),
        comment="系统话题库",
    )
    op.create_table(
        "t_topic_interest",
        sa.Column("topic_id", sa.BigInteger(), nullable=False, comment="话题ID"),
        sa.Column("interest_id", sa.BigInteger(), nullable=False, comment="兴趣领域ID"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.ForeignKeyConstraint(["topic_id"], ["t_topic.id"], ondelete="CASCADE", name="fk_t_topic_interest_topic"),
        sa.ForeignKeyConstraint(["interest_id"], ["t_interest_domain.id"], ondelete="RESTRICT", name="fk_t_topic_interest_domain"),
        sa.PrimaryKeyConstraint("topic_id", "interest_id", name="pk_t_topic_interest"),
        comment="话题与兴趣领域关联表",
    )
    op.create_index("idx_t_topic_interest_interest_id", "t_topic_interest", ["interest_id"])


def downgrade() -> None:
    op.drop_index("idx_t_topic_interest_interest_id", table_name="t_topic_interest")
    op.drop_table("t_topic_interest")
    op.drop_table("t_topic")
