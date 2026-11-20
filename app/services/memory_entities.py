"""[新增] 实体候选、确认历史及按有效证据读取实体。"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    LifeEntity, LifeEntityAlias, MemoryDraftVersion, MemoryElement, MemoryElementEntity,
    MemoryEntityCandidate, MemoryEntityEvidence, MemoryEntityResolution, MemoryFragment,
    MemorySession, MemorySourceRevision, User,
)
from app.schemas.memory_extraction import StructuredElement
from app.schemas.memory_processing import CandidateResolutionCreate
from app.services.memory_service import MemoryService
from app.services.memory_versions import fingerprint


class MemoryEntityService:
    def __init__(self) -> None:
        self.memories = MemoryService()

    @staticmethod
    def _owner_entities(owner_id: int):
        return select(LifeEntity).where(
            LifeEntity.owner_user_id == owner_id, LifeEntity.is_del.is_(False)
        )

    @staticmethod
    def _lock_owner(db: Session, owner_id: int) -> None:
        # 跨会话的写入以同一所有者串行化，避免并发创建相同身份的实体。
        db.scalar(select(User.id).where(User.id == owner_id).with_for_update())

    @staticmethod
    def _create(db: Session, owner_id: int, candidate, name: str, proof: str | None) -> LifeEntity:
        entity = LifeEntity(
            owner_user_id=owner_id, entity_type=candidate.entity_type, canonical_name=name,
            attributes={"identity_quote": proof} if proof else {},
            created_by=owner_id, updated_by=owner_id,
        )
        db.add(entity)
        db.flush()
        return entity

    @staticmethod
    def _link(
        db: Session, owner_id: int, element: MemoryElement, entity: LifeEntity,
        candidate: MemoryEntityCandidate,
    ) -> None:
        link = db.scalar(
            select(MemoryElementEntity).where(
                MemoryElementEntity.memory_element_id == element.id,
                MemoryElementEntity.entity_id == entity.id,
                MemoryElementEntity.relation_type == candidate.relation_type,
                MemoryElementEntity.is_del.is_(False),
            )
        )
        if link is None:
            link = MemoryElementEntity(
                memory_element_id=element.id, entity_id=entity.id, relation_type=candidate.relation_type,
                evidence_text=candidate.mention_text, created_by=owner_id, updated_by=owner_id,
            )
            db.add(link)
            db.flush()
        alias = db.scalar(
            select(LifeEntityAlias).where(
                LifeEntityAlias.entity_id == entity.id,
                LifeEntityAlias.alias_name == candidate.mention_text,
                LifeEntityAlias.is_del.is_(False),
            )
        )
        if alias is None:
            alias = LifeEntityAlias(
                entity_id=entity.id, alias_name=candidate.mention_text,
                source_fragment_id=element.source_fragment_id, created_by=owner_id, updated_by=owner_id,
            )
            db.add(alias)
            db.flush()
        exists = db.scalar(
            select(MemoryEntityEvidence.id).where(
                MemoryEntityEvidence.element_entity_id == link.id,
                MemoryEntityEvidence.evidence_key == candidate.evidence_key,
            )
        )
        if exists is None:
            linked_evidence = {
                key: value for key, value in candidate.evidence.items()
                if key not in {"possible_locations", "locations_truncated"}
            }
            if candidate.narrator_user_id is not None:
                linked_evidence["narrator_user_id"] = candidate.narrator_user_id
            db.add(MemoryEntityEvidence(
                element_entity_id=link.id, alias_id=alias.id, evidence=linked_evidence,
                evidence_key=candidate.evidence_key, created_by=owner_id, updated_by=owner_id,
            ))

    @classmethod
    def _supported(cls, db: Session, entity_id: int) -> bool:
        return db.scalar(cls._evidence_query().where(LifeEntity.id == entity_id).limit(1)) is not None

    @staticmethod
    def reference_narrator(references: list[dict]) -> int | None:
        narrators = {reference.get("narrator_user_id") for reference in references}
        return next(iter(narrators)) if len(narrators) == 1 else None

    @staticmethod
    def _registered_narrator(db: Session, user_id: int | None) -> int | None:
        if user_id is None:
            return None
        return db.scalar(select(User.id).where(User.id == user_id, User.is_del.is_(False)))

    @staticmethod
    def _evidence_anchor(db: Session, element: MemoryElement, references: list[dict]) -> str | None:
        """以段落上下文和原文位置定位证据，原声时间与整理稿版本号可以变化。"""
        if not references or element.draft_version_id is None:
            return None
        draft = db.get(MemoryDraftVersion, element.draft_version_id)
        if draft is None or draft.is_del:
            return None
        source = db.get(MemorySourceRevision, draft.source_revision_id)
        if source is None or source.is_del or source.fragment_id != element.source_fragment_id:
            return None
        paragraphs = {paragraph["id"]: paragraph for paragraph in draft.paragraphs}
        anchors = []
        for reference in references:
            paragraph = paragraphs.get(reference["paragraph_id"])
            if paragraph is None or reference["draft_version_id"] != draft.id:
                return None
            raw_ranges = paragraph["source_ranges"]
            if raw_ranges:
                raw_evidence = [
                    [span, source.source_text[span["start_offset"] : span["end_offset"]]]
                    for span in raw_ranges
                ]
                kind = (
                    "transcript_raw"
                    if source.source_kind in {"app_asr", "server_asr"}
                    else source.source_kind
                )
                location = [kind, source.source_asset_id, raw_evidence]
            else:
                # 人工补述以段落 ID 定位，并比较该段文字，防止沿用已改写的指代。
                location = ["owner_paragraph", paragraph["id"]]
            anchors.append([location, paragraph["text"], reference["text_ranges"]])
        return fingerprint([element.source_quote, anchors])

    @staticmethod
    def _previous_elements(element: MemoryElement):
        return (
            MemoryElement.session_id == element.session_id,
            MemoryElement.source_fragment_id == element.source_fragment_id,
            MemoryElement.draft_version_id < element.draft_version_id,
            MemoryElement.source_quote == element.source_quote,
            MemoryElement.is_del.is_(True),
        )

    def _previous_identity(
        self, db: Session, element: MemoryElement, candidate: MemoryEntityCandidate,
        owner_id: int, anchor: str | None,
    ) -> LifeEntity | None:
        if anchor is None:
            return None
        query = (
            select(MemoryElement, MemoryEntityEvidence, LifeEntity)
            .join(MemoryElementEntity, MemoryElementEntity.memory_element_id == MemoryElement.id)
            .join(LifeEntity, LifeEntity.id == MemoryElementEntity.entity_id)
            .join(MemoryEntityEvidence, MemoryEntityEvidence.element_entity_id == MemoryElementEntity.id)
            .join(LifeEntityAlias, LifeEntityAlias.id == MemoryEntityEvidence.alias_id)
            .where(
                *self._previous_elements(element),
                MemoryElementEntity.relation_type == candidate.relation_type,
                MemoryElementEntity.is_del.is_(False),
                LifeEntity.owner_user_id == owner_id,
                LifeEntity.entity_type == candidate.entity_type,
                LifeEntity.is_del.is_(False),
                LifeEntityAlias.entity_id == LifeEntity.id,
                LifeEntityAlias.alias_name == candidate.mention_text,
                LifeEntityAlias.is_del.is_(False),
                MemoryEntityEvidence.is_del.is_(False),
            )
            .order_by(MemoryElement.draft_version_id.desc())
        )
        last_version = None
        matches = {}
        for previous, evidence, entity in db.execute(query):
            if last_version is not None and previous.draft_version_id != last_version:
                break
            proof = entity.attributes.get("identity_quote")
            if (
                isinstance(proof, str) and candidate.mention_text in proof
                and proof.strip() != candidate.mention_text and proof in element.source_quote
                and evidence.evidence.get("narrator_user_id") == candidate.narrator_user_id
                and self._evidence_anchor(db, previous, evidence.evidence.get("draft_references", [])) == anchor
            ):
                last_version = previous.draft_version_id
                matches[entity.id] = entity
        return next(iter(matches.values())) if len(matches) == 1 else None

    def _previous_resolutions(
        self, db: Session, element: MemoryElement, candidate: MemoryEntityCandidate, anchor: str | None,
    ) -> dict[int, str]:
        """从旧候选的可核对证据继承人工结论，包括使用旧指纹保存的记录。"""
        if anchor is None:
            return {}
        query = (
            select(MemoryElement, MemoryEntityCandidate.evidence, MemoryEntityResolution)
            .select_from(MemoryEntityCandidate)
            .join(MemoryElement, MemoryElement.id == MemoryEntityCandidate.element_id)
            .join(MemoryEntityResolution, MemoryEntityResolution.candidate_id == MemoryEntityCandidate.id)
            .where(
                *self._previous_elements(element),
                MemoryEntityCandidate.mention_text == candidate.mention_text,
                MemoryEntityCandidate.entity_type == candidate.entity_type,
                MemoryEntityCandidate.relation_type == candidate.relation_type,
                MemoryEntityCandidate.narrator_user_id == candidate.narrator_user_id,
                MemoryEntityCandidate.is_del.is_(False),
                MemoryEntityResolution.is_del.is_(False),
            )
            .order_by(MemoryEntityResolution.id.desc())
        )
        actions = {}
        for previous, evidence, decision in db.execute(query):
            if self._evidence_anchor(db, previous, evidence.get("draft_references", [])) == anchor:
                actions.setdefault(decision.entity_id, decision.action)
        return actions

    def store_mentions(
        self, db: Session, element: MemoryElement, result: StructuredElement, *,
        possible_locations: list[dict], locations_truncated: bool,
        narrator_by_mention: dict[str, int | None],
    ) -> None:
        owner_id = db.get(MemorySession, element.session_id).owner_user_id
        self._lock_owner(db, owner_id)
        anchor = self._evidence_anchor(db, element, element.draft_references or [])
        seen = set()
        for mention in result.entity_mentions:
            name = mention.mention_text
            key = (mention.entity_type, name, mention.relation_type)
            if key in seen or name not in result.source_quote or "\x00" in name:
                continue
            # [修改] 引句重复时保留候选和多个出处，等所有者明确位置。
            if not element.draft_references and not possible_locations:
                continue
            seen.add(key)
            narrator_id = self._registered_narrator(
                db, narrator_by_mention.get(
                    name, self.reference_narrator(element.draft_references or [])
                )
            )
            relative = name.startswith(("我", "咱"))
            proof = mention.identity_quote
            if not proof or proof not in result.source_quote or name not in proof or proof.strip() == name:
                proof = None
            if relative and narrator_id is None:
                proof = None
            evidence = {"draft_references": element.draft_references or [], "quote": name}
            if possible_locations:
                evidence.update(
                    location_status="ambiguous", possible_locations=possible_locations,
                    locations_truncated=locations_truncated,
                )
            # [删除-旧] digest = fingerprint([source_revision_id, result.summary, result.source_quote, key])
            # [修改] 重新生成的摘要不参与身份确认，历史结论按引用的证据比对。
            candidate = MemoryEntityCandidate(
                element_id=element.id, entity_type=mention.entity_type, mention_text=name,
                relation_type=mention.relation_type, narrator_user_id=narrator_id,
                evidence=evidence,
                evidence_key=fingerprint([element.source_fragment_id, anchor, key, narrator_id]),
                created_by=owner_id, updated_by=owner_id,
            )
            previous = self._previous_resolutions(db, element, candidate, anchor)
            excluded = {target for target, action in previous.items() if action == "reject_match"}
            accepted = next((target for target in previous if target not in excluded), None)
            candidate_query = self._owner_entities(owner_id).where(
                LifeEntity.entity_type == mention.entity_type,
                (LifeEntity.canonical_name == name) | LifeEntity.id.in_(
                    select(LifeEntityAlias.entity_id).where(
                        LifeEntityAlias.alias_name == name, LifeEntityAlias.is_del.is_(False)
                    )
                ),
            )
            if relative:
                # 第一人称亲属称谓以说出称谓的人为边界。
                candidate_query = candidate_query.where(LifeEntity.id.in_(
                    self._evidence_query().where(
                        LifeEntityAlias.alias_name == name,
                        MemoryEntityEvidence.evidence["narrator_user_id"].as_integer() == narrator_id,
                        MemoryEntityEvidence.evidence["narrator_user_id"].as_integer().is_not(None),
                    )
                ))
            matches = [
                entity for entity in db.scalars(candidate_query.order_by(LifeEntity.id))
                if entity.id not in excluded and self._supported(db, entity.id)
            ]
            candidate.candidate_entity_ids = [entity.id for entity in matches]
            inherited = db.get(LifeEntity, accepted) if accepted else None
            if inherited is not None and (
                inherited.owner_user_id != owner_id or inherited.is_del
                or inherited.entity_type != mention.entity_type
            ):
                inherited = None
            proven = [
                entity for entity in matches
                if proof is not None and entity.attributes.get("identity_quote") == proof
            ]
            # [修改] 旧元素退役后，其已核对的同片段证据仍可用于身份继承。
            prior_identity = (
                self._previous_identity(db, element, candidate, owner_id, anchor)
                if not relative or narrator_id is not None else None
            )
            if prior_identity is not None and prior_identity.id in excluded:
                prior_identity = None
            resolved = inherited or prior_identity or (proven[0] if len(proven) == 1 else None)
            if not possible_locations:
                if resolved is None and proof and not matches and not excluded:
                    resolved = self._create(db, owner_id, candidate, name, proof)
                if resolved is not None:
                    candidate.status = "resolved"
                    self._link(db, owner_id, element, resolved, candidate)
            db.add(candidate)

    @staticmethod
    def _evidence_query():
        return (
            select(LifeEntity.id)
            .join(MemoryElementEntity, MemoryElementEntity.entity_id == LifeEntity.id)
            .join(MemoryElement, MemoryElement.id == MemoryElementEntity.memory_element_id)
            .join(MemoryFragment, MemoryFragment.id == MemoryElement.source_fragment_id)
            .join(MemorySession, MemorySession.id == MemoryElement.session_id)
            .join(MemoryEntityEvidence, MemoryEntityEvidence.element_entity_id == MemoryElementEntity.id)
            .join(LifeEntityAlias, LifeEntityAlias.id == MemoryEntityEvidence.alias_id)
            .where(
                LifeEntity.is_del.is_(False), MemoryElementEntity.is_del.is_(False),
                MemoryElement.is_del.is_(False), MemoryFragment.is_del.is_(False),
                MemorySession.is_del.is_(False), MemoryEntityEvidence.is_del.is_(False),
                LifeEntityAlias.is_del.is_(False), MemorySession.owner_user_id == LifeEntity.owner_user_id,
            )
        )

    @classmethod
    def element_refs(cls, db: Session, element_id: int) -> list[dict]:
        query = cls._evidence_query().where(MemoryElement.id == element_id).with_only_columns(
            LifeEntity.id, LifeEntity.entity_type, LifeEntityAlias.alias_name,
            MemoryElementEntity.relation_type,
        ).distinct().order_by(LifeEntity.id, LifeEntityAlias.alias_name)
        refs = {}
        for entity_id, kind, name, role in db.execute(query):
            refs.setdefault((entity_id, role), {
                "id": entity_id, "entity_type": kind, "display_name": name, "relation_type": role,
            })
        return list(refs.values())

    def list_entities(
        self, db: Session, user: User, session_id: int, page: int, page_size: int,
        entity_type: str | None, element_id: int | None,
    ) -> dict:
        self.memories._session_access(db, user.id, session_id)
        query = self._evidence_query().where(MemorySession.id == session_id)
        if entity_type is not None:
            query = query.where(LifeEntity.entity_type == entity_type)
        if element_id is not None:
            query = query.where(MemoryElement.id == element_id)
        summaries = query.with_only_columns(
            LifeEntity.id, LifeEntity.entity_type, func.min(LifeEntityAlias.alias_name).label("display_name")
        ).group_by(LifeEntity.id, LifeEntity.entity_type)
        total = db.scalar(select(func.count()).select_from(summaries.subquery())) or 0
        items = db.execute(
            summaries.order_by(LifeEntity.id).offset((page - 1) * page_size).limit(page_size)
        ).mappings()
        return {"items": list(items), "page": page, "page_size": page_size, "total": total}

    def get_entity(self, db: Session, user: User, session_id: int, entity_id: int) -> dict:
        self.memories._session_access(db, user.id, session_id)
        query = self._evidence_query().where(
            MemorySession.id == session_id, LifeEntity.id == entity_id,
        ).with_only_columns(
            LifeEntity.entity_type, LifeEntityAlias.alias_name, MemoryElement.id,
            MemoryEntityEvidence.evidence,
        ).order_by(LifeEntityAlias.alias_name, MemoryElement.id, MemoryEntityEvidence.id)
        rows = db.execute(query).all()
        if not rows:
            raise HTTPException(404, "实体不存在或无当前会话证据")
        return {
            "id": entity_id, "entity_type": rows[0][0], "display_name": rows[0][1],
            "aliases": sorted({row[1] for row in rows}),
            "evidence": [{"element_id": row[2], **row[3]} for row in rows],
        }

    @staticmethod
    def _candidates(session_id: int):
        return (
            select(MemoryEntityCandidate)
            .join(MemoryElement, MemoryElement.id == MemoryEntityCandidate.element_id)
            .join(MemoryFragment, MemoryFragment.id == MemoryElement.source_fragment_id)
            .where(
                MemoryElement.session_id == session_id,
                MemoryElement.is_del.is_(False), MemoryFragment.is_del.is_(False),
                MemoryEntityCandidate.is_del.is_(False),
            )
        )

    def list_candidates(
        self, db: Session, user: User, session_id: int, page: int, page_size: int, element_id: int | None
    ) -> dict:
        memory, role = self.memories._session_access(db, user.id, session_id)
        if role != "owner":
            raise HTTPException(403, "只有所有者可以查看实体候选")
        query = self._candidates(session_id).where(MemoryEntityCandidate.status == "pending")
        if element_id is not None:
            query = query.where(MemoryEntityCandidate.element_id == element_id)
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = []
        for row in db.scalars(query.order_by(MemoryEntityCandidate.id).offset((page - 1) * page_size).limit(page_size)):
            entities = db.scalars(
                self._owner_entities(memory.owner_user_id).where(LifeEntity.id.in_(row.candidate_entity_ids))
            )
            items.append({
                "id": row.id, "revision": row.revision, "mention_text": row.mention_text,
                "element_id": row.element_id, "entity_type": row.entity_type,
                "narrator_user_id": row.narrator_user_id,
                "evidence": row.evidence, "candidate_entities": [
                    {"id": entity.id, "display_name": entity.canonical_name}
                    for entity in entities if self._supported(db, entity.id)
                ],
            })
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    @staticmethod
    def _select_location(db: Session, candidate: MemoryEntityCandidate, index: int | None) -> None:
        if candidate.evidence.get("location_status") != "ambiguous":
            if index is not None and index != candidate.evidence.get("selected_location_index"):
                raise HTTPException(422, "候选没有该待确认位置")
            return
        choices = candidate.evidence["possible_locations"]
        if index is None or index >= len(choices):
            raise HTTPException(422, "请选择有效的引句位置")
        candidate.evidence = {
            **candidate.evidence,
            "location_status": "confirmed",
            "selected_location_index": index,
            "draft_references": choices[index]["draft_references"],
        }
        candidate.narrator_user_id = MemoryEntityService._registered_narrator(
            db, MemoryEntityService.reference_narrator(choices[index]["draft_references"])
        )

    def resolve(
        self, db: Session, user: User, session_id: int, candidate_id: int, body: CandidateResolutionCreate
    ) -> dict:
        with db.begin():
            memory, role = self.memories._session_access(db, user.id, session_id, lock=True)
            if role != "owner":
                raise HTTPException(403, "只有所有者可以确认实体")
            self._lock_owner(db, memory.owner_user_id)
            candidate = db.scalar(self._candidates(session_id).where(MemoryEntityCandidate.id == candidate_id))
            if candidate is None:
                raise HTTPException(409, "候选已失效或不存在")
            # [修改] 沿用已有请求指纹，只有选了位置时才加入新字段。
            excluded_fields = (
                {"evidence_location_index"} if body.evidence_location_index is None else set()
            )
            payload = body.model_dump(exclude=excluded_fields)
            request_hash = fingerprint(payload)
            resolution = db.scalar(select(MemoryEntityResolution).where(
                MemoryEntityResolution.candidate_id == candidate.id,
                MemoryEntityResolution.candidate_revision == body.expected_revision,
            ))
            if resolution is not None and resolution.request_hash != request_hash:
                raise HTTPException(409, "候选版本已处理")
            if resolution is None:
                if candidate.revision != body.expected_revision or candidate.status != "pending":
                    raise HTTPException(409, "候选版本已变化")
                self._select_location(db, candidate, body.evidence_location_index)
                element = db.get(MemoryElement, candidate.element_id)
                if body.action == "create_new":
                    target = self._create(db, user.id, candidate, body.canonical_name, None)
                else:
                    target = db.scalar(self._owner_entities(user.id).where(
                        LifeEntity.id == body.entity_id, LifeEntity.entity_type == candidate.entity_type,
                    ))
                    if target is None:
                        raise HTTPException(404, "目标实体不存在")
                if body.action == "reject_match":
                    if target.id not in candidate.candidate_entity_ids:
                        raise HTTPException(409, "所选实体不在候选中")
                    candidate.candidate_entity_ids = [
                        item for item in candidate.candidate_entity_ids if item != target.id
                    ]
                else:
                    self._link(db, user.id, element, target, candidate)
                    candidate.status = "resolved"
                candidate.revision += 1
                candidate.updated_by = user.id
                resolution = MemoryEntityResolution(
                    candidate_id=candidate.id, candidate_revision=body.expected_revision,
                    action=body.action, entity_id=target.id, request_hash=request_hash,
                    created_by=user.id, updated_by=user.id,
                )
                db.add(resolution)
                db.flush()
            return {
                "id": resolution.id, "action": resolution.action, "entity_id": resolution.entity_id,
                "candidate_id": candidate.id, "revision": candidate.revision,
                "status": candidate.status, "entities": self.element_refs(db, candidate.element_id),
            }
