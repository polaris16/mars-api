"""记忆会话、素材片段及 OSS 素材管理业务逻辑。"""

import hmac
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    FragmentAttachment,
    MediaAsset,
    MemoryContributor,
    MemoryFragment,
    MemorySession,
    MemorySessionMember,
    Topic,
    User,
)
from app.schemas.memory import (
    AttachmentBatchCreate,
    AttachmentPatch,
    FragmentCreate,
    FragmentPatch,
    FragmentRawTranscriptUpdate,
    MediaAssetResponse,
    SessionCreate,
    SessionPatch,
    UploadAuthorizationRequest,
    UploadCompleteRequest,
)
from app.services.memory_contributor_service import MemoryContributorService
from app.services.memory_session_member_service import MemorySessionMemberService
from app.services.object_storage_service import ObjectStorageService
from app.services.topic_service import TopicService

MAX_ATTACHMENTS_PER_FRAGMENT = 9


class MemoryService:
    """所有查询限定当前用户，写操作由 Service 显式管理事务。"""

    @staticmethod
    def _not_found() -> HTTPException:
        return HTTPException(status_code=404, detail="资源不存在")

    def __init__(self) -> None:
        self.members = MemorySessionMemberService()
        self.contributors = MemoryContributorService()

    def _session_access(
        self,
        session: Session,
        user_id: int,
        session_id: int,
        *,
        lock: bool = False,
        owner_only: bool = False,
    ) -> tuple[MemorySession, str]:
        query = select(MemorySession).where(
            MemorySession.id == session_id,
            MemorySession.is_del.is_(False),
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None or (owner_only and row.owner_user_id != user_id):
            raise self._not_found()
        if row.owner_user_id == user_id:
            return row, "owner"
        member = session.scalar(
            select(MemorySessionMember).where(
                MemorySessionMember.session_id == session_id,
                MemorySessionMember.user_id == user_id,
                MemorySessionMember.is_del.is_(False),
            )
        )
        if member is None or not self._shared_access_is_effective(session, row, member):
            raise self._not_found()
        return row, member.role

    def _shared_access_is_effective(
        self,
        session: Session,
        memory: MemorySession,
        member: MemorySessionMember,
    ) -> bool:
        owner_active = (
            session.scalar(
                select(User.id).where(
                    User.id == memory.owner_user_id,
                    User.is_del.is_(False),
                )
            )
            is not None
        )
        return owner_active and self.members.is_member_effective(
            session,
            memory.owner_user_id,
            member,
            datetime.now(timezone.utc),
        )

    def _require_contribution(
        self,
        session: Session,
        memory: MemorySession,
        user: User,
        role: str,
    ) -> MemoryContributor | None:
        if role == "owner":
            return None
        if role not in {"contributor", "editor"}:
            raise HTTPException(status_code=403, detail="没有素材贡献权限")
        return self.contributors.require_consented_contributor(session, memory, user)

    def _require_material_edit(
        self,
        session: Session,
        memory: MemorySession,
        user: User,
        role: str,
        contributor_id: int,
    ) -> None:
        contributor = self._require_contribution(session, memory, user, role)
        if role == "contributor" and contributor.id != contributor_id:
            raise HTTPException(status_code=403, detail="只能修改本人提供的素材")

    @staticmethod
    def _owner_contributor(
        session: Session, memory_session: MemorySession, user: User
    ) -> MemoryContributor:
        """为当前账户生成/复用会话内的素材提供者身份。"""
        contributor = session.scalar(
            select(MemoryContributor).where(
                MemoryContributor.session_id == memory_session.id,
                MemoryContributor.account_user_id == user.id,
                MemoryContributor.contributor_type == "owner",
                MemoryContributor.is_del.is_(False),
            )
        )
        if contributor is None:
            contributor = MemoryContributor(
                session_id=memory_session.id,
                contributor_type="owner",
                account_user_id=user.id,
                display_name=user.display_name or user.username,
                identity_status="verified",
                consent_status="pending",
                created_by=user.id,
                updated_by=user.id,
            )
            session.add(contributor)
            session.flush()
        return contributor

    def _fragment_access(
        self,
        session: Session,
        user_id: int,
        fragment_id: int,
        *,
        lock: bool = False,
        expected_session_id: int | None = None,
    ) -> tuple[MemoryFragment, MemorySession, str]:
        filters = (MemoryFragment.id == fragment_id, MemoryFragment.is_del.is_(False))
        session_id = session.scalar(select(MemoryFragment.session_id).where(*filters))
        if session_id is None or (
            expected_session_id is not None and session_id != expected_session_id
        ):
            raise self._not_found()
        # Lock the session before the fragment, matching membership and consent changes.
        memory, role = self._session_access(session, user_id, session_id, lock=lock)
        query = select(MemoryFragment).where(
            *filters,
            MemoryFragment.session_id == session_id,
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise MemoryService._not_found()
        return row, memory, role

    @staticmethod
    def _asset(session: Session, user_id: int, asset_id: int, lock: bool = False) -> MediaAsset:
        query = select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.uploaded_by_user_id == user_id,
            MediaAsset.is_del.is_(False),
        )
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise MemoryService._not_found()
        return row

    @staticmethod
    def _validate_file_signature(media_type: str, prefix: bytes) -> None:
        """拒绝常见图片/音视频类型与实际文件头不匹配的上传内容。"""
        is_image = (
            prefix.startswith(b"\xff\xd8\xff")
            or prefix.startswith(b"\x89PNG\r\n\x1a\n")
            or prefix.startswith((b"GIF87a", b"GIF89a"))
            or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
            or (
                len(prefix) >= 12
                and prefix[4:8] == b"ftyp"
                and prefix[8:12] in {b"heic", b"heix", b"mif1", b"avif"}
            )
        )
        is_video = (len(prefix) >= 12 and prefix[4:8] == b"ftyp") or prefix.startswith(
            b"\x1a\x45\xdf\xa3"
        )
        is_audio = (
            prefix.startswith((b"ID3", b"fLaC", b"OggS"))
            or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE")
            or (len(prefix) > 1 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0)
            or prefix.startswith(b"\x1a\x45\xdf\xa3")
            or (len(prefix) >= 12 and prefix[4:8] == b"ftyp")
        )
        valid = {"image": is_image, "video": is_video, "audio": is_audio}.get(media_type, False)
        if not valid:
            raise HTTPException(
                status_code=422, detail="文件内容与声明的媒体类型不符或格式暂不支持"
            )

    @staticmethod
    def _asset_response(
        asset: MediaAsset, storage: ObjectStorageService | None = None
    ) -> MediaAssetResponse:
        settings = get_settings()
        url = None
        expires = None
        if (
            storage is not None
            and not asset.is_del
            and asset.upload_status in {"uploaded", "attached"}
        ):
            url = storage.create_read_url(
                asset.object_key, settings.object_storage_read_ttl_seconds
            )
            expires = datetime.now(timezone.utc) + timedelta(
                seconds=settings.object_storage_read_ttl_seconds
            )
        return MediaAssetResponse(
            id=asset.id,
            purpose=asset.purpose,
            media_type=asset.media_type,
            filename=asset.original_filename,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
            duration_ms=asset.duration_ms,
            sample_rate_hz=asset.sample_rate_hz,
            upload_status=asset.upload_status,
            created_at=asset.created_at,
            download_url=url,
            url_expires_at=expires,
        )

    @staticmethod
    def _refresh_asset_status(session: Session, asset: MediaAsset) -> None:
        fragment_ref = (
            session.scalar(
                select(func.count(MemoryFragment.id)).where(
                    MemoryFragment.source_asset_id == asset.id, MemoryFragment.is_del.is_(False)
                )
            )
            or 0
        )
        attachment_ref = (
            session.scalar(
                select(func.count(FragmentAttachment.id)).where(
                    FragmentAttachment.media_asset_id == asset.id,
                    FragmentAttachment.is_del.is_(False),
                )
            )
            or 0
        )
        asset.upload_status = "attached" if fragment_ref + attachment_ref else "uploaded"

    def _refresh_assets_after_unlink(self, session: Session, asset_ids: set[int]) -> None:
        if not asset_ids:
            return
        assets = session.scalars(
            select(MediaAsset)
            .where(MediaAsset.id.in_(asset_ids), MediaAsset.is_del.is_(False))
            .order_by(MediaAsset.id)
            .with_for_update()
        ).all()
        # Autoflush is disabled. Publish the soft deletes before counting remaining links.
        session.flush()
        for asset in assets:
            self._refresh_asset_status(session, asset)

    @staticmethod
    def _latest_recorded_at(session: Session, session_id: int) -> datetime | None:
        return session.scalar(
            select(func.max(MemoryFragment.recorded_at)).where(
                MemoryFragment.session_id == session_id,
                MemoryFragment.is_del.is_(False),
            )
        )

    @staticmethod
    def _latest_excerpt(session: Session, session_id: int) -> str | None:
        excerpt = session.scalar(
            select(
                func.coalesce(
                    MemoryFragment.transcript_edited,
                    MemoryFragment.transcript_raw,
                    MemoryFragment.content_text,
                )
            )
            .where(
                MemoryFragment.session_id == session_id,
                MemoryFragment.is_del.is_(False),
            )
            .order_by(MemoryFragment.recorded_at.desc(), MemoryFragment.sequence_no.desc())
            .limit(1)
        )
        return excerpt[:160] if excerpt else None

    def create_session(self, session: Session, user: User, body: SessionCreate) -> MemorySession:
        if (
            body.topic_id is not None
            and session.scalar(
                select(Topic.id).where(
                    Topic.id == body.topic_id, Topic.is_enabled.is_(True), Topic.is_del.is_(False)
                )
            )
            is None
        ):
            raise HTTPException(status_code=422, detail="话题不存在或未启用")
        session.rollback()
        with session.begin():
            row = MemorySession(
                owner_user_id=user.id,
                topic_id=body.topic_id,
                title=body.title,
                created_by=user.id,
                updated_by=user.id,
            )
            session.add(row)
            session.flush()
        return row

    def list_sessions(
        self,
        session: Session,
        user: User,
        page: int,
        page_size: int,
        sort: str,
        order: str,
        is_favorite: bool | None,
    ) -> dict:
        user_id = user.id
        filters = [
            MemorySession.is_del.is_(False),
            or_(
                MemorySession.owner_user_id == user_id,
                MemorySessionMember.id.is_not(None),
            ),
        ]
        if is_favorite is not None:
            filters.append(MemorySession.is_favorite.is_(is_favorite))
        sort_column = (
            MemorySession.created_at
            if sort == "created_at"
            else func.coalesce(MemorySession.last_material_at, MemorySession.created_at)
        )
        ordering = sort_column.desc() if order == "desc" else sort_column.asc()
        candidates = session.execute(
            select(MemorySession, MemorySessionMember)
            .outerjoin(
                MemorySessionMember,
                (
                    (MemorySessionMember.session_id == MemorySession.id)
                    & (MemorySessionMember.user_id == user_id)
                    & (MemorySessionMember.status == "active")
                    & MemorySessionMember.is_del.is_(False)
                    & or_(
                        MemorySessionMember.valid_until.is_(None),
                        MemorySessionMember.valid_until > datetime.now(timezone.utc),
                    )
                ),
            )
            .where(*filters)
            .order_by(ordering, MemorySession.id.desc()),
        )
        # Filter invalid source relationships before counting or slicing a page.
        rows = [
            row
            for row, member in candidates
            if row.owner_user_id == user_id
            or self._shared_access_is_effective(session, row, member)
        ]
        total = len(rows)
        start = (page - 1) * page_size
        items = []
        for row in rows[start : start + page_size]:
            fragment_count = (
                session.scalar(
                    select(func.count(MemoryFragment.id)).where(
                        MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False)
                    )
                )
                or 0
            )
            attachment_count = (
                session.scalar(
                    select(func.count(FragmentAttachment.id))
                    .join(MemoryFragment, MemoryFragment.id == FragmentAttachment.fragment_id)
                    .where(
                        MemoryFragment.session_id == row.id,
                        MemoryFragment.is_del.is_(False),
                        FragmentAttachment.is_del.is_(False),
                    )
                )
                or 0
            )
            duration_ms = (
                session.scalar(
                    select(func.coalesce(func.sum(MediaAsset.duration_ms), 0))
                    .join(MemoryFragment, MemoryFragment.source_asset_id == MediaAsset.id)
                    .where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))
                )
                or 0
            )
            topic_title = (
                session.scalar(select(Topic.title).where(Topic.id == row.topic_id))
                if row.topic_id
                else None
            )
            # [修改] 摘要使用录制时间，与会话的最近素材时间保持一致。
            excerpt = self._latest_excerpt(session, row.id)
            items.append(
                {
                    "id": row.id,
                    "topic_id": row.topic_id,
                    "topic_title": topic_title,
                    "title": row.title,
                    "is_favorite": row.is_favorite,
                    "last_recorded_at": row.last_material_at,
                    "created_at": row.created_at,
                    "fragment_count": fragment_count,
                    "attachment_count": attachment_count,
                    "duration_ms": duration_ms,
                    "transcript_excerpt": excerpt,
                }
            )
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    def patch_session(
        self, session: Session, user: User, session_id: int, body: SessionPatch
    ) -> MemorySession:
        values = body.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(status_code=422, detail="至少提供一个要更新的字段")
        if (
            "topic_id" in values
            and values["topic_id"] is not None
            and session.scalar(
                select(Topic.id).where(
                    Topic.id == values["topic_id"],
                    Topic.is_enabled.is_(True),
                    Topic.is_del.is_(False),
                )
            )
            is None
        ):
            raise HTTPException(status_code=422, detail="话题不存在或未启用")
        session.rollback()
        with session.begin():
            row, _ = self._session_access(session, user.id, session_id, lock=True, owner_only=True)
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_by = user.id
        return row

    def delete_session(self, session: Session, user: User, session_id: int) -> None:
        with session.begin():
            row, _ = self._session_access(session, user.id, session_id, lock=True, owner_only=True)
            now = datetime.now(timezone.utc)
            row.is_del = True
            row.updated_by = user.id
            row.updated_at = now
            fragments = session.scalars(
                select(MemoryFragment).where(
                    MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False)
                )
            ).all()
            fragment_ids = [fragment.id for fragment in fragments]
            affected_asset_ids = {
                fragment.source_asset_id
                for fragment in fragments
                if fragment.source_asset_id is not None
            }
            for fragment in fragments:
                fragment.is_del = True
                fragment.updated_by = user.id
                fragment.updated_at = now
            if fragment_ids:
                attachments = session.scalars(
                    select(FragmentAttachment).where(
                        FragmentAttachment.fragment_id.in_(fragment_ids),
                        FragmentAttachment.is_del.is_(False),
                    )
                ).all()
                for attachment in attachments:
                    attachment.is_del = True
                    attachment.updated_by = user.id
                    attachment.updated_at = now
                    affected_asset_ids.add(attachment.media_asset_id)
            self._refresh_assets_after_unlink(session, affected_asset_ids)

    def create_upload_authorization(
        self,
        session: Session,
        user: User,
        body: UploadAuthorizationRequest,
        storage: ObjectStorageService,
    ) -> dict:
        settings = get_settings()
        # 先缓存标量主键；后续预检回滚会使 ORM 对象过期，不能再通过 user.id 触发隐式查询。
        user_id = user.id
        if body.purpose == "recording":
            if (
                body.media_type != "audio"
                or body.session_id is None
                or body.fragment_id is not None
            ):
                raise HTTPException(status_code=422, detail="录音上传需要 audio 类型和 session_id")
        elif body.purpose == "attachment":
            if (
                body.media_type not in {"image", "video", "audio"}
                or body.fragment_id is None
                or body.session_id is not None
            ):
                raise HTTPException(status_code=422, detail="附件上传需要 fragment_id")
        else:
            if (
                body.media_type not in {"text", "video"}
                or body.session_id is None
                or body.fragment_id is not None
            ):
                raise HTTPException(
                    status_code=422, detail="source upload requires text/video and session_id"
                )
        limits = {
            "audio": settings.object_storage_max_audio_bytes,
            "image": settings.object_storage_max_image_bytes,
            "video": settings.object_storage_max_video_bytes,
            "text": settings.object_storage_max_text_bytes,
        }
        if body.size_bytes > limits[body.media_type]:
            raise HTTPException(status_code=413, detail="文件超过允许大小")
        if (
            body.media_type == "image"
            and not body.content_type.startswith("image/")
            or body.media_type == "video"
            and not body.content_type.startswith("video/")
            or body.media_type == "audio"
            and not body.content_type.startswith("audio/")
            or body.media_type == "text"
            and not body.content_type.startswith("text/")
        ):
            raise HTTPException(status_code=422, detail="文件类型与 Content-Type 不匹配")
        session.rollback()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.object_storage_upload_ttl_seconds)
        with session.begin():
            if body.fragment_id is not None:
                _, memory, role = self._fragment_access(
                    session, user_id, body.fragment_id, lock=True
                )
            else:
                memory, role = self._session_access(session, user_id, body.session_id, lock=True)
            self._require_contribution(session, memory, user, role)
            if body.sha256:
                existing = session.scalar(
                    select(MediaAsset).where(
                        MediaAsset.uploaded_by_user_id == user_id,
                        MediaAsset.purpose == body.purpose,
                        MediaAsset.media_type == body.media_type,
                        MediaAsset.content_sha256 == body.sha256.lower(),
                        MediaAsset.size_bytes == body.size_bytes,
                        MediaAsset.upload_status.in_(["uploaded", "attached"]),
                        MediaAsset.is_del.is_(False),
                    )
                )
                if existing is not None:
                    return {"reused": True, "asset_id": existing.id}
            # Store the grant target in the server-generated key so pending uploads can be reauthorized.
            key = f"users/{user_id}/{body.purpose}/scope/{memory.id}/{body.fragment_id or 0}/{uuid.uuid4().hex}"
            asset = MediaAsset(
                uploaded_by_user_id=user_id,
                purpose=body.purpose,
                media_type=body.media_type,
                storage_provider=storage.provider,
                storage_region=storage.region,
                bucket_name=storage.bucket,
                object_key=key,
                original_filename=body.filename,
                content_type=body.content_type,
                expected_size_bytes=body.size_bytes,
                expected_sha256=body.sha256.lower() if body.sha256 else None,
                upload_status="pending_upload",
                upload_expires_at=expires_at,
                created_by=user_id,
                updated_by=user_id,
            )
            session.add(asset)
            session.flush()
        url = storage.create_upload_url(
            key, body.content_type, settings.object_storage_upload_ttl_seconds, body.size_bytes
        )
        return {
            "reused": False,
            "asset_id": asset.id,
            "upload_method": "PUT",
            "upload_url": url,
            "required_headers": {"Content-Type": body.content_type, "If-None-Match": "*"},
            "expires_at": expires_at,
        }

    @staticmethod
    def _require_pending_upload(asset: MediaAsset) -> None:
        expires_at = asset.upload_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if asset.upload_status != "pending_upload" or expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="上传授权已失效或素材状态不可确认")

    def _check_upload_scope(self, session: Session, user: User, key: str, *, lock: bool) -> None:
        parts = key.split("/")
        if len(parts) < 4 or parts[3] != "scope":
            return
        try:
            _, _, _, _, session_id, fragment_id, _ = parts
            session_id, fragment_id = int(session_id), int(fragment_id)
        except ValueError:
            raise self._not_found() from None
        if fragment_id:
            _, memory, role = self._fragment_access(
                session,
                user.id,
                fragment_id,
                lock=lock,
                expected_session_id=session_id,
            )
        else:
            memory, role = self._session_access(session, user.id, session_id, lock=lock)
        self._require_contribution(session, memory, user, role)

    def complete_upload(
        self,
        session: Session,
        user: User,
        asset_id: int,
        body: UploadCompleteRequest,
        storage: ObjectStorageService,
    ) -> MediaAsset:
        user_id = user.id
        asset = self._asset(session, user_id, asset_id)
        if asset.upload_status in {"uploaded", "attached"}:
            return asset
        self._require_pending_upload(asset)
        purpose, media_type = asset.purpose, asset.media_type
        object_key, expected_size = asset.object_key, asset.expected_size_bytes
        self._check_upload_scope(session, user, object_key, lock=False)
        expected_sha256, declared_type = asset.expected_sha256, asset.content_type
        # Release the read transaction while the object is being downloaded and hashed.
        session.rollback()
        size, content_type, digest, prefix = storage.inspect_and_hash(
            object_key,
            expected_size,
            validate_utf8=media_type == "text",
        )
        actual_type = content_type.split(";")[0].strip().lower()
        if size != expected_size or actual_type != declared_type.split(";")[0].strip().lower():
            raise HTTPException(status_code=422, detail="已上传文件的大小或类型与声明不符")
        # Text is validated incrementally over the full stream by the storage adapter.
        if media_type != "text":
            self._validate_file_signature(media_type, prefix)
        if expected_sha256 and not hmac.compare_digest(digest, expected_sha256.lower()):
            raise HTTPException(status_code=422, detail="已上传文件 SHA-256 与声明不符")
        try:
            with session.begin():
                self._check_upload_scope(session, user, object_key, lock=True)
                # Another request may have confirmed or linked the object during the hash check.
                asset = self._asset(session, user_id, asset_id, lock=True)
                if asset.upload_status in {"uploaded", "attached"}:
                    return asset
                self._require_pending_upload(asset)
                asset.size_bytes = size
                asset.content_sha256 = digest
                asset.duration_ms = body.duration_ms
                asset.sample_rate_hz = body.sample_rate_hz
                asset.upload_status = "uploaded"
                asset.uploaded_at = datetime.now(timezone.utc)
                asset.updated_by = user_id
        except IntegrityError:
            # 并发上传同一文件时，部分唯一索引可能由另一请求先成功占用。
            with session.begin():
                self._check_upload_scope(session, user, object_key, lock=True)
                pending = self._asset(session, user_id, asset_id, lock=True)
                if pending.upload_status in {"uploaded", "attached"}:
                    return pending
                self._require_pending_upload(pending)
                existing = session.scalar(
                    select(MediaAsset).where(
                        MediaAsset.id != asset_id,
                        MediaAsset.uploaded_by_user_id == user_id,
                        MediaAsset.purpose == purpose,
                        MediaAsset.media_type == media_type,
                        MediaAsset.content_sha256 == digest,
                        MediaAsset.size_bytes == size,
                        MediaAsset.upload_status.in_(["uploaded", "attached"]),
                        MediaAsset.is_del.is_(False),
                    )
                )
                if existing is None:
                    raise
                pending.upload_status = "failed"
                pending.purge_after = datetime.now(timezone.utc) + timedelta(days=30)
                pending.updated_by = user_id
            return existing
        return asset

    def create_fragment(
        self,
        session: Session,
        user: User,
        session_id: int,
        body: FragmentCreate,
        *,
        after_flush: Callable[[MemoryFragment], None] | None = None,
        request_key: uuid.UUID | None = None,
    ) -> MemoryFragment:
        # [删除-旧] 原实现只接受 audio_asset_id，片段固定引用音频并禁止素材复用；保留旧实现便于追溯。
        # self._session(session, user.id, session_id)
        # asset = self._asset(session, user.id, body.audio_asset_id)
        # if asset.purpose != "recording" or asset.media_type != "audio" or asset.upload_status not in {"uploaded", "attached"}:
        #     raise HTTPException(status_code=422, detail="音频素材未上传完成或用途不正确")
        # session.rollback()
        # with session.begin():
        #     row = self._session(session, user.id, session_id, lock=True)
        #     asset = self._asset(session, user.id, body.audio_asset_id, lock=True)
        #     if session.scalar(select(MemoryFragment.id).where(MemoryFragment.audio_asset_id == asset.id)):
        #         raise HTTPException(status_code=409, detail="该音频素材已关联语音片段")
        #     # 序号在含软删除片段的所有历史记录中单调递增，避免撞上唯一约束。
        #     last_no = session.scalar(select(func.max(MemoryFragment.sequence_no)).where(MemoryFragment.session_id == row.id)) or 0
        #     fragment = MemoryFragment(session_id=row.id, sequence_no=last_no + 1, audio_asset_id=asset.id, transcript_raw=body.transcript_raw, recorded_at=body.recorded_at or datetime.now(timezone.utc), created_by=user.id, updated_by=user.id)
        #     session.add(fragment)
        #     row.last_material_at = fragment.recorded_at
        #     row.updated_by = user.id
        #     asset.upload_status = "attached"
        #     asset.updated_by = user.id
        #     session.flush()
        # return fragment
        # [新增] 统一处理文本、音频、视频片段，并在每条素材记录上保存提供者。
        # [COMPAT] Keep audio_asset_id accepted as a source_asset_id alias.
        source_asset_id = body.source_asset_id or body.audio_asset_id
        request_hash = None
        if request_key is not None:
            payload = body.model_dump(mode="json", exclude={"audio_asset_id"})
            payload["source_asset_id"] = source_asset_id
            request_hash = sha256(
                json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
            ).hexdigest()
        with session.begin():
            row, role = self._session_access(session, user.id, session_id, lock=True)
            contributor = self._require_contribution(session, row, user, role)
            # [新增] 持有会话锁时查找请求键，同一次保存只允许首次请求落库。
            if request_key is not None:
                existing = session.scalar(
                    select(MemoryFragment).where(
                        MemoryFragment.session_id == row.id,
                        MemoryFragment.created_by == user.id,
                        MemoryFragment.client_request_key == request_key,
                    )
                )
                if existing is not None:
                    if existing.is_del or existing.client_request_hash != request_hash:
                        raise HTTPException(
                            status_code=409, detail="该幂等键已被其他内容或已删除片段使用"
                        )
                    return existing
            asset = (
                self._asset(session, user.id, source_asset_id, lock=True)
                if source_asset_id is not None
                else None
            )
            if body.source_type == "audio" and (
                asset is None or asset.purpose != "recording" or asset.media_type != "audio"
            ):
                raise HTTPException(
                    status_code=422, detail="audio fragment requires a recording asset"
                )
            if body.source_type == "video" and (
                asset is None or asset.purpose != "source" or asset.media_type != "video"
            ):
                raise HTTPException(
                    status_code=422, detail="video fragment requires a video source asset"
                )
            if (
                body.source_type == "text"
                and asset is not None
                and (asset.purpose != "source" or asset.media_type != "text")
            ):
                raise HTTPException(
                    status_code=422, detail="text fragment asset must be a text source"
                )
            if asset is not None and asset.upload_status not in {"uploaded", "attached"}:
                raise HTTPException(status_code=422, detail="source asset upload is not complete")
            last_no = (
                session.scalar(
                    select(func.max(MemoryFragment.sequence_no)).where(
                        MemoryFragment.session_id == row.id
                    )
                )
                or 0
            )
            if contributor is None:
                contributor = self._owner_contributor(session, row, user)
            fragment = MemoryFragment(
                session_id=row.id,
                sequence_no=last_no + 1,
                contributor_id=contributor.id,
                source_type=body.source_type,
                source_asset_id=asset.id if asset else None,
                content_text=body.content_text,
                transcript_raw=body.transcript_raw,
                client_request_key=request_key,
                client_request_hash=request_hash,
                recorded_at=body.recorded_at or datetime.now(timezone.utc),
                created_by=user.id,
                updated_by=user.id,
            )
            session.add(fragment)
            # [删除-旧] row.last_material_at = fragment.recorded_at
            row.updated_by = user.id
            if asset is not None:
                asset.upload_status = "attached"
                asset.updated_by = user.id
            session.flush()
            # [修改] 补录旧片段可能晚于最近片段到达，取现存片段的最大业务时间。
            row.last_material_at = self._latest_recorded_at(session, row.id)
            if row.topic_id is not None:
                TopicService.record_participation(session, row.topic_id, user.id)
            if after_flush is not None:
                after_flush(fragment)
        return fragment

    def list_fragments(
        self,
        session: Session,
        user: User,
        session_id: int,
        page: int,
        page_size: int,
        storage: ObjectStorageService | None = None,
    ) -> dict:
        self._session_access(session, user.id, session_id)
        total = (
            session.scalar(
                select(func.count(MemoryFragment.id)).where(
                    MemoryFragment.session_id == session_id, MemoryFragment.is_del.is_(False)
                )
            )
            or 0
        )
        rows = session.scalars(
            select(MemoryFragment)
            .where(MemoryFragment.session_id == session_id, MemoryFragment.is_del.is_(False))
            .order_by(MemoryFragment.sequence_no)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [self._fragment_response(session, row, storage) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def _fragment_response(
        self,
        session: Session,
        fragment: MemoryFragment,
        storage: ObjectStorageService | None = None,
    ) -> dict:
        source_asset = (
            session.get(MediaAsset, fragment.source_asset_id)
            if fragment.source_asset_id is not None
            else None
        )
        attachments = session.scalars(
            select(FragmentAttachment)
            .where(
                FragmentAttachment.fragment_id == fragment.id, FragmentAttachment.is_del.is_(False)
            )
            .order_by(FragmentAttachment.sort_order, FragmentAttachment.id)
        ).all()
        return {
            "id": fragment.id,
            "session_id": fragment.session_id,
            "sequence_no": fragment.sequence_no,
            "contributor_id": fragment.contributor_id,
            "source_type": fragment.source_type,
            "source_asset": self._asset_response(source_asset, storage) if source_asset else None,
            "content_text": fragment.content_text,
            "transcript_raw": fragment.transcript_raw,
            "transcript_edited": fragment.transcript_edited,
            "recorded_at": fragment.recorded_at,
            # [移除] 旧 audio 响应字段与 source_asset 指向同一素材。
            # "audio": self._asset_response(source_asset, storage) if fragment.source_type == "audio" and source_asset else None,
            "attachments": [
                self._attachment_response(session, item, storage) for item in attachments
            ],
        }

    def get_fragment(
        self,
        session: Session,
        user: User,
        session_id: int,
        fragment_id: int,
        storage: ObjectStorageService,
    ) -> dict:
        fragment, _, _ = self._fragment_access(
            session, user.id, fragment_id, expected_session_id=session_id
        )
        return self._fragment_response(session, fragment, storage)

    def patch_fragment(
        self, session: Session, user: User, session_id: int, fragment_id: int, body: FragmentPatch
    ) -> MemoryFragment:
        with session.begin():
            fragment, memory, role = self._fragment_access(
                session,
                user.id,
                fragment_id,
                lock=True,
                expected_session_id=session_id,
            )
            self._require_material_edit(session, memory, user, role, fragment.contributor_id)
            fragment.transcript_edited = body.transcript_edited
            fragment.updated_by = user.id
        return fragment

    # [新增] 允许原素材提供者补交晚到的 ASR，同时保护已引用的原文。
    def save_fragment_raw_transcript(
        self,
        session: Session,
        user: User,
        session_id: int,
        fragment_id: int,
        body: FragmentRawTranscriptUpdate,
        *,
        after_flush: Callable[[MemoryFragment], None],
    ) -> MemoryFragment:
        with session.begin():
            fragment, memory, role = self._fragment_access(
                session,
                user.id,
                fragment_id,
                lock=True,
                expected_session_id=session_id,
            )
            self._require_contribution(session, memory, user, role)
            author = session.get(MemoryContributor, fragment.contributor_id)
            if (
                author is None
                or author.is_del
                or author.session_id != memory.id
                or author.account_user_id != user.id
            ):
                raise HTTPException(status_code=403, detail="只有原素材提供者可以补交原始转写")
            if fragment.source_type not in {"audio", "video"}:
                raise HTTPException(status_code=422, detail="只支持为音频或视频片段补交原始转写")
            existing = fragment.transcript_raw
            if existing and existing.strip() and existing != body.transcript_raw:
                raise HTTPException(status_code=409, detail="已有原始转写，修改请使用校订文本")
            if existing != body.transcript_raw:
                fragment.transcript_raw = body.transcript_raw
                fragment.updated_by = user.id
                fragment.updated_at = datetime.now(timezone.utc)
                session.flush()
            after_flush(fragment)
        return fragment

    def delete_fragment(
        self, session: Session, user: User, session_id: int, fragment_id: int
    ) -> None:
        with session.begin():
            fragment, memory, role = self._fragment_access(
                session,
                user.id,
                fragment_id,
                lock=True,
                expected_session_id=session_id,
            )
            self._require_material_edit(session, memory, user, role, fragment.contributor_id)
            now = datetime.now(timezone.utc)
            fragment.is_del = True
            fragment.updated_by = user.id
            fragment.updated_at = now
            attachments = session.scalars(
                select(FragmentAttachment).where(
                    FragmentAttachment.fragment_id == fragment.id,
                    FragmentAttachment.is_del.is_(False),
                )
            ).all()
            if role == "contributor" and any(
                item.contributor_id != fragment.contributor_id for item in attachments
            ):
                raise HTTPException(status_code=403, detail="该片段含其他成员的附件")
            affected_asset_ids = {attachment.media_asset_id for attachment in attachments}
            for attachment in attachments:
                attachment.is_del = True
                attachment.updated_by = user.id
                attachment.updated_at = now
            if fragment.source_asset_id is not None:
                affected_asset_ids.add(fragment.source_asset_id)
            self._refresh_assets_after_unlink(session, affected_asset_ids)
            # [新增] 直接文字没有文件引用，仍需刷出软删除再重算会话时间。
            session.flush()
            memory.last_material_at = self._latest_recorded_at(session, memory.id)
            memory.updated_by = user.id
            memory.updated_at = now

    def create_attachments(
        self,
        session: Session,
        user: User,
        fragment_id: int,
        body: AttachmentBatchCreate,
        storage: ObjectStorageService | None = None,
    ) -> list[dict]:
        with session.begin():
            fragment, memory, role = self._fragment_access(session, user.id, fragment_id, lock=True)
            contributor = self._require_contribution(session, memory, user, role)
            if contributor is None:
                contributor = self._owner_contributor(session, memory, user)
            current = (
                session.scalar(
                    select(func.count(FragmentAttachment.id)).where(
                        FragmentAttachment.fragment_id == fragment.id,
                        FragmentAttachment.is_del.is_(False),
                    )
                )
                or 0
            )
            if current + len(body.items) > MAX_ATTACHMENTS_PER_FRAGMENT:
                raise HTTPException(status_code=422, detail="每段语音最多关联 9 个附件")
            ids = [item.asset_id for item in body.items]
            if len(ids) != len(set(ids)):
                raise HTTPException(status_code=422, detail="同一请求中不能重复添加素材")
            assets = session.scalars(
                select(MediaAsset)
                .where(
                    MediaAsset.id.in_(sorted(ids)),
                    MediaAsset.uploaded_by_user_id == user.id,
                    MediaAsset.is_del.is_(False),
                )
                .order_by(MediaAsset.id)
                .with_for_update()
            ).all()
            assets_by_id = {asset.id: asset for asset in assets}
            if len(assets_by_id) != len(ids):
                raise self._not_found()
            result = []
            for item in body.items:
                asset = assets_by_id[item.asset_id]
                if asset.purpose != "attachment" or asset.upload_status not in {
                    "uploaded",
                    "attached",
                }:
                    raise HTTPException(status_code=422, detail="附件素材未上传完成或用途不正确")
                if session.scalar(
                    select(FragmentAttachment.id).where(
                        FragmentAttachment.fragment_id == fragment.id,
                        FragmentAttachment.media_asset_id == asset.id,
                        FragmentAttachment.is_del.is_(False),
                    )
                ):
                    raise HTTPException(status_code=409, detail="该素材已关联到此语音片段")
                link = FragmentAttachment(
                    fragment_id=fragment.id,
                    media_asset_id=asset.id,
                    contributor_id=contributor.id,
                    caption=item.caption,
                    sort_order=item.sort_order,
                    created_by=user.id,
                    updated_by=user.id,
                )
                session.add(link)
                asset.upload_status = "attached"
                asset.updated_by = user.id
                result.append(link)
            session.flush()
            return [self._attachment_response(session, link, storage) for link in result]

    def _attachment_response(
        self,
        session: Session,
        link: FragmentAttachment,
        storage: ObjectStorageService | None = None,
    ) -> dict:
        asset = session.get(MediaAsset, link.media_asset_id)
        return {
            "id": link.id,
            "fragment_id": link.fragment_id,
            "contributor_id": link.contributor_id,
            "asset": self._asset_response(asset, storage),
            "caption": link.caption,
            "sort_order": link.sort_order,
            "created_at": link.created_at,
        }

    def list_attachments(
        self, session: Session, user: User, fragment_id: int, storage: ObjectStorageService
    ) -> list[dict]:
        self._fragment_access(session, user.id, fragment_id)
        rows = session.scalars(
            select(FragmentAttachment)
            .where(
                FragmentAttachment.fragment_id == fragment_id, FragmentAttachment.is_del.is_(False)
            )
            .order_by(FragmentAttachment.sort_order, FragmentAttachment.id)
        ).all()
        return [self._attachment_response(session, row, storage) for row in rows]

    def patch_attachment(
        self,
        session: Session,
        user: User,
        fragment_id: int,
        attachment_id: int,
        body: AttachmentPatch,
        storage: ObjectStorageService | None = None,
    ) -> dict:
        values = body.model_dump(exclude_unset=True)
        if not values:
            raise HTTPException(status_code=422, detail="至少提供一个要更新的字段")
        with session.begin():
            _, memory, role = self._fragment_access(session, user.id, fragment_id, lock=True)
            link = session.scalar(
                select(FragmentAttachment).where(
                    FragmentAttachment.id == attachment_id,
                    FragmentAttachment.fragment_id == fragment_id,
                    FragmentAttachment.is_del.is_(False),
                )
            )
            if link is None:
                raise self._not_found()
            self._require_material_edit(session, memory, user, role, link.contributor_id)
            for key, value in values.items():
                setattr(link, key, value)
            link.updated_by = user.id
        return self._attachment_response(session, link, storage)

    def delete_attachment(
        self, session: Session, user: User, fragment_id: int, attachment_id: int
    ) -> None:
        with session.begin():
            _, memory, role = self._fragment_access(session, user.id, fragment_id, lock=True)
            link = session.scalar(
                select(FragmentAttachment).where(
                    FragmentAttachment.id == attachment_id,
                    FragmentAttachment.fragment_id == fragment_id,
                    FragmentAttachment.is_del.is_(False),
                )
            )
            if link is None:
                raise self._not_found()
            self._require_material_edit(session, memory, user, role, link.contributor_id)
            now = datetime.now(timezone.utc)
            link.is_del = True
            link.updated_by = user.id
            link.updated_at = now
            self._refresh_assets_after_unlink(session, {link.media_asset_id})

    def list_assets(
        self,
        session: Session,
        user: User,
        page: int,
        page_size: int,
        purpose: str | None,
        media_type: str | None,
        storage: ObjectStorageService,
    ) -> dict:
        filters = [
            MediaAsset.uploaded_by_user_id == user.id,
            MediaAsset.is_del.is_(False),
            MediaAsset.upload_status.in_(["uploaded", "attached"]),
        ]
        if purpose:
            filters.append(MediaAsset.purpose == purpose)
        if media_type:
            filters.append(MediaAsset.media_type == media_type)
        total = session.scalar(select(func.count(MediaAsset.id)).where(*filters)) or 0
        rows = session.scalars(
            select(MediaAsset)
            .where(*filters)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [self._asset_response(row, storage) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def get_asset(
        self, session: Session, user: User, asset_id: int, storage: ObjectStorageService
    ) -> MediaAssetResponse:
        return self._asset_response(self._asset(session, user.id, asset_id), storage)

    def delete_asset(self, session: Session, user: User, asset_id: int) -> None:
        with session.begin():
            asset = self._asset(session, user.id, asset_id, lock=True)
            clip_refs = (
                session.scalar(
                    select(func.count(MemoryFragment.id)).where(
                        MemoryFragment.source_asset_id == asset.id, MemoryFragment.is_del.is_(False)
                    )
                )
                or 0
            )
            attachment_refs = (
                session.scalar(
                    select(func.count(FragmentAttachment.id)).where(
                        FragmentAttachment.media_asset_id == asset.id,
                        FragmentAttachment.is_del.is_(False),
                    )
                )
                or 0
            )
            if clip_refs + attachment_refs:
                raise HTTPException(status_code=409, detail="素材仍被语音片段引用，请先移除关联")
            asset.is_del = True
            asset.purge_after = datetime.now(timezone.utc) + timedelta(days=30)
            asset.updated_by = user.id

    def get_session_detail(
        self, session: Session, user: User, session_id: int, storage: ObjectStorageService
    ) -> dict:
        row, _ = self._session_access(session, user.id, session_id)
        fragments = session.scalars(
            select(MemoryFragment)
            .where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))
            .order_by(MemoryFragment.sequence_no)
        ).all()
        attachment_count = (
            session.scalar(
                select(func.count(FragmentAttachment.id))
                .join(MemoryFragment, MemoryFragment.id == FragmentAttachment.fragment_id)
                .where(
                    MemoryFragment.session_id == row.id,
                    MemoryFragment.is_del.is_(False),
                    FragmentAttachment.is_del.is_(False),
                )
            )
            or 0
        )
        duration_ms = (
            session.scalar(
                select(func.coalesce(func.sum(MediaAsset.duration_ms), 0))
                .join(MemoryFragment, MemoryFragment.source_asset_id == MediaAsset.id)
                .where(MemoryFragment.session_id == row.id, MemoryFragment.is_del.is_(False))
            )
            or 0
        )
        topic_title = (
            session.scalar(select(Topic.title).where(Topic.id == row.topic_id))
            if row.topic_id
            else None
        )
        excerpt = self._latest_excerpt(session, row.id)
        return {
            "id": row.id,
            "topic_id": row.topic_id,
            "topic_title": topic_title,
            "title": row.title,
            "is_favorite": row.is_favorite,
            "last_recorded_at": row.last_material_at,
            "created_at": row.created_at,
            "fragment_count": len(fragments),
            "attachment_count": attachment_count,
            "duration_ms": duration_ms,
            "transcript_excerpt": excerpt,
            "fragments": [self._fragment_response(session, f, storage) for f in fragments],
        }
