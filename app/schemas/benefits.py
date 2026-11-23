"""[新增] 企业权益、领取和预约的接口请求。"""

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Id = Annotated[int, Field(gt=0, le=2**63 - 1)]
PositiveCount = Annotated[int, Field(gt=0, le=2**31 - 1)]
OrderIndex = Annotated[int, Field(ge=0, le=2**31 - 1)]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,49}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Ref = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
# 内容寻址的受控文档，既可在本地 Mock 存储，也可在私有对象存储预置。
DocumentRef = Annotated[str, StringConstraints(pattern=r"^benefits/documents/[a-f0-9]{64}\.txt$")]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9]{7,15}$")]
ShortReason = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
EnterpriseRole = Literal["admin", "issuer", "support"]
StaffRole = Literal["admin", "ops", "dispatcher", "provider"]
OrderStatus = Literal["requested", "reserved", "in_progress", "completed", "cancelled", "failed"]
GrantStatus = Literal["issued", "claimed", "voided", "expired"]
OrderAction = Literal["confirm_booking", "reschedule", "start", "complete", "cancel", "fail"]
# [新增] 运营目录按各资源的公开字段接受显式排序。
BenefitServiceSort = Literal["default", "code_asc", "code_desc"]
BenefitVersionSort = Literal["default", "version_asc", "version_desc"]
BenefitPackageSort = Literal["default", "title_asc", "title_desc"]
# [新增] 对普通业务列表公开各自可排序的字段。
BenefitNameSort = Literal["default", "name_asc", "name_desc"]
BenefitPlanSort = Literal["default", "title_asc", "title_desc"]
BenefitStaffSort = Literal["default", "role_asc", "role_desc"]
BenefitPurchaseSort = Literal["default", "created_at_asc", "created_at_desc"]
BenefitInvitationSort = Literal["default", "expires_at_asc", "expires_at_desc"]
# [新增] 未预约的服务单在按预约时间查询时置尾。
BenefitOrderSort = Literal[
    "default",
    "created_at_asc",
    "created_at_desc",
    "scheduled_start_at_asc",
    "scheduled_start_at_desc",
]


class BenefitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


class EnterpriseCreate(BenefitInput):
    code: Code
    display_name: Name
    icon_url: Ref | None = None
    admin_user_id: Id


class EnterprisePatch(BenefitInput):
    display_name: Name | None = None
    icon_url: Ref | None = None
    status: Literal["active", "suspended"] | None = None


class MemberPatch(BenefitInput):
    role: EnterpriseRole | None = None
    status: Literal["active", "disabled"] | None = None
    work_display_name: Name | None = None
    work_title: Name | None = None
    work_phone: Phone | None = None


class MemberCreate(MemberPatch):
    user_id: Id
    role: EnterpriseRole
    status: Literal["active", "disabled"] = "active"


class StaffReplace(BenefitInput):
    roles: set[StaffRole]


class ServiceCreate(BenefitInput):
    code: Code


class ServicePatch(BenefitInput):
    status: Literal["active", "disabled"]


class ServiceVersionInput(BenefitInput):
    name: Name
    summary: Annotated[str, StringConstraints(max_length=300)] | None = None
    description_ref: DocumentRef
    terms_ref: DocumentRef
    delivery_terms: Annotated[str, StringConstraints(min_length=1, max_length=5000)]
    fulfillment_mode: Literal["online", "appointment"]


class PackageItemInput(BenefitInput):
    service_version_id: Id
    quota_per_unit: PositiveCount
    icon_key: Code | None = None
    sort_order: OrderIndex = 0


class PackageInput(BenefitInput):
    title: Name
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    icon_url: Ref | None = None
    items: list[PackageItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.service_version_id for item in self.items}) != len(self.items):
            raise ValueError("同一计划版本中的服务版本不能重复")
        return self


class PackageCreate(PackageInput):
    package_code: Code


class PurchaseDraft(BenefitInput):
    enterprise_id: Id
    package_id: Id
    purchase_reference: Name
    purchase_contract_ref: DocumentRef | None = None
    purchased_units: PositiveCount
    paid_amount_minor: Id
    currency: Literal["CNY"] = "CNY"
    issue_until: AwareDatetime
    claim_until: AwareDatetime
    service_valid_from: AwareDatetime
    service_valid_until: AwareDatetime
    display_timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)] = (
        "Asia/Shanghai"
    )

    @model_validator(mode="after")
    def valid_windows(self):
        if not (
            self.issue_until <= self.claim_until <= self.service_valid_until
            and self.service_valid_from < self.service_valid_until
        ):
            raise ValueError("发放、领取或服务有效期不符合顺序")
        try:
            ZoneInfo(self.display_timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("展示时区无效") from exc
        return self


class PurchaseActivate(BenefitInput):
    paid_at: AwareDatetime


class IssueItem(BenefitInput):
    recipient_phone: Phone
    claim_expires_at: AwareDatetime | None = None


class IssueBatch(BenefitInput):
    items: list[IssueItem] = Field(min_length=1, max_length=100)


class GrantVoid(BenefitInput):
    reason: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ClaimInput(BenefitInput):
    grant_id: Id | None = None
    code: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def one_credential(self):
        if (self.grant_id is None) == (self.code is None):
            raise ValueError("请提供一个待领取 ID 或权益码")
        return self


class ContactReplace(BenefitInput):
    member_ids: list[Id] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_members(self):
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("联系人不能重复")
        return self


class OrderCreate(BenefitInput):
    memory_session_id: Id | None = None
    preferred_start_at: AwareDatetime | None = None
    preferred_end_at: AwareDatetime | None = None
    contact_phone: Phone | None = None
    address: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None

    @model_validator(mode="after")
    def valid_preference(self):
        start, end = self.preferred_start_at, self.preferred_end_at
        if (start is None) != (end is None) or (start is not None and end <= start):
            raise ValueError("期望时段需成对填写且结束时间晚于开始时间")
        return self


class OrderCancel(BenefitInput):
    expected_version: PositiveCount
    reason: ShortReason


class OrderActionInput(BenefitInput):
    expected_version: PositiveCount
    action: OrderAction
    assignee_user_id: Id | None = None
    scheduled_start_at: AwareDatetime | None = None
    scheduled_end_at: AwareDatetime | None = None
    result_ref: (
        Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,199}$")] | None
    ) = None
    reason: ShortReason | None = None

    @model_validator(mode="after")
    def action_fields(self):
        booking = (self.assignee_user_id, self.scheduled_start_at, self.scheduled_end_at)
        if self.action in {"confirm_booking", "reschedule"}:
            if any(value is None for value in booking) or booking[2] <= booking[1]:
                raise ValueError("需指定履约人和有效预约时段")
        elif any(value is not None for value in booking):
            raise ValueError("此操作不接受预约字段")
        if (self.action == "complete") != (self.result_ref is not None):
            raise ValueError("完成服务时须填写履约记录引用")
        if (self.action in {"cancel", "fail"}) != (self.reason is not None):
            raise ValueError("取消或失败时须填写原因码")
        return self


T = TypeVar("T")


class BenefitPage(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class EnterpriseSummary(BaseModel):
    id: int
    code: str
    display_name: str
    icon_url: str | None
    status: str


class BenefitEnterpriseView(EnterpriseSummary):
    plan_count: int
    card_count: int
    status_counts: dict[str, int]


class BenefitContactView(BaseModel):
    id: int
    work_display_name: str | None
    work_title: str | None
    work_phone: str


class BenefitCardView(BaseModel):
    available_balance_id: int | None
    service_code: str
    service_version_id: int
    icon_key: str | None
    name: str
    summary: str | None
    description_ref: str
    terms_ref: str
    fulfillment_mode: Literal["online", "appointment"]
    valid_from: datetime
    valid_until: datetime
    display_timezone: str
    granted_count: int
    reserved_count: int
    used_count: int
    remaining_count: int
    status: Literal["not_started", "expired", "exhausted", "reserved_only", "available"]


class BenefitGrantCardView(BenefitCardView):
    balance_id: int


class BenefitPlanCardView(BenefitCardView):
    sort_order: int


class BenefitGrantView(BaseModel):
    grant_id: int
    plan_name: str
    package_id: int
    enterprise: EnterpriseSummary
    claimed_at: datetime
    valid_from: datetime
    valid_until: datetime
    display_timezone: str
    contacts: list[BenefitContactView]
    cards: list[BenefitGrantCardView]


class BenefitPlanView(BaseModel):
    enterprise: EnterpriseSummary
    package_id: int
    plan_name: str
    icon_url: str | None
    grant_ids: list[int]
    cards: list[BenefitPlanCardView]
    contacts: list[BenefitContactView]


class BenefitInvitationItem(BaseModel):
    service_version_id: int
    quota_per_unit: int
    icon_key: str | None


class BenefitInvitationView(BaseModel):
    grant_id: int
    enterprise: EnterpriseSummary
    plan_name: str
    claim_expires_at: datetime
    items: list[BenefitInvitationItem]


class BenefitIssuedItem(BaseModel):
    grant_id: int
    code: str | None
    status: GrantStatus
    claim_expires_at: datetime


class BenefitIssueView(BaseModel):
    issue_request_id: int
    items: list[BenefitIssuedItem]


class BenefitOrderView(BaseModel):
    id: int
    balance_id: int
    status: OrderStatus
    version: int
    memory_session_id: int | None
    assignee_user_id: int | None
    scheduled_start_at: datetime | None
    scheduled_end_at: datetime | None
    reserved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    result_ref: str | None
    terminal_reason: str | None
    created_at: datetime
    request_data: dict
    remaining_count: int
    contact: dict[str, str] | None = None


class BenefitEventView(BaseModel):
    id: int
    order_version: int
    action: str
    from_status: OrderStatus | None
    to_status: OrderStatus
    reserved_delta: int
    used_delta: int
    event_data: dict


class BenefitActionView(BaseModel):
    order: BenefitOrderView
    event: BenefitEventView
