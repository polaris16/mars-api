"""[新增] 扩展用户关系约束，支持双向联系人。

Revision ID: 20261121_02
Revises: 20261121_01
"""

from alembic import op

revision = "20261121_02"
down_revision = "20261121_01"
branch_labels = None
depends_on = None

TABLE = "t_user_relationship"
TYPE = "ck_t_user_relationship_type"
CODE = "ck_t_user_relationship_relation_code"
OLD_TYPES = "relationship_type IN ('family', 'agent', 'caregiver', 'other')"
OLD_CODES = (
    "relation_code IN ('parent_of', 'child_of', 'spouse_of', 'sibling_of', 'related_to', "
    "'agent_for', 'caregiver_for', 'other')"
)
COMMENTS = (
    ("relationship_pair_id", "关系组 ID，双向亲属记录共用", "关系组 ID，双向联系人或亲属记录共用"),
    (
        "relationship_type",
        "关系类型：family 亲属、agent 代理、caregiver 照护、other 其他",
        "关系类型：contact 联系人、family 亲属、agent 代理、caregiver 照护、other 其他",
    ),
    (
        "relation_code",
        "主体对目标的关系码，如 parent_of 的反向为 child_of",
        "主体对目标的关系码，联系人双向均为 contact_of",
    ),
)


def upgrade() -> None:
    op.drop_constraint(TYPE, TABLE, type_="check")
    op.drop_constraint(CODE, TABLE, type_="check")
    op.create_check_constraint(
        TYPE, TABLE, "relationship_type IN ('family', 'agent', 'caregiver', 'other', 'contact')"
    )
    op.create_check_constraint(
        CODE,
        TABLE,
        "(relationship_type = 'contact' AND relation_code = 'contact_of') OR "
        f"(relationship_type <> 'contact' AND {OLD_CODES})",
    )
    op.create_table_comment(TABLE, "注册用户间的联系人、亲属、代理或其他关系")
    for column, _, new_comment in COMMENTS:
        op.alter_column(TABLE, column, comment=new_comment)


def downgrade() -> None:
    # 恢复原约束前显式退出，避免将联系人误写成亲属或丢弃授权来源。
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM t_user_relationship WHERE relationship_type = 'contact') THEN
                RAISE EXCEPTION '存在联系人关系，无法回退此迁移';
            END IF;
        END;
        $$;
        """
    )
    op.drop_constraint(CODE, TABLE, type_="check")
    op.drop_constraint(TYPE, TABLE, type_="check")
    op.create_check_constraint(TYPE, TABLE, OLD_TYPES)
    op.create_check_constraint(CODE, TABLE, OLD_CODES)
    op.create_table_comment(TABLE, "注册用户间的亲友或代理关系")
    for column, old_comment, _ in COMMENTS:
        op.alter_column(TABLE, column, comment=old_comment)
