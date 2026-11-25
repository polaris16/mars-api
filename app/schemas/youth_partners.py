"""[新增] 青年伙伴注册、排班、预约及核销契约。"""

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, model_validator

from app.schemas.benefits import BenefitInput, Id, Name, OrderIndex, ShortReason
from app.schemas.product_experience import ImageUpload

Label = Annotated[str, StringConstraints(min_length=1, max_length=32)]
Note = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Side = Literal["portrait", "emblem"]
PartnerStatus = Literal["draft", "pending_review", "approved", "rejected", "disabled"]


def zone_for(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("请选择有效的 IANA 时区") from exc


class PartnerPublicDetailsInput(BenefitInput):
    specialties: Annotated[list[Label], Field(max_length=20)] = Field(default_factory=list)
    message_to_users: Note | None = None


class PartnerProfileInput(PartnerPublicDetailsInput):
    display_name: Name
    major: Name | None = None
    education_label: Annotated[str, StringConstraints(max_length=50)] | None = None
    bio: Annotated[str, StringConstraints(max_length=300)] | None = None
    tags: Annotated[list[Label], Field(max_length=20)] = Field(default_factory=list)
    photo_asset_id: Id | None = None
    requested_service_version_ids: Annotated[list[Id], Field(max_length=50)] = Field(
        default_factory=list
    )
    requested_area_codes: Annotated[list[Label], Field(max_length=50)] = Field(default_factory=list)


class PartnerReview(BenefitInput):
    decision: Literal["approve", "reject"]
    approved_service_version_ids: Annotated[list[Id], Field(max_length=50)] = Field(
        default_factory=list
    )
    approved_area_codes: Annotated[list[Label], Field(max_length=50)] = Field(default_factory=list)
    reason: Note | None = None

    @model_validator(mode="after")
    def check_requirements(self):
        if self.decision == "approve":
            if not self.approved_service_version_ids or not self.approved_area_codes:
                raise ValueError("批准时须填写核准服务和区域")
        elif self.reason is None:
            raise ValueError("驳回时须填写原因")
        return self


class PhotoComplete(BenefitInput):
    asset_id: Id


class IdentityUpload(ImageUpload):
    side: Side


class IdentityComplete(PhotoComplete):
    side: Side


class Window(BenefitInput):
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def check_order(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("开始时间须早于结束时间")
        return self


class SlotUpdate(BenefitInput):
    windows: Annotated[list[Window], Field(max_length=48)]


class ServiceLocationInput(BenefitInput):
    id: Id | None = None
    name: Name
    area_code: Label
    time_zone: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    address: Note
    arrival_notes: Note | None = None
    travel_before_minutes: Annotated[int, Field(ge=0, le=1440)]
    travel_after_minutes: Annotated[int, Field(ge=0, le=1440)]
    sort_order: OrderIndex = 0

    @model_validator(mode="after")
    def check_zone(self):
        zone_for(self.time_zone)
        return self


class ServiceLocationsInput(BenefitInput):
    items: Annotated[list[ServiceLocationInput], Field(max_length=50)]

    @model_validator(mode="after")
    def check_ids(self):
        ids = [item.id for item in self.items if item.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("地点 ID 不可重复")
        return self


class BookingInput(BenefitInput):
    partner_id: Id
    location_id: Id
    starts_at: AwareDatetime


class PartnerAction(BenefitInput):
    action: Literal["confirm", "reject", "cancel", "finish"]
    reason_code: ShortReason | None = None
    reason_text: Note | None = None

    @model_validator(mode="after")
    def check_reason(self):
        if self.action in {"reject", "cancel"}:
            if not self.reason_code or not self.reason_text:
                raise ValueError("拒绝或取消时须填写原因编码和说明")
        elif self.reason_code or self.reason_text:
            raise ValueError("此操作无需填写原因")
        return self


class CheckInInput(BenefitInput):
    qr_payload: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,4}\.[A-Za-z0-9_-]{43}$")]


class CheckInCodeView(BaseModel):
    qr_payload: str
    expires_at: datetime


class RatingInput(BenefitInput):
    score: Annotated[int, Field(ge=1, le=5)]
    comment: Note | None = None


class RatingView(BaseModel):
    score: int
    comment: str | None
    created_at: datetime


class PartnerPhotoView(BaseModel):
    image_url: str
    url_expires_at: datetime
    width_px: int | None
    height_px: int | None


class PartnerDisplay(BaseModel):
    display_name: str
    major: str | None
    education_label: str | None
    bio: str | None
    tags: list[str]
    photo: PartnerPhotoView | None = None


class PartnerApplicationView(PartnerDisplay):
    id: int
    photo_asset_id: int | None
    status: PartnerStatus
    reviewed_at: datetime | None
    review_notes: str | None
    requested_service_version_ids: list[int]
    requested_area_codes: list[str]
    identity_upload_status: dict[Side, bool]
    specialties: list[str]
    message_to_users: str | None


class PartnerServiceLabel(BaseModel):
    service_version_id: int
    name: str


class PartnerDetailsView(PartnerDisplay):
    partner_id: int
    specialties: list[str]
    message_to_users: str | None
    services: list[PartnerServiceLabel]
    completed_service_count: int
    total_service_seconds: int
    average_rating: float | None
    rating_count: int
    is_marked: bool
    can_book: bool = False
    can_consult: bool = False
    earliest_start_at: datetime | None = None
    availability_label: Literal["today", "tomorrow", "this_week", "later"] | None = None


class PartnerMarkView(BaseModel):
    partner_id: int
    is_available: bool
    marked_at: datetime
    partner: PartnerDisplay | None


class PartnerCardView(PartnerDisplay):
    partner_id: int
    service_version_id: int
    services: list[PartnerServiceLabel]
    completed_service_count: int
    average_rating: float | None
    rating_count: int
    earliest_start_at: datetime | None
    availability_label: Literal["today", "tomorrow", "this_week", "later"] | None


class UploadAuthorizationView(BaseModel):
    asset_id: int
    upload_url: str
    required_headers: dict[str, str]
    expires_at: datetime


class UploadCompleteView(BaseModel):
    asset_id: int
    upload_status: Literal["uploaded"]


class IdentityCompleteView(UploadCompleteView):
    side: Side


class IdentityLink(BaseModel):
    download_url: str
    expires_at: datetime


class IdentityDocumentsView(BaseModel):
    portrait: IdentityLink
    emblem: IdentityLink


class ScheduleView(BaseModel):
    time_zone: str
    available_windows: list[Window]
    occupied_windows: list[Window]


class SlotsView(BaseModel):
    time_zone: str
    slots: list[Window]


class ServiceLocationView(ServiceLocationInput):
    id: int
