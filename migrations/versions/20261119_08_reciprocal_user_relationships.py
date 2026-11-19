"""Store directional relationship facts as explicitly paired rows."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261119_08"
down_revision = "20261119_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_t_user_relationship_delegate_status", table_name="t_user_relationship")
    op.drop_constraint("uq_t_user_relationship_pair_type", "t_user_relationship", type_="unique")
    op.drop_constraint("fk_t_user_relationship_principal", "t_user_relationship", type_="foreignkey")
    op.drop_constraint("fk_t_user_relationship_delegate", "t_user_relationship", type_="foreignkey")
    op.drop_constraint("ck_t_user_relationship_distinct_users", "t_user_relationship", type_="check")

    op.alter_column("t_user_relationship", "principal_user_id", new_column_name="subject_user_id")
    op.alter_column("t_user_relationship", "delegate_user_id", new_column_name="related_user_id")
    op.add_column("t_user_relationship", sa.Column("relationship_pair_id", postgresql.UUID(as_uuid=True), nullable=True, comment="Shared identifier for reciprocal relationship rows"))
    op.add_column("t_user_relationship", sa.Column("relation_code", sa.String(length=30), nullable=True, comment="Directional relation code; reciprocal family codes are stored in paired rows"))
    op.add_column("t_user_relationship", sa.Column("initiated_by_user_id", sa.BigInteger(), nullable=True, comment="User who initiated the relationship invitation"))

    op.execute("""
        UPDATE t_user_relationship
        SET relationship_pair_id = gen_random_uuid(),
            relation_code = CASE relationship_type
                WHEN 'agent' THEN 'agent_for'
                WHEN 'caregiver' THEN 'caregiver_for'
                ELSE 'related_to'
            END,
            initiated_by_user_id = COALESCE(created_by, subject_user_id)
    """)

    op.alter_column("t_user_relationship", "relationship_pair_id", nullable=False)
    op.alter_column("t_user_relationship", "relationship_pair_id", server_default=sa.text("gen_random_uuid()"))
    op.alter_column("t_user_relationship", "relation_code", nullable=False)
    op.alter_column("t_user_relationship", "initiated_by_user_id", nullable=False)
    op.create_foreign_key("fk_t_user_relationship_subject", "t_user_relationship", "t_user", ["subject_user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_t_user_relationship_related", "t_user_relationship", "t_user", ["related_user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_t_user_relationship_initiator", "t_user_relationship", "t_user", ["initiated_by_user_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_t_user_relationship_distinct_users", "t_user_relationship", "subject_user_id <> related_user_id")
    op.create_check_constraint("ck_t_user_relationship_relation_code", "t_user_relationship", "relation_code IN ('parent_of', 'child_of', 'spouse_of', 'sibling_of', 'related_to', 'agent_for', 'caregiver_for', 'other')")
    op.create_unique_constraint("uq_t_user_relationship_pair_subject", "t_user_relationship", ["relationship_pair_id", "subject_user_id"])
    op.create_index("idx_t_user_relationship_subject_status", "t_user_relationship", ["subject_user_id", "status"])
    op.create_index("idx_t_user_relationship_related_status", "t_user_relationship", ["related_user_id", "status"])


def downgrade() -> None:
    op.drop_index("idx_t_user_relationship_related_status", table_name="t_user_relationship")
    op.drop_index("idx_t_user_relationship_subject_status", table_name="t_user_relationship")
    op.drop_constraint("uq_t_user_relationship_pair_subject", "t_user_relationship", type_="unique")
    op.drop_constraint("ck_t_user_relationship_relation_code", "t_user_relationship", type_="check")
    op.drop_constraint("ck_t_user_relationship_distinct_users", "t_user_relationship", type_="check")
    op.drop_constraint("fk_t_user_relationship_initiator", "t_user_relationship", type_="foreignkey")
    op.drop_constraint("fk_t_user_relationship_related", "t_user_relationship", type_="foreignkey")
    op.drop_constraint("fk_t_user_relationship_subject", "t_user_relationship", type_="foreignkey")
    op.drop_column("t_user_relationship", "initiated_by_user_id")
    op.drop_column("t_user_relationship", "relation_code")
    op.drop_column("t_user_relationship", "relationship_pair_id")
    op.alter_column("t_user_relationship", "related_user_id", new_column_name="delegate_user_id")
    op.alter_column("t_user_relationship", "subject_user_id", new_column_name="principal_user_id")
    op.create_check_constraint("ck_t_user_relationship_distinct_users", "t_user_relationship", "principal_user_id <> delegate_user_id")
    op.create_foreign_key("fk_t_user_relationship_delegate", "t_user_relationship", "t_user", ["delegate_user_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_t_user_relationship_principal", "t_user_relationship", "t_user", ["principal_user_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_t_user_relationship_pair_type", "t_user_relationship", ["principal_user_id", "delegate_user_id", "relationship_type"])
    op.create_index("idx_t_user_relationship_delegate_status", "t_user_relationship", ["delegate_user_id", "status"])
