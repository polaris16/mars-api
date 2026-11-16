"""数据库模型；新增模型后需在此导入，供 Alembic 发现。"""

from app.models.base import Base
from app.models.auth_session import AuthSession
from app.models.interest_domain import InterestDomain
from app.models.t_user import TUser
from app.models.topic import Topic
from app.models.topic_interest import TopicInterest
from app.models.topic_participant import TopicParticipant
from app.models.user_interest import UserInterest

__all__ = ["Base", "TUser", "AuthSession", "InterestDomain", "UserInterest", "Topic", "TopicInterest", "TopicParticipant"]
