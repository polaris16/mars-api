"""[新增] 青年伙伴每日发布、模板复制和客户预约日历。"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.benefits import BenefitInput
from app.schemas.youth_partners import zone_for

TemplateKind = Literal["workday", "restday"]
CalendarStatus = Literal["unset", "legacy", "published", "closed"]
SlotStatus = Literal["available", "occupied", "unavailable"]
SlotReason = Literal[
    "booking_cutoff", "benefit_not_active", "benefit_quota_exhausted", "recipient_schedule_conflict"
]
Version = Annotated[int, Field(ge=0, le=2**31 - 1)]
StartTime = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
EndTime = Annotated[str, StringConstraints(pattern=r"^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)$")]
TimeZone = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class LocalWindow(BenefitInput):
    start_local: StartTime
    end_local: EndTime

    @model_validator(mode="after")
    def check_end(self):
        if self.end_local <= self.start_local:
            raise ValueError("结束时间须晚于开始时间")
        return self


class ScheduleInput(BenefitInput):
    windows: Annotated[list[LocalWindow], Field(max_length=48)]
    booking_start_times: Annotated[list[StartTime], Field(max_length=48)]

    @model_validator(mode="after")
    def check_schedule(self):
        self.windows.sort(key=lambda window: window.start_local)
        self.booking_start_times.sort()
        if any(a.end_local > b.start_local for a, b in zip(self.windows, self.windows[1:])):
            raise ValueError("开放区间不可重叠")
        if len(set(self.booking_start_times)) != len(self.booking_start_times):
            raise ValueError("预约开始时间不可重复")
        if any(
            not any(w.start_local <= start < w.end_local for w in self.windows)
            for start in self.booking_start_times
        ):
            raise ValueError("预约开始时间须位于开放区间内")
        return self


class PublishDateInput(ScheduleInput):
    version: Version
    time_zone: TimeZone | None = None

    @model_validator(mode="after")
    def check_zone(self):
        if self.time_zone:
            zone_for(self.time_zone)
        return self


class TemplateDate(BenefitInput):
    date: date
    template_kind: TemplateKind | None = None
    version: Version | None = None


class ApplyTemplatesInput(BenefitInput):
    dates: Annotated[list[TemplateDate], Field(min_length=1, max_length=31)]
    mode: Literal["fill_unset", "replace"] = "fill_unset"
    time_zone: TimeZone | None = None

    @model_validator(mode="after")
    def check_dates(self):
        if len({item.date for item in self.dates}) != len(self.dates):
            raise ValueError("复制日期不可重复")
        if self.mode == "replace" and any(item.version is None for item in self.dates):
            raise ValueError("覆盖模式须为每个日期提供版本")
        if self.time_zone:
            zone_for(self.time_zone)
        return self


class TemplateView(BaseModel):
    kind: TemplateKind
    is_configured: bool
    windows: list[LocalWindow]
    booking_start_times: list[StartTime]
    updated_at: datetime | None


class TemplatesView(BaseModel):
    items: list[TemplateView]


class AbsoluteWindow(BaseModel):
    starts_at: datetime
    ends_at: datetime


class PublishedDateView(BaseModel):
    date: date
    status: CalendarStatus
    version: int
    windows: list[LocalWindow]
    booking_start_times: list[StartTime]
    occupied_windows: list[AbsoluteWindow]


class PartnerCalendarView(BaseModel):
    time_zone: str
    days: list[PublishedDateView]


class SkippedDateView(BaseModel):
    date: date
    reason: Literal["already_configured"]


class ApplyTemplatesView(BaseModel):
    time_zone: str
    published_days: list[PublishedDateView]
    skipped_days: list[SkippedDateView]


class CalendarSlotView(AbsoluteWindow):
    status: SlotStatus
    reason: SlotReason | None


class BookingDateView(BaseModel):
    date: date
    available_count: int
    slots: list[CalendarSlotView]


class BookingCalendarView(BaseModel):
    time_zone: str
    today: date
    location_id: int
    service_duration_minutes: int
    days: list[BookingDateView]
