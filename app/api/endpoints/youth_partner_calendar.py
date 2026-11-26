"""[新增] 青年伙伴模板、每日发布及客户预约日历接口。"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.endpoints.benefits import BenefitRoute, Context, require_benefits
from app.schemas.benefits import Id
from app.schemas.youth_partner_calendar import (
    ApplyTemplatesInput,
    ApplyTemplatesView,
    BookingCalendarView,
    PartnerCalendarView,
    PublishDateInput,
    PublishedDateView,
    ScheduleInput,
    TemplateKind,
    TemplatesView,
    TemplateView,
)
from app.services import youth_partner_calendar as calendar

router = APIRouter(
    tags=["青年伙伴"], route_class=BenefitRoute, dependencies=[Depends(require_benefits)]
)
TEMPLATES = "/users/me/youth-partner-availability-templates"
CALENDAR = "/users/me/youth-partner-calendar"
BOOKINGS = "/users/me/benefit-balances/{balance_id}/youth-partners/{id}/booking-calendar"


@router.get(TEMPLATES, response_model=TemplatesView, summary="查询本人的排班模板")
def list_youth_partner_templates(ctx: Context):
    return calendar.list_templates(ctx)


@router.put(f"{TEMPLATES}/{{kind}}", response_model=TemplateView, summary="保存指定类型的排班模板")
def save_youth_partner_template(ctx: Context, kind: TemplateKind, body: ScheduleInput):
    return calendar.save_template(ctx, kind, body)


@router.get(CALENDAR, response_model=PartnerCalendarView, summary="查询本人的每日发布日历")
def get_youth_partner_calendar(ctx: Context, from_date: date, to_date: date):
    return calendar.get_calendar(ctx, from_date, to_date)


@router.put(
    f"{CALENDAR}/{{date}}", response_model=PublishedDateView, summary="发布指定日期的预约档期"
)
def publish_youth_partner_date(
    ctx: Context,
    calendar_date: Annotated[date, Path(alias="date")],
    body: PublishDateInput,
):
    return calendar.publish_date(ctx, calendar_date, body)


@router.post(
    f"{CALENDAR}/apply-templates",
    response_model=ApplyTemplatesView,
    summary="将模板批量复制到指定日期",
)
def apply_youth_partner_templates(ctx: Context, body: ApplyTemplatesInput):
    return calendar.apply_templates(ctx, body)


@router.get(BOOKINGS, response_model=BookingCalendarView, summary="按权益和地点查看预约日历")
def get_youth_partner_booking_calendar(
    ctx: Context,
    balance_id: Id,
    id: Id,
    location_id: Annotated[int, Query(gt=0, le=2**63 - 1)],
):
    return calendar.booking_calendar(ctx, balance_id, id, location_id)
