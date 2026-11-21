"""[新增] 支持同一片段的多个附件关联复用同一个文件。

Revision ID: 20261122_01
Revises: 20261121_03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261122_01"
down_revision = "20261121_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_fragment_attachment",
        sa.Column(
            "client_file_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="客户端新建附件关联的操作标识",
        ),
    )
    op.create_check_constraint(
        "ck_t_fragment_attachment_client_file_creator",
        "t_fragment_attachment",
        "client_file_id IS NULL OR created_by IS NOT NULL",
    )
    op.create_index(
        "idx_t_fragment_attachment_active_asset",
        "t_fragment_attachment",
        ["fragment_id", "media_asset_id"],
        postgresql_where=sa.text("is_del = false"),
    )
    op.create_index(
        "uq_t_fragment_attachment_client_file",
        "t_fragment_attachment",
        ["fragment_id", "created_by", "client_file_id"],
        unique=True,
        postgresql_where=sa.text("client_file_id IS NOT NULL"),
    )
    op.drop_index("uq_t_fragment_attachment_active_asset", table_name="t_fragment_attachment")


def downgrade() -> None:
    # 先恢复旧唯一索引。已有重复有效关联时，数据库会中止回退并保留数据。
    op.create_index(
        "uq_t_fragment_attachment_active_asset",
        "t_fragment_attachment",
        ["fragment_id", "media_asset_id"],
        unique=True,
        postgresql_where=sa.text("is_del = false"),
    )
    op.drop_index("idx_t_fragment_attachment_active_asset", table_name="t_fragment_attachment")
    op.drop_index("uq_t_fragment_attachment_client_file", table_name="t_fragment_attachment")
    op.drop_constraint(
        "ck_t_fragment_attachment_client_file_creator", "t_fragment_attachment", type_="check"
    )
    op.drop_column("t_fragment_attachment", "client_file_id")
