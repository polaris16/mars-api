"""Rename recording sessions to generic memory sessions without losing data."""

from alembic import op

revision = "20261119_01"
down_revision = "20261116_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename in place so existing sessions and foreign-key references are preserved.
    op.rename_table("t_recording_session", "t_memory_session")
    op.alter_column("t_memory_session", "user_id", new_column_name="owner_user_id")
    op.alter_column("t_memory_session", "last_recorded_at", new_column_name="last_material_at")
    op.execute("ALTER TABLE t_memory_session RENAME CONSTRAINT t_recording_session_pkey TO t_memory_session_pkey")
    op.execute("ALTER TABLE t_memory_session RENAME CONSTRAINT fk_t_recording_session_user TO fk_t_memory_session_owner")
    op.execute("ALTER TABLE t_memory_session RENAME CONSTRAINT fk_t_recording_session_topic TO fk_t_memory_session_topic")
    op.execute("ALTER INDEX idx_t_recording_session_user_last_recorded RENAME TO idx_t_memory_session_owner_last_material")
    op.execute("ALTER TABLE t_memory_fragment RENAME CONSTRAINT fk_t_memory_fragment_session TO fk_t_memory_fragment_memory_session")
    op.execute("COMMENT ON TABLE t_memory_session IS '记忆素材会话'")
    op.execute("COMMENT ON COLUMN t_memory_session.owner_user_id IS '回忆录所属用户ID'")
    op.execute("COMMENT ON COLUMN t_memory_session.last_material_at IS '最近新增素材时间'")


def downgrade() -> None:
    op.execute("COMMENT ON TABLE t_memory_session IS '可持续追加语音片段的录音会话'")
    op.execute("COMMENT ON COLUMN t_memory_session.owner_user_id IS '所属用户ID'")
    op.execute("COMMENT ON COLUMN t_memory_session.last_material_at IS '最近录音时间'")
    op.execute("ALTER TABLE t_memory_fragment RENAME CONSTRAINT fk_t_memory_fragment_memory_session TO fk_t_memory_fragment_session")
    op.execute("ALTER INDEX idx_t_memory_session_owner_last_material RENAME TO idx_t_recording_session_user_last_recorded")
    op.execute("ALTER TABLE t_memory_session RENAME CONSTRAINT fk_t_memory_session_topic TO fk_t_recording_session_topic")
    op.execute("ALTER TABLE t_memory_session RENAME CONSTRAINT fk_t_memory_session_owner TO fk_t_recording_session_user")
    op.execute("ALTER TABLE t_memory_session RENAME CONSTRAINT t_memory_session_pkey TO t_recording_session_pkey")
    op.alter_column("t_memory_session", "last_material_at", new_column_name="last_recorded_at")
    op.alter_column("t_memory_session", "owner_user_id", new_column_name="user_id")
    op.rename_table("t_memory_session", "t_recording_session")
