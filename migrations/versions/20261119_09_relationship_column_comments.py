"""Align comments after renaming relationship endpoint columns."""

from alembic import op
import sqlalchemy as sa


revision = "20261119_09"
down_revision = "20261119_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "t_user_relationship", "subject_user_id",
        existing_type=sa.BigInteger(), existing_nullable=False,
        comment="User represented as the subject of this directional relation",
    )
    op.alter_column(
        "t_user_relationship", "related_user_id",
        existing_type=sa.BigInteger(), existing_nullable=False,
        comment="Other user referenced by this directional relation",
    )


def downgrade() -> None:
    op.alter_column(
        "t_user_relationship", "subject_user_id",
        existing_type=sa.BigInteger(), existing_nullable=False,
        comment="User who owns or grants the relationship",
    )
    op.alter_column(
        "t_user_relationship", "related_user_id",
        existing_type=sa.BigInteger(), existing_nullable=False,
        comment="Related family member, caregiver, or delegate user",
    )
