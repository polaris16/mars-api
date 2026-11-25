"""[新增] 青年伙伴注册、地点、档期及线上预约接口。"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import PaginateOnly
from app.api.endpoints.benefits import BenefitRoute, Context, Key, Page, Storage, require_benefits
from app.schemas.benefits import BenefitOrderView, BenefitPage, Id
from app.schemas.list_sort import CreatedAtSort
from app.schemas.product_experience import ImageUpload
from app.schemas.youth_partners import (
    BookingInput,
    CheckInCodeView,
    CheckInInput,
    IdentityComplete,
    IdentityUpload,
    PartnerAction,
    PartnerProfileInput,
    PartnerReview,
    PartnerDetailsView,
    PartnerMarkView,
    PartnerPublicDetailsInput,
    PartnerStatus,
    PhotoComplete,
    RatingInput,
    RatingView,
    ServiceLocationsInput,
    SlotUpdate,
    PartnerApplicationView,
    PartnerCardView,
    IdentityDocumentsView,
    IdentityCompleteView,
    ScheduleView,
    SlotsView,
    ServiceLocationView,
    UploadAuthorizationView,
    UploadCompleteView,
)
from app.services import youth_partner_bookings as bookings
from app.services import youth_partner_details as details
from app.services import youth_partner_profiles as profiles
from app.services import youth_partner_schedule as schedule

router = APIRouter(
    tags=["青年伙伴"],
    route_class=BenefitRoute,
    dependencies=[Depends(require_benefits)],
)
ME = "/users/me"
PROFILE = f"{ME}/youth-partner-profile"
PHOTO = f"{ME}/youth-partner-photo"
IDENTITY = f"{ME}/youth-partner-identity"
OPS = "/ops/youth-partners"
AVAILABILITY = f"{ME}/youth-partner-availability"
PLACE = "/enterprises/{enterprise_id}/benefit-grants/{grant_id}/service-locations"
BALANCE = f"{ME}/benefit-balances/{{balance_id}}"
ORDER = f"{ME}/benefit-service-orders/{{id}}"
FULFILLMENT = "/fulfillment/benefit-service-orders"
MARKS = f"{ME}/youth-partner-marks"


@router.get(
    "/youth-partners/{partner_id}",
    response_model=PartnerDetailsView,
    summary="查询青年伙伴公开详情",
)
def get_youth_partner_details(
    ctx: Context,
    storage: Storage,
    partner_id: Id,
    balance_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
    location_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
):
    return details.get_details(ctx, storage, partner_id, balance_id, location_id)


@router.patch(
    f"{OPS}/{{partner_id}}/public-details",
    response_model=PartnerApplicationView,
    summary="维护已上架伙伴的擅长领域及寄语",
)
def revise_youth_partner_public_details(
    ctx: Context,
    storage: Storage,
    partner_id: Id,
    body: PartnerPublicDetailsInput,
):
    return profiles.revise_public_details(ctx, partner_id, body, storage)


@router.put(f"{MARKS}/{{partner_id}}", status_code=204, summary="收藏青年伙伴")
def mark_youth_partner(ctx: Context, partner_id: Id):
    details.set_mark(ctx, partner_id, marked=True)


@router.delete(f"{MARKS}/{{partner_id}}", status_code=204, summary="取消收藏青年伙伴")
def unmark_youth_partner(ctx: Context, partner_id: Id):
    details.set_mark(ctx, partner_id, marked=False)


@router.get(MARKS, response_model=BenefitPage[PartnerMarkView], summary="查询我的青年伙伴收藏")
def list_youth_partner_marks(
    ctx: Context,
    storage: Storage,
    page: Page,
    sort: Literal["default", "marked_at_asc", "marked_at_desc"] = "default",
    paginate: PaginateOnly = True,
):
    return details.list_marks(ctx, storage, page.number, page.size, sort)


@router.get(PROFILE, response_model=PartnerApplicationView, summary="读取本人的青年伙伴资料")
def get_youth_partner_profile(ctx: Context, storage: Storage):
    return profiles.my_profile(ctx, storage)


@router.put(PROFILE, response_model=PartnerApplicationView, summary="保存青年伙伴注册资料")
def save_youth_partner_profile(ctx: Context, body: PartnerProfileInput, storage: Storage):
    return profiles.save_profile(ctx, body, storage)


@router.post(f"{PROFILE}/submit", response_model=PartnerApplicationView, summary="提交青年伙伴申请")
def submit_youth_partner_profile(ctx: Context):
    return profiles.submit_profile(ctx)


@router.post(
    f"{PHOTO}/upload-authorizations",
    response_model=UploadAuthorizationView,
    summary="申请上传伙伴公开展示照",
)
def authorize_youth_partner_photo(ctx: Context, body: ImageUpload, storage: Storage):
    return profiles.issue_upload(ctx, body, storage)


@router.post(
    f"{PHOTO}/complete", response_model=UploadCompleteView, summary="确认伙伴公开展示照上传"
)
def complete_youth_partner_photo(ctx: Context, body: PhotoComplete, storage: Storage):
    return profiles.confirm_upload(ctx, body.asset_id, storage)


@router.post(
    f"{IDENTITY}/upload-authorizations",
    response_model=UploadAuthorizationView,
    summary="申请上传指定面的身份证照片",
)
def authorize_youth_partner_identity(ctx: Context, body: IdentityUpload, storage: Storage):
    return profiles.issue_upload(ctx, body, storage, side=body.side)


@router.post(
    f"{IDENTITY}/complete", response_model=IdentityCompleteView, summary="确认指定面身份证照片上传"
)
def complete_youth_partner_identity(ctx: Context, body: IdentityComplete, storage: Storage):
    return profiles.confirm_upload(ctx, body.asset_id, storage, side=body.side)


@router.get(OPS, response_model=BenefitPage[PartnerApplicationView], summary="查找青年伙伴审核申请")
def list_youth_partner_applications(
    ctx: Context,
    page: Page,
    status: PartnerStatus | None = None,
    sort: CreatedAtSort = "default",
    paginate: PaginateOnly = True,
):
    return profiles.applications(ctx, page.number, page.size, status, sort)


@router.get(f"{OPS}/{{id}}", response_model=PartnerApplicationView, summary="读取青年伙伴审核申请")
def read_youth_partner_application(ctx: Context, id: Id, storage: Storage):
    return profiles.application(ctx, id, storage)


@router.get(
    f"{OPS}/{{id}}/identity-documents",
    response_model=IdentityDocumentsView,
    summary="签发待审证件的查看链接",
)
def read_youth_partner_identity(ctx: Context, id: Id, storage: Storage):
    return profiles.identity_documents(ctx, id, storage)


@router.post(
    f"{OPS}/{{id}}/review", response_model=PartnerApplicationView, summary="批准或驳回青年伙伴申请"
)
def review_youth_partner_application(ctx: Context, id: Id, body: PartnerReview):
    return profiles.review_application(ctx, id, body)


@router.get(AVAILABILITY, response_model=ScheduleView, summary="查询本人的开放时间及预约占用")
def get_youth_partner_availability(
    ctx: Context,
    day: Annotated[date | None, Query(alias="date")] = None,
    time_zone: Annotated[str, Query(max_length=64)] = "Asia/Shanghai",
):
    return schedule.schedule(ctx, day, time_zone)


@router.put(AVAILABILITY, response_model=ScheduleView, summary="替换本人指定日期的开放时段")
def replace_youth_partner_availability(
    ctx: Context,
    body: SlotUpdate,
    day: Annotated[date, Query(alias="date")],
    time_zone: Annotated[str, Query(max_length=64)] = "Asia/Shanghai",
):
    return schedule.schedule(ctx, day, time_zone, body)


@router.get(
    PLACE, response_model=list[ServiceLocationView], summary="列出本机构该发放记录的可预约地点"
)
def get_grant_service_locations(ctx: Context, enterprise_id: Id, grant_id: Id):
    return schedule.manage_locations(ctx, enterprise_id, grant_id)


@router.put(
    PLACE, response_model=list[ServiceLocationView], summary="替换本机构该发放记录的可预约地点"
)
def replace_grant_service_locations(
    ctx: Context, enterprise_id: Id, grant_id: Id, body: ServiceLocationsInput
):
    return schedule.manage_locations(ctx, enterprise_id, grant_id, body)


@router.get(
    f"{BALANCE}/service-locations",
    response_model=list[ServiceLocationView],
    summary="查询当前权益余额下的服务地点",
)
def list_my_youth_partner_locations(ctx: Context, balance_id: Id):
    return schedule.locations_for_balance(ctx, balance_id)


@router.get(
    f"{BALANCE}/youth-partners",
    response_model=BenefitPage[PartnerCardView],
    summary="按权益版本与档期查找青年伙伴",
)
def list_youth_partners(
    ctx: Context,
    balance_id: Id,
    page: Page,
    storage: Storage,
    location_id: Annotated[int | None, Query(gt=0, le=2**63 - 1)] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,
    available_today: bool = False,
    sort: Literal["default", "rating_desc"] = "default",
    paginate: PaginateOnly = True,
):
    return schedule.list_partners(
        ctx,
        storage,
        balance_id,
        page.number,
        page.size,
        location_id,
        keyword,
        tag,
        available_today,
        sort,
    )


@router.get(
    f"{BALANCE}/youth-partners/{{id}}/slots",
    response_model=SlotsView,
    summary="查询青年伙伴在指定地点的可约时间",
)
def list_youth_partner_slots(
    ctx: Context, balance_id: Id, id: Id, location_id: Annotated[int, Query(gt=0, le=2**63 - 1)]
):
    return schedule.get_slots(ctx, balance_id, id, location_id)


@router.post(
    f"{BALANCE}/youth-partner-bookings",
    status_code=201,
    response_model=BenefitOrderView,
    summary="预约青年伙伴并暂占权益",
)
def create_youth_partner_booking(
    ctx: Context, balance_id: Id, idem: Key, body: BookingInput, response: Response
):
    result, created = bookings.create_booking(ctx, balance_id, idem, body)
    if not created:
        response.status_code = 200
    return result


@router.post(
    f"{FULFILLMENT}/{{id}}/youth-partner-actions",
    response_model=BenefitOrderView,
    summary="处理本人获派的青年伙伴预约",
)
def apply_youth_partner_action(ctx: Context, id: Id, idem: Key, body: PartnerAction):
    return bookings.change_booking(ctx, id, idem, body)


@router.post(
    f"{ORDER}/check-in-code", response_model=CheckInCodeView, summary="生成本人到场核销二维码"
)
def issue_youth_partner_check_in(ctx: Context, id: Id):
    return bookings.issue_check_in(ctx, id)


@router.post(
    f"{FULFILLMENT}/youth-partner-check-in",
    response_model=BenefitOrderView,
    summary="伙伴扫码核销到场服务",
)
def scan_youth_partner_check_in(ctx: Context, idem: Key, body: CheckInInput):
    return bookings.scan_check_in(ctx, idem, body.qr_payload)


@router.post(f"{ORDER}/rating", response_model=RatingView, summary="评价已完成的青年伙伴服务")
def rate_youth_partner_order(ctx: Context, id: Id, body: RatingInput):
    return bookings.rate_booking(ctx, id, body)
