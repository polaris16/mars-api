"""[新增] 时间线、节点关系和证据的公开响应。"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

NodeId = Annotated[
    str, StringConstraints(pattern=r"^(event|entity|element|mention):[1-9][0-9]{0,18}$")
]
EntityType = Literal["person", "place", "organization", "time_period"]
# [新增] 时间线支持按时间方向查看，证据支持按公开证据标识排序。
TimelineSort = Literal["default", "time_asc", "time_desc"]
GraphEvidenceSort = Literal["default", "evidence_id_asc", "evidence_id_desc"]
Relation = Literal[
    "subject",
    "participant",
    "location",
    "departure_location",
    "destination_location",
    "mentioned",
    "time_period",
    "before",
]


class GraphScope(BaseModel):
    view_scope: Literal["memoir", "session"]
    session_id: int | None


class GraphPage(GraphScope):
    page: int
    page_size: int
    total: int


class GraphTime(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    exact_at: datetime | None = None
    precision: Literal["instant", "day", "month", "year", "range", "open_range", "unknown"] = "unknown"
    origin: Literal["source", "review_answer", "unknown"] = "unknown"


class TimelineItem(BaseModel):
    node_id: NodeId
    element_type: Literal["event", "fact", "life_stage", "other"]
    title: str
    time: GraphTime
    evidence_count: int


class TimelinePage(GraphPage):
    items: list[TimelineItem]


class EntityItem(BaseModel):
    node_id: NodeId
    entity_type: EntityType
    display_name: str
    related_event_count: int


class GraphEntityPage(GraphPage):
    items: list[EntityItem]


class GraphPlace(BaseModel):
    node_id: NodeId
    display_name: str
    role: Literal["location", "departure_location", "destination_location"]


class EventNode(TimelineItem):
    node_type: Literal["event"] = "event"
    places: list[GraphPlace] = Field(default_factory=list)


class EntityNode(EntityItem):
    node_type: Literal["entity"] = "entity"
    aliases: list[str] = Field(default_factory=list)


GraphNode = Annotated[EventNode | EntityNode, Field(discriminator="node_type")]


class GraphNodeResponse(GraphScope):
    node: GraphNode


class GraphEdge(BaseModel):
    from_node_id: NodeId
    to_node_id: NodeId
    relation_type: Relation
    evidence_count: int


class GraphNeighborhood(GraphScope):
    root_node_id: NodeId
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


class GraphEvidence(BaseModel):
    evidence_id: str
    source_type: Literal["element", "entity_mention", "review_answer"]
    excerpt: str | None = None
    session_id: int | None = None
    fragment_id: int | None = None
    element_id: int | None = None
    source_revision_id: int | None = None
    draft_version_id: int | None = None
    draft_references: list[dict] = Field(default_factory=list)
    answer_id: int | None = None


class GraphEvidencePage(GraphPage):
    items: list[GraphEvidence]
