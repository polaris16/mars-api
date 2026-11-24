"""[新增] 领取产品预览任务、发布成图和回收临时对象。"""

import hashlib
import logging
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image, ImageDraw
from sqlalchemy import and_, or_, select

from app.core.config import get_settings
from app.models import MediaAsset, ProductExperiencePreview, ProductExperienceRound
from app.services.benefit_common import new_row, now_utc, utc
from app.services.product_experience import close_round, round_status
from app.services.product_media import (
    CONFIRMED,
    GENERATED_IMAGE,
    MAX_UPLOAD_BYTES,
    OUTPUT_TYPE,
    PERSON_IMAGE,
    PRODUCT_IMAGE,
    encode_jpeg,
    read_verified_image,
)

LOGGER = logging.getLogger(__name__)
LEASE_LENGTH = timedelta(minutes=5)
PURGE_GRACE = timedelta(hours=1)


def _preview_job(db, job_id):
    return db.scalar(
        select(ProductExperiencePreview)
        .where(
            ProductExperiencePreview.id == job_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _round(db, round_id, *, skip_locked=False):
    return db.scalar(
        select(ProductExperienceRound)
        .where(
            ProductExperienceRound.id == round_id,
        )
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )


def _failed(round_row, job, now, code):
    job.status, job.finished_at, job.error_code = "failed", now, code
    job.next_attempt_at = job.lease_token = job.lease_until = None
    job.updated_at = round_row.updated_at = now
    round_row.preview_reserved -= 1


def claim_preview(db):
    with db.begin():
        now = now_utc()
        model = ProductExperiencePreview
        due = or_(
            and_(model.status == "queued", model.next_attempt_at <= now),
            and_(model.status == "processing", model.lease_until <= now),
        )
        candidates = db.execute(
            select(model.id, model.round_id)
            .where(due, model.is_del.is_(False))
            .order_by(model.id)
            .limit(20)
        ).all()
        for job_id, round_id in candidates:
            round_row = _round(db, round_id, skip_locked=True)
            if round_row is None or round_row.is_del or round_row.status != "open":
                continue
            job = _preview_job(db, job_id)
            if job.is_del or job.status not in {"queued", "processing"}:
                continue
            deadline = job.lease_until if job.status == "processing" else job.next_attempt_at
            if deadline is None or utc(deadline) > now:
                continue
            if round_status(round_row, now) == "expired":
                close_round(db, round_row, "expired", now)
                return None, True
            if job.attempt_count >= job.max_attempts:
                _failed(round_row, job, now, "preview_retry_exhausted")
                return None, True
            job.lease_token, job.lease_until = uuid4(), now + LEASE_LENGTH
            job.status, job.next_attempt_at, job.updated_at = "processing", None, now
            job.attempt_count += 1
            job.error_code = None
            return (job.id, round_id, job.lease_token), True
    return None, False


def _locked_run(db, claim):
    job_id, round_id, token = claim
    round_row = _round(db, round_id)
    job = _preview_job(db, job_id)
    now = now_utc()
    if (
        round_row is None
        or job is None
        or round_row.is_del
        or job.is_del
        or round_status(round_row, now) != "open"
        or job.status != "processing"
        or job.lease_token != token
        or utc(job.lease_until) <= now
    ):
        return None
    return round_row, job, now


def _inputs(db, claim):
    with db.begin():
        run = _locked_run(db, claim)
        if run is None:
            return None
        _, job, _ = run
        photo = db.get(MediaAsset, job.source_asset_id)
        references = job.generation_options["reference_asset_ids"]
        reference = db.get(MediaAsset, references[0]) if references else None
        if any(
            asset is None
            or asset.is_del
            or asset.upload_status not in CONFIRMED
            or asset.purpose != purpose
            for asset, purpose in ((photo, PERSON_IMAGE), (reference, PRODUCT_IMAGE))
        ):
            raise HTTPException(422, detail="生成参考图片不可用")
        # 只携带 Worker 所需的对象元数据跨事务读取。
        db.expunge(photo)
        db.expunge(reference)
        return photo, reference, job.provider_code


def _mock_images(photo, reference, storage):
    person = read_verified_image(photo, storage)
    product = read_verified_image(reference, storage)
    person.thumbnail((1440, 1920), Image.Resampling.LANCZOS)
    product.thumbnail(
        (max(1, person.width // 4), max(1, person.height // 4)), Image.Resampling.LANCZOS
    )
    person.paste(product, (person.width - product.width, person.height - product.height))
    ImageDraw.Draw(person).text((8, 8), "MOCK", fill="white", stroke_width=2, stroke_fill="black")
    preview = person.copy()
    preview.thumbnail((540, 720), Image.Resampling.LANCZOS)
    return [(encode_jpeg(image), image.size) for image in (preview, person)]


def _stage_objects(db, storage, claim, images):
    with db.begin():
        run = _locked_run(db, claim)
        if run is None:
            return None
        round_row, job, now = run
        rows = []
        for name, (data, _) in zip(("preview", "output"), images, strict=True):
            key = f"product-experience/{round_row.owner_user_id}/jobs/{job.id}/{job.lease_token.hex}/{name}.jpg"
            row = new_row(
                MediaAsset,
                None,
                uploaded_by_user_id=round_row.owner_user_id,
                purpose=GENERATED_IMAGE,
                media_type="image",
                storage_provider=storage.provider,
                storage_region=storage.region,
                bucket_name=storage.bucket,
                object_key=key,
                content_type=OUTPUT_TYPE,
                expected_size_bytes=len(data),
                purge_after=utc(job.lease_until) + PURGE_GRACE,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            rows.append(row)
        db.flush()
        return [(row.id, row.object_key) for row in rows]


def _publish(db, claim, staged, images):
    with db.begin():
        run = _locked_run(db, claim)
        if run is None:
            return
        round_row, job, now = run
        for (asset_id, _), (data, dimensions) in zip(staged, images, strict=True):
            row = db.get(MediaAsset, asset_id, with_for_update=True)
            row.upload_status, row.uploaded_at, row.updated_at = "uploaded", now, now
            row.size_bytes, row.content_sha256 = len(data), hashlib.sha256(data).hexdigest()
            row.width_px, row.height_px = dimensions
            row.purge_after = utc(round_row.expires_at) + PURGE_GRACE
        job.preview_asset_id, job.output_asset_id = staged[0][0], staged[1][0]
        job.status, job.finished_at, job.updated_at = "succeeded", now, now
        job.lease_token = job.lease_until = None
        round_row.preview_reserved -= 1
        round_row.preview_used += 1
        round_row.updated_at = now


def _handle_failure(db, claim, retryable):
    with db.begin():
        run = _locked_run(db, claim)
        if run is None:
            return
        round_row, job, now = run
        if retryable and job.attempt_count < job.max_attempts:
            job.status, job.next_attempt_at = "queued", now + timedelta(seconds=5)
            job.lease_token = job.lease_until = None
            job.updated_at = now
        else:
            _failed(
                round_row,
                job,
                now,
                "preview_processing_failed" if retryable else "invalid_preview_input",
            )


def run_next_preview(db, storage) -> bool:
    if get_settings().product_experience_provider == "disabled":
        return False
    claim, did_work = claim_preview(db)
    if claim is None:
        return did_work
    try:
        inputs = _inputs(db, claim)
        if inputs is None:
            return True
        photo, reference, provider = inputs
        if provider != "mock":
            raise HTTPException(503, detail="处理提供方未配置")
        images = _mock_images(photo, reference, storage)
        if any(len(data) > MAX_UPLOAD_BYTES for data, _ in images):
            raise HTTPException(422, detail="生成图超出处理上限")
        staged = _stage_objects(db, storage, claim, images)
        if staged is None:
            return True
        for (_, key), (data, _) in zip(staged, images, strict=True):
            storage.write_generated_image(key, data, OUTPUT_TYPE)
        _publish(db, claim, staged, images)
    except HTTPException as exc:
        LOGGER.info("Product preview %s failed with status %s", claim[0], exc.status_code)
        _handle_failure(db, claim, retryable=exc.status_code >= 500)
    return True


def cleanup_product_experiences(db, storage, *, limit=50) -> tuple[int, int]:
    now = now_utc()
    with db.begin():
        rounds = db.scalars(
            select(ProductExperienceRound)
            .where(
                ProductExperienceRound.status == "open",
                ProductExperienceRound.expires_at <= now,
            )
            .order_by(ProductExperienceRound.expires_at, ProductExperienceRound.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rounds:
            close_round(db, row, "expired", now)
        closed = len(rounds)
        assets = db.scalars(
            select(MediaAsset)
            .where(
                MediaAsset.purpose.in_((PERSON_IMAGE, PRODUCT_IMAGE, GENERATED_IMAGE)),
                MediaAsset.purge_after <= now,
            )
            .order_by(MediaAsset.purge_after, MediaAsset.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        claimed = []
        for asset in assets:
            # 条件上传到期后才能删除，重复确认的去重记录继续持有原摘要。
            asset.is_del = asset.is_del or asset.upload_status != "failed"
            if asset.upload_status == "pending_upload":
                asset.upload_status = "expired"
            asset.purge_after = now + timedelta(minutes=5)
            claimed.append((asset.id, asset.object_key))
    deleted = 0
    for asset_id, key in claimed:
        try:
            storage.delete_object(key)
        except HTTPException:
            LOGGER.warning("Product image cleanup failed for asset %s", asset_id)
            continue
        with db.begin():
            asset = db.get(MediaAsset, asset_id, with_for_update=True)
            asset.purge_after = None
        deleted += 1
    return closed, deleted
