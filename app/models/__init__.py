"""数据库模型；新增模型后需在此导入，供 Alembic 发现。"""

from app.models.base import Base
from app.models.t_auth_session import AuthSession
from app.models.t_interest_domain import InterestDomain
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
from app.models.t_memory_source_revision import MemorySourceRevision
from app.models.t_memory_draft_version import MemoryDraftVersion
from app.models.t_memory_entity_candidate import MemoryEntityCandidate
from app.models.t_memory_entity_resolution import MemoryEntityResolution
from app.models.t_memory_entity_evidence import MemoryEntityEvidence

# [新增] 第四阶段核验、冲突证据及问答表。
from app.models.t_memory_conflict_group import MemoryConflictGroup
from app.models.t_memory_conflict_evidence import MemoryConflictEvidence
from app.models.t_memory_review_question import MemoryReviewQuestion
from app.models.t_memory_review_answer import MemoryReviewAnswer
from app.models.t_memory_validation_job import MemoryValidationJob

# [新增] 第五阶段图谱事件及版本化成员表。
from app.models.t_memory_graph_event import MemoryGraphEvent
from app.models.t_memory_graph_event_element import MemoryGraphEventElement

# [新增] 企业采购、权益发放领取和履约记账模型。
from app.models.t_enterprise import Enterprise
from app.models.t_enterprise_member import EnterpriseMember
from app.models.t_benefit_staff import BenefitStaff
from app.models.t_benefit_service import BenefitService
from app.models.t_benefit_service_version import BenefitServiceVersion
from app.models.t_benefit_package import BenefitPackage
from app.models.t_benefit_package_item import BenefitPackageItem
from app.models.t_benefit_purchase_batch import BenefitPurchaseBatch
from app.models.t_benefit_batch_contact import BenefitBatchContact
from app.models.t_benefit_issue_request import BenefitIssueRequest
from app.models.t_benefit_grant import BenefitGrant
from app.models.t_benefit_balance import BenefitBalance
from app.models.t_benefit_service_order import BenefitServiceOrder
from app.models.t_benefit_order_event import BenefitOrderEvent

# [新增] 产品目录、生成预览、作品和好友分享模型。
from app.models.t_product_category import ProductCategory
from app.models.t_product import Product
from app.models.t_product_version import ProductVersion
from app.models.t_product_image import ProductImage
from app.models.t_product_experience_round import ProductExperienceRound
from app.models.t_product_experience_preview import ProductExperiencePreview
from app.models.t_product_experience_result import ProductExperienceResult
from app.models.t_product_experience_share import ProductExperienceShare
from app.models.t_youth_partner_profile import YouthPartnerProfile
from app.models.t_youth_partner_service import YouthPartnerService
from app.models.t_youth_partner_identity_document import YouthPartnerIdentityDocument
from app.models.t_youth_partner_identity_access import YouthPartnerIdentityAccess
from app.models.t_youth_partner_availability import YouthPartnerAvailability
from app.models.t_benefit_grant_service_location import BenefitGrantServiceLocation
from app.models.t_youth_partner_booking import YouthPartnerBooking
from app.models.t_youth_partner_check_in_code import YouthPartnerCheckInCode
from app.models.t_youth_partner_rating import YouthPartnerRating

# [修改-旧] 原有导出列表；保留旧代码，新增模型需一并导出。
# __all__ = ["Base", "User", "AuthSession", "InterestDomain", "UserInterest", "Topic", "TopicInterest", "TopicParticipant"]
# 将录音与媒体模型注册到公开导出列表。
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
    "MemorySourceRevision",
    "MemoryDraftVersion",
    "MemoryEntityCandidate",
    "MemoryEntityResolution",
    "MemoryEntityEvidence",
    # [新增] 冲突核对与回忆录级复验模型。
    "MemoryConflictGroup",
    "MemoryConflictEvidence",
    "MemoryReviewQuestion",
    "MemoryReviewAnswer",
    "MemoryValidationJob",
    # [新增] 图谱事件节点和成员证据。
    "MemoryGraphEvent",
    "MemoryGraphEventElement",
    # [新增] 企业组织和权益领域模型。
    "Enterprise",
    "EnterpriseMember",
    "BenefitStaff",
    "BenefitService",
    "BenefitServiceVersion",
    "BenefitPackage",
    "BenefitPackageItem",
    "BenefitPurchaseBatch",
    "BenefitBatchContact",
    "BenefitIssueRequest",
    "BenefitGrant",
    "BenefitBalance",
    "BenefitServiceOrder",
    "BenefitOrderEvent",
    # [新增] 产品体验模型。
    "ProductCategory",
    "Product",
    "ProductVersion",
    "ProductImage",
    "ProductExperienceRound",
    "ProductExperiencePreview",
    "ProductExperienceResult",
    "ProductExperienceShare",
    "YouthPartnerProfile",
    "YouthPartnerService",
    "YouthPartnerIdentityDocument",
    "YouthPartnerIdentityAccess",
    "YouthPartnerAvailability",
    "BenefitGrantServiceLocation",
    "YouthPartnerBooking",
    "YouthPartnerCheckInCode",
    "YouthPartnerRating",
]
