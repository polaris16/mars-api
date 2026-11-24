"""[新增] 正式作品、收件箱和按当前好友状态签发读取链接。"""

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import aliased

from app.models import (
    MediaAsset,
    ProductExperiencePreview,
    ProductExperienceResult,
    ProductExperienceRound,
    ProductExperienceShare,
    ProductVersion,
    User,
    UserRelationship,
)
from app.services.benefit_common import (
    conflict,
    display,
    flush_unique,
    missing,
    new_row,
    now_utc,
    page_data,
    stamp,
    active_row,
)
from app.services.product_media import image_link
from app.services.user_relationship_service import UserRelationshipService


def _friendship(sender, recipient, now):
    return select(UserRelationship.relationship_pair_id).where(
        UserRelationship.subject_user_id == sender,
        UserRelationship.related_user_id == recipient,
        UserRelationship.relationship_type == "contact",
        UserRelationship.relation_code == "contact_of",
        UserRelationship.is_del.is_(False),
        UserRelationshipService._current_contact(now),
    )


def _available_share(now):
    share = ProductExperienceShare
    sender = aliased(User)
    recipient = aliased(User)
    return (
        _friendship(share.sender_user_id, share.recipient_user_id, now)
        .where(UserRelationship.relationship_pair_id == share.relationship_pair_id)
        .exists()
        & select(sender.id)
        .where(sender.id == share.sender_user_id, sender.is_del.is_(False))
        .exists()
        & select(recipient.id)
        .where(recipient.id == share.recipient_user_id, recipient.is_del.is_(False))
        .exists()
    )


def _order(model, sort):
    return (
        model.created_at.asc() if sort == "created_at_asc" else model.created_at.desc(),
        model.id.desc(),
    )


def _page(db, query, page, size, order, render):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.execute(query.order_by(*order).offset((page - 1) * size).limit(size))
    return page_data([render(*row) for row in rows], page, size, total)


class ProductResultService:
    @staticmethod
    def _owned_result(ctx, result_id, *, lock=False):
        result = ProductExperienceResult
        query = (
            select(result)
            .join(ProductExperienceRound, ProductExperienceRound.id == result.round_id)
            .where(
                result.id == result_id,
                result.is_del.is_(False),
                ProductExperienceRound.owner_user_id == ctx.actor_id,
            )
        )
        if lock:
            query = query.with_for_update(of=result).execution_options(populate_existing=True)
        row = ctx.db.scalar(query)
        if row is None:
            raise missing()
        return row

    @staticmethod
    def _result_query(user_id, *, with_output=False):
        result = ProductExperienceResult
        preview = ProductExperiencePreview
        thumb = aliased(MediaAsset)
        query = (
            select(result, ProductVersion, thumb)
            .join(ProductExperienceRound, ProductExperienceRound.id == result.round_id)
            .join(preview, preview.id == result.preview_id)
            .join(ProductVersion, ProductVersion.id == preview.product_version_id)
            .join(thumb, thumb.id == preview.preview_asset_id)
            .where(ProductExperienceRound.owner_user_id == user_id, result.is_del.is_(False))
        )
        if with_output:
            query = query.join(MediaAsset, MediaAsset.id == result.image_asset_id).add_columns(
                preview, MediaAsset
            )
        return query

    @staticmethod
    def _result_card(result, version, thumb, storage):
        return {
            "result_id": result.id,
            "product_id": version.product_id,
            "product_name": version.name,
            "created_at": result.created_at,
            "thumbnail": image_link(thumb, storage),
        }

    def results(self, ctx, storage, page, size, sort):
        with ctx.db.begin():
            return _page(
                ctx.db,
                self._result_query(ctx.actor_id),
                page,
                size,
                _order(ProductExperienceResult, sort),
                lambda result, version, thumb: self._result_card(result, version, thumb, storage),
            )

    def detail(self, ctx, storage, result_id):
        with ctx.db.begin():
            row = ctx.db.execute(
                self._result_query(ctx.actor_id, with_output=True).where(
                    ProductExperienceResult.id == result_id
                )
            ).one_or_none()
            if row is None:
                raise missing()
            result, version, thumb, preview, output = row
            return {
                **self._result_card(result, version, thumb, storage),
                "product_version_id": version.id,
                "mode": preview.mode,
                "image": image_link(output, storage),
            }

    def delete_result(self, ctx, result_id):
        with ctx.db.begin():
            row = self._owned_result(ctx, result_id, lock=True)
            now = now_utc()
            row.is_del = True
            stamp(row, ctx.actor_id)
            ctx.db.execute(
                update(ProductExperienceShare)
                .where(
                    ProductExperienceShare.result_id == row.id,
                    ProductExperienceShare.status == "active",
                )
                .values(status="revoked", revoked_at=now, updated_by=ctx.actor_id, updated_at=now)
            )
            preview = ctx.db.get(ProductExperiencePreview, row.preview_id)
            for asset_id in sorted({row.image_asset_id, preview.preview_asset_id}):
                asset = active_row(ctx.db, MediaAsset, asset_id, lock=True)
                asset.purge_after = now + timedelta(hours=1)

    @staticmethod
    def _share_view(row, available=True):
        status = row.status if row.status == "revoked" or available else "unavailable"
        return {
            "share_id": row.id,
            **display(row, "result_id sender_user_id recipient_user_id created_at"),
            "status": status,
        }

    def share(self, ctx, result_id, idem, recipient_id):
        with ctx.db.begin():
            active_row(ctx.db, User, ctx.actor_id, lock=True)
            previous = ctx.db.scalar(
                select(ProductExperienceShare).where(
                    ProductExperienceShare.sender_user_id == ctx.actor_id,
                    ProductExperienceShare.idempotency_key == idem,
                )
            )
            if previous is not None:
                if (previous.result_id, previous.recipient_user_id) != (result_id, recipient_id):
                    raise conflict("idempotency_conflict", "该幂等键已用于其他分享")
                pair = ctx.db.scalar(
                    _friendship(ctx.actor_id, recipient_id, now_utc())
                    .where(UserRelationship.relationship_pair_id == previous.relationship_pair_id)
                    .limit(1)
                )
                recipient = ctx.db.get(User, recipient_id)
                return self._share_view(
                    previous, pair is not None and recipient is not None and not recipient.is_del
                ), False
            self._owned_result(ctx, result_id, lock=True)
            active_row(ctx.db, User, recipient_id)
            # 绑定本次好友组，重新加好友后不会恢复以前的分享授权。
            pair = ctx.db.scalar(
                _friendship(ctx.actor_id, recipient_id, now_utc())
                .order_by(UserRelationship.id.desc())
                .limit(1)
            )
            if pair is None:
                raise missing()
            existing = ctx.db.scalar(
                select(ProductExperienceShare.id).where(
                    ProductExperienceShare.result_id == result_id,
                    ProductExperienceShare.recipient_user_id == recipient_id,
                    ProductExperienceShare.relationship_pair_id == pair,
                    ProductExperienceShare.status == "active",
                    ProductExperienceShare.is_del.is_(False),
                )
            )
            if existing is not None:
                raise conflict("share_exists", "该作品已向这位好友分享")
            row = new_row(
                ProductExperienceShare,
                ctx.actor_id,
                result_id=result_id,
                sender_user_id=ctx.actor_id,
                recipient_user_id=recipient_id,
                relationship_pair_id=pair,
                idempotency_key=idem,
            )
            ctx.db.add(row)
            flush_unique(ctx.db)
            return self._share_view(row), True

    def shares(self, ctx, direction, page, size, sort):
        with ctx.db.begin():
            share = ProductExperienceShare
            result = ProductExperienceResult
            authorized = _available_share(now_utc()) & result.is_del.is_(False)
            query = (
                select(share, authorized.label("authorized"))
                .join(result, result.id == share.result_id)
                .where(share.is_del.is_(False))
            )
            if direction == "incoming":
                query = query.where(
                    share.recipient_user_id == ctx.actor_id, share.status == "active", authorized
                )
            else:
                query = query.where(share.sender_user_id == ctx.actor_id)
            return _page(ctx.db, query, page, size, _order(share, sort), self._share_view)

    def share_detail(self, ctx, storage, share_id):
        with ctx.db.begin():
            share = ProductExperienceShare
            result = ProductExperienceResult
            authorized = _available_share(now_utc()) & (share.status == "active")
            row = ctx.db.execute(
                select(share, MediaAsset, authorized.label("authorized"))
                .join(result, result.id == share.result_id)
                .join(MediaAsset, MediaAsset.id == result.image_asset_id)
                .where(
                    share.id == share_id,
                    share.is_del.is_(False),
                    result.is_del.is_(False),
                    (share.sender_user_id == ctx.actor_id)
                    | ((share.recipient_user_id == ctx.actor_id) & authorized),
                )
            ).one_or_none()
            if row is None:
                raise missing()
            record, asset, available = row
            return {**self._share_view(record, available), "image": image_link(asset, storage)}

    def revoke(self, ctx, share_id):
        with ctx.db.begin():
            row = active_row(ctx.db, ProductExperienceShare, share_id, lock=True)
            if row.sender_user_id != ctx.actor_id:
                raise missing()
            if row.status == "active":
                row.status, row.revoked_at = "revoked", now_utc()
                stamp(row, ctx.actor_id)
