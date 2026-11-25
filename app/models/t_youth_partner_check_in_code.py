"""[新增] 到场核销的一次性二维码摘要。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
)

from app.models.base import AuditFields, Base


class YouthPartnerCheckInCode(AuditFields, Base):
    __tablename__ = "t_youth_partner_check_in_code"
    __table_args__ = (
        CheckConstraint(
            "octet_length(token_digest) = 32 AND crypto_key_version > 0",
            name="ck_youth_partner_check_in_code_digest",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at < expires_at",
            name="ck_youth_partner_check_in_code_consumed",
        ),
        Index("idx_youth_partner_check_in_code_expiry", "expires_at"),
        {"comment": "到场核销的一次性二维码摘要"},
    )

    service_order_id = Column(
        BigInteger,
        ForeignKey("t_youth_partner_booking.service_order_id", ondelete="RESTRICT"),
        primary_key=True,
        comment="青年伙伴预约的服务单 ID",
    )
    token_digest = Column(
        LargeBinary, nullable=False, unique=True, comment="二维码令牌的 32 字节摘要"
    )
    crypto_key_version = Column(SmallInteger, nullable=False, comment="令牌摘要密钥版本")
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="二维码过期时间")
    consumed_at = Column(DateTime(timezone=True), nullable=True, comment="二维码消费时间")
