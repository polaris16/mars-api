"""[新增] 企业权益的用户、企业、运营及履约接口。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import AwareDatetime
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, Paginate, PaginateOnly
from app.core.config import get_settings
from app.db.database import get_db
from app.schemas.benefits import (
    BenefitActionView,
    BenefitEnterpriseView,
    BenefitGrantView,
    BenefitInvitationSort,
    BenefitInvitationView,
    BenefitIssueView,
    BenefitNameSort,
    BenefitOrderSort,
    BenefitOrderView,
    BenefitPackageSort,
    BenefitPage,
    BenefitPlanSort,
    BenefitPlanView,
    BenefitPurchaseSort,
    BenefitServiceSort,
    BenefitStaffSort,
    BenefitVersionSort,
    ClaimInput,
    ContactReplace,
    EnterpriseCreate,
    EnterprisePatch,
    GrantStatus,
    GrantVoid,
    Id,
    IssueBatch,
    MemberCreate,
    MemberPatch,
    OrderActionInput,
    OrderCancel,
    OrderCreate,
    OrderStatus,
    PackageCreate,
    PackageInput,
    PurchaseActivate,
    PurchaseDraft,
    ServiceCreate,
    ServicePatch,
    ServiceVersionInput,
    StaffReplace,
)
from app.services.benefit_catalog import BenefitCatalogService
from app.services.benefit_common import BenefitContext, BenefitError
from app.services.benefit_grants import BenefitGrantService
from app.services.benefit_orders import BenefitOrderService
from app.services.object_storage_service import ObjectStorageService, get_object_storage_service


class BenefitRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def route(request):
            try:
                response = await handle(request)
            except BenefitError as exc:
                response = JSONResponse(
                    {**exc.extra, "code": exc.code, "detail": exc.detail},
                    status_code=exc.status_code,
                )
            except RequestValidationError as exc:
                # 上游默认错误会回显输入。权益接口的输入包含领码、电话和地址。
                response = JSONResponse(
                    {
                        "detail": [
                            {key: item[key] for key in ("loc", "type", "msg")}
                            for item in exc.errors()
                        ]
                    },
                    status_code=422,
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return route


def require_benefits() -> None:
    if not get_settings().benefit_enabled:
        raise HTTPException(503, "企业权益服务尚未启用")


def benefit_context(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> BenefitContext:
    # 以已验证的 ORM 主键取身份，避免鉴权回滚后访问过期属性开启隐式事务。
    return BenefitContext(db, inspect(user).identity[0])


class Pagination:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=2**31 - 1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        self.number = page
        self.size = page_size


Context = Annotated[BenefitContext, Depends(benefit_context)]
Page = Annotated[Pagination, Depends(Pagination)]
# 运营目录列表可切到全量模式。
CatalogPaginate = Annotated[bool, Query(description="设为 false 时返回全部匹配记录")]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]
Storage = Annotated[ObjectStorageService, Depends(get_object_storage_service)]
PurchaseStatus = Literal["draft", "active", "closed"]

router = APIRouter()
ops = APIRouter(
    prefix="/ops",
    tags=["权益运营"],
    route_class=BenefitRoute,
    dependencies=[Depends(require_benefits)],
)
enterprise = APIRouter(
    prefix="/enterprises/{enterprise_id}",
    tags=["企业权益"],
    route_class=BenefitRoute,
    dependencies=[Depends(require_benefits)],
)
consumer = APIRouter(
    prefix="/users/me",
    tags=["我的权益"],
    route_class=BenefitRoute,
    dependencies=[Depends(require_benefits)],
)
fulfillment = APIRouter(
    prefix="/fulfillment",
    tags=["权益履约"],
    route_class=BenefitRoute,
    dependencies=[Depends(require_benefits)],
)

catalog = BenefitCatalogService()
grants = BenefitGrantService()
orders = BenefitOrderService()


@ops.get("/enterprises")
def list_enterprises(
    ctx: Context,
    page: Page,
    paginate: PaginateOnly = True,
    sort: BenefitNameSort = "default",
) -> dict:
    return catalog.enterprises(ctx, page.number, page.size, sort=sort)


@ops.post("/enterprises", status_code=201)
def create_enterprise(ctx: Context, body: EnterpriseCreate) -> dict:
    return catalog.write_enterprise(ctx, body)


@ops.patch("/enterprises/{id}")
def patch_enterprise(ctx: Context, id: Id, body: EnterprisePatch) -> dict:
    return catalog.write_enterprise(ctx, body, id)


@ops.get("/benefit-staff")
def list_benefit_staff(
    ctx: Context,
    page: Page,
    paginate: PaginateOnly = True,
    sort: BenefitStaffSort = "default",
) -> dict:
    return catalog.staff(ctx, page.number, page.size, sort=sort)


@ops.put("/benefit-staff/{user_id}")
def replace_benefit_staff(ctx: Context, user_id: Id, body: StaffReplace) -> dict:
    return catalog.replace_staff(ctx, user_id, body)


@ops.get("/benefit-services")
def list_benefit_services(
    ctx: Context, page: Page, paginate: CatalogPaginate = True, sort: BenefitServiceSort = "default"
) -> dict:
    return catalog.services(ctx, page.number, page.size, paginate_results=paginate, sort=sort)


@ops.post("/benefit-services", status_code=201)
def create_benefit_service(ctx: Context, body: ServiceCreate) -> dict:
    return catalog.write_service(ctx, body)


@ops.patch("/benefit-services/{id}")
def patch_benefit_service(ctx: Context, id: Id, body: ServicePatch) -> dict:
    return catalog.write_service(ctx, body, id)


@ops.get("/benefit-services/{id}/versions")
def list_benefit_service_versions(
    ctx: Context,
    id: Id,
    page: Page,
    paginate: CatalogPaginate = True,
    sort: BenefitVersionSort = "default",
) -> dict:
    return catalog.versions(ctx, id, page.number, page.size, paginate_results=paginate, sort=sort)


@ops.post("/benefit-services/{id}/versions", status_code=201)
def create_benefit_service_version(ctx: Context, id: Id, body: ServiceVersionInput) -> dict:
    return catalog.write_version(ctx, id, body)


@ops.put("/benefit-services/{id}/versions/{version_id}")
def replace_benefit_service_version(
    ctx: Context, id: Id, version_id: Id, body: ServiceVersionInput
) -> dict:
    return catalog.write_version(ctx, id, body, version_id)


@ops.post("/benefit-services/{id}/versions/{version_id}/publish")
def publish_benefit_service_version(ctx: Context, id: Id, version_id: Id, storage: Storage) -> dict:
    return catalog.change_version_state(ctx, id, version_id, "published", storage)


@ops.post("/benefit-services/{id}/versions/{version_id}/retire")
def retire_benefit_service_version(ctx: Context, id: Id, version_id: Id) -> dict:
    return catalog.change_version_state(ctx, id, version_id, "retired")


@ops.get("/benefit-packages")
def list_benefit_packages(
    ctx: Context, page: Page, paginate: CatalogPaginate = True, sort: BenefitPackageSort = "default"
) -> dict:
    return catalog.packages(ctx, page.number, page.size, paginate_results=paginate, sort=sort)


@ops.get("/benefit-packages/{id}")
def read_benefit_package(ctx: Context, id: Id) -> dict:
    return catalog.packages(ctx, 1, 1, id)


@ops.post("/benefit-packages", status_code=201)
def create_benefit_package(ctx: Context, body: PackageCreate) -> dict:
    return catalog.write_package(ctx, body)


@ops.put("/benefit-packages/{id}")
def replace_benefit_package(ctx: Context, id: Id, body: PackageInput) -> dict:
    return catalog.write_package(ctx, body, id)


@ops.post("/benefit-packages/{id}/publish")
def publish_benefit_package(ctx: Context, id: Id) -> dict:
    return catalog.change_package_state(ctx, id, "published")


@ops.post("/benefit-packages/{id}/retire")
def retire_benefit_package(ctx: Context, id: Id) -> dict:
    return catalog.change_package_state(ctx, id, "retired")


@ops.get("/benefit-purchases")
def list_ops_benefit_purchases(
    ctx: Context,
    page: Page,
    status: PurchaseStatus | None = None,
    paginate: PaginateOnly = True,
    sort: BenefitPurchaseSort = "default",
) -> dict:
    return catalog.purchases(ctx, page.number, page.size, status=status, sort=sort)


@ops.post("/benefit-purchases", status_code=201)
def create_benefit_purchase(ctx: Context, body: PurchaseDraft) -> dict:
    return catalog.write_purchase(ctx, body)


@ops.get("/benefit-purchases/{batch_id}")
def read_ops_benefit_purchase(ctx: Context, batch_id: Id) -> dict:
    return catalog.purchases(ctx, 1, 1, batch_id=batch_id)


@ops.get("/benefit-purchases/{batch_id}/contract")
def read_ops_benefit_purchase_contract(ctx: Context, batch_id: Id, storage: Storage) -> dict:
    return catalog.purchase_contract(ctx, batch_id, storage)


@ops.patch("/benefit-purchases/{batch_id}")
def update_benefit_purchase_draft(ctx: Context, batch_id: Id, body: PurchaseDraft) -> dict:
    return catalog.write_purchase(ctx, body, batch_id)


@ops.post("/benefit-purchases/{batch_id}/activate")
def activate_benefit_purchase(ctx: Context, batch_id: Id, body: PurchaseActivate) -> dict:
    return catalog.change_purchase_state(ctx, batch_id, "active", body.paid_at)


@ops.post("/benefit-purchases/{batch_id}/close")
def close_benefit_purchase(ctx: Context, batch_id: Id) -> dict:
    return catalog.change_purchase_state(ctx, batch_id, "closed")


@consumer.get("/enterprises")
def list_my_enterprises(
    ctx: Context,
    page: Page,
    paginate: Paginate = True,
    sort: BenefitNameSort = "default",
) -> dict:
    return catalog.enterprises(
        ctx, page.number, page.size, own=True, sort=sort, paginate_results=paginate
    )


@enterprise.get("/members")
def list_enterprise_members(
    ctx: Context,
    enterprise_id: Id,
    page: Page,
    paginate: PaginateOnly = True,
    sort: BenefitNameSort = "default",
) -> dict:
    return catalog.members(ctx, enterprise_id, page.number, page.size, sort=sort)


@enterprise.post("/members", status_code=201)
def create_enterprise_member(ctx: Context, enterprise_id: Id, body: MemberCreate) -> dict:
    return catalog.write_member(ctx, enterprise_id, body)


@enterprise.patch("/members/{member_id}")
def update_enterprise_member(
    ctx: Context, enterprise_id: Id, member_id: Id, body: MemberPatch
) -> dict:
    return catalog.write_member(ctx, enterprise_id, body, member_id)


@enterprise.get("/benefit-purchases")
def list_enterprise_benefit_purchases(
    ctx: Context,
    enterprise_id: Id,
    page: Page,
    status: PurchaseStatus | None = None,
    paginate: PaginateOnly = True,
    sort: BenefitPurchaseSort = "default",
) -> dict:
    return catalog.purchases(ctx, page.number, page.size, enterprise_id, status=status, sort=sort)


@enterprise.get("/benefit-purchases/{batch_id}")
def read_enterprise_benefit_purchase(ctx: Context, enterprise_id: Id, batch_id: Id) -> dict:
    return catalog.purchases(ctx, 1, 1, enterprise_id, batch_id)


@enterprise.get("/benefit-purchases/{batch_id}/contract")
def read_enterprise_benefit_purchase_contract(
    ctx: Context, enterprise_id: Id, batch_id: Id, storage: Storage
) -> dict:
    return catalog.purchase_contract(ctx, batch_id, storage, enterprise_id)


@enterprise.put("/benefit-purchases/{batch_id}/contacts")
def set_benefit_purchase_contacts(
    ctx: Context, enterprise_id: Id, batch_id: Id, body: ContactReplace
) -> dict:
    return catalog.replace_contacts(ctx, enterprise_id, batch_id, body)


@enterprise.post(
    "/benefit-purchases/{batch_id}/issues", status_code=201, response_model=BenefitIssueView
)
def issue_benefit_grants(
    ctx: Context, enterprise_id: Id, batch_id: Id, idem: Key, body: IssueBatch, response: Response
) -> dict:
    result, created = grants.issue(ctx, enterprise_id, batch_id, idem, body)
    if not created:
        response.status_code = 200
    return result


@enterprise.get("/benefit-purchases/{batch_id}/grants")
def list_issued_benefit_grants(
    ctx: Context,
    enterprise_id: Id,
    batch_id: Id,
    page: Page,
    status: GrantStatus | None = None,
    paginate: PaginateOnly = True,
    sort: BenefitInvitationSort = "default",
) -> dict:
    return grants.issued_grants(
        ctx, enterprise_id, batch_id, status, page.number, page.size, sort=sort
    )


@enterprise.post("/benefit-grants/{grant_id}/void")
def void_benefit_grant(ctx: Context, enterprise_id: Id, grant_id: Id, body: GrantVoid) -> dict:
    return grants.void(ctx, enterprise_id, grant_id, body)


@consumer.get("/benefit-invitations", response_model=BenefitPage[BenefitInvitationView])
def list_benefit_invitations(
    ctx: Context,
    page: Page,
    paginate: PaginateOnly = True,
    sort: BenefitInvitationSort = "default",
) -> dict:
    return grants.invitations(ctx, page.number, page.size, sort=sort)


@consumer.post("/benefit-claims", status_code=201, response_model=BenefitGrantView)
def claim_benefit_grant(ctx: Context, body: ClaimInput, response: Response) -> dict:
    result, created = grants.claim(ctx, body)
    if not created:
        response.status_code = 200
    return result


@consumer.get("/benefit-enterprises", response_model=BenefitPage[BenefitEnterpriseView])
def list_my_benefit_enterprises(
    ctx: Context,
    page: Page,
    paginate: Paginate = True,
    sort: BenefitNameSort = "default",
) -> dict:
    return grants.enterprises(ctx, page.number, page.size, sort=sort, paginate_results=paginate)


@consumer.get("/benefit-plans", response_model=BenefitPage[BenefitPlanView])
def list_my_benefit_plans(
    ctx: Context,
    page: Page,
    enterprise_id: Id | None = None,
    paginate: PaginateOnly = True,
    sort: BenefitPlanSort = "default",
) -> dict:
    return grants.plans(ctx, enterprise_id, page.number, page.size, sort=sort)


@consumer.get("/benefit-grants/{grant_id}", response_model=BenefitGrantView)
def read_my_benefit_grant(ctx: Context, grant_id: Id) -> dict:
    return grants.detail(ctx, grant_id)


@consumer.get("/benefit-service-versions/{version_id}/documents/{document_type}")
def read_benefit_version_document(
    ctx: Context, version_id: Id, document_type: Literal["description", "terms"], storage: Storage
) -> dict:
    return grants.document(ctx, version_id, document_type, storage)


@consumer.post(
    "/benefit-balances/{balance_id}/service-orders",
    status_code=201,
    response_model=BenefitOrderView,
)
def create_benefit_service_order(
    ctx: Context, balance_id: Id, idem: Key, body: OrderCreate, response: Response
) -> dict:
    result, created = orders.create(ctx, balance_id, idem, body)
    if not created:
        response.status_code = 200
    return result


@consumer.get("/benefit-service-orders", response_model=BenefitPage[BenefitOrderView])
def list_my_benefit_service_orders(
    ctx: Context,
    page: Page,
    grant_id: Id | None = None,
    status: OrderStatus | None = None,
    paginate: PaginateOnly = True,
    sort: BenefitOrderSort = "default",
) -> dict:
    return orders.orders(ctx, page.number, page.size, grant_id=grant_id, status=status, sort=sort)


@consumer.get("/benefit-service-orders/{order_id}", response_model=BenefitOrderView)
def read_my_benefit_service_order(ctx: Context, order_id: Id) -> dict:
    return orders.orders(ctx, 1, 1, order_id=order_id)


@consumer.post("/benefit-service-orders/{order_id}/cancel", response_model=BenefitOrderView)
def cancel_my_benefit_service_order(
    ctx: Context, order_id: Id, idem: Key, body: OrderCancel
) -> dict:
    return orders.action(ctx, order_id, idem, body, fulfill=False)["order"]


@fulfillment.get("/benefit-service-orders", response_model=BenefitPage[BenefitOrderView])
def list_fulfillment_benefit_orders(
    ctx: Context,
    page: Page,
    assignee_user_id: Id | None = None,
    status: OrderStatus | None = None,
    start_from: AwareDatetime | None = None,
    start_before: AwareDatetime | None = None,
    paginate: PaginateOnly = True,
    sort: BenefitOrderSort = "default",
) -> dict:
    return orders.orders(
        ctx,
        page.number,
        page.size,
        fulfill=True,
        assignee=assignee_user_id,
        status=status,
        start_from=start_from,
        start_before=start_before,
        sort=sort,
    )


@fulfillment.get("/benefit-service-orders/{id}", response_model=BenefitOrderView)
def read_fulfillment_benefit_order(ctx: Context, id: Id) -> dict:
    return orders.orders(ctx, 1, 1, fulfill=True, order_id=id)


@fulfillment.post("/benefit-service-orders/{id}/actions", response_model=BenefitActionView)
def apply_benefit_service_order_action(
    ctx: Context, id: Id, idem: Key, body: OrderActionInput
) -> dict:
    return orders.action(ctx, id, idem, body, fulfill=True)


for group in (ops, enterprise, consumer, fulfillment):
    router.include_router(group)
