"""[新增] 创建企业、权益目录、发放领取和服务核销结构。

Revision ID: 20261122_02
Revises: 20261122_01
Create Date: 2026-11-22 21:10:02.651634
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261122_02"
down_revision = "20261122_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 按外键依赖顺序创建独立业务表，引用已有账号与记忆会话。
    op.create_table(
        "t_benefit_package",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="权益包版本 ID"),
        sa.Column(
            "package_code", sa.String(length=50), nullable=False, comment="跨版本的稳定计划编码"
        ),
        sa.Column(
            "version_no", sa.Integer(), nullable=False, comment="同一计划内从 1 开始的版本号"
        ),
        sa.Column("title", sa.String(length=100), nullable=False, comment="面向用户的计划名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="权益包计划说明"),
        sa.Column("icon_url", sa.String(length=500), nullable=True, comment="权益包计划图标地址"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
            comment="版本状态：draft 草稿，published 已发布，retired 已退役",
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
            name="ck_benefit_package_published",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_benefit_package_status"
        ),
        sa.CheckConstraint("version_no > 0", name="ck_benefit_package_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_code", "version_no", name="uq_benefit_package_version"),
        comment="企业可采购的权益包版本",
    )
    op.create_index(
        "idx_benefit_package_status", "t_benefit_package", ["status", "id"], unique=False
    )
    op.create_table(
        "t_benefit_service",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="权益服务 ID"),
        sa.Column(
            "code",
            sa.String(length=50),
            nullable=False,
            comment="稳定服务编码，用于导航和处理器匹配",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="目录状态：active 启用，disabled 停止新增引用",
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
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_benefit_service_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_benefit_service_code"),
        comment="权益服务目录主档",
    )
    op.create_table(
        "t_enterprise",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="合作企业 ID"),
        sa.Column("code", sa.String(length=50), nullable=False, comment="稳定机构编码"),
        sa.Column(
            "display_name", sa.String(length=100), nullable=False, comment="面向用户的企业名称"
        ),
        sa.Column("icon_url", sa.String(length=500), nullable=True, comment="企业展示图标地址"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="机构状态：active 启用，suspended 停止企业工作台操作",
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
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_enterprise_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_enterprise_code"),
        comment="权益合作企业",
    )
    op.create_table(
        "t_benefit_purchase_batch",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="权益采购批次 ID"),
        sa.Column("enterprise_id", sa.BigInteger(), nullable=False, comment="采购企业 ID"),
        sa.Column(
            "package_id", sa.BigInteger(), nullable=False, comment="采购时绑定的权益包版本 ID"
        ),
        sa.Column(
            "purchase_reference",
            sa.String(length=100),
            nullable=False,
            comment="企业内唯一采购参考号",
        ),
        sa.Column(
            "purchase_contract_ref",
            sa.String(length=500),
            nullable=True,
            comment="企业签署的采购合同文档引用",
        ),
        sa.Column("purchased_units", sa.Integer(), nullable=False, comment="采购的完整权益包份数"),
        sa.Column(
            "paid_amount_minor",
            sa.BigInteger(),
            nullable=False,
            comment="以币种最小单位记录的实收金额",
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'CNY'"),
            nullable=False,
            comment="实收币种代码",
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True, comment="核验的实收时间"),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
            comment="批次状态：draft 草稿，active 已生效，closed 已停止发放",
        ),
        sa.Column(
            "activated_at", sa.DateTime(timezone=True), nullable=True, comment="采购批次生效时间"
        ),
        sa.Column(
            "closed_at", sa.DateTime(timezone=True), nullable=True, comment="采购批次关闭时间"
        ),
        sa.Column(
            "issue_until", sa.DateTime(timezone=True), nullable=False, comment="企业发放截止时间"
        ),
        sa.Column(
            "claim_until",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="用户领取截止时间上限",
        ),
        sa.Column(
            "service_valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="服务可使用的起始时间",
        ),
        sa.Column(
            "service_valid_until",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="服务可使用的截止时间",
        ),
        sa.Column(
            "display_timezone",
            sa.String(length=64),
            server_default=sa.text("'Asia/Shanghai'"),
            nullable=False,
            comment="App 显示计划日期使用的时区",
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
            "(status IN ('active', 'closed')) = (activated_at IS NOT NULL) AND (activated_at IS NULL OR (paid_at IS NOT NULL AND paid_at <= activated_at AND activated_at < issue_until)) AND (status = 'closed') = (closed_at IS NOT NULL) AND (closed_at IS NULL OR closed_at >= activated_at)",
            name="ck_benefit_purchase_status_times",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'closed')", name="ck_benefit_purchase_status"
        ),
        sa.CheckConstraint(
            "issue_until <= claim_until AND claim_until <= service_valid_until AND service_valid_from < service_valid_until",
            name="ck_benefit_purchase_windows",
        ),
        sa.CheckConstraint(
            "purchased_units > 0 AND paid_amount_minor > 0", name="ck_benefit_purchase_amounts"
        ),
        sa.ForeignKeyConstraint(["enterprise_id"], ["t_enterprise.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["package_id"], ["t_benefit_package.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "enterprise_id", "purchase_reference", name="uq_benefit_purchase_reference"
        ),
        comment="企业权益包采购批次、付款及生效信息",
    )
    op.create_index(
        "idx_benefit_purchase_enterprise_status",
        "t_benefit_purchase_batch",
        ["enterprise_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "idx_benefit_purchase_package", "t_benefit_purchase_batch", ["package_id"], unique=False
    )
    op.create_table(
        "t_benefit_service_version",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="服务版本 ID"),
        sa.Column("service_id", sa.BigInteger(), nullable=False, comment="所属权益服务主档 ID"),
        sa.Column(
            "version_no", sa.Integer(), nullable=False, comment="同一服务内从 1 开始的版本号"
        ),
        sa.Column(
            "name", sa.String(length=100), nullable=False, comment="该版本的服务及权益卡片名称"
        ),
        sa.Column("summary", sa.String(length=300), nullable=True, comment="权益卡片短说明"),
        sa.Column(
            "description_ref",
            sa.String(length=500),
            nullable=False,
            comment="服务说明文档的固定版本引用",
        ),
        sa.Column(
            "terms_ref",
            sa.String(length=500),
            nullable=False,
            comment="服务条款或合同的固定版本引用",
        ),
        sa.Column("delivery_terms", sa.Text(), nullable=False, comment="单次交付及核销标准"),
        sa.Column(
            "fulfillment_mode",
            sa.String(length=20),
            nullable=False,
            comment="履约方式：online 线上，appointment 预约",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
            comment="版本状态：draft 草稿，published 已发布，retired 已退役",
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
            name="ck_benefit_service_version_published",
        ),
        sa.CheckConstraint(
            "fulfillment_mode IN ('online', 'appointment')",
            name="ck_benefit_service_version_fulfillment",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_benefit_service_version_status"
        ),
        sa.CheckConstraint("version_no > 0", name="ck_benefit_service_version_number"),
        sa.ForeignKeyConstraint(["service_id"], ["t_benefit_service.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "version_no", name="uq_benefit_service_version_number"),
        comment="权益服务说明、条款与履约规则版本",
    )
    op.create_index(
        "idx_benefit_service_version_status",
        "t_benefit_service_version",
        ["service_id", "status", "version_no"],
        unique=False,
    )
    op.create_table(
        "t_benefit_staff",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="平台岗位登记 ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="平台工作人员账号 ID"),
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            comment="岗位：admin 管理，ops 运营，dispatcher 派单，provider 履约",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="岗位状态：active 启用，disabled 停用",
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
            "role IN ('admin', 'ops', 'dispatcher', 'provider')", name="ck_benefit_staff_role"
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_benefit_staff_status"),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role", name="uq_benefit_staff_user_role"),
        comment="权益平台运营与履约岗位",
    )
    op.create_index(
        "idx_benefit_staff_role_status",
        "t_benefit_staff",
        ["role", "status", "user_id"],
        unique=False,
    )
    op.create_table(
        "t_enterprise_member",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="企业成员 ID"),
        sa.Column("enterprise_id", sa.BigInteger(), nullable=False, comment="所属合作企业 ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="成员的用户账号 ID"),
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            comment="企业角色：admin 管理，issuer 发放，support 咨询",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="成员状态：active 启用，disabled 停用",
        ),
        sa.Column(
            "work_display_name", sa.String(length=100), nullable=True, comment="工作展示称呼"
        ),
        sa.Column(
            "work_title", sa.String(length=100), nullable=True, comment="对外展示的工作职位或简介"
        ),
        sa.Column(
            "work_phone", sa.String(length=32), nullable=True, comment="企业提供的工作联系电话"
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
            "role IN ('admin', 'issuer', 'support')", name="ck_enterprise_member_role"
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_enterprise_member_status"),
        sa.ForeignKeyConstraint(["enterprise_id"], ["t_enterprise.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("enterprise_id", "user_id", name="uq_enterprise_member_user"),
        comment="企业成员、组织权限及工作联系资料",
    )
    op.create_index(
        "idx_enterprise_member_user_status",
        "t_enterprise_member",
        ["user_id", "status", "enterprise_id"],
        unique=False,
    )
    op.create_table(
        "t_benefit_batch_contact",
        sa.Column(
            "purchase_batch_id",
            sa.BigInteger(),
            nullable=False,
            comment="配置联系人的权益采购批次 ID",
        ),
        sa.Column(
            "enterprise_member_id",
            sa.BigInteger(),
            nullable=False,
            comment="企业联系人对应的成员登记 ID",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="联系人的展示顺序",
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
        sa.CheckConstraint("sort_order >= 0", name="ck_benefit_batch_contact_sort_order"),
        sa.ForeignKeyConstraint(
            ["enterprise_member_id"], ["t_enterprise_member.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["purchase_batch_id"], ["t_benefit_purchase_batch.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("purchase_batch_id", "enterprise_member_id"),
        comment="权益采购批次展示的客户经理",
    )
    op.create_index(
        "idx_benefit_batch_contact_member",
        "t_benefit_batch_contact",
        ["enterprise_member_id"],
        unique=False,
    )
    op.create_table(
        "t_benefit_issue_request",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="批量发放请求 ID"),
        sa.Column(
            "purchase_batch_id", sa.BigInteger(), nullable=False, comment="对应权益采购批次 ID"
        ),
        sa.Column(
            "issued_by_user_id",
            sa.BigInteger(),
            nullable=False,
            comment="实际发放权益的企业用户账号 ID",
        ),
        sa.Column(
            "idempotency_key", sa.Uuid(), nullable=False, comment="企业工作台发放请求的幂等标识"
        ),
        sa.Column(
            "request_digest",
            sa.LargeBinary(),
            nullable=False,
            comment="发放请求的带密钥摘要，32 字节",
        ),
        sa.Column(
            "digest_key_version", sa.SmallInteger(), nullable=False, comment="请求摘要密钥版本"
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
            "octet_length(request_digest) = 32 AND digest_key_version > 0",
            name="ck_benefit_issue_request_digest",
        ),
        sa.ForeignKeyConstraint(["issued_by_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["purchase_batch_id"], ["t_benefit_purchase_batch.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "purchase_batch_id",
            "issued_by_user_id",
            "idempotency_key",
            name="uq_benefit_issue_request_key",
        ),
        comment="企业权益批量发放请求及幂等记录",
    )
    op.create_index(
        "idx_benefit_issue_request_batch",
        "t_benefit_issue_request",
        ["purchase_batch_id", "id"],
        unique=False,
    )
    op.create_table(
        "t_benefit_package_item",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="权益包明细 ID"),
        sa.Column("package_id", sa.BigInteger(), nullable=False, comment="权益包版本 ID"),
        sa.Column(
            "service_version_id",
            sa.BigInteger(),
            nullable=False,
            comment="固定的服务说明、条款及履约版本 ID",
        ),
        sa.Column("icon_key", sa.String(length=50), nullable=True, comment="权益卡片图标映射标识"),
        sa.Column(
            "quota_per_unit", sa.Integer(), nullable=False, comment="每份权益包含的该服务次数"
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="卡片展示顺序",
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
        sa.CheckConstraint("quota_per_unit > 0", name="ck_benefit_package_item_quota"),
        sa.CheckConstraint("sort_order >= 0", name="ck_benefit_package_item_sort_order"),
        sa.ForeignKeyConstraint(["package_id"], ["t_benefit_package.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["service_version_id"], ["t_benefit_service_version.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_id", "service_version_id", name="uq_benefit_package_item_version"
        ),
        comment="权益包版本中的服务及次数",
    )
    op.create_index(
        "idx_benefit_package_item_order",
        "t_benefit_package_item",
        ["package_id", "sort_order", "id"],
        unique=False,
    )
    op.create_index(
        "idx_benefit_package_item_version",
        "t_benefit_package_item",
        ["service_version_id"],
        unique=False,
    )
    op.create_table(
        "t_benefit_grant",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="发放和领取共用的权益 ID"),
        sa.Column(
            "issue_request_id", sa.BigInteger(), nullable=False, comment="所属批量发放请求 ID"
        ),
        sa.Column(
            "item_no", sa.Integer(), nullable=False, comment="该权益在发放请求内从 1 开始的顺序"
        ),
        sa.Column(
            "crypto_key_version",
            sa.SmallInteger(),
            nullable=False,
            comment="码与手机号摘要的密钥版本",
        ),
        sa.Column(
            "code_digest", sa.LargeBinary(), nullable=False, comment="权益码的带密钥摘要，32 字节"
        ),
        sa.Column(
            "delivery_ciphertext",
            sa.LargeBinary(),
            nullable=True,
            comment="待领取权益码的加密交付副本",
        ),
        sa.Column(
            "recipient_phone_digest",
            sa.LargeBinary(),
            nullable=False,
            comment="定向接收手机号的带密钥摘要，32 字节",
        ),
        sa.Column(
            "recipient_phone_last4",
            sa.CHAR(length=4),
            nullable=False,
            comment="供脱敏展示的接收手机号尾号",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'issued'"),
            nullable=False,
            comment="状态：issued 待领，claimed 已领，voided 作废，expired 未领过期",
        ),
        sa.Column(
            "claim_expires_at", sa.DateTime(timezone=True), nullable=False, comment="领取截止时间"
        ),
        sa.Column(
            "void_reason", sa.String(length=200), nullable=True, comment="未领取权益作废原因"
        ),
        sa.Column(
            "recipient_user_id", sa.BigInteger(), nullable=True, comment="成功领取的用户账号 ID"
        ),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="成功领取整份权益的时间",
        ),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="领取时冻结的服务起始时间",
        ),
        sa.Column(
            "valid_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="领取时冻结的服务截止时间",
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
            "(status = 'claimed') = (recipient_user_id IS NOT NULL) AND (status = 'claimed') = (claimed_at IS NOT NULL) AND (status = 'claimed') = (valid_from IS NOT NULL) AND (status = 'claimed') = (valid_until IS NOT NULL) AND (status <> 'claimed' OR (claimed_at < claim_expires_at AND claimed_at < valid_until AND valid_from < valid_until))",
            name="ck_benefit_grant_claim",
        ),
        sa.CheckConstraint(
            "(status = 'issued') = (delivery_ciphertext IS NOT NULL)",
            name="ck_benefit_grant_delivery",
        ),
        sa.CheckConstraint(
            "(status = 'voided') = (void_reason IS NOT NULL)", name="ck_benefit_grant_void_reason"
        ),
        sa.CheckConstraint(
            "recipient_phone_last4 ~ '^[0-9]{4}$'", name="ck_benefit_grant_phone_last4"
        ),
        sa.CheckConstraint(
            "status IN ('issued', 'claimed', 'voided', 'expired')", name="ck_benefit_grant_status"
        ),
        sa.CheckConstraint(
            "crypto_key_version > 0 AND octet_length(code_digest) = 32 AND octet_length(recipient_phone_digest) = 32",
            name="ck_benefit_grant_digests",
        ),
        sa.CheckConstraint("item_no > 0", name="ck_benefit_grant_item_number"),
        sa.ForeignKeyConstraint(
            ["issue_request_id"], ["t_benefit_issue_request.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("crypto_key_version", "code_digest", name="uq_benefit_grant_code"),
        sa.UniqueConstraint("issue_request_id", "item_no", name="uq_benefit_grant_issue_item"),
        comment="同一份权益的定向发放、码凭据和领取归属",
    )
    op.create_index(
        "idx_benefit_grant_expiry",
        "t_benefit_grant",
        ["status", "claim_expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_benefit_grant_invitation",
        "t_benefit_grant",
        ["crypto_key_version", "recipient_phone_digest", "claim_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'issued' AND is_del = false"),
    )
    op.create_index(
        "idx_benefit_grant_recipient",
        "t_benefit_grant",
        ["recipient_user_id", "claimed_at", "id"],
        unique=False,
    )
    op.create_table(
        "t_benefit_balance",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="服务余额 ID"),
        sa.Column("grant_id", sa.BigInteger(), nullable=False, comment="已领取的权益 ID"),
        sa.Column(
            "package_item_id",
            sa.BigInteger(),
            nullable=False,
            comment="领取时适用的权益包服务明细 ID",
        ),
        sa.Column("granted_count", sa.Integer(), nullable=False, comment="领取时发放的服务总次数"),
        sa.Column(
            "reserved_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="当前占用次数",
        ),
        sa.Column(
            "used_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="累计已核销次数",
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
            "granted_count > 0 AND reserved_count >= 0 AND used_count >= 0 AND used_count <= granted_count AND reserved_count <= granted_count - used_count",
            name="ck_benefit_balance_counts",
        ),
        sa.ForeignKeyConstraint(["grant_id"], ["t_benefit_grant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["package_item_id"], ["t_benefit_package_item.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grant_id", "package_item_id", name="uq_benefit_balance_grant_item"),
        comment="用户一份权益内单项服务的发放、占用及核销次数",
    )
    op.create_table(
        "t_benefit_service_order",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="服务单 ID，也是履约请求标识"),
        sa.Column(
            "balance_id",
            sa.BigInteger(),
            nullable=False,
            comment="本次申请占用和核销的具体服务余额 ID",
        ),
        sa.Column(
            "requested_by_user_id",
            sa.BigInteger(),
            nullable=False,
            comment="发起服务申请的用户账号 ID",
        ),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, comment="创建服务单请求的幂等标识"),
        sa.Column(
            "request_digest",
            sa.LargeBinary(),
            nullable=False,
            comment="创建请求的带密钥摘要，32 字节",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            comment="状态：requested 待预约，reserved 已占用，in_progress 履约中，completed 完成，cancelled 取消，failed 失败",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="服务单业务状态版本",
        ),
        sa.Column(
            "memory_session_id",
            sa.BigInteger(),
            nullable=True,
            comment="用户指定的记忆会话 ID，按会话权限另行访问",
        ),
        sa.Column(
            "request_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="期望时段、选项及业务对象引用等非敏感请求数据",
        ),
        sa.Column(
            "private_data_ciphertext",
            sa.LargeBinary(),
            nullable=True,
            comment="地址和联系电话等履约资料密文",
        ),
        sa.Column(
            "private_data_key_version",
            sa.SmallInteger(),
            nullable=True,
            comment="履约资料加密密钥版本",
        ),
        sa.Column(
            "digest_key_version", sa.SmallInteger(), nullable=False, comment="创建请求摘要密钥版本"
        ),
        sa.Column(
            "assignee_user_id", sa.BigInteger(), nullable=True, comment="获派的服务人员账号 ID"
        ),
        sa.Column(
            "scheduled_start_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="正式预约开始时间",
        ),
        sa.Column(
            "scheduled_end_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="正式预约结束时间",
        ),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="首次占用服务次数的时间",
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True, comment="开始履约的时间"
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="进入完成、取消或失败终态的时间",
        ),
        sa.Column(
            "result_ref",
            sa.String(length=200),
            nullable=True,
            comment="完成的服务成果或履约证据引用",
        ),
        sa.Column(
            "terminal_reason",
            sa.String(length=50),
            nullable=True,
            comment="取消、失败或爽约的业务原因码",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="线上处理尝试次数",
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="线上任务可下次认领的时间",
        ),
        sa.Column(
            "lease_token", sa.Uuid(), nullable=True, comment="线上处理器用于提交结果的租约标识"
        ),
        sa.Column(
            "lease_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="当前线上处理租约到期时间",
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
            "(status IN ('completed', 'cancelled', 'failed')) = (finished_at IS NOT NULL) AND (status <> 'completed' OR result_ref IS NOT NULL)",
            name="ck_benefit_order_finished",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_data) = 'object'", name="ck_benefit_order_request_data"
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'reserved', 'in_progress', 'completed', 'cancelled', 'failed')",
            name="ck_benefit_order_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('in_progress', 'completed') OR started_at IS NOT NULL",
            name="ck_benefit_order_started_at",
        ),
        sa.CheckConstraint(
            "status NOT IN ('reserved', 'in_progress', 'completed') OR reserved_at IS NOT NULL",
            name="ck_benefit_order_reserved_at",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL) AND (finished_at IS NULL OR (lease_token IS NULL AND next_attempt_at IS NULL))",
            name="ck_benefit_order_lease",
        ),
        sa.CheckConstraint(
            "(private_data_ciphertext IS NULL) = (private_data_key_version IS NULL) AND (private_data_key_version IS NULL OR private_data_key_version > 0)",
            name="ck_benefit_order_private_data",
        ),
        sa.CheckConstraint(
            "(scheduled_start_at IS NULL AND scheduled_end_at IS NULL) OR (scheduled_start_at IS NOT NULL AND scheduled_end_at IS NOT NULL AND scheduled_start_at < scheduled_end_at)",
            name="ck_benefit_order_schedule",
        ),
        sa.CheckConstraint(
            "octet_length(request_digest) = 32 AND digest_key_version > 0",
            name="ck_benefit_order_request_digest",
        ),
        sa.CheckConstraint("version > 0 AND attempt_count >= 0", name="ck_benefit_order_counters"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["balance_id"], ["t_benefit_balance.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["memory_session_id"], ["t_memory_session.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requested_by_user_id", "idempotency_key", name="uq_benefit_order_request"
        ),
        comment="一次权益预约或线上服务申请的当前履约状态",
    )
    op.create_index(
        "idx_benefit_order_assignee_schedule",
        "t_benefit_service_order",
        ["assignee_user_id", "status", "scheduled_start_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_benefit_order_balance", "t_benefit_service_order", ["balance_id", "id"], unique=False
    )
    op.create_index(
        "idx_benefit_order_queue",
        "t_benefit_service_order",
        ["next_attempt_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "next_attempt_at IS NOT NULL AND status IN ('reserved', 'in_progress') AND is_del = false"
        ),
    )
    op.create_table(
        "t_benefit_order_event",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="服务单流水 ID"),
        sa.Column(
            "service_order_id", sa.BigInteger(), nullable=False, comment="发生该动作的权益服务单 ID"
        ),
        sa.Column("action_key", sa.Uuid(), nullable=False, comment="同一服务单内动作的幂等标识"),
        sa.Column(
            "request_digest",
            sa.LargeBinary(),
            nullable=False,
            comment="动作请求的带密钥摘要，32 字节",
        ),
        sa.Column(
            "digest_key_version", sa.SmallInteger(), nullable=False, comment="动作请求摘要密钥版本"
        ),
        sa.Column(
            "action",
            sa.String(length=30),
            nullable=False,
            comment="创建、确认预约、改约、开始、核销、取消或失败",
        ),
        sa.Column(
            "from_status",
            sa.String(length=20),
            nullable=True,
            comment="动作之前的服务单状态，创建时为空",
        ),
        sa.Column(
            "to_status", sa.String(length=20), nullable=False, comment="动作之后的服务单状态"
        ),
        sa.Column("order_version", sa.Integer(), nullable=False, comment="动作完成后的服务单版本"),
        sa.Column(
            "reserved_delta",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="占用次数变更量",
        ),
        sa.Column(
            "used_delta",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="核销次数变更量",
        ),
        sa.Column(
            "event_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="预约时段、人员、原因及回执等变更记录",
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
            "((used_delta = 0 AND reserved_delta IN (-1, 0, 1)) OR (used_delta = 1 AND reserved_delta = -1)) AND (action = 'complete') = (used_delta = 1)",
            name="ck_benefit_event_balance_delta",
        ),
        sa.CheckConstraint(
            "(from_status IS NULL OR from_status IN ('requested', 'reserved', 'in_progress', 'completed', 'cancelled', 'failed')) AND to_status IN ('requested', 'reserved', 'in_progress', 'completed', 'cancelled', 'failed')",
            name="ck_benefit_event_statuses",
        ),
        sa.CheckConstraint(
            "action IN ('create', 'confirm_booking', 'reschedule', 'start', 'complete', 'cancel', 'fail')",
            name="ck_benefit_event_action",
        ),
        sa.CheckConstraint("jsonb_typeof(event_data) = 'object'", name="ck_benefit_event_data"),
        sa.CheckConstraint(
            "order_version > 0 AND (order_version = 1) = (from_status IS NULL) AND (action = 'create') = (order_version = 1)",
            name="ck_benefit_event_version",
        ),
        sa.CheckConstraint(
            "octet_length(request_digest) = 32 AND digest_key_version > 0",
            name="ck_benefit_event_request_digest",
        ),
        sa.ForeignKeyConstraint(
            ["service_order_id"], ["t_benefit_service_order.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_order_id", "action_key", name="uq_benefit_event_action"),
        sa.UniqueConstraint(
            "service_order_id", "order_version", name="uq_benefit_event_order_version"
        ),
        comment="服务单动作、占用及核销次数变化流水",
    )


def downgrade() -> None:
    # 先移除流水、服务单和余额等依赖表，再移除被引用的主档。
    op.drop_table("t_benefit_order_event")
    op.drop_index(
        "idx_benefit_order_queue",
        table_name="t_benefit_service_order",
        postgresql_where=sa.text(
            "next_attempt_at IS NOT NULL AND status IN ('reserved', 'in_progress') AND is_del = false"
        ),
    )
    op.drop_index("idx_benefit_order_balance", table_name="t_benefit_service_order")
    op.drop_index("idx_benefit_order_assignee_schedule", table_name="t_benefit_service_order")
    op.drop_table("t_benefit_service_order")
    op.drop_table("t_benefit_balance")
    op.drop_index("idx_benefit_grant_recipient", table_name="t_benefit_grant")
    op.drop_index(
        "idx_benefit_grant_invitation",
        table_name="t_benefit_grant",
        postgresql_where=sa.text("status = 'issued' AND is_del = false"),
    )
    op.drop_index("idx_benefit_grant_expiry", table_name="t_benefit_grant")
    op.drop_table("t_benefit_grant")
    op.drop_index("idx_benefit_package_item_version", table_name="t_benefit_package_item")
    op.drop_index("idx_benefit_package_item_order", table_name="t_benefit_package_item")
    op.drop_table("t_benefit_package_item")
    op.drop_index("idx_benefit_issue_request_batch", table_name="t_benefit_issue_request")
    op.drop_table("t_benefit_issue_request")
    op.drop_index("idx_benefit_batch_contact_member", table_name="t_benefit_batch_contact")
    op.drop_table("t_benefit_batch_contact")
    op.drop_index("idx_enterprise_member_user_status", table_name="t_enterprise_member")
    op.drop_table("t_enterprise_member")
    op.drop_index("idx_benefit_staff_role_status", table_name="t_benefit_staff")
    op.drop_table("t_benefit_staff")
    op.drop_index("idx_benefit_service_version_status", table_name="t_benefit_service_version")
    op.drop_table("t_benefit_service_version")
    op.drop_index("idx_benefit_purchase_package", table_name="t_benefit_purchase_batch")
    op.drop_index("idx_benefit_purchase_enterprise_status", table_name="t_benefit_purchase_batch")
    op.drop_table("t_benefit_purchase_batch")
    op.drop_table("t_enterprise")
    op.drop_table("t_benefit_service")
    op.drop_index("idx_benefit_package_status", table_name="t_benefit_package")
    op.drop_table("t_benefit_package")
