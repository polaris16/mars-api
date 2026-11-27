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
    MAX_ATTACHMENTS_PER_SESSION,
    MemoryService,
    UploadItemError,
)
from app.services.object_storage_service import ObjectStorageService


class AttachmentUploadService:
    def __init__(self) -> None:
        self.memory = MemoryService()

    def _check_target(self, db: Session, user: User, target_id: int, *, story: bool) -> int:
        # 进入批次前按常规 HTTP 状态处理目标权限，逐项写入时还会加锁复核。
        db.rollback()
        with db.begin():
            memory, role = self.memory._attachment_target(db, user.id, target_id, story=story)
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
        target_id: int,
        body: AttachmentUploadBatch,
        storage: ObjectStorageService,
        *, story: bool = False,
    ) -> dict:
        self._check_target(db, user, target_id, story=story)
        fragment_id = None if story else target_id
        ask = body.duplicate_mode == "ask"
        representatives: dict[int, UUID] = {}
        upload_grants: dict[UUID, int] = {}
        results = []
        for item in body.items:
            try:
                grant = self.memory.create_upload_authorization(
                    db,
                    user,
                    UploadAuthorizationRequest(
                        purpose="attachment",
                        fragment_id=fragment_id,
                        session_id=target_id if story else None,
                        **item.model_dump(exclude={"client_file_id"}),
                    ),
                    storage,
                    batch_file_id=item.client_file_id,
                    ask_about_attachment_duplicates=ask,
                )
            except HTTPException as exc:
                db.rollback()
                results.append(self._item_error(item.client_file_id, exc, authorizing=True))
            else:
                result = {**grant, "client_file_id": item.client_file_id}
                pending_representative = grant.get("representative_client_file_id")
                batch_representative = representatives.get(grant["asset_id"])
                if pending_representative is not None:
                    result.update(status="duplicate", reason="pending_upload")
                elif grant.get("operation_replay"):
                    result.update(status="already_attached")
                elif grant.get("attachment_id") is not None:
                    result.update(status="duplicate", reason="existing_attachment")
                elif ask and grant["reused"] and batch_representative is not None:
                    result.update(
                        status="duplicate",
                        reason="same_batch",
                        representative_client_file_id=batch_representative,
                    )
                else:
                    result.update(status="reused" if grant["reused"] else "upload_required")
                    if ask:
                        if grant["reused"]:
                            representatives[grant["asset_id"]] = item.client_file_id
                        else:
                            upload_grants[item.client_file_id] = grant["asset_id"]
                results.append(result)
        # 代表项实际取得本批直传授权才算本批重复，已失败的授权不参与依赖。
        for result in results:
            if result.get("reason") == "pending_upload" and (
                upload_grants.get(result["representative_client_file_id"]) == result["asset_id"]
            ):
                result["reason"] = "same_batch"
        return {"items": results}

    def complete_and_attach(
        self,
        db: Session,
        user: User,
        target_id: int,
        body: AttachmentCompleteBatch,
        storage: ObjectStorageService,
        *, story: bool = False,
    ) -> dict:
        memory_id = self._check_target(db, user, target_id, story=story)
        fragment_id = None if story else target_id
        user_id = user.id
        ask = body.duplicate_mode == "ask"
        # 先完成代表项，再处理依赖项。响应仍按 App 传入的顺序返回。
        results: dict[UUID, dict] = {}
        verification_errors: dict[tuple[int, str], HTTPException] = {}
        items = sorted(body.items, key=lambda item: item.representative_client_file_id is not None)
        for item in items:
            try:
                upload_key = self.memory.batch_attachment_upload_key(
                    user_id,
                    memory_id,
                    fragment_id,
                    item.representative_client_file_id or item.client_file_id,
                )
                asset_id = self._verify_file(
                    db, user, user_id, memory_id, fragment_id, item, storage, upload_key, verification_errors
                )
                # 确认结果独立提交，写入关联之前结束可能由素材读取开启的事务。
                db.rollback()
                result = self._attach(db, user, user_id, target_id, asset_id, item, ask=ask, story=story)
            except HTTPException as exc:
                db.rollback()
                result = self._item_error(item.client_file_id, exc, authorizing=False)
            results[item.client_file_id] = result
        return {"items": [results[item.client_file_id] for item in body.items]}

    # 同一批次的依赖项复用代表项的校验结果。失败后等下一批重试。
    def _verify_file(
        self,
        db: Session,
        user: User,
        user_id: int,
        memory_id: int,
        fragment_id: int | None,
        item: AttachmentCompleteItem,
        storage: ObjectStorageService,
        upload_key: str,
        verification_errors: dict[tuple[int, str], HTTPException],
    ) -> int:
        operation = self.memory._attachment_operation(
            db, memory_id, user_id, item.client_file_id, fragment_id=fragment_id
        )
        if operation is not None and operation.media_asset_id != item.asset_id:
            requested = self.memory._asset(db, user_id, item.asset_id)
            resolved = self.memory._batch_completion_asset(db, requested, upload_key)
            self._check_operation_asset(operation, resolved.id)

        verification_key = (item.asset_id, upload_key)
        previous_error = verification_errors.get(verification_key)
        if previous_error is not None:
            raise previous_error
        try:
            asset = self.memory.complete_upload(
                db,
                user,
                item.asset_id,
                UploadCompleteRequest(
                    duration_ms=item.duration_ms, sample_rate_hz=item.sample_rate_hz
                ),
                storage,
                attachment_upload_key=upload_key,
            )
        except HTTPException as exc:
            verification_errors[verification_key] = exc
            raise
        except OSError as exc:
            error = HTTPException(502, "对象存储暂时无法读取附件")
            verification_errors[verification_key] = error
            raise error from exc
        return asset.id

    @staticmethod
    def _check_operation_asset(operation: FragmentAttachment, asset_id: int) -> None:
        if operation.media_asset_id != asset_id:
            raise UploadItemError(
                409, "同一 client_file_id 已用于其他文件", code="file_id_conflict"
            )

    def _attach(
        self,
        db: Session,
        user: User,
        user_id: int,
        target_id: int,
        asset_id: int,
        item: AttachmentCompleteItem,
        *,
        ask: bool,
        story: bool,
    ) -> dict:
        with db.begin():
            memory, role = self.memory._attachment_target(db, user_id, target_id, story=story, lock=True)
            fragment_id = None if story else target_id
            contributor = self.memory._require_contribution(db, memory, user, role)
            asset = self.memory._asset(db, user_id, asset_id, lock=True)
            if asset.purpose != "attachment" or asset.upload_status not in CONFIRMED_UPLOAD_STATES:
                raise UploadItemError(404, "此素材不可用于该附件上传", code="asset_unavailable")
            active = (
                FragmentAttachment.session_id == memory.id,
                FragmentAttachment.is_del.is_(False),
            )
            result = {"client_file_id": item.client_file_id, "asset_id": asset.id}
            # 操作级幂等优先于按文件查重。保留多份后仍返回该操作的关联 ID。
            operation = self.memory._attachment_operation(
                db, memory.id, user_id, item.client_file_id, fragment_id=fragment_id
            )
            if operation is not None:
                self._check_operation_asset(operation, asset.id)
                return {**result, "status": "already_attached", "attachment_id": operation.id}
            attachment_id = self.memory._active_attachment_id(db, memory.id, asset.id)
            if item.duplicate_action == "skip":
                return {**result, "status": "skipped", "attachment_id": attachment_id}
            if item.duplicate_action != "keep" and (
                attachment_id is not None or item.representative_client_file_id is not None
            ):
                return {
                    **result,
                    "status": "duplicate" if ask else "already_attached",
                    "attachment_id": attachment_id,
                }

            count = db.scalar(select(func.count(FragmentAttachment.id)).where(*active)) or 0
            if count >= MAX_ATTACHMENTS_PER_SESSION:
                raise UploadItemError(
                    409,
                    "每个故事最多关联 9 个有效附件",
                    code="attachment_limit_reached",
                    retryable=True,
                )
            if contributor is None:
                contributor = self.memory._owner_contributor(db, memory, user)
            link = self.memory._add_attachment_link(
                db,
                memory.id,
                fragment_id,
                contributor.id,
                user_id,
                asset,
                AttachmentInput(
                    asset_id=asset.id, caption=item.caption, sort_order=item.sort_order
                ),
                client_file_id=item.client_file_id if ask else None,
            )
            db.flush()
            if fragment_id is not None:
                self.memory._schedule_attachments_added(db, memory, fragment_id, user_id)
            return {**result, "status": "attached", "attachment_id": link.id}
