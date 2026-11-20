"""[新增] 图谱事件及其版本化成员。

Revision ID: 20261121_01
Revises: 20261120_02
Create Date: 2026-11-21 00:39:23.493703
"""

import sqlalchemy as sa
from alembic import op

revision = "20261121_01"
down_revision = "20261120_02"
branch_labels = None
depends_on = None

EVENT_TABLE = "t_memory_graph_event"
MEMBER_TABLE = "t_memory_graph_event_element"
ACTIVE_MEMBER = "is_del = false AND status = 'active'"


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_by", sa.BigInteger(), comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del", sa.Boolean(), server_default=sa.text("false"), nullable=False, comment="是否软删除"
        ),
    )


def upgrade() -> None:
    op.create_table(
        EVENT_TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="图谱事件主键 ID"),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("t_user.id", name="fk_memory_graph_event_owner", ondelete="RESTRICT"),
            nullable=False,
            comment="回忆录所有者 ID",
        ),
        sa.Column(
            "anchor_key", sa.String(64), nullable=False, comment="用来恢复同一事件节点的语义锚点 SHA-256"
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'published'"),
            nullable=False,
            comment="published 当前发布，retired 已退役或暂无有效依据",
        ),
        sa.Column(
            "last_validation_job_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_validation_job.id", name="fk_memory_graph_event_job", ondelete="RESTRICT"
            ),
            nullable=False,
            comment="最近发布或退役此节点的复验任务 ID",
        ),
        *_audit_columns(),
        sa.CheckConstraint("anchor_key ~ '^[0-9a-f]{64}$'", name="ck_memory_graph_event_anchor_key"),
        sa.CheckConstraint(
            "status IN ('published', 'retired')", name="ck_memory_graph_event_status"
        ),
        sa.UniqueConstraint("owner_user_id", "anchor_key", name="uq_memory_graph_event_owner_anchor"),
        comment="整本回忆录的稳定图谱事件节点",
    )
    op.create_index(
        "idx_memory_graph_event_published",
        EVENT_TABLE,
        ["owner_user_id", "id"],
        postgresql_where=sa.text("is_del = false AND status = 'published'"),
    )
    op.create_index(
        "idx_memory_graph_event_validation_job", EVENT_TABLE, ["last_validation_job_id"]
    )

    op.create_table(
        MEMBER_TABLE,
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="图谱事件成员主键 ID"
        ),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_graph_event.id", name="fk_memory_graph_member_event", ondelete="RESTRICT"
            ),
            nullable=False,
            comment="归入的图谱事件 ID",
        ),
        sa.Column(
            "element_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_element.id", name="fk_memory_graph_member_element", ondelete="RESTRICT"
            ),
            nullable=False,
            comment="本次发布引用的记忆元素 ID",
        ),
        sa.Column(
            "source_revision_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_source_revision.id",
                name="fk_memory_graph_member_source",
                ondelete="RESTRICT",
            ),
            nullable=False,
            comment="支撑该成员的原文版本 ID",
        ),
        sa.Column(
            "draft_version_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_draft_version.id", name="fk_memory_graph_member_draft", ondelete="RESTRICT"
            ),
            nullable=False,
            comment="元素绑定的整理稿版本 ID",
        ),
        sa.Column(
            "element_anchor_key",
            sa.String(64),
            nullable=False,
            comment="成员的语义锚点 SHA-256，用于重跑及拆分溯源",
        ),
        sa.Column(
            "membership_basis",
            sa.String(24),
            nullable=False,
            comment="source 独立证据归属，confirmed_same_event 用户确认同一事件",
        ),
        sa.Column(
            "identity_group_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_conflict_group.id",
                name="fk_memory_graph_member_identity",
                ondelete="RESTRICT",
            ),
            comment="确认属于同一事件的核对组 ID",
        ),
        sa.Column(
            "identity_answer_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_review_answer.id", name="fk_memory_graph_member_answer", ondelete="RESTRICT"
            ),
            comment="发布时采用的同一事件确认回答 ID",
        ),
        sa.Column(
            "identity_evidence_fingerprint",
            sa.String(64),
            comment="发布时归组证据的 SHA-256，用于核对裁定是否仍适用",
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="active 当前成员，superseded 已被替代或失效",
        ),
        sa.Column(
            "published_by_job_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "t_memory_validation_job.id", name="fk_memory_graph_member_job", ondelete="RESTRICT"
            ),
            nullable=False,
            comment="首次发布此版事件成员的复验任务 ID",
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "element_anchor_key ~ '^[0-9a-f]{64}$'", name="ck_memory_graph_member_anchor_key"
        ),
        sa.CheckConstraint(
            "identity_evidence_fingerprint IS NULL "
            "OR identity_evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_memory_graph_member_identity_fingerprint",
        ),
        # 独立来源不绑定裁定。确认同一事件时要保存结论及所用证据版本。
        sa.CheckConstraint(
            "(membership_basis = 'source' AND identity_group_id IS NULL "
            "AND identity_answer_id IS NULL AND identity_evidence_fingerprint IS NULL) OR "
            "(membership_basis = 'confirmed_same_event' AND identity_group_id IS NOT NULL "
            "AND identity_answer_id IS NOT NULL AND identity_evidence_fingerprint IS NOT NULL)",
            name="ck_memory_graph_member_identity_basis",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')", name="ck_memory_graph_member_status"
        ),
        comment="图谱事件成员、来源版本和归组依据",
    )
    op.create_index(
        "uq_memory_graph_member_active_element",
        MEMBER_TABLE,
        ["element_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_MEMBER),
    )
    for name, columns in (
        ("idx_memory_graph_member_active_event", ["event_id", "id"]),
        ("idx_memory_graph_member_active_source", ["source_revision_id"]),
        ("idx_memory_graph_member_active_draft", ["draft_version_id"]),
        ("idx_memory_graph_member_active_identity", ["identity_group_id"]),
    ):
        op.create_index(name, MEMBER_TABLE, columns, postgresql_where=sa.text(ACTIVE_MEMBER))
    op.create_index(
        "idx_memory_graph_member_anchor_history", MEMBER_TABLE, ["element_anchor_key", "id"]
    )


def downgrade() -> None:
    # 先删除子表，表内索引随表删除。已有记忆和裁定记录继续保留。
    op.drop_table(MEMBER_TABLE)
    op.drop_table(EVENT_TABLE)
