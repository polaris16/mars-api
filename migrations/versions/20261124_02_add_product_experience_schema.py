"""[新增] 产品目录、预览轮次、作品和好友分享。

Revision ID: 20261124_02
Revises: 20261124_01
Create Date: 2026-11-24 21:24:48.134152
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261124_02"
down_revision: Union[str, Sequence[str], None] = "20261124_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRODUCT_TABLES = (
    "t_product_experience_share",
    "t_product_experience_result",
    "t_product_experience_preview",
    "t_product_experience_round",
    "t_product_image",
    "t_product_version",
    "t_product_category",
    "t_product",
)
_PRODUCT_PURPOSES = "'product_reference', 'experience_source', 'experience_generated'"
_ORIGINAL_PURPOSE = "purpose IN ('recording', 'source', 'attachment')"
_MEDIA_PRODUCT_CHECKS = {
    "ck_t_media_asset_product_image": (
        f"purpose NOT IN ({_PRODUCT_PURPOSES}) OR media_type = 'image'"
    ),
    "ck_t_media_asset_image_dimensions": (
        "(width_px IS NULL AND height_px IS NULL) OR "
        "(width_px IS NOT NULL AND height_px IS NOT NULL AND width_px > 0 AND height_px > 0)"
    ),
    "ck_t_media_asset_product_dimensions": (
        f"purpose NOT IN ({_PRODUCT_PURPOSES}) "
        "OR upload_status NOT IN ('uploaded', 'attached') "
        "OR (width_px IS NOT NULL AND height_px IS NOT NULL)"
    ),
    "ck_t_media_asset_upload_expiry": (
        "upload_expires_at IS NOT NULL OR purpose = 'experience_generated'"
    ),
}
_MEDIA_CONTENT_FILTER = (
    "is_del = false AND content_sha256 IS NOT NULL AND upload_status IN ('uploaded', 'attached')"
)


def _replace_media_dedup(*, allow_generated_duplicates: bool) -> None:
    # 保留原按用户与用途去重的范围，体验成图可按任务独立清理。
    predicate = _MEDIA_CONTENT_FILTER
    if allow_generated_duplicates:
        predicate += " AND purpose <> 'experience_generated'"
    op.drop_index("uq_t_media_asset_owner_content", table_name="t_media_asset")
    op.create_index(
        "uq_t_media_asset_owner_content",
        "t_media_asset",
        ["uploaded_by_user_id", "purpose", "content_sha256", "size_bytes", "media_type"],
        unique=True,
        postgresql_where=sa.text(predicate),
    )


def _protect_existing_product_data() -> None:
    # 回退会删除业务表并重新限制媒体用途。已有记录时保持整个修订不变。
    tables = (*_PRODUCT_TABLES, "t_media_asset", "t_benefit_service_version")
    op.execute(f"LOCK TABLE {', '.join(tables)} IN ACCESS EXCLUSIVE MODE")
    checks = [f"EXISTS (SELECT 1 FROM {table})" for table in _PRODUCT_TABLES]
    checks += [
        f"EXISTS (SELECT 1 FROM t_media_asset WHERE purpose IN ({_PRODUCT_PURPOSES}))",
        "EXISTS (SELECT 1 FROM t_benefit_service_version WHERE online_handler IS NOT NULL)",
    ]
    op.execute(
        "DO $$ BEGIN IF "
        + " OR ".join(checks)
        + " THEN RAISE EXCEPTION 'Product experience data prevents downgrade'; END IF; END; $$"
    )


def upgrade() -> None:
    # 产品及体验表按依赖顺序创建，已有媒体和权益记录保留。
    op.create_table(
        "t_product",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="稳定产品 ID"),
        sa.Column("code", sa.String(length=50), nullable=False, comment="运营产品编码"),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="目录展示顺序",
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="是否上架",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        comment="产品目录主档",
    )
    op.create_table(
        "t_product_category",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="分类 ID"),
        sa.Column("code", sa.String(length=50), nullable=False, comment="稳定分类编码"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="分类显示名称"),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="显示顺序",
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="是否展示并允许生成",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        comment="产品目录分类",
    )
    op.create_table(
        "t_product_version",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="产品版本 ID"),
        sa.Column("product_id", sa.BigInteger(), nullable=False, comment="所属产品 ID"),
        sa.Column("version_no", sa.Integer(), nullable=False, comment="同产品内递增版本号"),
        sa.Column("category_id", sa.BigInteger(), nullable=False, comment="该版本所属分类 ID"),
        sa.Column("name", sa.String(length=150), nullable=False, comment="该版本产品名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="产品说明"),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="品类属性 JSON 对象",
        ),
        sa.Column(
            "generation_modes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="可用生成方式：try_on 试穿、scene_composite 场景合成",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
            comment="版本状态：draft 草稿、published 已发布、retired 已退役",
        ),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), nullable=True, comment="首次发布时间"
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint(
            "(status IN ('published', 'retired')) = (published_at IS NOT NULL)",
            name="ck_product_version_published",
        ),
        sa.CheckConstraint(
            "generation_modes <@ ARRAY['try_on', 'scene_composite']::text[] AND (status = 'draft' OR cardinality(generation_modes) > 0)",
            name="ck_product_version_modes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes) = 'object'", name="ck_product_version_attributes"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_product_version_status"
        ),
        sa.CheckConstraint("version_no > 0", name="ck_product_version_number"),
        sa.ForeignKeyConstraint(["category_id"], ["t_product_category.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["t_product.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "version_no", name="uq_product_version_number"),
        comment="产品目录资料和生成能力版本",
    )
    op.create_index(
        "idx_product_version_category",
        "t_product_version",
        ["category_id", "status", "product_id"],
        unique=False,
    )
    op.create_index(
        "uq_product_version_published",
        "t_product_version",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published' AND is_del = false"),
    )
    op.create_table(
        "t_product_image",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="图片关联 ID"),
        sa.Column(
            "product_version_id", sa.BigInteger(), nullable=False, comment="图片所属产品版本 ID"
        ),
        sa.Column(
            "media_asset_id", sa.BigInteger(), nullable=False, comment="已确认的产品参考图媒体 ID"
        ),
        sa.Column(
            "angle",
            sa.String(length=20),
            server_default=sa.text("'unspecified'"),
            nullable=False,
            comment="图片角度：未指定、正面、侧面、背面或细节",
        ),
        sa.Column(
            "is_cover",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否用于产品封面",
        ),
        sa.Column(
            "is_reference",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="是否可供模型参考",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="图片展示顺序",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint(
            "angle IN ('unspecified', 'front', 'side', 'back', 'detail')",
            name="ck_product_image_angle",
        ),
        sa.CheckConstraint("is_cover OR is_reference", name="ck_product_image_usage"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["product_version_id"], ["t_product_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="产品版本的封面和模型参考图",
    )
    op.create_index(
        "uq_product_image_version_asset",
        "t_product_image",
        ["product_version_id", "media_asset_id"],
        unique=True,
        postgresql_where=sa.text("is_del = false"),
    )
    op.create_index(
        "uq_product_image_version_cover",
        "t_product_image",
        ["product_version_id"],
        unique=True,
        postgresql_where=sa.text("is_cover = true AND is_del = false"),
    )
    op.create_table(
        "t_product_experience_round",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="体验轮次 ID"),
        sa.Column(
            "owner_user_id", sa.BigInteger(), nullable=False, comment="权益领取人和轮次所有人 ID"
        ),
        sa.Column("balance_id", sa.BigInteger(), nullable=False, comment="本轮关联的权益余额 ID"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'open'"),
            nullable=False,
            comment="轮次状态：open 待确认、completed 已确认、expired 已到期",
        ),
        sa.Column(
            "preview_limit",
            sa.SmallInteger(),
            server_default=sa.text("20"),
            nullable=False,
            comment="本轮预览上限",
        ),
        sa.Column(
            "preview_used",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="成功生成的预览次数",
        ),
        sa.Column(
            "preview_reserved",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="在途任务占用的预览次数",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="本轮截止时间"),
        sa.Column(
            "closed_at", sa.DateTime(timezone=True), nullable=True, comment="确认成功或到期关闭时间"
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint(
            "(status = 'open') = (closed_at IS NULL) AND (closed_at IS NULL OR preview_reserved = 0)",
            name="ck_product_experience_round_closed",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'expired')", name="ck_product_experience_round_status"
        ),
        sa.CheckConstraint(
            "preview_limit > 0 AND preview_used >= 0 AND preview_reserved >= 0 AND preview_used + preview_reserved <= preview_limit",
            name="ck_product_experience_round_counts",
        ),
        sa.ForeignKeyConstraint(["balance_id"], ["t_benefit_balance.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        comment="产品体验轮次和预览额度",
    )
    op.create_index(
        "idx_product_experience_round_expiry",
        "t_product_experience_round",
        ["expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "idx_product_experience_round_owner",
        "t_product_experience_round",
        ["owner_user_id", "id"],
        unique=False,
    )
    op.create_index(
        "uq_product_experience_round_open",
        "t_product_experience_round",
        ["balance_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "t_product_experience_preview",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="预览任务 ID"),
        sa.Column("round_id", sa.BigInteger(), nullable=False, comment="使用预览额度的轮次 ID"),
        sa.Column(
            "source_asset_id", sa.BigInteger(), nullable=False, comment="已确认的人物照片版本 ID"
        ),
        sa.Column(
            "product_version_id",
            sa.BigInteger(),
            nullable=False,
            comment="本次生成使用的固定产品版本 ID",
        ),
        sa.Column("mode", sa.String(length=30), nullable=False, comment="生成方式：试穿或场景合成"),
        sa.Column(
            "generation_options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="生成参数和参考图 ID 快照，采用 JSON 对象",
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, comment="同轮次提交预览的幂等键"),
        sa.Column(
            "request_fingerprint",
            sa.CHAR(length=64),
            nullable=False,
            comment="规范化请求的 SHA-256 摘要",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
            comment="任务状态：待执行、执行中、成功、失败或取消",
        ),
        sa.Column(
            "provider_code", sa.String(length=50), nullable=False, comment="固定处理提供方编码"
        ),
        sa.Column(
            "model_code", sa.String(length=100), nullable=False, comment="实际使用的模型标识"
        ),
        sa.Column(
            "provider_job_id",
            sa.String(length=200),
            nullable=True,
            comment="供应商任务 ID，用于查询或恢复原任务",
        ),
        sa.Column(
            "attempt_count",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="处理尝试次数",
        ),
        sa.Column(
            "max_attempts", sa.SmallInteger(), nullable=False, comment="该任务允许的最大处理次数"
        ),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=True, comment="下一次可领取时间"
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True, comment="Worker 本次领取的租约令牌"),
        sa.Column(
            "lease_until", sa.DateTime(timezone=True), nullable=True, comment="本次执行租约截止时间"
        ),
        sa.Column(
            "error_code", sa.String(length=80), nullable=True, comment="可向 App 返回的失败原因编码"
        ),
        sa.Column("preview_asset_id", sa.BigInteger(), nullable=True, comment="缩略预览图 ID"),
        sa.Column("output_asset_id", sa.BigInteger(), nullable=True, comment="可确认的原成图 ID"),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True, comment="任务进入终态的时间"
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL) AND (status = 'processing') = (lease_token IS NOT NULL)",
            name="ck_product_preview_lease",
        ),
        sa.CheckConstraint(
            "(preview_asset_id IS NULL) = (output_asset_id IS NULL) AND (status = 'succeeded') = (output_asset_id IS NOT NULL)",
            name="ck_product_preview_outputs",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL) AND (finished_at IS NULL OR next_attempt_at IS NULL)",
            name="ck_product_preview_terminal",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(generation_options) = 'object'", name="ck_product_preview_options"
        ),
        sa.CheckConstraint("mode IN ('try_on', 'scene_composite')", name="ck_product_preview_mode"),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[a-f0-9]{64}$'", name="ck_product_preview_fingerprint"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_product_preview_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_product_preview_attempts",
        ),
        sa.ForeignKeyConstraint(["output_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preview_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["product_version_id"], ["t_product_version.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["t_product_experience_round.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", "id", name="uq_product_preview_round_id"),
        sa.UniqueConstraint("round_id", "idempotency_key", name="uq_product_preview_request"),
        comment="产品体验预览生成任务和成图",
    )
    op.create_index(
        "idx_product_preview_lease",
        "t_product_experience_preview",
        ["lease_until", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'processing' AND is_del = false"),
    )
    op.create_index(
        "idx_product_preview_schedule",
        "t_product_experience_preview",
        ["next_attempt_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued' AND is_del = false"),
    )
    op.create_index(
        "idx_product_preview_source",
        "t_product_experience_preview",
        ["source_asset_id", "status"],
        unique=False,
    )
    op.create_table(
        "t_product_experience_result",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="正式作品 ID"),
        sa.Column(
            "round_id",
            sa.BigInteger(),
            nullable=False,
            comment="已确认的轮次 ID，作品所有人由轮次确定",
        ),
        sa.Column("preview_id", sa.BigInteger(), nullable=False, comment="同一轮次内选中的预览 ID"),
        sa.Column(
            "service_order_id",
            sa.BigInteger(),
            nullable=False,
            comment="本次扣次对应的权益服务单 ID",
        ),
        sa.Column(
            "image_asset_id", sa.BigInteger(), nullable=False, comment="用户选中预览对应的原成图 ID"
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.ForeignKeyConstraint(["image_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["round_id", "preview_id"],
            ["t_product_experience_preview.round_id", "t_product_experience_preview.id"],
            name="fk_product_result_round_preview",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["t_product_experience_round.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["service_order_id"], ["t_benefit_service_order.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("round_id", name="uq_product_result_round"),
        sa.UniqueConstraint("service_order_id", name="uq_product_result_order"),
        comment="已确认并完成扣次的产品体验作品",
    )
    op.create_index(
        "idx_product_result_image", "t_product_experience_result", ["image_asset_id"], unique=False
    )
    op.create_table(
        "t_product_experience_share",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="分享记录 ID"),
        sa.Column("result_id", sa.BigInteger(), nullable=False, comment="分享的正式作品 ID"),
        sa.Column(
            "sender_user_id", sa.BigInteger(), nullable=False, comment="发送人及作品所有人 ID"
        ),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False, comment="收件好友 ID"),
        sa.Column(
            "relationship_pair_id",
            sa.Uuid(),
            nullable=False,
            comment="分享时绑定的双向好友关系组 ID",
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, comment="发送人请求的幂等键"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="分享状态：active 已分享、revoked 已撤回",
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, comment="分享撤回时间"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)", name="ck_product_share_revoked"
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_product_share_status"),
        sa.CheckConstraint(
            "sender_user_id <> recipient_user_id", name="ck_product_share_distinct_users"
        ),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["result_id"], ["t_product_experience_result.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["sender_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sender_user_id", "idempotency_key", name="uq_product_share_request"),
        comment="产品体验作品的站内好友分享",
    )
    op.create_index(
        "idx_product_share_recipient",
        "t_product_experience_share",
        ["recipient_user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_product_share_sender",
        "t_product_experience_share",
        ["sender_user_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_product_share_active",
        "t_product_experience_share",
        ["result_id", "recipient_user_id", "relationship_pair_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND is_del = false"),
    )
    op.add_column(
        "t_benefit_service_version",
        sa.Column(
            "online_handler",
            sa.String(length=50),
            nullable=True,
            comment="线上履约能力编码，如 product_experience",
        ),
    )
    op.add_column(
        "t_media_asset",
        sa.Column("width_px", sa.Integer(), nullable=True, comment="图片宽度，单位像素"),
    )
    op.add_column(
        "t_media_asset",
        sa.Column("height_px", sa.Integer(), nullable=True, comment="图片高度，单位像素"),
    )
    op.alter_column(
        "t_media_asset",
        "purpose",
        existing_type=sa.VARCHAR(length=30),
        comment="用途：录音、片段来源、附件、产品图、体验原照或成图",
        existing_comment="用途：recording/source/attachment",
        existing_nullable=False,
    )
    op.alter_column(
        "t_media_asset",
        "upload_expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=True,
        comment="直传授权过期时间，后台成图可空",
        existing_comment="上传授权过期时间",
    )
    # Alembic 自动比对不覆盖已有表的检查约束及部分索引条件。
    op.create_check_constraint(
        "ck_benefit_service_version_online_handler",
        "t_benefit_service_version",
        "online_handler IS NULL OR "
        "(fulfillment_mode = 'online' AND online_handler = 'product_experience')",
    )
    op.drop_constraint("ck_t_media_asset_purpose", "t_media_asset", type_="check")
    op.create_check_constraint(
        "ck_t_media_asset_purpose",
        "t_media_asset",
        f"purpose IN ('recording', 'source', 'attachment', {_PRODUCT_PURPOSES})",
    )
    for name, condition in _MEDIA_PRODUCT_CHECKS.items():
        op.create_check_constraint(name, "t_media_asset", condition)
    _replace_media_dedup(allow_generated_duplicates=True)


def downgrade() -> None:
    _protect_existing_product_data()
    _replace_media_dedup(allow_generated_duplicates=False)
    for name in _MEDIA_PRODUCT_CHECKS:
        op.drop_constraint(name, "t_media_asset", type_="check")
    op.drop_constraint("ck_t_media_asset_purpose", "t_media_asset", type_="check")
    op.create_check_constraint("ck_t_media_asset_purpose", "t_media_asset", _ORIGINAL_PURPOSE)
    op.drop_constraint(
        "ck_benefit_service_version_online_handler", "t_benefit_service_version", type_="check"
    )
    op.alter_column(
        "t_media_asset",
        "upload_expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        comment="上传授权过期时间",
        existing_comment="直传授权过期时间，后台成图可空",
    )
    op.alter_column(
        "t_media_asset",
        "purpose",
        existing_type=sa.VARCHAR(length=30),
        comment="用途：recording/source/attachment",
        existing_comment="用途：录音、片段来源、附件、产品图、体验原照或成图",
        existing_nullable=False,
    )
    op.drop_column("t_media_asset", "height_px")
    op.drop_column("t_media_asset", "width_px")
    op.drop_column("t_benefit_service_version", "online_handler")
    op.drop_index(
        "uq_product_share_active",
        table_name="t_product_experience_share",
        postgresql_where=sa.text("status = 'active' AND is_del = false"),
    )
    op.drop_index("idx_product_share_sender", table_name="t_product_experience_share")
    op.drop_index("idx_product_share_recipient", table_name="t_product_experience_share")
    op.drop_table("t_product_experience_share")
    op.drop_index("idx_product_result_image", table_name="t_product_experience_result")
    op.drop_table("t_product_experience_result")
    op.drop_index("idx_product_preview_source", table_name="t_product_experience_preview")
    op.drop_index(
        "idx_product_preview_schedule",
        table_name="t_product_experience_preview",
        postgresql_where=sa.text("status = 'queued' AND is_del = false"),
    )
    op.drop_index(
        "idx_product_preview_lease",
        table_name="t_product_experience_preview",
        postgresql_where=sa.text("status = 'processing' AND is_del = false"),
    )
    op.drop_table("t_product_experience_preview")
    op.drop_index(
        "uq_product_experience_round_open",
        table_name="t_product_experience_round",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("idx_product_experience_round_owner", table_name="t_product_experience_round")
    op.drop_index(
        "idx_product_experience_round_expiry",
        table_name="t_product_experience_round",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_table("t_product_experience_round")
    op.drop_index(
        "uq_product_image_version_cover",
        table_name="t_product_image",
        postgresql_where=sa.text("is_cover = true AND is_del = false"),
    )
    op.drop_index(
        "uq_product_image_version_asset",
        table_name="t_product_image",
        postgresql_where=sa.text("is_del = false"),
    )
    op.drop_table("t_product_image")
    op.drop_index(
        "uq_product_version_published",
        table_name="t_product_version",
        postgresql_where=sa.text("status = 'published' AND is_del = false"),
    )
    op.drop_index("idx_product_version_category", table_name="t_product_version")
    op.drop_table("t_product_version")
    op.drop_table("t_product_category")
    op.drop_table("t_product")
