"""Create interest catalog and user selections."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_02"
down_revision = "20261116_01"
branch_labels = None
depends_on = None


INTERESTS = [
    ("technology", "科技数码", 10),
    ("artificial_intelligence", "人工智能", 20),
    ("business_finance", "商业财经", 30),
    ("learning_growth", "学习成长", 40),
    ("career_development", "职场发展", 50),
    ("sports_fitness", "运动健身", 60),
    ("travel_outdoors", "旅游户外", 70),
    ("food", "美食", 80),
    ("fashion_beauty", "时尚美妆", 90),
    ("movies_entertainment", "影视娱乐", 100),
    ("games_anime", "游戏动漫", 110),
    ("music", "音乐", 120),
    ("photography", "摄影", 130),
    ("reading", "阅读", 140),
    ("parenting_family", "亲子家庭", 150),
]


def upgrade() -> None:
    op.create_table(
        "t_interest_domain",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="兴趣领域ID"),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="t_interest_domain_pkey"),
        sa.UniqueConstraint("code", name="t_interest_domain_code_key"),
        sa.UniqueConstraint("name", name="t_interest_domain_name_key"),
        comment="兴趣领域字典表",
    )
    op.create_table(
        "t_user_interest",
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="用户ID"),
        sa.Column("interest_id", sa.BigInteger(), nullable=False, comment="兴趣领域ID"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="CASCADE", name="fk_t_user_interest_user"),
        sa.ForeignKeyConstraint(["interest_id"], ["t_interest_domain.id"], ondelete="RESTRICT", name="fk_t_user_interest_domain"),
        sa.PrimaryKeyConstraint("user_id", "interest_id", name="pk_t_user_interest"),
        comment="用户兴趣领域关联表",
    )
    op.create_index("idx_t_user_interest_interest_id", "t_user_interest", ["interest_id"])
    op.bulk_insert(
        sa.table(
            "t_interest_domain",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("sort_order", sa.Integer),
        ),
        [{"code": code, "name": name, "sort_order": order} for code, name, order in INTERESTS],
    )


def downgrade() -> None:
    op.drop_index("idx_t_user_interest_interest_id", table_name="t_user_interest")
    op.drop_table("t_user_interest")
    op.drop_table("t_interest_domain")
