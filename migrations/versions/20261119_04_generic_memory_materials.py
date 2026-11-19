"""Generalize media assets, memory fragments, and fragment attachments."""

from alembic import op
import sqlalchemy as sa


revision = "20261119_04"
down_revision = "20261119_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep historical media rows; rename user_id to clarify that it identifies
    # the account that uploaded the reusable object, not its story contributor.
    op.drop_constraint("fk_t_media_asset_user", "t_media_asset", type_="foreignkey")
    op.alter_column("t_media_asset", "user_id", new_column_name="uploaded_by_user_id")
    op.create_foreign_key(
        "fk_t_media_asset_uploaded_by", "t_media_asset", "t_user",
        ["uploaded_by_user_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_constraint("ck_t_media_asset_purpose", "t_media_asset", type_="check")
    op.create_check_constraint(
        "ck_t_media_asset_purpose", "t_media_asset",
        "purpose IN ('recording', 'source', 'attachment')",
    )
    op.drop_constraint("ck_t_media_asset_media_type", "t_media_asset", type_="check")
    op.create_check_constraint(
        "ck_t_media_asset_media_type", "t_media_asset",
        "media_type IN ('audio', 'image', 'video', 'text')",
    )
    op.drop_index("idx_t_media_asset_user_status", table_name="t_media_asset")
    op.create_index(
        "idx_t_media_asset_uploaded_by_status", "t_media_asset",
        ["uploaded_by_user_id", "upload_status"],
    )
    op.alter_column("t_media_asset", "uploaded_by_user_id", existing_type=sa.BigInteger(), comment="上传账户ID")
    op.alter_column("t_media_asset", "purpose", existing_type=sa.String(length=30), comment="用途：recording/source/attachment")
    op.alter_column("t_media_asset", "media_type", existing_type=sa.String(length=20), comment="媒体类型：audio/image/video/text")

    # Backfill one verified owner contributor for every existing session that
    # has fragments, then link existing fragments and attachments to that actor.
    op.execute(
        """
        INSERT INTO t_memory_contributor
            (session_id, contributor_type, account_user_id, display_name,
             identity_status, consent_status, created_by, updated_by)
        SELECT DISTINCT s.id, 'owner', s.owner_user_id,
               COALESCE(NULLIF(u.display_name, ''), u.username),
               'verified', 'pending', s.owner_user_id, s.owner_user_id
        FROM t_memory_session s
        JOIN t_user u ON u.id = s.owner_user_id
        JOIN t_memory_fragment f ON f.session_id = s.id
        WHERE NOT EXISTS (
            SELECT 1 FROM t_memory_contributor c
            WHERE c.session_id = s.id AND c.account_user_id = s.owner_user_id
              AND c.contributor_type = 'owner' AND c.is_del = false
        )
        """
    )

    op.drop_constraint("fk_t_memory_fragment_audio_asset", "t_memory_fragment", type_="foreignkey")
    op.drop_constraint("uq_t_memory_fragment_audio_asset", "t_memory_fragment", type_="unique")
    op.add_column("t_memory_fragment", sa.Column("contributor_id", sa.BigInteger(), nullable=True, comment="素材提供者ID"))
    op.add_column(
        "t_memory_fragment",
        sa.Column("source_type", sa.String(length=20), server_default=sa.text("'audio'"), nullable=False,
                  comment="素材来源类型：text/audio/video"),
    )
    op.add_column("t_memory_fragment", sa.Column("content_text", sa.Text(), nullable=True, comment="直接输入或文本文件内容"))
    op.alter_column("t_memory_fragment", "audio_asset_id", new_column_name="source_asset_id", nullable=True)
    op.execute(
        """
        UPDATE t_memory_fragment f
        SET contributor_id = c.id
        FROM t_memory_session s
        JOIN t_memory_contributor c
          ON c.session_id = s.id AND c.account_user_id = s.owner_user_id
         AND c.contributor_type = 'owner' AND c.is_del = false
        WHERE f.session_id = s.id AND f.contributor_id IS NULL
        """
    )
    op.alter_column("t_memory_fragment", "contributor_id", nullable=False)
    op.create_foreign_key(
        "fk_t_memory_fragment_source_asset", "t_memory_fragment", "t_media_asset",
        ["source_asset_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_t_memory_fragment_contributor", "t_memory_fragment", "t_memory_contributor",
        ["contributor_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_t_memory_fragment_source_type", "t_memory_fragment",
        "source_type IN ('text', 'audio', 'video')",
    )
    op.create_check_constraint(
        "ck_t_memory_fragment_text_has_content", "t_memory_fragment",
        "source_type <> 'text' OR (content_text IS NOT NULL AND length(btrim(content_text)) > 0) OR source_asset_id IS NOT NULL",
    )
    op.alter_column("t_memory_fragment", "source_type", existing_type=sa.String(length=20), comment="来源类型：text/audio/video")
    op.alter_column("t_memory_fragment", "source_asset_id", existing_type=sa.BigInteger(), comment="来源文件对象ID")
    op.execute("COMMENT ON TABLE t_memory_fragment IS '记忆会话中的独立素材片段'")

    op.add_column("t_fragment_attachment", sa.Column("contributor_id", sa.BigInteger(), nullable=True, comment="附件提供者ID"))
    op.execute(
        """
        UPDATE t_fragment_attachment a
        SET contributor_id = f.contributor_id
        FROM t_memory_fragment f
        WHERE a.fragment_id = f.id AND a.contributor_id IS NULL
        """
    )
    op.alter_column("t_fragment_attachment", "contributor_id", nullable=False)
    op.create_foreign_key(
        "fk_t_fragment_attachment_contributor", "t_fragment_attachment", "t_memory_contributor",
        ["contributor_id"], ["id"], ondelete="RESTRICT",
    )
    op.alter_column("t_fragment_attachment", "contributor_id", existing_type=sa.BigInteger(), comment="添加附件的提供者ID")


def downgrade() -> None:
    # New text/video fragments cannot be represented by the old audio-only schema.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM t_memory_fragment WHERE source_type <> 'audio' OR content_text IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade: non-audio or direct-text memory fragments exist';
            END IF;
            IF EXISTS (
                SELECT source_asset_id FROM t_memory_fragment
                WHERE source_asset_id IS NOT NULL
                GROUP BY source_asset_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade: a media asset is reused by multiple fragments';
            END IF;
        END $$
        """
    )
    op.drop_constraint("fk_t_fragment_attachment_contributor", "t_fragment_attachment", type_="foreignkey")
    op.drop_column("t_fragment_attachment", "contributor_id")

    op.drop_constraint("ck_t_memory_fragment_text_has_content", "t_memory_fragment", type_="check")
    op.drop_constraint("ck_t_memory_fragment_source_type", "t_memory_fragment", type_="check")
    op.drop_constraint("fk_t_memory_fragment_contributor", "t_memory_fragment", type_="foreignkey")
    op.drop_constraint("fk_t_memory_fragment_source_asset", "t_memory_fragment", type_="foreignkey")
    op.drop_column("t_memory_fragment", "content_text")
    op.drop_column("t_memory_fragment", "source_type")
    op.drop_column("t_memory_fragment", "contributor_id")
    op.alter_column("t_memory_fragment", "source_asset_id", new_column_name="audio_asset_id", nullable=False)
    op.create_foreign_key(
        "fk_t_memory_fragment_audio_asset", "t_memory_fragment", "t_media_asset",
        ["audio_asset_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("t_memory_fragment", "audio_asset_id", existing_type=sa.BigInteger(), comment="语音媒体对象ID")
    op.execute("COMMENT ON TABLE t_memory_fragment IS '录音会话中的语音记忆片段'")
    op.create_unique_constraint("uq_t_memory_fragment_audio_asset", "t_memory_fragment", ["audio_asset_id"])

    op.drop_index("idx_t_media_asset_uploaded_by_status", table_name="t_media_asset")
    op.create_index("idx_t_media_asset_user_status", "t_media_asset", ["user_id", "upload_status"])
    op.drop_constraint("ck_t_media_asset_media_type", "t_media_asset", type_="check")
    op.create_check_constraint("ck_t_media_asset_media_type", "t_media_asset", "media_type IN ('audio', 'image', 'video')")
    op.drop_constraint("ck_t_media_asset_purpose", "t_media_asset", type_="check")
    op.create_check_constraint("ck_t_media_asset_purpose", "t_media_asset", "purpose IN ('recording', 'attachment')")
    op.drop_constraint("fk_t_media_asset_uploaded_by", "t_media_asset", type_="foreignkey")
    op.alter_column("t_media_asset", "uploaded_by_user_id", new_column_name="user_id")
    op.create_foreign_key("fk_t_media_asset_user", "t_media_asset", "t_user", ["user_id"], ["id"], ondelete="CASCADE")
    op.alter_column("t_media_asset", "user_id", existing_type=sa.BigInteger(), comment="所属用户ID")
    op.alter_column("t_media_asset", "purpose", existing_type=sa.String(length=30), comment="用途：recording/attachment")
    op.alter_column("t_media_asset", "media_type", existing_type=sa.String(length=20), comment="媒体类型：audio/image/video")
