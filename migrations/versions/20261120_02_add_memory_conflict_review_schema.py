"""[新增] 冲突分组、证据、版本化问答及回忆录级复验。

Revision ID: 20261120_02
Revises: 20261120_01
Create Date: 2026-11-20 22:10:51.103401
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261120_02"
down_revision = "20261120_01"
branch_labels = None
depends_on = None


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
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
    )


def upgrade() -> None:
    op.create_table(
        "t_memory_conflict_group",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False, comment="回忆录所有者 ID"),
        sa.Column(
            "event_key",
            sa.String(length=64),
            nullable=False,
            comment="依据独立上下文生成的事件归组键",
        ),
        sa.Column(
            "field_key", sa.String(length=40), nullable=False, comment="被核对的事件属性或地点角色"
        ),
        sa.Column(
            "group_type", sa.String(length=24), nullable=False, comment="冲突或需要澄清的事件归属"
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'open'"),
            nullable=False,
            comment="待处理、已得出结论或已被替代",
        ),
        sa.Column(
            "evidence_fingerprint",
            sa.String(length=64),
            nullable=False,
            comment="当前证据语义集合的 SHA-256",
        ),
        sa.Column(
            "resolution_answer_id",
            sa.BigInteger(),
            nullable=True,
            comment="当前结论采纳的同组回答 ID，自动解决时为空",
        ),
        sa.Column(
            "resolution_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="采纳值、支持证据及适用元素，或确认的事件划分",
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "field_key IN ('event_identity', 'event_time', 'event_location', 'departure_location', 'destination_location')",
            name="ck_memory_group_field_key",
        ),
        sa.CheckConstraint(
            "group_type IN ('conflict', 'clarification')", name="ck_memory_group_type"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resolution_payload) = 'object'", name="ck_memory_group_resolution"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'superseded')", name="ck_memory_group_status"
        ),
        sa.CheckConstraint("length(btrim(event_key)) > 0", name="ck_memory_group_event_key"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        comment="同一事件属性的冲突或澄清分组",
    )
    op.create_index(
        "idx_memory_group_owner_status",
        "t_memory_conflict_group",
        ["owner_user_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_group_resolution_answer",
        "t_memory_conflict_group",
        ["resolution_answer_id"],
        unique=False,
    )
    op.create_index(
        "uq_memory_group_active_fact",
        "t_memory_conflict_group",
        ["owner_user_id", "event_key", "field_key"],
        unique=True,
        postgresql_where=sa.text("is_del = false AND status <> 'superseded'"),
    )
    op.create_table(
        "t_memory_validation_job",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            nullable=False,
            comment="任务作用范围的回忆录所有者 ID",
        ),
        sa.Column(
            "requested_by_user_id",
            sa.BigInteger(),
            nullable=True,
            comment="任务发起人 ID，系统触发时为空",
        ),
        sa.Column(
            "job_type", sa.String(length=24), nullable=False, comment="单元素核验、回忆录复验或重构"
        ),
        sa.Column(
            "target_element_id",
            sa.BigInteger(),
            nullable=True,
            comment="单元素任务需要核验的元素 ID",
        ),
        sa.Column(
            "retry_of_job_id", sa.BigInteger(), nullable=True, comment="手动重试关联的前一任务 ID"
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'queued'"),
            nullable=False,
            comment="排队、执行中、成功、失败或取消",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="已领取执行的次数，含租约超时",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
            comment="单次任务的最大尝试次数",
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="允许首次领取或重试的时间",
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True, comment="最近一次领取时间"
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="任务进入最终状态的时间",
        ),
        sa.Column(
            "worker_token", sa.Uuid(), nullable=True, comment="当前领取者用于发布结果的租约令牌"
        ),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="当前执行租约到期时间",
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=120),
            nullable=False,
            comment="同一所有者下的触发去重键",
        ),
        sa.Column(
            "input_fingerprint",
            sa.String(length=64),
            nullable=True,
            comment="开始核验时输入版本和权限快照的 SHA-256",
        ),
        sa.Column(
            "input_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="任务触发引用和实际核对的输入版本清单",
        ),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="已验证、冲突及待回答项的统计和引用",
        ),
        sa.Column(
            "error_code",
            sa.String(length=100),
            nullable=True,
            comment="任务失败、租约耗尽或取消的原因码",
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "(job_type = 'verify_element') = (target_element_id IS NOT NULL)",
            name="ck_memory_validation_job_target",
        ),
        sa.CheckConstraint(
            "(status = 'processing') = (worker_token IS NOT NULL) AND (status = 'processing') = (lease_expires_at IS NOT NULL)",
            name="ck_memory_validation_job_lease",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled')) = (finished_at IS NOT NULL)",
            name="ck_memory_validation_job_finished",
        ),
        sa.CheckConstraint(
            "job_type IN ('verify_element', 'review_memoir', 'rebuild_memoir')",
            name="ck_memory_validation_job_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_payload) = 'object'", name="ck_memory_validation_job_input"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_payload) = 'object'", name="ck_memory_validation_job_result"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'succeeded', 'failed', 'cancelled')",
            name="ck_memory_validation_job_status",
        ),
        sa.CheckConstraint(
            "max_attempts > 0 AND attempt_count BETWEEN 0 AND max_attempts",
            name="ck_memory_validation_job_attempts",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["retry_of_job_id"], ["t_memory_validation_job.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_element_id"], ["t_memory_element.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id", "idempotency_key", name="uq_memory_validation_job_request"
        ),
        comment="回忆录范围内的记忆验证、复验和重构任务",
    )
    op.create_index(
        "idx_memory_validation_job_lease",
        "t_memory_validation_job",
        ["lease_expires_at", "id"],
        unique=False,
        postgresql_where=sa.text("is_del = false AND status = 'processing'"),
    )
    op.create_index(
        "idx_memory_validation_job_owner_status",
        "t_memory_validation_job",
        ["owner_user_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_validation_job_queue",
        "t_memory_validation_job",
        ["available_at", "id"],
        unique=False,
        postgresql_where=sa.text("is_del = false AND status = 'queued'"),
    )
    op.create_index(
        "idx_memory_validation_job_retry",
        "t_memory_validation_job",
        ["retry_of_job_id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_validation_job_target",
        "t_memory_validation_job",
        ["target_element_id"],
        unique=False,
    )
    op.create_index(
        "uq_memory_validation_job_processing",
        "t_memory_validation_job",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("is_del = false AND status = 'processing'"),
    )
    op.create_table(
        "t_memory_review_question",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("conflict_group_id", sa.BigInteger(), nullable=False, comment="所属冲突组 ID"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="从 1 开始的组内问题版本"),
        sa.Column(
            "kind", sa.String(length=24), nullable=False, comment="澄清事件、核对取值或继续追问"
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="待回答、已回答、已跳过或已被替代",
        ),
        sa.Column("question_text", sa.Text(), nullable=False, comment="向所有者展示的具体核对问题"),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="当前版本的选项 ID、文字、候选结论和证据引用数组",
        ),
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="当前版本引用的同组证据 ID 数组",
        ),
        sa.Column(
            "evidence_fingerprint",
            sa.String(length=64),
            nullable=False,
            comment="提问时的证据语义集合指纹",
        ),
        sa.Column(
            "issued_by_job_id", sa.BigInteger(), nullable=False, comment="生成该版本的复验任务 ID"
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_ids) = 'array'", name="ck_memory_review_question_evidence"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(options) = 'array'", name="ck_memory_review_question_options"
        ),
        sa.CheckConstraint(
            "kind IN ('clarify_event', 'confirm_value', 'follow_up')",
            name="ck_memory_review_question_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'answered', 'skipped', 'superseded')",
            name="ck_memory_review_question_status",
        ),
        sa.CheckConstraint(
            "length(btrim(question_text)) > 0", name="ck_memory_review_question_text"
        ),
        sa.CheckConstraint("revision > 0", name="ck_memory_review_question_revision"),
        sa.ForeignKeyConstraint(
            ["conflict_group_id"], ["t_memory_conflict_group.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_job_id"], ["t_memory_validation_job.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conflict_group_id", "revision", name="uq_memory_review_question_revision"
        ),
        comment="面向所有者的冲突核对问题及其固定版本",
    )
    op.create_index(
        "idx_memory_review_question_job",
        "t_memory_review_question",
        ["issued_by_job_id"],
        unique=False,
    )
    op.create_index(
        "uq_memory_review_question_pending",
        "t_memory_review_question",
        ["conflict_group_id"],
        unique=True,
        postgresql_where=sa.text("is_del = false AND status = 'pending'"),
    )
    op.create_table(
        "t_memory_review_answer",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column(
            "question_id", sa.BigInteger(), nullable=False, comment="所回答的具体问题版本 ID"
        ),
        sa.Column(
            "answered_by_user_id",
            sa.BigInteger(),
            nullable=False,
            comment="提交回答的所有者账号 ID",
        ),
        sa.Column(
            "action",
            sa.String(length=24),
            nullable=False,
            comment="确认选项、补述纠正、记不清或跳过",
        ),
        sa.Column(
            "selected_option_id",
            sa.String(length=100),
            nullable=True,
            comment="所选的当前问题版本选项 ID",
        ),
        sa.Column("answer_text", sa.Text(), nullable=True, comment="所有者补充或纠正的原始文字"),
        sa.Column(
            "answer_fingerprint",
            sa.String(length=64),
            nullable=False,
            comment="回答动作及载荷的 SHA-256",
        ),
        sa.Column(
            "validation_job_id",
            sa.BigInteger(),
            nullable=False,
            comment="受理回答时登记的首个复验任务 ID",
        ),
        sa.Column(
            "result_status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="待复验、已采纳、仍未确认、需再问或失去当前依据",
        ),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="复验结论、后续问题及有效证据引用",
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "(action = 'confirm' AND selected_option_id IS NOT NULL AND length(btrim(selected_option_id)) > 0 AND answer_text IS NULL) OR (action = 'correct' AND selected_option_id IS NULL AND answer_text IS NOT NULL AND length(btrim(answer_text)) > 0) OR (action IN ('cannot_remember', 'skip') AND selected_option_id IS NULL AND answer_text IS NULL)",
            name="ck_memory_review_answer_action_payload",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_payload) = 'object'", name="ck_memory_review_answer_result"
        ),
        sa.CheckConstraint(
            "result_status IN ('pending', 'applied', 'unresolved', 'awaiting_follow_up', 'stale')",
            name="ck_memory_review_answer_result_status",
        ),
        sa.ForeignKeyConstraint(["answered_by_user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["question_id"], ["t_memory_review_question.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["validation_job_id"], ["t_memory_validation_job.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_id", name="uq_memory_review_answer_question"),
        comment="核对问题的用户回答及后续复验结果",
    )
    op.create_index(
        "idx_memory_review_answer_job",
        "t_memory_review_answer",
        ["validation_job_id"],
        unique=False,
    )
    op.create_table(
        "t_memory_conflict_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键 ID"),
        sa.Column("conflict_group_id", sa.BigInteger(), nullable=False, comment="所属冲突组 ID"),
        sa.Column("element_id", sa.BigInteger(), nullable=True, comment="提供该说法的记忆元素 ID"),
        sa.Column(
            "source_revision_id", sa.BigInteger(), nullable=True, comment="对应原文或 ASR 版本 ID"
        ),
        sa.Column("draft_version_id", sa.BigInteger(), nullable=True, comment="对应整理稿版本 ID"),
        sa.Column(
            "answer_id", sa.BigInteger(), nullable=True, comment="提供该说法的同组补述回答 ID"
        ),
        sa.Column(
            "normalized_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="时间、地点或事件线索的规范值",
        ),
        sa.Column(
            "evidence_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment="整理稿、原文及原声的位置，或补述中的引用区间",
        ),
        sa.Column(
            "evidence_key",
            sa.String(length=64),
            nullable=False,
            comment="来源、叙述者及事实含义的 SHA-256",
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'active'"),
            nullable=False,
            comment="证据有效或已因来源修订、撤回而被替代",
        ),
        *_audit_columns(),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_payload) = 'object'", name="ck_memory_conflict_evidence_payload"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(normalized_value) = 'object'", name="ck_memory_conflict_evidence_value"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded')", name="ck_memory_conflict_evidence_status"
        ),
        sa.CheckConstraint(
            "(element_id IS NOT NULL AND source_revision_id IS NOT NULL AND draft_version_id IS NOT NULL AND answer_id IS NULL) OR (element_id IS NULL AND source_revision_id IS NULL AND draft_version_id IS NULL AND answer_id IS NOT NULL)",
            name="ck_memory_conflict_evidence_source",
        ),
        sa.ForeignKeyConstraint(["answer_id"], ["t_memory_review_answer.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["conflict_group_id"], ["t_memory_conflict_group.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["draft_version_id"], ["t_memory_draft_version.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["element_id"], ["t_memory_element.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_revision_id"], ["t_memory_source_revision.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="冲突组内的原始说法、规范值及来源证据",
    )
    op.create_index(
        "idx_memory_conflict_evidence_answer",
        "t_memory_conflict_evidence",
        ["answer_id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_conflict_evidence_draft",
        "t_memory_conflict_evidence",
        ["draft_version_id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_conflict_evidence_element",
        "t_memory_conflict_evidence",
        ["element_id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_conflict_evidence_group",
        "t_memory_conflict_evidence",
        ["conflict_group_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "idx_memory_conflict_evidence_source",
        "t_memory_conflict_evidence",
        ["source_revision_id"],
        unique=False,
    )
    op.create_index(
        "uq_memory_conflict_evidence_active",
        "t_memory_conflict_evidence",
        ["conflict_group_id", "evidence_key"],
        unique=True,
        postgresql_where=sa.text("is_del = false AND status = 'active'"),
    )
    # 回答经问题引用冲突组，当前结论的反向外键在所有引用表创建后再添加。
    op.create_foreign_key(
        "fk_memory_group_resolution_answer",
        "t_memory_conflict_group",
        "t_memory_review_answer",
        ["resolution_answer_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "t_memory_conflict",
        sa.Column(
            "conflict_group_id",
            sa.BigInteger(),
            nullable=True,
            comment="所属冲突组 ID，历史记录可空",
        ),
    )
    op.add_column(
        "t_memory_conflict",
        sa.Column(
            "validation_job_id",
            sa.BigInteger(),
            nullable=True,
            comment="发布本次成对冲突的复验任务 ID",
        ),
    )
    op.create_check_constraint(
        "ck_memory_conflict_group_job",
        "t_memory_conflict",
        "conflict_group_id IS NULL OR validation_job_id IS NOT NULL",
    )
    op.create_index(
        "idx_memory_conflict_group", "t_memory_conflict", ["conflict_group_id", "id"], unique=False
    )
    op.create_index(
        "idx_memory_conflict_validation_job",
        "t_memory_conflict",
        ["validation_job_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_memory_conflict_validation_job",
        "t_memory_conflict",
        "t_memory_validation_job",
        ["validation_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_memory_conflict_group",
        "t_memory_conflict",
        "t_memory_conflict_group",
        ["conflict_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "t_memory_element",
        sa.Column(
            "verification_reason",
            sa.String(length=40),
            nullable=True,
            comment="未通过系统核验的原因码，通过时为空",
        ),
    )
    op.add_column(
        "t_memory_element",
        sa.Column(
            "last_validation_job_id",
            sa.BigInteger(),
            nullable=True,
            comment="最近发布验证状态的复验任务 ID",
        ),
    )
    op.create_index(
        "idx_memory_element_validation_job",
        "t_memory_element",
        ["last_validation_job_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_memory_element_validation_job",
        "t_memory_element",
        "t_memory_validation_job",
        ["last_validation_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # 先移除现有表到新增表的外键，历史元素与成对冲突仍可保留。
    op.drop_constraint("fk_memory_element_validation_job", "t_memory_element", type_="foreignkey")
    op.drop_index("idx_memory_element_validation_job", table_name="t_memory_element")
    op.drop_column("t_memory_element", "last_validation_job_id")
    op.drop_column("t_memory_element", "verification_reason")
    op.drop_constraint("ck_memory_conflict_group_job", "t_memory_conflict", type_="check")
    op.drop_constraint("fk_memory_conflict_group", "t_memory_conflict", type_="foreignkey")
    op.drop_constraint("fk_memory_conflict_validation_job", "t_memory_conflict", type_="foreignkey")
    op.drop_index("idx_memory_conflict_validation_job", table_name="t_memory_conflict")
    op.drop_index("idx_memory_conflict_group", table_name="t_memory_conflict")
    op.drop_column("t_memory_conflict", "validation_job_id")
    op.drop_column("t_memory_conflict", "conflict_group_id")
    op.drop_constraint(
        "fk_memory_group_resolution_answer", "t_memory_conflict_group", type_="foreignkey"
    )
    op.drop_table("t_memory_conflict_evidence")
    op.drop_table("t_memory_review_answer")
    op.drop_table("t_memory_review_question")
    op.drop_table("t_memory_validation_job")
    op.drop_table("t_memory_conflict_group")
