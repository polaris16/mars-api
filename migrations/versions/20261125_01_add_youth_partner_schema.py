"""Add youth partner appointment schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261125_01"
down_revision = "20261124_02"
branch_labels = None
depends_on = None

_CHECKS = (
    (
        "t_benefit_service_version",
        "ck_benefit_service_version_durations",
        None,
        "(online_duration_minutes IS NULL OR online_duration_minutes > 0) AND (offline_duration_minutes IS NULL OR offline_duration_minutes > 0)",
    ),
    (
        "t_benefit_service_version",
        "ck_benefit_service_version_appointment_handler",
        None,
        "appointment_handler IS NULL OR (appointment_handler = 'youth_partner' AND fulfillment_mode = 'appointment' AND offline_duration_minutes IS NOT NULL)",
    ),
    (
        "t_benefit_staff",
        "ck_benefit_staff_role",
        "role IN ('admin', 'ops', 'dispatcher', 'provider')",
        "role IN ('admin', 'ops', 'dispatcher', 'provider', 'identity_reviewer')",
    ),
    (
        "t_media_asset",
        "ck_t_media_asset_purpose",
        "purpose IN ('recording', 'source', 'attachment', 'product_reference', 'experience_source', 'experience_generated')",
        "purpose IN ('recording', 'source', 'attachment', 'product_reference', 'experience_source', 'experience_generated', 'partner_photo', 'partner_identity')",
    ),
    (
        "t_media_asset",
        "ck_t_media_asset_partner_image",
        None,
        "purpose NOT IN ('partner_photo', 'partner_identity') OR media_type = 'image'",
    ),
    (
        "t_benefit_order_event",
        "ck_benefit_event_action",
        "action IN ('create', 'confirm_booking', 'reschedule', 'start', 'complete', 'cancel', 'fail')",
        "action IN ('create', 'confirm_booking', 'reschedule', 'start', 'complete', 'cancel', 'fail', 'check_in', 'finish_partner_service')",
    ),
    (
        "t_benefit_order_event",
        "ck_benefit_event_balance_delta",
        "((used_delta = 0 AND reserved_delta IN (-1, 0, 1)) OR (used_delta = 1 AND reserved_delta = -1)) AND (action = 'complete') = (used_delta = 1)",
        "((used_delta = 0 AND reserved_delta IN (-1, 0, 1)) OR (used_delta = 1 AND reserved_delta = -1)) AND (action IN ('complete', 'check_in')) = (used_delta = 1) AND (action <> 'finish_partner_service' OR reserved_delta = 0)",
    ),
)

_TABLES = (
    "t_youth_partner_profile",
    "t_youth_partner_service",
    "t_youth_partner_identity_document",
    "t_youth_partner_identity_access",
    "t_youth_partner_availability",
    "t_benefit_grant_service_location",
    "t_youth_partner_booking",
    "t_youth_partner_check_in_code",
    "t_youth_partner_rating",
)


def upgrade() -> None:
    op.add_column(
        "t_benefit_service_version",
        sa.Column(
            "online_duration_minutes", sa.Integer(), nullable=True, comment="单次线上服务分钟数"
        ),
    )
    op.add_column(
        "t_benefit_service_version",
        sa.Column(
            "offline_duration_minutes", sa.Integer(), nullable=True, comment="单次线下服务分钟数"
        ),
    )
    op.add_column(
        "t_benefit_service_version",
        sa.Column(
            "appointment_handler",
            sa.String(length=50),
            nullable=True,
            comment="预约履约能力编码，如 youth_partner",
        ),
    )
    op.alter_column(
        "t_benefit_staff",
        "role",
        comment="岗位：admin 管理，ops 运营，dispatcher 派单，provider 履约，identity_reviewer 证件审核",
    )
    op.alter_column(
        "t_media_asset",
        "purpose",
        comment="用途：录音、片段来源、附件、产品体验图片、伙伴展示照或身份证照片",
    )
    op.alter_column(
        "t_benefit_order_event",
        "action",
        comment="服务单动作，包括到场核销 check_in 和青年伙伴服务完成 finish_partner_service",
    )
    for table, name, old, new in _CHECKS:
        if old is not None:
            op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, new)

    op.create_table(
        "t_youth_partner_profile",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="申请人账号"),
        sa.Column("display_name", sa.String(length=100), nullable=False, comment="公开展示名"),
        sa.Column("major", sa.String(length=100), nullable=True, comment="专业"),
        sa.Column("education_label", sa.String(length=50), nullable=True, comment="学历或在读状态"),
        sa.Column("bio", sa.String(length=300), nullable=True, comment="公开简介"),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=32)),
            server_default=sa.text("'{}'"),
            nullable=False,
            comment="展示和筛选标签",
        ),
        sa.Column(
            "service_area_codes",
            postgresql.ARRAY(sa.String(length=32)),
            server_default=sa.text("'{}'"),
            nullable=False,
            comment="申请或获批的服务区域编码",
        ),
        sa.Column("photo_asset_id", sa.BigInteger(), nullable=True, comment="公开展示照素材 ID"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
            comment="状态：draft 草稿、pending_review 待审、approved 通过、rejected 驳回、disabled 停用",
        ),
        sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True, comment="最近审核人账号"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="最近审核时间"),
        sa.Column("review_notes", sa.String(length=500), nullable=True, comment="最近审核说明"),
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
            "status IN ('draft', 'pending_review', 'approved', 'rejected', 'disabled')",
            name="ck_youth_partner_profile_status",
        ),
        sa.ForeignKeyConstraint(["photo_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        comment="青年伙伴注册及公开资料",
    )
    op.create_index(
        "idx_youth_partner_profile_areas",
        "t_youth_partner_profile",
        ["service_area_codes"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_youth_partner_profile_status",
        "t_youth_partner_profile",
        ["status", "id"],
        unique=False,
    )
    op.create_index(
        "idx_youth_partner_profile_tags",
        "t_youth_partner_profile",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "t_youth_partner_service",
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="伙伴资料 ID"),
        sa.Column("service_version_id", sa.BigInteger(), nullable=False, comment="权益服务版本 ID"),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            comment="状态：pending 申请、approved 核准、disabled 停用",
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
            "status IN ('pending', 'approved', 'disabled')", name="ck_youth_partner_service_status"
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["service_version_id"], ["t_benefit_service_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("partner_id", "service_version_id"),
        comment="青年伙伴获准提供的权益服务版本",
    )
    op.create_index(
        "idx_youth_partner_service_version",
        "t_youth_partner_service",
        ["service_version_id", "status", "partner_id"],
        unique=False,
    )
    op.create_table(
        "t_youth_partner_identity_document",
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="伙伴资料 ID"),
        sa.Column(
            "portrait_side_asset_id", sa.BigInteger(), nullable=True, comment="身份证人像面素材 ID"
        ),
        sa.Column(
            "emblem_side_asset_id", sa.BigInteger(), nullable=True, comment="身份证国徽面素材 ID"
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
            "portrait_side_asset_id <> emblem_side_asset_id",
            name="ck_youth_partner_identity_distinct",
        ),
        sa.ForeignKeyConstraint(
            ["emblem_side_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["portrait_side_asset_id"], ["t_media_asset.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("partner_id"),
        comment="青年伙伴私有身份证照片引用",
    )
    op.create_table(
        "t_youth_partner_identity_access",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键"),
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="被审核伙伴资料 ID"),
        sa.Column(
            "reviewer_user_id", sa.BigInteger(), nullable=False, comment="获取查看链接的审核人账号"
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
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        comment="青年伙伴证件查看链接签发记录",
    )
    op.create_index(
        "idx_youth_partner_identity_access",
        "t_youth_partner_identity_access",
        ["partner_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "t_youth_partner_availability",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键"),
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="伙伴资料 ID"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False, comment="开放开始时间"),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False, comment="开放结束时间"),
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
        sa.CheckConstraint("starts_at < ends_at", name="ck_youth_partner_availability_interval"),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="青年伙伴开放预约的时间区间",
    )
    op.create_index(
        "idx_youth_partner_availability_start",
        "t_youth_partner_availability",
        ["partner_id", "starts_at"],
        unique=False,
    )
    op.create_table(
        "t_benefit_grant_service_location",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="主键"),
        sa.Column("grant_id", sa.BigInteger(), nullable=False, comment="权益发放记录 ID"),
        sa.Column("name", sa.String(length=100), nullable=False, comment="地点展示名称"),
        sa.Column("area_code", sa.String(length=32), nullable=False, comment="伙伴覆盖区域编码"),
        sa.Column(
            "time_zone", sa.String(length=64), nullable=False, comment="地点的 IANA 时区名称"
        ),
        sa.Column(
            "address_ciphertext", sa.LargeBinary(), nullable=False, comment="详细地址及到场说明密文"
        ),
        sa.Column(
            "crypto_key_version", sa.SmallInteger(), nullable=False, comment="地点加密密钥版本"
        ),
        sa.Column(
            "travel_before_minutes",
            sa.SmallInteger(),
            nullable=False,
            comment="服务前通勤缓冲分钟数",
        ),
        sa.Column(
            "travel_after_minutes",
            sa.SmallInteger(),
            nullable=False,
            comment="服务后通勤缓冲分钟数",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="展示顺序",
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
        sa.CheckConstraint("crypto_key_version > 0", name="ck_benefit_grant_location_key_version"),
        sa.CheckConstraint(
            "travel_before_minutes >= 0 AND travel_after_minutes >= 0",
            name="ck_benefit_grant_location_travel",
        ),
        sa.ForeignKeyConstraint(["grant_id"], ["t_benefit_grant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        comment="权益发放记录可用的线下服务地点",
    )
    op.create_index(
        "idx_benefit_grant_location_sort",
        "t_benefit_grant_service_location",
        ["grant_id", "sort_order", "id"],
        unique=False,
    )
    op.create_table(
        "t_youth_partner_booking",
        sa.Column("service_order_id", sa.BigInteger(), nullable=False, comment="权益服务单 ID"),
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="获派伙伴资料 ID"),
        sa.Column("location_id", sa.BigInteger(), nullable=False, comment="预约地点 ID"),
        sa.Column(
            "blocking_start_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="含服务前通勤的占用开始时间",
        ),
        sa.Column(
            "blocking_end_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="含服务后通勤的占用结束时间",
        ),
        sa.Column(
            "confirm_by", sa.DateTime(timezone=True), nullable=False, comment="伙伴确认截止时间"
        ),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), nullable=True, comment="伙伴确认时间"
        ),
        sa.Column(
            "checked_in_at", sa.DateTime(timezone=True), nullable=True, comment="到场核销时间"
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
            "blocking_start_at < blocking_end_at", name="ck_youth_partner_booking_interval"
        ),
        sa.CheckConstraint(
            "checked_in_at IS NULL OR (confirmed_at IS NOT NULL AND checked_in_at >= confirmed_at)",
            name="ck_youth_partner_booking_check_in",
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR confirmed_at <= confirm_by",
            name="ck_youth_partner_booking_confirm",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["t_benefit_grant_service_location.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["service_order_id"], ["t_benefit_service_order.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("service_order_id"),
        comment="青年伙伴预约地点和占用扩展",
    )
    op.create_index(
        "idx_youth_partner_booking_confirmation",
        "t_youth_partner_booking",
        ["confirm_by", "service_order_id"],
        unique=False,
        postgresql_where=sa.text("confirmed_at IS NULL AND is_del = false"),
    )
    op.create_index(
        "idx_youth_partner_booking_schedule",
        "t_youth_partner_booking",
        ["partner_id", "blocking_start_at"],
        unique=False,
    )
    op.create_table(
        "t_youth_partner_check_in_code",
        sa.Column(
            "service_order_id", sa.BigInteger(), nullable=False, comment="青年伙伴预约的服务单 ID"
        ),
        sa.Column(
            "token_digest", sa.LargeBinary(), nullable=False, comment="二维码令牌的 32 字节摘要"
        ),
        sa.Column(
            "crypto_key_version", sa.SmallInteger(), nullable=False, comment="令牌摘要密钥版本"
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False, comment="二维码过期时间"
        ),
        sa.Column(
            "consumed_at", sa.DateTime(timezone=True), nullable=True, comment="二维码消费时间"
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
            "consumed_at IS NULL OR consumed_at < expires_at",
            name="ck_youth_partner_check_in_code_consumed",
        ),
        sa.CheckConstraint(
            "octet_length(token_digest) = 32 AND crypto_key_version > 0",
            name="ck_youth_partner_check_in_code_digest",
        ),
        sa.ForeignKeyConstraint(
            ["service_order_id"], ["t_youth_partner_booking.service_order_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("service_order_id"),
        sa.UniqueConstraint("token_digest"),
        comment="到场核销的一次性二维码摘要",
    )
    op.create_index(
        "idx_youth_partner_check_in_code_expiry",
        "t_youth_partner_check_in_code",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "t_youth_partner_rating",
        sa.Column(
            "service_order_id", sa.BigInteger(), nullable=False, comment="青年伙伴预约的服务单 ID"
        ),
        sa.Column("score", sa.SmallInteger(), nullable=False, comment="服务星级，范围 1 至 5"),
        sa.Column("comment", sa.String(length=500), nullable=True, comment="可选评价内容"),
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
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_youth_partner_rating_score"),
        sa.ForeignKeyConstraint(
            ["service_order_id"], ["t_youth_partner_booking.service_order_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("service_order_id"),
        comment="领取人对已完成青年伙伴服务的评价",
    )


def downgrade() -> None:
    checks = [f"EXISTS (SELECT 1 FROM {table})" for table in _TABLES]
    checks.append(
        "EXISTS (SELECT 1 FROM t_benefit_service_version WHERE "
        "online_duration_minutes IS NOT NULL OR offline_duration_minutes IS NOT NULL "
        "OR appointment_handler IS NOT NULL)"
    )
    predicate = " OR ".join(checks)
    op.execute(
        f"DO $$ BEGIN IF {predicate} THEN RAISE EXCEPTION 'Youth partner data must be preserved before downgrade'; END IF; END $$;"
    )
    for table in reversed(_TABLES):
        op.drop_table(table)
    for table, name, old, _ in reversed(_CHECKS):
        op.drop_constraint(name, table, type_="check")
        if old is not None:
            op.create_check_constraint(name, table, old)
    op.alter_column(
        "t_benefit_staff",
        "role",
        comment="岗位：admin 管理，ops 运营，dispatcher 派单，provider 履约",
    )
    op.alter_column(
        "t_media_asset", "purpose", comment="用途：录音、片段来源、附件、产品图、体验原照或成图"
    )
    op.alter_column(
        "t_benefit_order_event", "action", comment="创建、确认预约、改约、开始、核销、取消或失败"
    )
    op.drop_column("t_benefit_service_version", "appointment_handler")
    op.drop_column("t_benefit_service_version", "offline_duration_minutes")
    op.drop_column("t_benefit_service_version", "online_duration_minutes")
