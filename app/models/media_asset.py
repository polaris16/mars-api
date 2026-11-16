"""[新增] 用户拥有的 OSS 媒体对象及上传状态。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import Base


class MediaAsset(Base):
    __tablename__ = "t_media_asset"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_media_asset_pkey"),
        UniqueConstraint("object_key", name="t_media_asset_object_key_key"),
        CheckConstraint("purpose IN ('recording', 'attachment')", name="ck_t_media_asset_purpose"),
        CheckConstraint("media_type IN ('audio', 'image', 'video')", name="ck_t_media_asset_media_type"),
        CheckConstraint(
            "upload_status IN ('pending_upload', 'uploaded', 'attached', 'failed', 'expired')",
            name="ck_t_media_asset_upload_status",
        ),
        CheckConstraint("expected_size_bytes > 0", name="ck_t_media_asset_expected_size_positive"),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="ck_t_media_asset_size_positive"),
        CheckConstraint(
            "upload_status NOT IN ('uploaded', 'attached') OR "
            "(size_bytes IS NOT NULL AND content_sha256 IS NOT NULL AND uploaded_at IS NOT NULL)",
            name="ck_t_media_asset_verified_when_uploaded",
        ),
        Index(
            "uq_t_media_asset_owner_content",
            "user_id",
            "purpose",
            "content_sha256",
            "size_bytes",
            "media_type",
            unique=True,
            postgresql_where=text(
                "is_del = false AND content_sha256 IS NOT NULL "
                "AND upload_status IN ('uploaded', 'attached')"
            ),
        ),
        Index("idx_t_media_asset_user_status", "user_id", "upload_status"),
        {"comment": "用户媒体对象与上传记录"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="媒体对象ID")
    user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="CASCADE", name="fk_t_media_asset_user"),
        nullable=False,
        comment="所属用户ID",
    )
    purpose = Column(String(30), nullable=False, comment="用途：recording/attachment")
    media_type = Column(String(20), nullable=False, comment="媒体类型：audio/image/video")
    storage_provider = Column(String(40), nullable=False, comment="对象存储提供商")
    storage_region = Column(String(50), nullable=False, comment="对象存储地域")
    bucket_name = Column(String(63), nullable=False, comment="对象存储Bucket")
    object_key = Column(String(512), nullable=False, comment="对象存储唯一对象键")
    original_filename = Column(String(255), nullable=True, comment="客户端原始文件名")
    content_type = Column(String(100), nullable=False, comment="文件MIME类型")
    expected_size_bytes = Column(BigInteger, nullable=False, comment="上传前声明文件大小")
    size_bytes = Column(BigInteger, nullable=True, comment="上传后校验的实际文件大小")
    expected_sha256 = Column(String(64), nullable=True, comment="客户端声明的SHA-256，待服务端校验")
    content_sha256 = Column(String(64), nullable=True, comment="服务端确认的文件SHA-256")
    duration_ms = Column(BigInteger, nullable=True, comment="音频或视频时长毫秒数")
    sample_rate_hz = Column(Integer, nullable=True, comment="音频采样率Hz")
    upload_status = Column(
        String(20), nullable=False, server_default=text("'pending_upload'"), comment="上传状态"
    )
    upload_expires_at = Column(DateTime(timezone=True), nullable=False, comment="上传授权过期时间")
    uploaded_at = Column(DateTime(timezone=True), nullable=True, comment="对象确认上传时间")
    purge_after = Column(DateTime(timezone=True), nullable=True, comment="计划物理清理时间")
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
