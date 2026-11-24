"""[新增] 异步生成预览的输入、执行租约和成图引用。"""

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class ProductExperiencePreview(AuditFields, Base):
    __tablename__ = "t_product_experience_preview"
    __table_args__ = (
        UniqueConstraint("round_id", "idempotency_key", name="uq_product_preview_request"),
        UniqueConstraint("round_id", "id", name="uq_product_preview_round_id"),
        CheckConstraint("mode IN ('try_on', 'scene_composite')", name="ck_product_preview_mode"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_product_preview_status",
        ),
        CheckConstraint(
            "jsonb_typeof(generation_options) = 'object'", name="ck_product_preview_options"
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[a-f0-9]{64}$'", name="ck_product_preview_fingerprint"
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_product_preview_attempts",
        ),
        CheckConstraint(
            "(preview_asset_id IS NULL) = (output_asset_id IS NULL) "
            "AND (status = 'succeeded') = (output_asset_id IS NOT NULL)",
            name="ck_product_preview_outputs",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL) "
            "AND (status = 'processing') = (lease_token IS NOT NULL)",
            name="ck_product_preview_lease",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL) "
            "AND (finished_at IS NULL OR next_attempt_at IS NULL)",
            name="ck_product_preview_terminal",
        ),
        Index(
            "idx_product_preview_schedule",
            "next_attempt_at",
            "id",
            postgresql_where=text("status = 'queued' AND is_del = false"),
        ),
        Index(
            "idx_product_preview_lease",
            "lease_until",
            "id",
            postgresql_where=text("status = 'processing' AND is_del = false"),
        ),
        Index("idx_product_preview_source", "source_asset_id", "status"),
        {"comment": "产品体验预览生成任务和成图"},
    )

    id = Column(BigInteger, primary_key=True, comment="预览任务 ID")
    round_id = Column(
        BigInteger,
        ForeignKey("t_product_experience_round.id", ondelete="RESTRICT"),
        nullable=False,
        comment="使用预览额度的轮次 ID",
    )
    source_asset_id = Column(
        BigInteger,
        ForeignKey("t_media_asset.id", ondelete="RESTRICT"),
        nullable=False,
        comment="已确认的人物照片版本 ID",
    )
    product_version_id = Column(
        BigInteger,
        ForeignKey("t_product_version.id", ondelete="RESTRICT"),
        nullable=False,
        comment="本次生成使用的固定产品版本 ID",
    )
    mode = Column(String(30), nullable=False, comment="生成方式：试穿或场景合成")
    generation_options = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="生成参数和参考图 ID 快照，采用 JSON 对象",
    )
    idempotency_key = Column(Uuid, nullable=False, comment="同轮次提交预览的幂等键")
    request_fingerprint = Column(CHAR(64), nullable=False, comment="规范化请求的 SHA-256 摘要")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'queued'"),
        comment="任务状态：待执行、执行中、成功、失败或取消",
    )
    provider_code = Column(String(50), nullable=False, comment="固定处理提供方编码")
    model_code = Column(String(100), nullable=False, comment="实际使用的模型标识")
    provider_job_id = Column(String(200), comment="供应商任务 ID，用于查询或恢复原任务")
    attempt_count = Column(
        SmallInteger, nullable=False, server_default=text("0"), comment="处理尝试次数"
    )
    max_attempts = Column(SmallInteger, nullable=False, comment="该任务允许的最大处理次数")
    next_attempt_at = Column(DateTime(timezone=True), comment="下一次可领取时间")
    lease_token = Column(Uuid, comment="Worker 本次领取的租约令牌")
    lease_until = Column(DateTime(timezone=True), comment="本次执行租约截止时间")
    error_code = Column(String(80), comment="可向 App 返回的失败原因编码")
    preview_asset_id = Column(
        BigInteger, ForeignKey("t_media_asset.id", ondelete="RESTRICT"), comment="缩略预览图 ID"
    )
    output_asset_id = Column(
        BigInteger, ForeignKey("t_media_asset.id", ondelete="RESTRICT"), comment="可确认的原成图 ID"
    )
    finished_at = Column(DateTime(timezone=True), comment="任务进入终态的时间")
