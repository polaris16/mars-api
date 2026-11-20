"""[新增] 图谱时间线、实体、节点关系和证据接口。"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.memory_graph import (
    EntityType, GraphEntityPage, GraphEvidencePage, GraphNeighborhood, GraphNodeResponse,
    NodeId, Relation, TimelinePage,
)
from app.services.memory_graph_service import MemoryGraphService

router = APIRouter(prefix="/memory-graph", tags=["memory graph"])
Db = Annotated[Session, Depends(get_db)]
Graph = Annotated[MemoryGraphService, Depends(MemoryGraphService)]
SessionId = Annotated[int, Query(gt=0, le=2**63 - 1)]
Page = Annotated[int, Query(ge=1, le=2**31 - 1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/timeline", response_model=TimelinePage, summary="查看回忆录时间线")
def list_memory_graph_timeline(
    user: CurrentUser, db: Db, service: Graph, session_id: SessionId | None = None,
    page: Page = 1, page_size: PageSize = 20, from_date: date | None = None,
    to_date: date | None = None, entity_node_id: NodeId | None = None,
    include_undated: bool | None = None,
):
    return service.timeline(
        db, user, session_id, page, page_size, from_date=from_date, to_date=to_date,
        entity_node_id=entity_node_id, include_undated=include_undated,
    )


@router.get("/entities", response_model=GraphEntityPage, summary="查看图谱实体目录")
def list_memory_graph_entities(
    user: CurrentUser, db: Db, service: Graph, session_id: SessionId | None = None,
    page: Page = 1, page_size: PageSize = 20, entity_type: EntityType | None = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
):
    return service.entities(db, user, session_id, page, page_size, entity_type=entity_type, keyword=keyword)


@router.get("/nodes/{node_id}", response_model=GraphNodeResponse, summary="查看图谱节点详情")
def get_memory_graph_node(
    node_id: NodeId, user: CurrentUser, db: Db, service: Graph, session_id: SessionId | None = None,
):
    return service.node(db, user, session_id, node_id)


@router.get("/neighborhood", response_model=GraphNeighborhood, summary="查看图谱节点的关系邻域")
def get_memory_graph_neighborhood(
    node_id: NodeId, user: CurrentUser, db: Db, service: Graph, session_id: SessionId | None = None,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
):
    return service.neighborhood(db, user, session_id, node_id, depth)


@router.get("/evidence", response_model=GraphEvidencePage, summary="查看图谱节点或关系的证据")
def list_memory_graph_evidence(
    user: CurrentUser, db: Db, service: Graph, session_id: SessionId | None = None,
    page: Page = 1, page_size: PageSize = 20, node_id: NodeId | None = None,
    from_node_id: NodeId | None = None, to_node_id: NodeId | None = None,
    relation_type: Relation | None = None,
):
    return service.evidence(
        db, user, session_id, page, page_size, node_id=node_id, from_node_id=from_node_id,
        to_node_id=to_node_id, relation_type=relation_type,
    )
