"""[新增] 权益发放记录可用的线下服务地点。"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    text,
)

from app.models.base import AuditFields, Base


class BenefitGrantServiceLocation(AuditFields, Base):
    __tablename__ = "t_benefit_grant_service_location"
    __table_args__ = (
        CheckConstraint(
            "travel_before_minutes >= 0 AND travel_after_minutes >= 0",
            name="ck_benefit_grant_location_travel",
        ),
        CheckConstraint("crypto_key_version > 0", name="ck_benefit_grant_location_key_version"),
        Index("idx_benefit_grant_location_sort", "grant_id", "sort_order", "id"),
        {"comment": "权益发放记录可用的线下服务地点"},
    )

    id = Column(BigInteger, primary_key=True, comment="主键")
    grant_id = Column(
        BigInteger,
        ForeignKey("t_benefit_grant.id", ondelete="RESTRICT"),
        nullable=False,
        comment="权益发放记录 ID",
    )
    name = Column(String(100), nullable=False, comment="地点展示名称")
    area_code = Column(String(32), nullable=False, comment="伙伴覆盖区域编码")
    time_zone = Column(String(64), nullable=False, comment="地点的 IANA 时区名称")
    address_ciphertext = Column(LargeBinary, nullable=False, comment="详细地址及到场说明密文")
    crypto_key_version = Column(SmallInteger, nullable=False, comment="地点加密密钥版本")
    travel_before_minutes = Column(SmallInteger, nullable=False, comment="服务前通勤缓冲分钟数")
    travel_after_minutes = Column(SmallInteger, nullable=False, comment="服务后通勤缓冲分钟数")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="展示顺序")
