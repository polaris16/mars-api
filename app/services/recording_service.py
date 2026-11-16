"""[新增] 录音会话、ASR 片段及 OSS 素材管理业务逻辑。"""

from datetime import datetime, timedelta, timezone
import hmac
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import FragmentAttachment, MediaAsset, MemoryFragment, RecordingSession, TUser, Topic
from app.schemas.recording import (
    AttachmentBatchCreate,
    AttachmentPatch,
    FragmentCreate,
    FragmentPatch,
    MediaAssetResponse,
    SessionCreate,
    SessionPatch,
    UploadAuthorizationRequest,
    UploadCompleteRequest,
)
from app.services.object_storage_service import ObjectStorageService

MAX_ATTACHMENTS_PER_FRAGMENT = 9


class RecordingService:
    """所有查询限定当前用户，写操作由 Service 显式管理事务。"""

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(status_code=404, detail="资源不存在")

    @staticmethod
    def _session(session: Session, user_id: int, session_id: int, lock: bool = False) -> RecordingSession:
        query = select(RecordingSession).where(
            RecordingSession.id == session_id,
            RecordingSession.user_id == user_id,
            RecordingSession.is_del.is_(False),
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordingService._not_found()
        return row

    @staticmethod
    def _fragment(session: Session, user_id: int, fragment_id: int, lock: bool = False) -> MemoryFragment:
        query = (
            select(MemoryFragment)
            .join(RecordingSession, RecordingSession.id == MemoryFragment.session_id)
            .where(
                MemoryFragment.id == fragment_id,
                MemoryFragment.is_del.is_(False),
                RecordingSession.user_id == user_id,
                RecordingSession.is_del.is_(False),
            )
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordingService._not_found()
        return row

    @staticmethod
    def _asset(session: Session, user_id: int, asset_id: int, lock: bool = False) -> MediaAsset:
        query = select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.user_id == user_id,
            MediaAsset.is_del.is_(False),
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordingService._not_found()
        return row

    @staticmethod
    def _validate_file_signature(media_type: str, prefix: bytes) -> None:
        """拒绝常见图片/音视频类型与实际文件头不匹配的上传内容。"""
        is_image = (
            prefix.startswith(b"\xff\xd8\xff")
            or prefix.startswith(b"\x89PNG\r\n\x1a\n")
            or prefix.startswith((b"GIF87a", b"GIF89a"))
            or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
            or (len(prefix) >= 12 and prefix[4:8] == b"ftyp" and prefix[8:12] in {b"heic", b"heix", b"mif1", b"avif"})
        )
        is_video = (len(prefix) >= 12 and prefix[4:8] == b"ftyp") or prefix.startswith(b"\x1a\x45\xdf\xa3")
        is_audio = (
            prefix.startswith((b"ID3", b"fLaC", b"OggS"))
            or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE")
            or (len(prefix) > 1 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0)
            or prefix.startswith(b"\x1a\x45\xdf\xa3")
            or (len(prefix) >= 12 and prefix[4:8] == b"ftyp")
        )
        valid = {"image": is_image, "video": is_video, "audio": is_audio}.get(media_type, False)
        if not valid:
            raise HTTPException(status_code=422, detail="文件内容与声明的媒体类型不符或格式暂不支持")

    @staticmethod
    def _asset_response(asset: MediaAsset, storage: ObjectStorageService | None = None) -> MediaAssetResponse:
        settings = get_settings()
        url = None
        expires = None
        if storage is not None and asset.upload_status in {"uploaded", "attached"}:
            url = storage.create_read_url(asset.object_key, settings.object_storage_read_ttl_seconds)
            expires = datetime.now(timezone.utc) + timedelta(seconds=settings.object_storage_read_ttl_seconds)
        return MediaAssetResponse(
            id=asset.id, purpose=asset.purpose, media_type=asset.media_type,
            filename=asset.original_filename, content_type=asset.content_type,
            size_bytes=asset.size_bytes, duration_ms=asset.duration_ms,
            sample_rate_hz=asset.sample_rate_hz, upload_status=asset.upload_status,
            created_at=asset.created_at, download_url=url, url_expires_at=expires,
        )

    @staticmethod
    def _refresh_asset_status(session: Session, asset: MediaAsset) -> None:
        fragment_ref = session.scalar(select(func.count(MemoryFragment.id)).where(
            MemoryFragment.audio_asset_id == asset.id, MemoryFragment.is_del.is_(False)
        )) or 0
        attachment_ref = session.scalar(select(func.count(FragmentAttachment.id)).where(
            FragmentAttachment.media_asset_id == asset.id, FragmentAttachment.is_del.is_(False)
        )) or 0
        asset.upload_status = "attached" if fragment_ref + attachment_ref else "uploaded"

    def create_session(self, session: Session, user: TUser, body: SessionCreate) -> RecordingSession:
        if body.topic_id is not None and session.scalar(select(Topic.id).where(
            Topic.id == body.topic_id, Topic.is_enabled.is_(True), Topic.is_del.is_(False)
        )) is None:
            raise HTTPException(status_code=422, detail="话题不存在或未启用")
        session.rollback()
        with session.begin():
            row = RecordingSession(user_id=user.id, topic_id=body.topic_id, title=body.title, created_by=user.id, updated_by=user.id)
            session.add(row)
            session.flush()
        return row

    def list_sessions(self, session: Session, user: TUser, page: int, page_size: int, sort: str, order: str, is_favorite: bool | None) -> dict:
        filters = [RecordingSession.user_id == user.id, RecordingSession.is_del.is_(False)]
        if is_favorite is not None:
            filters.append(RecordingSession.is_favorite.is_(is_favorite))
        total = session.scalar(select(func.count(RecordingSession.id)).where(*filters)) or 0
        sort_column = RecordingSession.created_at if sort == "created_at" else func.coalesce(RecordingSession.last_recorded_at, RecordingSession.created_at)
        ordering = sort_column.desc() if order == "desc" else sort_column.asc()
        rows = session.scalars(select(RecordingSession).where(*filters).order_by(ordering, RecordingSession.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        items = []
        for row in rows:
            fragment_count = session.scalar(select(func.count(MemoryFragment.id)).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))) or 0
            attachment_count = session.scalar(select(func.count(FragmentAttachment.id)).join(MemoryFragment, MemoryFragment.id == FragmentAttachment.fragment_id).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False), FragmentAttachment.is_del.is_(False))) or 0
            duration_ms = session.scalar(select(func.coalesce(func.sum(MediaAsset.duration_ms), 0)).join(MemoryFragment, MemoryFragment.audio_asset_id == MediaAsset.id).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))) or 0
            topic_title = session.scalar(select(Topic.title).where(Topic.id == row.topic_id)) if row.topic_id else None
            excerpt = session.scalar(select(func.coalesce(MemoryFragment.transcript_edited, MemoryFragment.transcript_raw)).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False)).order_by(MemoryFragment.sequence_no.desc()).limit(1))
            items.append({"id": row.id, "topic_id": row.topic_id, "topic_title": topic_title, "title": row.title, "is_favorite": row.is_favorite, "last_recorded_at": row.last_recorded_at, "created_at": row.created_at, "fragment_count": fragment_count, "attachment_count": attachment_count, "duration_ms": duration_ms, "transcript_excerpt": excerpt[:160] if excerpt else None})
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    def patch_session(self, session: Session, user: TUser, session_id: int, body: SessionPatch) -> RecordingSession:
        values = body.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(status_code=422, detail="至少提供一个要更新的字段")
        if "topic_id" in values and values["topic_id"] is not None and session.scalar(select(Topic.id).where(Topic.id == values["topic_id"], Topic.is_enabled.is_(True), Topic.is_del.is_(False))) is None:
            raise HTTPException(status_code=422, detail="话题不存在或未启用")
        session.rollback()
        with session.begin():
            row = self._session(session, user.id, session_id, lock=True)
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_by = user.id
        return row

    def delete_session(self, session: Session, user: TUser, session_id: int) -> None:
        with session.begin():
            row = self._session(session, user.id, session_id, lock=True)
            now = datetime.now(timezone.utc)
            row.is_del = True
            row.updated_by = user.id
            row.updated_at = now
            fragments = session.scalars(select(MemoryFragment).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))).all()
            fragment_ids = [fragment.id for fragment in fragments]
            affected_asset_ids = {fragment.audio_asset_id for fragment in fragments}
            for fragment in fragments:
                fragment.is_del = True
                fragment.updated_by = user.id
                fragment.updated_at = now
            if fragment_ids:
                attachments = session.scalars(select(FragmentAttachment).where(FragmentAttachment.fragment_id.in_(fragment_ids), FragmentAttachment.is_del.is_(False))).all()
                for attachment in attachments:
                    attachment.is_del = True
                    attachment.updated_by = user.id
                    attachment.updated_at = now
                    affected_asset_ids.add(attachment.media_asset_id)
            for asset_id in affected_asset_ids:
                asset = session.get(MediaAsset, asset_id)
                if asset:
                    self._refresh_asset_status(session, asset)

    def create_upload_authorization(self, session: Session, user: TUser, body: UploadAuthorizationRequest, storage: ObjectStorageService) -> dict:
        settings = get_settings()
        # 先缓存标量主键；后续预检回滚会使 ORM 对象过期，不能再通过 user.id 触发隐式查询。
        user_id = user.id
        if body.purpose == "recording":
            if body.media_type != "audio" or body.session_id is None or body.fragment_id is not None:
                raise HTTPException(status_code=422, detail="录音上传需要 audio 类型和 session_id")
            self._session(session, user_id, body.session_id)
        else:
            if body.media_type not in {"image", "video", "audio"} or body.fragment_id is None or body.session_id is not None:
                raise HTTPException(status_code=422, detail="附件上传需要 fragment_id")
            self._fragment(session, user_id, body.fragment_id)
        limits = {"audio": settings.object_storage_max_audio_bytes, "image": settings.object_storage_max_image_bytes, "video": settings.object_storage_max_video_bytes}
        if body.size_bytes > limits[body.media_type]:
            raise HTTPException(status_code=413, detail="文件超过允许大小")
        if body.media_type == "image" and not body.content_type.startswith("image/") or body.media_type == "video" and not body.content_type.startswith("video/") or body.media_type == "audio" and not body.content_type.startswith("audio/"):
            raise HTTPException(status_code=422, detail="文件类型与 Content-Type 不匹配")
        if body.sha256:
            existing = session.scalar(select(MediaAsset).where(MediaAsset.user_id == user_id, MediaAsset.purpose == body.purpose, MediaAsset.media_type == body.media_type, MediaAsset.content_sha256 == body.sha256.lower(), MediaAsset.size_bytes == body.size_bytes, MediaAsset.upload_status.in_(["uploaded", "attached"]), MediaAsset.is_del.is_(False)))
            if existing:
                return {"reused": True, "asset_id": existing.id}
        session.rollback()
        now = datetime.now(timezone.utc)
        key = f"users/{user_id}/{body.purpose}/{uuid.uuid4().hex}"
        expires_at = now + timedelta(seconds=settings.object_storage_upload_ttl_seconds)
        with session.begin():
            asset = MediaAsset(user_id=user_id, purpose=body.purpose, media_type=body.media_type, storage_provider=storage.provider, storage_region=storage.region, bucket_name=storage.bucket, object_key=key, original_filename=body.filename, content_type=body.content_type, expected_size_bytes=body.size_bytes, expected_sha256=body.sha256.lower() if body.sha256 else None, upload_status="pending_upload", upload_expires_at=expires_at, created_by=user_id, updated_by=user_id)
            session.add(asset)
            session.flush()
        url = storage.create_upload_url(key, body.content_type, settings.object_storage_upload_ttl_seconds, body.size_bytes)
        return {"reused": False, "asset_id": asset.id, "upload_method": "PUT", "upload_url": url, "required_headers": {"Content-Type": body.content_type, "If-None-Match": "*"}, "expires_at": expires_at}

    def complete_upload(self, session: Session, user: TUser, asset_id: int, body: UploadCompleteRequest, storage: ObjectStorageService) -> MediaAsset:
        asset = self._asset(session, user.id, asset_id)
        if asset.upload_status in {"uploaded", "attached"}:
            return asset
        if asset.upload_status != "pending_upload" or asset.upload_expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="上传授权已失效或素材状态不可确认")
        size, content_type, digest, prefix = storage.inspect_and_hash(asset.object_key, asset.expected_size_bytes)
        actual_type = content_type.split(";")[0].strip().lower()
        declared_type = asset.content_type.split(";")[0].strip().lower()
        if size != asset.expected_size_bytes or actual_type != declared_type:
            raise HTTPException(status_code=422, detail="已上传文件的大小或类型与声明不符")
        self._validate_file_signature(asset.media_type, prefix)
        if asset.expected_sha256 and not hmac.compare_digest(digest, asset.expected_sha256.lower()):
            raise HTTPException(status_code=422, detail="已上传文件 SHA-256 与声明不符")
        session.rollback()
        try:
            with session.begin():
                asset = self._asset(session, user.id, asset_id)
                asset.size_bytes = size
                asset.content_sha256 = digest
                asset.duration_ms = body.duration_ms
                asset.sample_rate_hz = body.sample_rate_hz
                asset.upload_status = "uploaded"
                asset.uploaded_at = datetime.now(timezone.utc)
                asset.updated_by = user.id
        except IntegrityError:
            # 并发上传同一文件时，部分唯一索引可能由另一请求先成功占用。
            session.rollback()
            existing = session.scalar(select(MediaAsset).where(
                MediaAsset.user_id == user.id,
                MediaAsset.purpose == asset.purpose,
                MediaAsset.media_type == asset.media_type,
                MediaAsset.content_sha256 == digest,
                MediaAsset.size_bytes == size,
                MediaAsset.upload_status.in_(["uploaded", "attached"]),
                MediaAsset.is_del.is_(False),
            ))
            if existing is None:
                raise
            session.rollback()
            with session.begin():
                pending = self._asset(session, user.id, asset_id)
                pending.upload_status = "failed"
                pending.purge_after = datetime.now(timezone.utc) + timedelta(days=30)
                pending.updated_by = user.id
            return existing
        return asset

    def create_fragment(self, session: Session, user: TUser, session_id: int, body: FragmentCreate) -> MemoryFragment:
        self._session(session, user.id, session_id)
        asset = self._asset(session, user.id, body.audio_asset_id)
        if asset.purpose != "recording" or asset.media_type != "audio" or asset.upload_status not in {"uploaded", "attached"}:
            raise HTTPException(status_code=422, detail="音频素材未上传完成或用途不正确")
        session.rollback()
        with session.begin():
            row = self._session(session, user.id, session_id, lock=True)
            asset = self._asset(session, user.id, body.audio_asset_id, lock=True)
            if session.scalar(select(MemoryFragment.id).where(MemoryFragment.audio_asset_id == asset.id)):
                raise HTTPException(status_code=409, detail="该音频素材已关联语音片段")
            # 序号在含软删除片段的所有历史记录中单调递增，避免撞上唯一约束。
            last_no = session.scalar(select(func.max(MemoryFragment.sequence_no)).where(MemoryFragment.session_id == row.id)) or 0
            fragment = MemoryFragment(session_id=row.id, sequence_no=last_no + 1, audio_asset_id=asset.id, transcript_raw=body.transcript_raw, recorded_at=body.recorded_at or datetime.now(timezone.utc), created_by=user.id, updated_by=user.id)
            session.add(fragment)
            row.last_recorded_at = fragment.recorded_at
            row.updated_by = user.id
            asset.upload_status = "attached"
            asset.updated_by = user.id
            session.flush()
        return fragment

    def list_fragments(self, session: Session, user: TUser, session_id: int, page: int, page_size: int, storage: ObjectStorageService | None = None) -> dict:
        self._session(session, user.id, session_id)
        total = session.scalar(select(func.count(MemoryFragment.id)).where(MemoryFragment.session_id == session_id, MemoryFragment.is_del.is_(False))) or 0
        rows = session.scalars(select(MemoryFragment).where(MemoryFragment.session_id == session_id, MemoryFragment.is_del.is_(False)).order_by(MemoryFragment.sequence_no).offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [self._fragment_response(session, row, storage) for row in rows], "page": page, "page_size": page_size, "total": total}

    def _fragment_response(self, session: Session, fragment: MemoryFragment, storage: ObjectStorageService | None = None) -> dict:
        audio = session.get(MediaAsset, fragment.audio_asset_id)
        attachments = session.scalars(select(FragmentAttachment).where(FragmentAttachment.fragment_id == fragment.id, FragmentAttachment.is_del.is_(False)).order_by(FragmentAttachment.sort_order, FragmentAttachment.id)).all()
        return {"id": fragment.id, "session_id": fragment.session_id, "sequence_no": fragment.sequence_no, "transcript_raw": fragment.transcript_raw, "transcript_edited": fragment.transcript_edited, "recorded_at": fragment.recorded_at, "audio": self._asset_response(audio, storage), "attachments": [self._attachment_response(session, item, storage) for item in attachments]}

    def get_fragment(self, session: Session, user: TUser, session_id: int, fragment_id: int, storage: ObjectStorageService) -> dict:
        self._session(session, user.id, session_id)
        fragment = self._fragment(session, user.id, fragment_id)
        if fragment.session_id != session_id:
            raise self._not_found()
        return self._fragment_response(session, fragment, storage)

    def patch_fragment(self, session: Session, user: TUser, session_id: int, fragment_id: int, body: FragmentPatch) -> MemoryFragment:
        with session.begin():
            self._session(session, user.id, session_id)
            fragment = self._fragment(session, user.id, fragment_id, lock=True)
            if fragment.session_id != session_id:
                raise self._not_found()
            fragment.transcript_edited = body.transcript_edited
            fragment.updated_by = user.id
        return fragment

    def delete_fragment(self, session: Session, user: TUser, session_id: int, fragment_id: int) -> None:
        with session.begin():
            self._session(session, user.id, session_id, lock=True)
            fragment = self._fragment(session, user.id, fragment_id, lock=True)
            if fragment.session_id != session_id:
                raise self._not_found()
            now = datetime.now(timezone.utc)
            fragment.is_del = True
            fragment.updated_by = user.id
            fragment.updated_at = now
            attachments = session.scalars(select(FragmentAttachment).where(FragmentAttachment.fragment_id == fragment.id, FragmentAttachment.is_del.is_(False))).all()
            assets = [session.get(MediaAsset, attachment.media_asset_id) for attachment in attachments]
            for attachment in attachments:
                attachment.is_del = True
                attachment.updated_by = user.id
                attachment.updated_at = now
            audio = session.get(MediaAsset, fragment.audio_asset_id)
            for asset in [audio, *assets]:
                if asset:
                    self._refresh_asset_status(session, asset)

    def create_attachments(self, session: Session, user: TUser, fragment_id: int, body: AttachmentBatchCreate, storage: ObjectStorageService | None = None) -> list[dict]:
        with session.begin():
            fragment = self._fragment(session, user.id, fragment_id, lock=True)
            current = session.scalar(select(func.count(FragmentAttachment.id)).where(FragmentAttachment.fragment_id == fragment.id, FragmentAttachment.is_del.is_(False))) or 0
            if current + len(body.items) > MAX_ATTACHMENTS_PER_FRAGMENT:
                raise HTTPException(status_code=422, detail="每段语音最多关联 9 个附件")
            ids = [item.asset_id for item in body.items]
            if len(ids) != len(set(ids)):
                raise HTTPException(status_code=422, detail="同一请求中不能重复添加素材")
            assets = session.scalars(
                select(MediaAsset)
                .where(MediaAsset.id.in_(sorted(ids)), MediaAsset.user_id == user.id, MediaAsset.is_del.is_(False))
                .order_by(MediaAsset.id)
                .with_for_update()
            ).all()
            assets_by_id = {asset.id: asset for asset in assets}
            if len(assets_by_id) != len(ids):
                raise self._not_found()
            result = []
            for item in body.items:
                asset = assets_by_id[item.asset_id]
                if asset.purpose != "attachment" or asset.upload_status not in {"uploaded", "attached"}:
                    raise HTTPException(status_code=422, detail="附件素材未上传完成或用途不正确")
                if session.scalar(select(FragmentAttachment.id).where(FragmentAttachment.fragment_id == fragment.id, FragmentAttachment.media_asset_id == asset.id, FragmentAttachment.is_del.is_(False))):
                    raise HTTPException(status_code=409, detail="该素材已关联到此语音片段")
                link = FragmentAttachment(fragment_id=fragment.id, media_asset_id=asset.id, caption=item.caption, sort_order=item.sort_order, created_by=user.id, updated_by=user.id)
                session.add(link)
                asset.upload_status = "attached"
                asset.updated_by = user.id
                result.append(link)
            session.flush()
            return [self._attachment_response(session, link, storage) for link in result]

    def _attachment_response(self, session: Session, link: FragmentAttachment, storage: ObjectStorageService | None = None) -> dict:
        asset = session.get(MediaAsset, link.media_asset_id)
        return {"id": link.id, "fragment_id": link.fragment_id, "asset": self._asset_response(asset, storage), "caption": link.caption, "sort_order": link.sort_order, "created_at": link.created_at}

    def list_attachments(self, session: Session, user: TUser, fragment_id: int, storage: ObjectStorageService) -> list[dict]:
        self._fragment(session, user.id, fragment_id)
        rows = session.scalars(select(FragmentAttachment).where(FragmentAttachment.fragment_id == fragment_id, FragmentAttachment.is_del.is_(False)).order_by(FragmentAttachment.sort_order, FragmentAttachment.id)).all()
        return [self._attachment_response(session, row, storage) for row in rows]

    def patch_attachment(self, session: Session, user: TUser, fragment_id: int, attachment_id: int, body: AttachmentPatch, storage: ObjectStorageService | None = None) -> dict:
        values = body.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(status_code=422, detail="至少提供一个要更新的字段")
        with session.begin():
            self._fragment(session, user.id, fragment_id, lock=True)
            link = session.scalar(select(FragmentAttachment).where(FragmentAttachment.id == attachment_id, FragmentAttachment.fragment_id == fragment_id, FragmentAttachment.is_del.is_(False)))
            if link is None:
                raise self._not_found()
            for key, value in values.items():
                setattr(link, key, value)
            link.updated_by = user.id
        return self._attachment_response(session, link, storage)

    def delete_attachment(self, session: Session, user: TUser, fragment_id: int, attachment_id: int) -> None:
        with session.begin():
            self._fragment(session, user.id, fragment_id, lock=True)
            link = session.scalar(select(FragmentAttachment).where(FragmentAttachment.id == attachment_id, FragmentAttachment.fragment_id == fragment_id, FragmentAttachment.is_del.is_(False)))
            if link is None:
                raise self._not_found()
            now = datetime.now(timezone.utc)
            link.is_del = True
            link.updated_by = user.id
            link.updated_at = now
            asset = self._asset(session, user.id, link.media_asset_id, lock=True)
            self._refresh_asset_status(session, asset)

    def list_assets(self, session: Session, user: TUser, page: int, page_size: int, purpose: str | None, media_type: str | None, storage: ObjectStorageService) -> dict:
        filters = [MediaAsset.user_id == user.id, MediaAsset.is_del.is_(False), MediaAsset.upload_status.in_(["uploaded", "attached"])]
        if purpose:
            filters.append(MediaAsset.purpose == purpose)
        if media_type:
            filters.append(MediaAsset.media_type == media_type)
        total = session.scalar(select(func.count(MediaAsset.id)).where(*filters)) or 0
        rows = session.scalars(select(MediaAsset).where(*filters).order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [self._asset_response(row, storage) for row in rows], "page": page, "page_size": page_size, "total": total}

    def get_asset(self, session: Session, user: TUser, asset_id: int, storage: ObjectStorageService) -> MediaAssetResponse:
        return self._asset_response(self._asset(session, user.id, asset_id), storage)

    def delete_asset(self, session: Session, user: TUser, asset_id: int) -> None:
        with session.begin():
            asset = self._asset(session, user.id, asset_id, lock=True)
            clip_refs = session.scalar(select(func.count(MemoryFragment.id)).where(MemoryFragment.audio_asset_id == asset.id, MemoryFragment.is_del.is_(False))) or 0
            attachment_refs = session.scalar(select(func.count(FragmentAttachment.id)).where(FragmentAttachment.media_asset_id == asset.id, FragmentAttachment.is_del.is_(False))) or 0
            if clip_refs + attachment_refs:
                raise HTTPException(status_code=409, detail="素材仍被语音片段引用，请先移除关联")
            asset.is_del = True
            asset.purge_after = datetime.now(timezone.utc) + timedelta(days=30)
            asset.updated_by = user.id

    def get_session_detail(self, session: Session, user: TUser, session_id: int, storage: ObjectStorageService) -> dict:
        row = self._session(session, user.id, session_id)
        fragments = session.scalars(select(MemoryFragment).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False)).order_by(MemoryFragment.sequence_no)).all()
        attachment_count = session.scalar(select(func.count(FragmentAttachment.id)).join(MemoryFragment, MemoryFragment.id == FragmentAttachment.fragment_id).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False), FragmentAttachment.is_del.is_(False))) or 0
        duration_ms = session.scalar(select(func.coalesce(func.sum(MediaAsset.duration_ms), 0)).join(MemoryFragment, MemoryFragment.audio_asset_id == MediaAsset.id).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))) or 0
        topic_title = session.scalar(select(Topic.title).where(Topic.id == row.topic_id)) if row.topic_id else None
        excerpt = session.scalar(select(func.coalesce(MemoryFragment.transcript_edited, MemoryFragment.transcript_raw)).where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False)).order_by(MemoryFragment.sequence_no.desc()).limit(1))
        return {"id": row.id, "topic_id": row.topic_id, "topic_title": topic_title, "title": row.title, "is_favorite": row.is_favorite, "last_recorded_at": row.last_recorded_at, "created_at": row.created_at, "fragment_count": len(fragments), "attachment_count": attachment_count, "duration_ms": duration_ms, "transcript_excerpt": excerpt[:160] if excerpt else None, "fragments": [self._fragment_response(session, f, storage) for f in fragments]}
