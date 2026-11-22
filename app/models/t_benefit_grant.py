"""[新增] 同一份权益从定向发放到领取的生命周期。"""

from sqlalchemy import (
    CHAR,
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
    text,
)

from app.models.base import AuditFields, Base


class BenefitGrant(AuditFields, Base):
    __tablename__ = "t_benefit_grant"
    __table_args__ = (
        UniqueConstraint("issue_request_id", "item_no", name="uq_benefit_grant_issue_item"),
        UniqueConstraint("crypto_key_version", "code_digest", name="uq_benefit_grant_code"),
        CheckConstraint("item_no > 0", name="ck_benefit_grant_item_number"),
        CheckConstraint(
            "crypto_key_version > 0 AND octet_length(code_digest) = 32 "
            "AND octet_length(recipient_phone_digest) = 32",
            name="ck_benefit_grant_digests",
        ),
        CheckConstraint(
            "recipient_phone_last4 ~ '^[0-9]{4}$'", name="ck_benefit_grant_phone_last4"
        ),
        CheckConstraint(
            "status IN ('issued', 'claimed', 'voided', 'expired')", name="ck_benefit_grant_status"
        ),
        CheckConstraint(
            "(status = 'issued') = (delivery_ciphertext IS NOT NULL)",
            name="ck_benefit_grant_delivery",
        ),
        CheckConstraint(
            "(status = 'claimed') = (recipient_user_id IS NOT NULL) AND "
            "(status = 'claimed') = (claimed_at IS NOT NULL) AND "
            "(status = 'claimed') = (valid_from IS NOT NULL) AND "
            "(status = 'claimed') = (valid_until IS NOT NULL) AND "
            "(status <> 'claimed' OR (claimed_at < claim_expires_at AND "
            "claimed_at < valid_until AND valid_from < valid_until))",
            name="ck_benefit_grant_claim",
        ),
        CheckConstraint(
            "(status = 'voided') = (void_reason IS NOT NULL)", name="ck_benefit_grant_void_reason"
        ),
        Index(
            "idx_benefit_grant_invitation",
            "crypto_key_version",
            "recipient_phone_digest",
            "claim_expires_at",
            "id",
            postgresql_where=text("status = 'issued' AND is_del = false"),
        ),
        Index("idx_benefit_grant_recipient", "recipient_user_id", "claimed_at", "id"),
        Index("idx_benefit_grant_expiry", "status", "claim_expires_at", "id"),
        {"comment": "同一份权益的定向发放、码凭据和领取归属"},
    )

    id = Column(BigInteger, primary_key=True, comment="发放和领取共用的权益 ID")
    issue_request_id = Column(
        BigInteger,
        ForeignKey("t_benefit_issue_request.id", ondelete="RESTRICT"),
        nullable=False,
        comment="所属批量发放请求 ID",
    )
    item_no = Column(Integer, nullable=False, comment="该权益在发放请求内从 1 开始的顺序")
    crypto_key_version = Column(SmallInteger, nullable=False, comment="码与手机号摘要的密钥版本")
    code_digest = Column(LargeBinary, nullable=False, comment="权益码的带密钥摘要，32 字节")
    delivery_ciphertext = Column(LargeBinary, comment="待领取权益码的加密交付副本")
    recipient_phone_digest = Column(
        LargeBinary, nullable=False, comment="定向接收手机号的带密钥摘要，32 字节"
    )
    recipient_phone_last4 = Column(CHAR(4), nullable=False, comment="供脱敏展示的接收手机号尾号")
    status = Column(
        String(20),
        nullable=False,
        server_default=text("'issued'"),
        comment="状态：issued 待领，claimed 已领，voided 作废，expired 未领过期",
    )
    claim_expires_at = Column(DateTime(timezone=True), nullable=False, comment="领取截止时间")
    void_reason = Column(String(200), comment="未领取权益作废原因")
    recipient_user_id = Column(
        BigInteger, ForeignKey("t_user.id", ondelete="RESTRICT"), comment="成功领取的用户账号 ID"
    )
    claimed_at = Column(DateTime(timezone=True), comment="成功领取整份权益的时间")
    valid_from = Column(DateTime(timezone=True), comment="领取时冻结的服务起始时间")
    valid_until = Column(DateTime(timezone=True), comment="领取时冻结的服务截止时间")
