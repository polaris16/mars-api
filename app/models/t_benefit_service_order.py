"""[新增] 记录预约或线上服务的当前履约状态和租约。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditFields, Base


class BenefitServiceOrder(AuditFields, Base):
    __tablename__ = "t_benefit_service_order"
    __table_args__ = (
        UniqueConstraint(
            "requested_by_user_id", "idempotency_key", name="uq_benefit_order_request"
        ),
        CheckConstraint(
            "status IN ('requested', 'reserved', 'in_progress', 'completed', 'cancelled', 'failed')",
            name="ck_benefit_order_status",
        ),
        CheckConstraint("version > 0 AND attempt_count >= 0", name="ck_benefit_order_counters"),
        CheckConstraint(
            "octet_length(request_digest) = 32 AND digest_key_version > 0",
            name="ck_benefit_order_request_digest",
        ),
        CheckConstraint(
            "jsonb_typeof(request_data) = 'object'", name="ck_benefit_order_request_data"
        ),
        CheckConstraint(
            "(private_data_ciphertext IS NULL) = (private_data_key_version IS NULL) AND "
            "(private_data_key_version IS NULL OR private_data_key_version > 0)",
            name="ck_benefit_order_private_data",
        ),
        CheckConstraint(
            "(scheduled_start_at IS NULL AND scheduled_end_at IS NULL) OR "
            "(scheduled_start_at IS NOT NULL AND scheduled_end_at IS NOT NULL "
            "AND scheduled_start_at < scheduled_end_at)",
            name="ck_benefit_order_schedule",
        ),
        CheckConstraint(
            "status NOT IN ('reserved', 'in_progress', 'completed') OR reserved_at IS NOT NULL",
            name="ck_benefit_order_reserved_at",
        ),
        CheckConstraint(
            "status NOT IN ('in_progress', 'completed') OR started_at IS NOT NULL",
            name="ck_benefit_order_started_at",
        ),
        CheckConstraint(
            "(status IN ('completed', 'cancelled', 'failed')) = (finished_at IS NOT NULL) AND "
            "(status <> 'completed' OR result_ref IS NOT NULL)",
            name="ck_benefit_order_finished",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL) AND "
            "(finished_at IS NULL OR (lease_token IS NULL AND next_attempt_at IS NULL))",
            name="ck_benefit_order_lease",
        ),
        Index("idx_benefit_order_balance", "balance_id", "id"),
        Index(
            "idx_benefit_order_recipient_schedule",
            "requested_by_user_id",
            "scheduled_start_at",
            "id",
            postgresql_where=text("is_del = false AND status IN ('reserved', 'in_progress')"),
        ),
        Index(
            "idx_benefit_order_assignee_schedule",
            "assignee_user_id",
            "status",
            "scheduled_start_at",
            "id",
        ),
        Index(
            "idx_benefit_order_queue",
            "next_attempt_at",
            "id",
            postgresql_where=text(
                "next_attempt_at IS NOT NULL AND status IN ('reserved', 'in_progress') "
                "AND is_del = false"
            ),
        ),
        {"comment": "一次权益预约或线上服务申请的当前履约状态"},
    )

    id = Column(BigInteger, primary_key=True, comment="服务单 ID，也是履约请求标识")
    balance_id = Column(
        BigInteger,
        ForeignKey("t_benefit_balance.id", ondelete="RESTRICT"),
        nullable=False,
        comment="本次申请占用和核销的具体服务余额 ID",
    )
    requested_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="发起服务申请的用户账号 ID",
    )
    idempotency_key = Column(Uuid, nullable=False, comment="创建服务单请求的幂等标识")
    request_digest = Column(LargeBinary, nullable=False, comment="创建请求的带密钥摘要，32 字节")
    status = Column(
        String(20),
        nullable=False,
        comment="状态：requested 待预约，reserved 已占用，in_progress 履约中，completed 完成，"
        "cancelled 取消，failed 失败",
    )
    version = Column(
        Integer, nullable=False, server_default=text("1"), comment="服务单业务状态版本"
    )
    memory_session_id = Column(
        BigInteger,
        ForeignKey("t_memory_session.id", ondelete="RESTRICT"),
        comment="用户指定的记忆会话 ID，按会话权限另行访问",
    )
    request_data = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="期望时段、选项及业务对象引用等非敏感请求数据",
    )
    private_data_ciphertext = Column(LargeBinary, comment="地址和联系电话等履约资料密文")
    private_data_key_version = Column(SmallInteger, comment="履约资料加密密钥版本")
    digest_key_version = Column(SmallInteger, nullable=False, comment="创建请求摘要密钥版本")
    assignee_user_id = Column(
        BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT"), comment="获派的服务人员账号 ID"
    )
    scheduled_start_at = Column(DateTime(timezone=True), comment="正式预约开始时间")
    scheduled_end_at = Column(DateTime(timezone=True), comment="正式预约结束时间")
    reserved_at = Column(DateTime(timezone=True), comment="首次占用服务次数的时间")
    started_at = Column(DateTime(timezone=True), comment="开始履约的时间")
    finished_at = Column(DateTime(timezone=True), comment="进入完成、取消或失败终态的时间")
    result_ref = Column(String(200), comment="完成的服务成果或履约证据引用")
    terminal_reason = Column(String(50), comment="取消、失败或爽约的业务原因码")
    attempt_count = Column(
        Integer, nullable=False, server_default=text("0"), comment="线上处理尝试次数"
    )
    next_attempt_at = Column(DateTime(timezone=True), comment="线上任务可下次认领的时间")
    lease_token = Column(Uuid, comment="线上处理器用于提交结果的租约标识")
    lease_until = Column(DateTime(timezone=True), comment="当前线上处理租约到期时间")
