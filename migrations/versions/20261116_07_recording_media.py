"""Create resumable recording sessions and reusable media assets."""

from alembic import op
import sqlalchemy as sa

revision = "20261116_07"
down_revision = "20261116_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # [新增] 录音列表中的会话；一个会话可持续追加多个语音片段。
    op.create_table(
        "t_recording_session",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="录音会话ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="所属用户ID"),
        sa.Column("topic_id", sa.BigInteger(), nullable=True, comment="关联话题ID"),
        sa.Column("title", sa.String(length=100), server_default=sa.text("'未命名'"), nullable=False, comment="会话标题"),
        sa.Column("is_favorite", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否收藏"),
        sa.Column("last_recorded_at", sa.DateTime(timezone=True), nullable=True, comment="最近录音时间"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="CASCADE", name="fk_t_recording_session_user"),
        sa.ForeignKeyConstraint(["topic_id"], ["t_topic.id"], ondelete="SET NULL", name="fk_t_recording_session_topic"),
        sa.PrimaryKeyConstraint("id", name="t_recording_session_pkey"),
        comment="可持续追加语音片段的录音会话",
    )
    op.create_index(
        "idx_t_recording_session_user_last_recorded",
        "t_recording_session",
        ["user_id", "last_recorded_at"],
        postgresql_where=sa.text("is_del = false AND last_recorded_at IS NOT NULL"),
    )

    # [新增] 单份文件的对象元数据与上传授权状态；不保存签名URL。
    op.create_table(
        "t_media_asset",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="媒体对象ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="所属用户ID"),
        sa.Column("purpose", sa.String(length=30), nullable=False, comment="用途：recording/attachment"),
        sa.Column("media_type", sa.String(length=20), nullable=False, comment="媒体类型：audio/image/video"),
        sa.Column("storage_provider", sa.String(length=40), nullable=False, comment="对象存储提供商"),
        sa.Column("storage_region", sa.String(length=50), nullable=False, comment="对象存储地域"),
        sa.Column("bucket_name", sa.String(length=63), nullable=False, comment="对象存储Bucket"),
        sa.Column("object_key", sa.String(length=512), nullable=False, comment="对象存储唯一对象键"),
        sa.Column("original_filename", sa.String(length=255), nullable=True, comment="客户端原始文件名"),
        sa.Column("content_type", sa.String(length=100), nullable=False, comment="文件MIME类型"),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False, comment="上传前声明文件大小"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True, comment="上传后校验的实际文件大小"),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True, comment="客户端声明的SHA-256，待服务端校验"),
        sa.Column("content_sha256", sa.String(length=64), nullable=True, comment="服务端确认的文件SHA-256"),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True, comment="音频或视频时长毫秒数"),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=True, comment="音频采样率Hz"),
        sa.Column("upload_status", sa.String(length=20), server_default=sa.text("'pending_upload'"), nullable=False, comment="上传状态"),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True), nullable=False, comment="上传授权过期时间"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True, comment="对象确认上传时间"),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True, comment="计划物理清理时间"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.CheckConstraint("purpose IN ('recording', 'attachment')", name="ck_t_media_asset_purpose"),
        sa.CheckConstraint("media_type IN ('audio', 'image', 'video')", name="ck_t_media_asset_media_type"),
        sa.CheckConstraint(
            "upload_status IN ('pending_upload', 'uploaded', 'attached', 'failed', 'expired')",
            name="ck_t_media_asset_upload_status",
        ),
        sa.CheckConstraint("expected_size_bytes > 0", name="ck_t_media_asset_expected_size_positive"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="ck_t_media_asset_size_positive"),
        sa.CheckConstraint(
            "upload_status NOT IN ('uploaded', 'attached') OR "
            "(size_bytes IS NOT NULL AND content_sha256 IS NOT NULL AND uploaded_at IS NOT NULL)",
            name="ck_t_media_asset_verified_when_uploaded",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="CASCADE", name="fk_t_media_asset_user"),
        sa.PrimaryKeyConstraint("id", name="t_media_asset_pkey"),
        sa.UniqueConstraint("object_key", name="t_media_asset_object_key_key"),
        comment="用户媒体对象与上传记录",
    )
    op.create_index(
        "uq_t_media_asset_owner_content",
        "t_media_asset",
        ["user_id", "purpose", "content_sha256", "size_bytes", "media_type"],
        unique=True,
        postgresql_where=sa.text(
            "is_del = false AND content_sha256 IS NOT NULL "
            "AND upload_status IN ('uploaded', 'attached')"
        ),
    )
    op.create_index("idx_t_media_asset_user_status", "t_media_asset", ["user_id", "upload_status"])

    # [新增] 一个会话包含多个片段；每段保留 ASR 原文及可选编辑文本。
    op.create_table(
        "t_memory_fragment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="记忆片段ID"),
        sa.Column("session_id", sa.BigInteger(), nullable=False, comment="所属录音会话ID"),
        sa.Column("sequence_no", sa.Integer(), nullable=False, comment="会话内片段顺序"),
        sa.Column("audio_asset_id", sa.BigInteger(), nullable=False, comment="语音媒体对象ID"),
        sa.Column("transcript_raw", sa.Text(), nullable=True, comment="ASR原始转写文本"),
        sa.Column("transcript_edited", sa.Text(), nullable=True, comment="用户修订后的转写文本"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="录音发生时间"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.ForeignKeyConstraint(["session_id"], ["t_recording_session.id"], ondelete="CASCADE", name="fk_t_memory_fragment_session"),
        sa.ForeignKeyConstraint(["audio_asset_id"], ["t_media_asset.id"], ondelete="CASCADE", name="fk_t_memory_fragment_audio_asset"),
        sa.PrimaryKeyConstraint("id", name="t_memory_fragment_pkey"),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_t_memory_fragment_session_sequence"),
        sa.UniqueConstraint("audio_asset_id", name="uq_t_memory_fragment_audio_asset"),
        comment="录音会话中的语音记忆片段",
    )

    # [新增] 关联表允许同一媒体对象复用于不同片段，限制同一有效关联重复。
    op.create_table(
        "t_fragment_attachment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="关联ID"),
        sa.Column("fragment_id", sa.BigInteger(), nullable=False, comment="语音片段ID"),
        sa.Column("media_asset_id", sa.BigInteger(), nullable=False, comment="媒体对象ID"),
        sa.Column("caption", sa.String(length=200), nullable=True, comment="附件说明"),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="展示顺序"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="创建时间"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人ID"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False, comment="更新时间"),
        sa.Column("is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否删除"),
        sa.CheckConstraint("sort_order >= 0", name="ck_t_fragment_attachment_sort_order"),
        sa.ForeignKeyConstraint(["fragment_id"], ["t_memory_fragment.id"], ondelete="CASCADE", name="fk_t_fragment_attachment_fragment"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["t_media_asset.id"], ondelete="CASCADE", name="fk_t_fragment_attachment_media_asset"),
        sa.PrimaryKeyConstraint("id", name="t_fragment_attachment_pkey"),
        comment="语音片段与多媒体素材关联表",
    )
    op.create_index(
        "uq_t_fragment_attachment_active_asset",
        "t_fragment_attachment",
        ["fragment_id", "media_asset_id"],
        unique=True,
        postgresql_where=sa.text("is_del = false"),
    )
    op.create_index(
        "idx_t_fragment_attachment_order",
        "t_fragment_attachment",
        ["fragment_id", "sort_order", "id"],
    )


def downgrade() -> None:
    op.drop_index("idx_t_fragment_attachment_order", table_name="t_fragment_attachment")
    op.drop_index("uq_t_fragment_attachment_active_asset", table_name="t_fragment_attachment")
    op.drop_table("t_fragment_attachment")
    op.drop_table("t_memory_fragment")
    op.drop_index("idx_t_media_asset_user_status", table_name="t_media_asset")
    op.drop_index("uq_t_media_asset_owner_content", table_name="t_media_asset")
    op.drop_table("t_media_asset")
    op.drop_index("idx_t_recording_session_user_last_recorded", table_name="t_recording_session")
    op.drop_table("t_recording_session")
