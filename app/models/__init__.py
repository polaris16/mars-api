"""数据库模型；新增模型后需在此导入，供 Alembic 发现。"""

from app.models.base import Base
from app.models.auth_session import AuthSession
from app.models.interest_domain import InterestDomain
# [新增] 录音、语音片段及可复用媒体对象模型，供 Alembic 加载。
from app.models.fragment_attachment import FragmentAttachment
from app.models.media_asset import MediaAsset
from app.models.memory_fragment import MemoryFragment
from app.models.recording_session import RecordingSession
from app.models.t_user import TUser
from app.models.topic import Topic
from app.models.topic_interest import TopicInterest
from app.models.topic_participant import TopicParticipant
from app.models.user_interest import UserInterest

# [修改-旧] 原有导出列表；保留旧代码，新增模型需一并导出。
# __all__ = ["Base", "TUser", "AuthSession", "InterestDomain", "UserInterest", "Topic", "TopicInterest", "TopicParticipant"]
# [新增] 将录音与媒体模型注册到公开导出列表。
__all__ = [
    "Base",
    "TUser",
    "AuthSession",
    "InterestDomain",
    "UserInterest",
    "Topic",
    "TopicInterest",
    "TopicParticipant",
    "RecordingSession",
    "MediaAsset",
    "MemoryFragment",
    "FragmentAttachment",
]
