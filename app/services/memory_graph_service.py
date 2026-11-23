"""[新增] 提供授权范围内的图谱分页、邻域和证据查询。"""

from collections import defaultdict, deque
from datetime import date

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import User
from app.schemas.list_sort import NameSort
from app.schemas.memory_graph import (
    EntityNode,
    EventNode,
    GraphEdge,
    GraphEvidenceSort,
    TimelineSort,
)
from app.services.memory_graph_evidence import locate_records
from app.services.memory_graph_view import EdgeKey, GraphView, memoir_view, session_view
from app.services.memory_service import MemoryService
from app.services.memory_validation_rules import current_records

MAX_NEIGHBOR_NODES = 80
MAX_NEIGHBOR_EDGES = 200


def _node_order(node_id: str) -> tuple[str, int]:
    kind, number = node_id.split(":")
    return kind, int(number)


class MemoryGraphService:
    def _view(self, db: Session, user: User, session_id: int | None) -> GraphView:
        # 数据、授权和统计在同一个只读快照计算；鉴权阶段已结束其事务。
        with db.begin():
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            if user.is_del:
                raise HTTPException(401, "用户不存在或已停用")
            if session_id is None:
                return memoir_view(db, user.id, locate_records(current_records(db, user.id)))
            memory, _ = MemoryService()._session_access(db, user.id, session_id)
            records = current_records(db, memory.owner_user_id, session_id=session_id)
            return session_view(db, session_id, locate_records(records))

    @staticmethod
    def _node(view: GraphView, node_id: str):
        node = view.nodes.get(node_id)
        if node is None:
            raise HTTPException(404, "图谱节点不存在或无当前可见证据")
        return node

    @staticmethod
    def _page(view: GraphView, items: list, page: int, page_size: int) -> dict:
        offset = (page - 1) * page_size
        return {
            **view.scope,
            "page": page,
            "page_size": page_size,
            "total": len(items),
            "items": items[offset : offset + page_size],
        }

    def timeline(
        self,
        db: Session,
        user: User,
        session_id: int | None,
        page: int,
        page_size: int,
        *,
        from_date: date | None,
        to_date: date | None,
        entity_node_id: str | None,
        include_undated: bool | None,
        sort: TimelineSort = "default",
    ) -> dict:
        if from_date is not None and to_date is not None and from_date > to_date:
            raise HTTPException(422, "起始日期不能晚于结束日期")
        view = self._view(db, user, session_id)
        related = None
        if entity_node_id is not None:
            if not isinstance(self._node(view, entity_node_id), EntityNode):
                raise HTTPException(422, "请提供实体节点 ID")
            related = {source for source, target, _ in view.edges if target == entity_node_id}
        if include_undated is None:
            include_undated = from_date is None and to_date is None
        items = []
        for node in view.nodes.values():
            if not isinstance(node, EventNode) or (
                related is not None and node.node_id not in related
            ):
                continue
            start, end = node.time.start_date, node.time.end_date
            if (
                start is None
                and end is None
                and not include_undated
                or from_date is not None
                and end is not None
                and end < from_date
                or to_date is not None
                and start is not None
                and start > to_date
            ):
                continue
            items.append(node)

        # [删除-旧] items.sort(key=lambda node: (
        #     node.time.precision == "unknown", node.time.start_date or node.time.end_date or date.max,
        #     _node_order(node.node_id),
        # ))
        # [新增] 只改变有时间锚点的排序方向，未知时间仍放在最后。
        def order(node: EventNode) -> tuple[bool, int, tuple[str, int]]:
            anchor = node.time.start_date or node.time.end_date
            if anchor is None:
                ordinal = date.max.toordinal()
            else:
                ordinal = anchor.toordinal()
                if sort == "time_desc":
                    ordinal = -ordinal
            return node.time.precision == "unknown", ordinal, _node_order(node.node_id)

        items.sort(key=order)
        return self._page(view, items, page, page_size)

    def entities(
        self,
        db: Session,
        user: User,
        session_id: int | None,
        page: int,
        page_size: int,
        *,
        entity_type: str | None,
        keyword: str | None,
        sort: NameSort = "default",
    ) -> dict:
        view = self._view(db, user, session_id)
        query = keyword.strip().casefold() if keyword else ""
        items = [
            node
            for node in view.nodes.values()
            if isinstance(node, EntityNode)
            and (entity_type is None or node.entity_type == entity_type)
            and any(query in value.casefold() for value in (node.display_name, *node.aliases))
        ]
        items.sort(key=lambda node: (node.display_name.casefold(), _node_order(node.node_id)))
        # [新增] Python 的稳定排序让同名节点在降序时仍按节点 ID 排列。
        if sort == "name_desc":
            items.sort(key=lambda node: node.display_name.casefold(), reverse=True)
        return self._page(view, items, page, page_size)

    def node(self, db: Session, user: User, session_id: int | None, node_id: str) -> dict:
        view = self._view(db, user, session_id)
        return {**view.scope, "node": self._node(view, node_id)}

    def _edge_proofs(self, view: GraphView, key: EdgeKey):
        source, target, relation = key
        left, right = self._node(view, source), self._node(view, target)
        if relation == "before" and isinstance(left, EventNode) and isinstance(right, EventNode):
            end, start = left.time.end_date, right.time.start_date
            if end is not None and start is not None and end < start:
                return {**view.time_proofs[source], **view.time_proofs[target]}
        proofs = view.edges.get(key)
        if not proofs:
            raise HTTPException(404, "图谱关系不存在或无当前可见证据")
        return proofs

    @staticmethod
    def _time_neighbors(node: EventNode, events: list[EventNode]) -> list[EdgeKey]:
        """邻域只带临近且可证明的时间关系。"""
        incoming = max(
            (item for item in events if item.time.end_date is not None
             and node.time.start_date is not None and item.time.end_date < node.time.start_date),
            key=lambda item: (item.time.end_date, _node_order(item.node_id)), default=None,
        )
        outgoing = min(
            (item for item in events if item.time.start_date is not None
             and node.time.end_date is not None and node.time.end_date < item.time.start_date),
            key=lambda item: (item.time.start_date, _node_order(item.node_id)), default=None,
        )
        result = []
        if incoming is not None:
            result.append((incoming.node_id, node.node_id, "before"))
        if outgoing is not None:
            result.append((node.node_id, outgoing.node_id, "before"))
        return result

    def neighborhood(
        self, db: Session, user: User, session_id: int | None, node_id: str, depth: int,
    ) -> dict:
        view = self._view(db, user, session_id)
        root = self._node(view, node_id)
        by_node = defaultdict(list)
        for key in view.edges:
            by_node[key[0]].append(key)
            by_node[key[1]].append(key)
        events = [node for node in view.nodes.values() if isinstance(node, EventNode)]
        nodes = {node_id: root}
        edges = {}
        pending = deque([(node_id, 0)])
        truncated = False
        while pending:
            current, distance = pending.popleft()
            if distance == depth:
                continue
            incident = list(by_node[current])
            if isinstance(nodes[current], EventNode):
                incident.extend(self._time_neighbors(nodes[current], events))
            for key in sorted(incident, key=lambda item: (_node_order(item[0]), _node_order(item[1]), item[2])):
                if key in edges:
                    continue
                source, target, relation = key
                other = target if current == source else source
                if len(edges) == MAX_NEIGHBOR_EDGES or (other not in nodes and len(nodes) == MAX_NEIGHBOR_NODES):
                    truncated = True
                    continue
                if other not in nodes:
                    nodes[other] = view.nodes[other]
                    pending.append((other, distance + 1))
                edges[key] = GraphEdge(
                    from_node_id=source,
                    to_node_id=target,
                    relation_type=relation,
                    evidence_count=len(self._edge_proofs(view, key)),
                )
        return {
            **view.scope,
            "root_node_id": node_id,
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "truncated": truncated,
        }

    def evidence(
        self,
        db: Session,
        user: User,
        session_id: int | None,
        page: int,
        page_size: int,
        *,
        node_id: str | None,
        from_node_id: str | None,
        to_node_id: str | None,
        relation_type: str | None,
        sort: GraphEvidenceSort = "default",
    ) -> dict:
        edge_parameters = (from_node_id, to_node_id, relation_type)
        if ((node_id is None and not all(edge_parameters))
                or (node_id is not None and any(edge_parameters))):
            raise HTTPException(422, "请提供节点 ID，或完整的关系起点、终点和类型")
        view = self._view(db, user, session_id)
        if node_id is not None:
            self._node(view, node_id)
            proofs = view.proofs[node_id]
        else:
            proofs = self._edge_proofs(view, edge_parameters)
        # [删除-旧] items = sorted(proofs.values(), key=lambda item: item.evidence_id)
        items = sorted(
            proofs.values(), key=lambda item: item.evidence_id, reverse=sort == "evidence_id_desc"
        )
        return self._page(view, items, page, page_size)
