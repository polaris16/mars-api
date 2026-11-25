"""[新增] 清理过期权益码和仍未确认的预约申请。"""

import argparse
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.models import (
    BenefitBalance,
    BenefitGrant,
    BenefitIssueRequest,
    BenefitPurchaseBatch,
    BenefitServiceOrder,
)
from app.services.benefit_common import BenefitContext, BenefitKeys, now_utc, utc
from app.services.benefit_orders import BenefitOrderService
from app.services.youth_partner_bookings import expire_bookings


def expire_benefit_records(db, limit: int = 100) -> tuple[int, int]:
    """每次处理有限记录。领取、发放和预约仍按各自的锁顺序串行。"""
    now = now_utc()
    with db.begin():
        grants = db.execute(
            select(BenefitGrant.id, BenefitIssueRequest.purchase_batch_id)
            .join(BenefitIssueRequest, BenefitIssueRequest.id == BenefitGrant.issue_request_id)
            .where(BenefitGrant.status == "issued", BenefitGrant.claim_expires_at <= now)
            .order_by(BenefitGrant.claim_expires_at, BenefitGrant.id)
            .limit(limit)
        ).all()
        orders = db.execute(
            select(BenefitServiceOrder.id, BenefitServiceOrder.balance_id)
            .join(BenefitBalance, BenefitBalance.id == BenefitServiceOrder.balance_id)
            .join(BenefitGrant, BenefitGrant.id == BenefitBalance.grant_id)
            .where(
                BenefitServiceOrder.status == "requested",
                BenefitServiceOrder.is_del.is_(False),
                BenefitGrant.valid_until <= now,
            )
            .order_by(BenefitServiceOrder.id)
            .limit(limit)
        ).all()
    expired_grants = expired_orders = 0
    for grant_id, batch_id in grants:
        with db.begin():
            db.get(BenefitPurchaseBatch, batch_id, with_for_update=True)
            row = db.get(BenefitGrant, grant_id, with_for_update=True)
            if row is not None and row.status == "issued" and utc(row.claim_expires_at) <= now:
                row.status = "expired"
                row.delivery_ciphertext = None
                row.updated_by, row.updated_at = None, now
                expired_grants += 1
    if orders:
        keys = BenefitKeys()
        service = BenefitOrderService()
        ctx = BenefitContext(db, None)
        for order_id, balance_id in orders:
            with db.begin():
                balance = db.get(BenefitBalance, balance_id, with_for_update=True)
                row = db.get(BenefitServiceOrder, order_id, with_for_update=True)
                if balance is None or row is None or row.status != "requested" or row.is_del:
                    continue
                grant = db.get(BenefitGrant, balance.grant_id)
                if grant is None or grant.valid_until is None or utc(grant.valid_until) > now:
                    continue
                row.status, row.finished_at, row.terminal_reason = (
                    "cancelled",
                    now,
                    "benefit_expired",
                )
                row.updated_by, row.updated_at = None, now
                row.version += 1
                idem = uuid5(NAMESPACE_URL, f"mars:benefit-expiry:{row.id}")
                data = {"reason": "benefit_expired"}
                service.record_event(
                    ctx,
                    row,
                    idem,
                    keys.current,
                    keys.fingerprint(keys.current, "expiry", data),
                    "cancel",
                    "requested",
                    data=data,
                )
                expired_orders += 1
    return expired_grants, expired_orders


def main() -> None:
    parser = argparse.ArgumentParser(description="关闭过期的权益发放和未确认预约")
    parser.add_argument("--limit", type=int, default=100, choices=range(1, 1001), metavar="1-1000")
    args = parser.parse_args()
    if not get_settings().benefit_enabled:
        parser.error("企业权益功能尚未启用")
    with SessionLocal() as db:
        grants, orders = expire_benefit_records(db, args.limit)
        bookings = expire_bookings(db, args.limit)
    print(
        f"expired_grants={grants} cancelled_requests={orders} cancelled_youth_bookings={bookings}"
    )


if __name__ == "__main__":
    main()
