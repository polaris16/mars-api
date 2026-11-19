"""数据库模型；新增模型后需在此导入，供 Alembic 发现。"""

from app.models.base import Base
from app.models.t_auth_session import AuthSession
from app.models.t_interest_domain import InterestDomain
# [新增] 录音、语音片段及可复用媒体对象模型，供 Alembic 加载。
from app.models.t_fragment_attachment import FragmentAttachment
from app.models.t_media_asset import MediaAsset
from app.models.t_memory_fragment import MemoryFragment
from app.models.t_memory_contributor import MemoryContributor
from app.models.t_memory_session import MemorySession
from app.models.t_memory_session_member import MemorySessionMember
from app.models.t_user import User
from app.models.t_topic import Topic
from app.models.t_topic_interest import TopicInterest
from app.models.t_topic_participant import TopicParticipant
from app.models.t_user_interest import UserInterest
from app.models.t_user_relationship import UserRelationship
from app.models.t_ai_processing_job import AIProcessingJob
from app.models.t_memory_element import MemoryElement
from app.models.t_life_entity import LifeEntity
from app.models.t_life_entity_alias import LifeEntityAlias
from app.models.t_memory_element_entity import MemoryElementEntity
from app.models.t_memory_conflict import MemoryConflict

# [修改-旧] 原有导出列表；保留旧代码，新增模型需一并导出。
# __all__ = ["Base", "User", "AuthSession", "InterestDomain", "UserInterest", "Topic", "TopicInterest", "TopicParticipant"]
# [新增] 将录音与媒体模型注册到公开导出列表。
__all__ = [
    "Base",
    "User",
    "AuthSession",
    "InterestDomain",
    "UserInterest",
    "Topic",
    "TopicInterest",
    "TopicParticipant",
    "MemorySession",
    "MemorySessionMember",
    "MemoryContributor",
    "UserRelationship",
    "MediaAsset",
    "MemoryFragment",
    "FragmentAttachment",
    "AIProcessingJob",
    "MemoryElement",
    "LifeEntity",
    "LifeEntityAlias",
    "MemoryElementEntity",
    "MemoryConflict",
]
