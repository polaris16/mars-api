"""[新增] 按片段批量签发、确认并关联附件。"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FragmentAttachment, User
from app.schemas.attachment_upload import (
    AttachmentCompleteBatch,
    AttachmentCompleteItem,
    AttachmentUploadBatch,
)
from app.schemas.memory import AttachmentInput, UploadAuthorizationRequest, UploadCompleteRequest
from app.services.memory_service import (
    CONFIRMED_UPLOAD_STATES,
    MAX_ATTACHMENTS_PER_FRAGMENT,
    MemoryService,
    UploadItemError,
)
from app.services.object_storage_service import ObjectStorageService


class AttachmentUploadService:
    def __init__(self) -> None:
        self.memory = MemoryService()

    def _check_target(self, db: Session, user: User, fragment_id: int) -> int:
        # 进入批次前按常规 HTTP 状态处理目标权限，逐项写入时还会加锁复核。
        db.rollback()
        with db.begin():
            _, memory, role = self.memory._fragment_access(db, user.id, fragment_id)
            self.memory._require_contribution(db, memory, user, role)
            return memory.id

    @staticmethod
    def _item_error(client_file_id: UUID, exc: HTTPException, *, authorizing: bool) -> dict:
        if isinstance(exc, UploadItemError):
            code, retryable = exc.code, exc.retryable
        elif exc.status_code >= 500:
            code, retryable = "storage_unavailable", True
        elif exc.status_code == 403:
            code, retryable = "contribution_not_allowed", False
        elif authorizing and exc.status_code == 413:
            code, retryable = "file_too_large", False
        elif exc.status_code in {413, 422}:
            code, retryable = "file_mismatch", False
        else:
            code, retryable = "asset_unavailable", False
        return {
            "client_file_id": client_file_id,
            "status": "failed",
            "error": {"code": code, "message": str(exc.detail), "retryable": retryable},
        }

    def authorize(
        self,
        db: Session,
        user: User,
        fragment_id: int,
        body: AttachmentUploadBatch,
        storage: ObjectStorageService,
    ) -> dict:
        self._check_target(db, user, fragment_id)
        results = []
        for item in body.items:
            try:
                grant = self.memory.create_upload_authorization(
                    db,
                    user,
                    UploadAuthorizationRequest(
                        purpose="attachment",
                        fragment_id=fragment_id,
                        **item.model_dump(exclude={"client_file_id"}),
                    ),
                    storage,
                    batch_file_id=item.client_file_id,
                )
            except HTTPException as exc:
                db.rollback()
                results.append(self._item_error(item.client_file_id, exc, authorizing=True))
            else:
                item_status = "reused" if grant.pop("reused") else "upload_required"
                results.append(
                    {**grant, "client_file_id": item.client_file_id, "status": item_status}
                )
        return {"items": results}

    def complete_and_attach(
        self,
        db: Session,
        user: User,
        fragment_id: int,
        body: AttachmentCompleteBatch,
        storage: ObjectStorageService,
    ) -> dict:
        memory_id = self._check_target(db, user, fragment_id)
        user_id = user.id
        results = []
        for item in body.items:
            try:
                asset = self.memory.complete_upload(
                    db,
                    user,
                    item.asset_id,
                    UploadCompleteRequest(
                        duration_ms=item.duration_ms, sample_rate_hz=item.sample_rate_hz
                    ),
                    storage,
                    attachment_upload_key=self.memory.batch_attachment_upload_key(
                        user_id, memory_id, fragment_id, item.client_file_id
                    ),
                )
                asset_id = asset.id
                # 确认结果独立提交，写入关联之前结束可能由素材读取开启的事务。
                db.rollback()
                result = self._attach(db, user, user_id, fragment_id, asset_id, item)
            except HTTPException as exc:
                db.rollback()
                result = self._item_error(item.client_file_id, exc, authorizing=False)
            except OSError:
                db.rollback()
                result = self._item_error(
                    item.client_file_id,
                    HTTPException(502, "对象存储暂时无法读取附件"),
                    authorizing=False,
                )
            results.append(result)
        return {"items": results}

    def _attach(
        self,
        db: Session,
        user: User,
        user_id: int,
        fragment_id: int,
        asset_id: int,
        item: AttachmentCompleteItem,
    ) -> dict:
        with db.begin():
            _, memory, role = self.memory._fragment_access(db, user_id, fragment_id, lock=True)
            contributor = self.memory._require_contribution(db, memory, user, role)
            asset = self.memory._asset(db, user_id, asset_id, lock=True)
            if asset.purpose != "attachment" or asset.upload_status not in CONFIRMED_UPLOAD_STATES:
                raise UploadItemError(404, "此素材不可用于该附件上传", code="asset_unavailable")
            active = (
                FragmentAttachment.fragment_id == fragment_id,
                FragmentAttachment.is_del.is_(False),
            )
            attachment_id = db.scalar(
                select(FragmentAttachment.id).where(
                    *active, FragmentAttachment.media_asset_id == asset.id
                )
            )
            result = {"client_file_id": item.client_file_id, "asset_id": asset.id}
            if attachment_id is not None:
                return {**result, "status": "already_attached", "attachment_id": attachment_id}

            count = db.scalar(select(func.count(FragmentAttachment.id)).where(*active)) or 0
            if count >= MAX_ATTACHMENTS_PER_FRAGMENT:
                raise UploadItemError(
                    409,
                    "每段记忆最多关联 9 个有效附件",
                    code="attachment_limit_reached",
                    retryable=True,
                )
            if contributor is None:
                contributor = self.memory._owner_contributor(db, memory, user)
            link = self.memory._add_attachment_link(
                db,
                fragment_id,
                contributor.id,
                user_id,
                asset,
                AttachmentInput(
                    asset_id=asset.id, caption=item.caption, sort_order=item.sort_order
                ),
            )
            db.flush()
            self.memory._schedule_attachments_added(db, memory, fragment_id, user_id)
            return {**result, "status": "attached", "attachment_id": link.id}
