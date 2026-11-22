"""[新增] 批量发放请求与全历史幂等标识。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    UniqueConstraint,
    Uuid,
)

from app.models.base import AuditFields, Base


class BenefitIssueRequest(AuditFields, Base):
    __tablename__ = "t_benefit_issue_request"
    __table_args__ = (
        UniqueConstraint(
            "purchase_batch_id",
            "issued_by_user_id",
            "idempotency_key",
            name="uq_benefit_issue_request_key",
        ),
        CheckConstraint(
            "octet_length(request_digest) = 32 AND digest_key_version > 0",
            name="ck_benefit_issue_request_digest",
        ),
        Index("idx_benefit_issue_request_batch", "purchase_batch_id", "id"),
        {"comment": "企业权益批量发放请求及幂等记录"},
    )

    id = Column(BigInteger, primary_key=True, comment="批量发放请求 ID")
    purchase_batch_id = Column(
        BigInteger,
        ForeignKey("t_benefit_purchase_batch.id", ondelete="RESTRICT"),
        nullable=False,
        comment="对应权益采购批次 ID",
    )
    issued_by_user_id = Column(
        BigInteger,
        ForeignKey("t_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="实际发放权益的企业用户账号 ID",
    )
    idempotency_key = Column(Uuid, nullable=False, comment="企业工作台发放请求的幂等标识")
    request_digest = Column(LargeBinary, nullable=False, comment="发放请求的带密钥摘要，32 字节")
    digest_key_version = Column(SmallInteger, nullable=False, comment="请求摘要密钥版本")
