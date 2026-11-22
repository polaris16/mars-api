"""[新增] 按服务单保存追加的状态和次数流水。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
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

ORDER_STATUSES = "'requested', 'reserved', 'in_progress', 'completed', 'cancelled', 'failed'"


class BenefitOrderEvent(AuditFields, Base):
    __tablename__ = "t_benefit_order_event"
    __table_args__ = (
        UniqueConstraint("service_order_id", "action_key", name="uq_benefit_event_action"),
        UniqueConstraint(
            "service_order_id", "order_version", name="uq_benefit_event_order_version"
        ),
        CheckConstraint(
            "action IN ('create', 'confirm_booking', 'reschedule', 'start', 'complete', 'cancel', 'fail')",
            name="ck_benefit_event_action",
        ),
        CheckConstraint(
            f"(from_status IS NULL OR from_status IN ({ORDER_STATUSES})) "
            f"AND to_status IN ({ORDER_STATUSES})",
            name="ck_benefit_event_statuses",
        ),
        CheckConstraint(
            "order_version > 0 AND (order_version = 1) = (from_status IS NULL) AND "
            "(action = 'create') = (order_version = 1)",
            name="ck_benefit_event_version",
        ),
        CheckConstraint(
            "octet_length(request_digest) = 32 AND digest_key_version > 0",
            name="ck_benefit_event_request_digest",
        ),
        CheckConstraint(
            "((used_delta = 0 AND reserved_delta IN (-1, 0, 1)) OR "
            "(used_delta = 1 AND reserved_delta = -1)) AND "
            "(action = 'complete') = (used_delta = 1)",
            name="ck_benefit_event_balance_delta",
        ),
        CheckConstraint("jsonb_typeof(event_data) = 'object'", name="ck_benefit_event_data"),
        {"comment": "服务单动作、占用及核销次数变化流水"},
    )

    id = Column(BigInteger, primary_key=True, comment="服务单流水 ID")
    service_order_id = Column(
        BigInteger,
        ForeignKey("t_benefit_service_order.id", ondelete="RESTRICT"),
        nullable=False,
        comment="发生该动作的权益服务单 ID",
    )
    action_key = Column(Uuid, nullable=False, comment="同一服务单内动作的幂等标识")
    request_digest = Column(LargeBinary, nullable=False, comment="动作请求的带密钥摘要，32 字节")
    digest_key_version = Column(SmallInteger, nullable=False, comment="动作请求摘要密钥版本")
    action = Column(
        String(30), nullable=False, comment="创建、确认预约、改约、开始、核销、取消或失败"
    )
    from_status = Column(String(20), comment="动作之前的服务单状态，创建时为空")
    to_status = Column(String(20), nullable=False, comment="动作之后的服务单状态")
    order_version = Column(Integer, nullable=False, comment="动作完成后的服务单版本")
    reserved_delta = Column(
        SmallInteger, nullable=False, server_default=text("0"), comment="占用次数变更量"
    )
    used_delta = Column(
        SmallInteger, nullable=False, server_default=text("0"), comment="核销次数变更量"
    )
    event_data = Column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="预约时段、人员、原因及回执等变更记录",
    )
