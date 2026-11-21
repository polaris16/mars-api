"""[新增] 为原始好友申请保存留言。

Revision ID: 20261121_03
Revises: 20261121_02
"""

import sqlalchemy as sa
from alembic import op

revision = "20261121_03"
down_revision = "20261121_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_user_relationship",
        sa.Column(
            "request_message", sa.String(200), nullable=True, comment="发起人填写的好友申请留言"
        ),
    )


def downgrade() -> None:
    op.drop_column("t_user_relationship", "request_message")
