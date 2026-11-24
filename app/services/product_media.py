"""[新增] 产品目录和体验图片的私有存储、校验及访问链接。"""

import hashlib
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.core.config import get_settings
from app.models import MediaAsset, ProductExperiencePreview, User
from app.schemas.product_experience import MAX_UPLOAD_BYTES
from app.services.benefit_common import (
    BenefitError,
    active_row,
    conflict,
    missing,
    new_row,
    now_utc,
    stamp,
    utc,
)

PRODUCT_IMAGE = "product_reference"
PERSON_IMAGE = "experience_source"
GENERATED_IMAGE = "experience_generated"
CONFIRMED = ("uploaded", "attached")
RUNNING = ("queued", "processing")
OUTPUT_TYPE = "image/jpeg"
MAX_IMAGE_PIXELS = 24_000_000


def decode_image(data: bytes, content_type: str) -> Image.Image:
    """以实际文件格式、像素上限和解码结果校验图片。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise BenefitError(422, "invalid_image", "图片超过文件大小上限")
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
            if (
                Image.MIME[image.format] != content_type
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("unsupported image format or dimensions")
            image.load()
            return ImageOps.exif_transpose(image).convert("RGB")
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        ValueError,
        SyntaxError,
    ) as exc:
        raise BenefitError(422, "invalid_image", "图片格式、尺寸或内容不符合要求") from exc


def encode_jpeg(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def read_verified_image(asset, storage) -> Image.Image:
    data = storage.read_object(asset.object_key, MAX_UPLOAD_BYTES, 30)
    if hashlib.sha256(data).hexdigest() != asset.content_sha256:
        raise conflict("image_content_changed", "图片校验失败，请重新上传")
    return decode_image(data, asset.content_type)


def image_link(asset, storage) -> dict:
    if asset.is_del or asset.upload_status not in CONFIRMED:
        raise missing()
    ttl = get_settings().object_storage_read_ttl_seconds
    return {
        "image_url": storage.create_read_url(asset.object_key, ttl),
        "url_expires_at": now_utc() + timedelta(seconds=ttl),
        "width_px": asset.width_px,
        "height_px": asset.height_px,
    }


def authorize_upload(ctx, storage, body, purpose: str, *, round_row=None) -> dict:
    ttl = get_settings().object_storage_upload_ttl_seconds
    expires = now_utc() + timedelta(seconds=ttl)
    if round_row is not None:
        expires = min(expires, utc(round_row.expires_at))
        ttl = int((expires - now_utc()).total_seconds())
        if ttl < 1:
            raise conflict("benefit_expired", "体验轮次已过期")
    key = f"product-experience/{ctx.actor_id}/{purpose}/{uuid4().hex}"
    filename = body.filename.replace("\\", "/").rsplit("/", 1)[-1]
    row = new_row(
        MediaAsset,
        ctx.actor_id,
        uploaded_by_user_id=ctx.actor_id,
        purpose=purpose,
        media_type="image",
        storage_provider=storage.provider,
        storage_region=storage.region,
        bucket_name=storage.bucket,
        object_key=key,
        original_filename=filename,
        content_type=body.content_type,
        expected_size_bytes=body.size_bytes,
        expected_sha256=body.sha256,
        upload_expires_at=expires,
        purge_after=expires + timedelta(hours=1),
    )
    ctx.db.add(row)
    ctx.db.flush()
    return {
        "asset_id" if purpose == PRODUCT_IMAGE else "photo_id": row.id,
        "upload_url": storage.create_upload_url(
            key, row.content_type, ttl, row.expected_size_bytes
        ),
        "url_expires_at": expires,
        "required_headers": {"Content-Type": row.content_type, "If-None-Match": "*"},
    }


def owned_image(ctx, asset_id: int, purpose: str, *, lock: bool = False):
    query = select(MediaAsset).where(
        MediaAsset.id == asset_id,
        MediaAsset.uploaded_by_user_id == ctx.actor_id,
        MediaAsset.purpose == purpose,
        MediaAsset.is_del.is_(False),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = ctx.db.scalar(query)
    if row is None:
        raise missing()
    return row


def _dedup_image(ctx, row, digest: str, size: int):
    return ctx.db.scalar(
        select(MediaAsset).where(
            MediaAsset.id != row.id,
            MediaAsset.purpose == row.purpose,
            MediaAsset.uploaded_by_user_id == ctx.actor_id,
            MediaAsset.is_del.is_(False),
            MediaAsset.upload_status.in_(CONFIRMED),
            MediaAsset.content_sha256 == digest,
            MediaAsset.size_bytes == size,
        )
    )


def _completion(ctx, row, storage, sequence_key):
    if row.upload_status in CONFIRMED:
        return {sequence_key: row.id, **image_link(row, storage)}
    if row.upload_status == "failed" and row.content_sha256:
        canonical = _dedup_image(ctx, row, row.content_sha256, row.size_bytes)
        if canonical is not None:
            return {sequence_key: canonical.id, **image_link(canonical, storage)}
    if row.upload_status != "pending_upload" or utc(row.upload_expires_at) <= now_utc():
        raise conflict("upload_expired", "上传授权已过期或该图片已停止确认")
    return None


def complete_upload(ctx, asset_id: int, purpose: str, storage) -> dict:
    sequence_key = "asset_id" if purpose == PRODUCT_IMAGE else "photo_id"
    with ctx.db.begin():
        row = owned_image(ctx, asset_id, purpose)
        previous = _completion(ctx, row, storage, sequence_key)
        if previous is not None:
            return previous
        key, content_type = row.object_key, row.content_type
        expected_size, expected_digest = row.expected_size_bytes, row.expected_sha256
    # 对象通过不可覆盖的签名上传，网络和图片解码在事务外执行。
    size, mime, digest, _ = storage.inspect_and_hash(key, expected_size)
    if (
        size != expected_size
        or mime != content_type
        or (expected_digest and digest != expected_digest)
    ):
        raise BenefitError(422, "image_upload_mismatch", "上传文件与授权的类型、大小或摘要不符")
    data = storage.read_object(key, expected_size, 30)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise conflict("image_content_changed", "图片在确认期间变化，请重新上传")
    width, height = decode_image(data, content_type).size
    with ctx.db.begin():
        # 同账号的内容复用串行检查，保留可重试的原授权记录。
        active_row(ctx.db, User, ctx.actor_id, lock=True)
        row = owned_image(ctx, asset_id, purpose, lock=True)
        previous = _completion(ctx, row, storage, sequence_key)
        if previous is not None:
            return previous
        canonical = _dedup_image(ctx, row, digest, size)
        stamp(row, ctx.actor_id)
        row.size_bytes, row.content_sha256 = size, digest
        row.width_px, row.height_px = width, height
        if canonical is None:
            row.upload_status, row.uploaded_at, row.purge_after = "uploaded", now_utc(), None
            canonical = row
        else:
            row.upload_status = "failed"
        return {sequence_key: canonical.id, **image_link(canonical, storage)}


def delete_photo(ctx, asset_id: int) -> None:
    with ctx.db.begin():
        row = owned_image(ctx, asset_id, PERSON_IMAGE, lock=True)
        active = ctx.db.scalar(
            select(ProductExperiencePreview.id)
            .where(
                ProductExperiencePreview.source_asset_id == row.id,
                ProductExperiencePreview.status.in_(RUNNING),
                ProductExperiencePreview.is_del.is_(False),
            )
            .limit(1)
        )
        if active is not None:
            raise conflict("photo_in_use", "图片仍有预览任务正在使用")
        row.is_del = True
        row.purge_after = max(now_utc(), utc(row.upload_expires_at))
        stamp(row, ctx.actor_id)
