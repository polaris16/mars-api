"""[新增] 将附件归属提升到故事，保留原片段来源。"""

import sqlalchemy as sa
from alembic import op

revision = "20261127_01"
down_revision = "20261126_01"
branch_labels = None
depends_on = None

TABLE = "t_fragment_attachment"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("session_id", sa.BigInteger(), comment="附件所属故事会话ID"))
    op.execute(
        "UPDATE t_fragment_attachment AS a SET session_id = f.session_id "
        "FROM t_memory_fragment AS f WHERE f.id = a.fragment_id"
    )
    # 历史数据如果超过新上限，先人工选择保留项，避免迁移擅自删除。
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT a.session_id
                FROM t_fragment_attachment AS a
                JOIN t_memory_session AS s ON s.id = a.session_id
                WHERE NOT a.is_del AND NOT s.is_del
                GROUP BY a.session_id
                HAVING count(*) > 9
            ) THEN
                RAISE EXCEPTION 'Existing story has more than 9 active attachments';
            END IF;
        END
        $$;
        """)
    op.alter_column(TABLE, "session_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key(
        "fk_t_story_attachment_session", TABLE, "t_memory_session", ["session_id"], ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_t_fragment_attachment_fragment", TABLE, type_="foreignkey")
    op.create_foreign_key(
        "fk_t_fragment_attachment_fragment", TABLE, "t_memory_fragment", ["fragment_id"], ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        TABLE, "fragment_id", existing_type=sa.BigInteger(), nullable=True,
        comment="旧版附件来源片段ID，新故事附件为空",
    )
    op.create_index("idx_t_story_attachment_order", TABLE, ["session_id", "sort_order", "id"])
    op.create_index(
        "idx_t_story_attachment_active_asset", TABLE, ["session_id", "media_asset_id"],
        postgresql_where=sa.text("is_del = false"),
    )
    op.create_index(
        "uq_t_story_attachment_client_file", TABLE, ["session_id", "created_by", "client_file_id"],
        unique=True,
        postgresql_where=sa.text("fragment_id IS NULL AND client_file_id IS NOT NULL"),
    )
    op.create_table_comment(TABLE, "故事会话与附件素材关联表")


def downgrade() -> None:
    # 已有故事级关联时，恢复旧的非空约束会中止迁移，避免丢失附件。
    op.alter_column(
        TABLE, "fragment_id", existing_type=sa.BigInteger(), nullable=False, comment="语音片段ID",
    )
    op.drop_constraint("fk_t_fragment_attachment_fragment", TABLE, type_="foreignkey")
    op.create_foreign_key(
        "fk_t_fragment_attachment_fragment", TABLE, "t_memory_fragment", ["fragment_id"], ["id"],
        ondelete="CASCADE",
    )
    for index in (
        "uq_t_story_attachment_client_file", "idx_t_story_attachment_active_asset",
        "idx_t_story_attachment_order",
    ):
        op.drop_index(index, table_name=TABLE)
    op.drop_constraint("fk_t_story_attachment_session", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "session_id")
    op.create_table_comment(TABLE, "语音片段与多媒体素材关联表")
