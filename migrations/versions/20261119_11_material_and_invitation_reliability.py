"""为片段重发、会话时间和待确认邀请补充持久化规则。"""

from alembic import op
import sqlalchemy as sa


revision = "20261119_11"
down_revision = "20261119_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("t_memory_fragment", sa.Column(
        "client_request_key", sa.Uuid(), nullable=True, comment="客户端保存请求的幂等键",
    ))
    op.add_column("t_memory_fragment", sa.Column(
        "client_request_hash", sa.String(64), nullable=True, comment="保存请求内容的 SHA-256 摘要",
    ))
    op.create_unique_constraint(
        "uq_t_memory_fragment_client_request", "t_memory_fragment",
        ["session_id", "created_by", "client_request_key"],
    )
    op.create_index(
        "idx_t_memory_fragment_session_recorded_active", "t_memory_fragment",
        ["session_id", "recorded_at", "sequence_no"], postgresql_where=sa.text("is_del = false"),
    )
    op.add_column("t_user_relationship", sa.Column(
        "invitation_expires_at", sa.DateTime(timezone=True), nullable=True,
        comment="待确认邀请的截止时间，独立于关系有效期",
    ))
    # 已确认的关系保留原有效期，只有等待确认的邀请需要补截止时间。
    op.execute("""
        UPDATE t_user_relationship
        SET invitation_expires_at = LEAST(created_at + INTERVAL '7 days', valid_until)
        WHERE status = 'pending' AND is_del = false
    """)
    # 补录和删除曾使旧会话时间失真。没有有效片段的会话应回到空值。
    op.execute("""
        WITH latest AS (
            SELECT s.id, max(f.recorded_at) AS recorded_at
            FROM t_memory_session AS s
            LEFT JOIN t_memory_fragment AS f ON f.session_id = s.id AND f.is_del = false
            WHERE s.is_del = false
            GROUP BY s.id
        )
        UPDATE t_memory_session AS target
        SET last_material_at = latest.recorded_at
        FROM latest
        WHERE target.id = latest.id AND target.last_material_at IS DISTINCT FROM latest.recorded_at
    """)


def downgrade() -> None:
    # 已修正的会话时间继续保留，旧版本可读取同一字段。
    op.drop_column("t_user_relationship", "invitation_expires_at")
    op.drop_index("idx_t_memory_fragment_session_recorded_active", table_name="t_memory_fragment")
    op.drop_constraint("uq_t_memory_fragment_client_request", "t_memory_fragment", type_="unique")
    op.drop_column("t_memory_fragment", "client_request_hash")
    op.drop_column("t_memory_fragment", "client_request_key")
