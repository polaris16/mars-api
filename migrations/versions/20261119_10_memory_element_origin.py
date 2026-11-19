"""Identify elements created by extraction and original-text fallback."""

import sqlalchemy as sa
from alembic import op

revision = "20261119_10"
down_revision = "20261119_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("t_memory_element", sa.Column(
        "origin", sa.String(20), nullable=False, server_default=sa.text("'extracted'"),
        comment="生成来源：extracted 正常抽取，fallback 原文兜底",
    ))
    op.create_check_constraint(
        "ck_t_memory_element_origin", "t_memory_element", "origin IN ('extracted', 'fallback')",
    )
    op.create_index(
        "uq_t_memory_element_active_fallback", "t_memory_element", ["source_fragment_id"], unique=True,
        postgresql_where=sa.text("origin = 'fallback' AND is_del = false"),
    )


def downgrade() -> None:
    # The previous schema cannot represent which rows contain fallback content.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM t_memory_element WHERE origin = 'fallback') THEN
                RAISE EXCEPTION 'Cannot downgrade while fallback elements exist';
            END IF;
        END $$;
    """)
    op.drop_index("uq_t_memory_element_active_fallback", table_name="t_memory_element")
    op.drop_constraint("ck_t_memory_element_origin", "t_memory_element", type_="check")
    op.drop_column("t_memory_element", "origin")
