"""[新增] 产品目录、虚拟体验和站内作品分享的 HTTP 入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import PaginateOnly
from app.api.endpoints.benefits import BenefitRoute, Context, Key, Page, Storage, require_benefits
from app.schemas.benefits import BenefitPage, Id
from app.schemas.product_experience import (
    CatalogPatch,
    CategoriesView,
    CategoryCreate,
    CategoryPatch,
    CategoryView,
    ConfirmInput,
    ConfirmView,
    CreatedSort,
    Direction,
    IdSort,
    ImageAuthorization,
    ImageConfirmed,
    ImageUpload,
    Mode,
    NameSort,
    PhotoAuthorization,
    PhotoConfirmed,
    PhotoUpload,
    PreviewInput,
    PreviewView,
    ProductAdminView,
    ProductCard,
    ProductCreate,
    ProductVersionInput,
    ProductVersionView,
    ResultCard,
    ResultView,
    RoundDetail,
    RoundView,
    ShareCard,
    ShareInput,
    ShareView,
    VersionSort,
)
from app.services.product_catalog import ProductCatalogService
from app.services.product_experience import ProductExperienceService
from app.services.product_experience_results import ProductResultService
from app.services.product_media import PERSON_IMAGE, complete_upload, delete_photo

router = APIRouter(route_class=BenefitRoute, dependencies=[Depends(require_benefits)])
catalog = ProductCatalogService()
experiences = ProductExperienceService()
results = ProductResultService()


@router.get("/products/categories", tags=["产品目录"], response_model=CategoriesView)
def list_product_categories(ctx: Context, mode: Mode | None = None):
    return catalog.categories(ctx, mode)


@router.get("/products", tags=["产品目录"], response_model=BenefitPage[ProductCard])
def list_products(
    ctx: Context,
    storage: Storage,
    page: Page,
    category_id: Id | None = None,
    mode: Mode | None = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    sort: NameSort = "default",
    paginate: PaginateOnly = True,
):
    return catalog.list_products(
        ctx, storage, page.number, page.size, category_id, mode, keyword, sort
    )


@router.get("/products/{product_id}", tags=["产品目录"], response_model=ProductVersionView)
def get_product(ctx: Context, storage: Storage, product_id: Id):
    return catalog.detail(ctx, storage, product_id)


@router.post(
    "/users/me/benefit-balances/{balance_id}/product-experience-rounds",
    tags=["产品体验"],
    status_code=201,
    response_model=RoundView,
    responses={200: {"model": RoundView}},
)
def open_product_experience_round(ctx: Context, balance_id: Id, response: Response):
    view, created = experiences.open_round(ctx, balance_id)
    if not created:
        response.status_code = 200
    return view


@router.get("/product-experiences/rounds/{round_id}", tags=["产品体验"], response_model=RoundDetail)
def get_product_experience_round(ctx: Context, storage: Storage, round_id: Id):
    return experiences.detail(ctx, storage, round_id)


@router.post(
    "/product-experiences/photo-upload-authorizations",
    tags=["产品体验"],
    status_code=201,
    response_model=PhotoAuthorization,
)
def authorize_product_photo(ctx: Context, storage: Storage, body: PhotoUpload):
    return experiences.photo_authorization(ctx, storage, body)


@router.post(
    "/product-experiences/photos/{photo_id}/complete",
    tags=["产品体验"],
    response_model=PhotoConfirmed,
)
def complete_product_photo(ctx: Context, storage: Storage, photo_id: Id):
    return complete_upload(ctx, photo_id, PERSON_IMAGE, storage)


@router.delete("/product-experiences/photos/{photo_id}", tags=["产品体验"], status_code=204)
def delete_product_photo(ctx: Context, photo_id: Id) -> Response:
    delete_photo(ctx, photo_id)
    return Response(status_code=204)


@router.post(
    "/product-experiences/rounds/{round_id}/previews",
    tags=["产品体验"],
    status_code=202,
    response_model=PreviewView,
    responses={200: {"model": PreviewView}},
)
def submit_product_preview(
    ctx: Context, round_id: Id, idem: Key, body: PreviewInput, response: Response
):
    view, created = experiences.submit(ctx, round_id, idem, body)
    if not created:
        response.status_code = 200
    return view


@router.get(
    "/product-experiences/rounds/{round_id}/previews/{preview_id}",
    tags=["产品体验"],
    response_model=PreviewView,
)
def get_product_preview(ctx: Context, storage: Storage, round_id: Id, preview_id: Id):
    return experiences.preview(ctx, storage, round_id, preview_id)


@router.post(
    "/product-experiences/rounds/{round_id}/confirm",
    tags=["产品体验"],
    status_code=201,
    response_model=ConfirmView,
    responses={200: {"model": ConfirmView}},
)
def confirm_product_experience(
    ctx: Context, round_id: Id, idem: Key, body: ConfirmInput, response: Response
):
    view, created = experiences.confirm(ctx, round_id, idem, body)
    if not created:
        response.status_code = 200
    return view


@router.get(
    "/product-experiences/results", tags=["产品体验"], response_model=BenefitPage[ResultCard]
)
def list_product_results(
    ctx: Context,
    storage: Storage,
    page: Page,
    sort: CreatedSort = "default",
    paginate: PaginateOnly = True,
):
    return results.results(ctx, storage, page.number, page.size, sort)


@router.get(
    "/product-experiences/results/{result_id}", tags=["产品体验"], response_model=ResultView
)
def get_product_result(ctx: Context, storage: Storage, result_id: Id):
    return results.detail(ctx, storage, result_id)


@router.delete("/product-experiences/results/{result_id}", tags=["产品体验"], status_code=204)
def delete_product_result(ctx: Context, result_id: Id) -> Response:
    results.delete_result(ctx, result_id)
    return Response(status_code=204)


@router.post(
    "/product-experiences/results/{result_id}/shares",
    tags=["产品体验"],
    status_code=201,
    response_model=ShareCard,
    responses={200: {"model": ShareCard}},
)
def share_product_result(
    ctx: Context, result_id: Id, idem: Key, body: ShareInput, response: Response
):
    view, created = results.share(ctx, result_id, idem, body.recipient_user_id)
    if not created:
        response.status_code = 200
    return view


@router.get("/product-experiences/shares", tags=["产品体验"], response_model=BenefitPage[ShareCard])
def list_product_shares(
    ctx: Context,
    page: Page,
    direction: Direction,
    sort: CreatedSort = "default",
    paginate: PaginateOnly = True,
):
    return results.shares(ctx, direction, page.number, page.size, sort)


@router.get("/product-experiences/shares/{share_id}", tags=["产品体验"], response_model=ShareView)
def get_product_share(ctx: Context, storage: Storage, share_id: Id):
    return results.share_detail(ctx, storage, share_id)


@router.delete("/product-experiences/shares/{share_id}", tags=["产品体验"], status_code=204)
def revoke_product_share(ctx: Context, share_id: Id) -> Response:
    results.revoke(ctx, share_id)
    return Response(status_code=204)


@router.get("/ops/product-categories", tags=["产品运营"], response_model=CategoriesView)
def list_ops_product_categories(ctx: Context):
    return catalog.categories(ctx, operations=True)


@router.post(
    "/ops/product-categories", tags=["产品运营"], status_code=201, response_model=CategoryView
)
def create_product_category(ctx: Context, body: CategoryCreate):
    return catalog.create_category(ctx, body)


@router.patch(
    "/ops/product-categories/{category_id}", tags=["产品运营"], response_model=CategoryView
)
def update_product_category(ctx: Context, category_id: Id, body: CategoryPatch):
    return catalog.update_category(ctx, category_id, body)


@router.get("/ops/products", tags=["产品运营"], response_model=BenefitPage[ProductAdminView])
def list_ops_products(
    ctx: Context,
    storage: Storage,
    page: Page,
    sort: IdSort = "default",
    paginate: PaginateOnly = True,
):
    return catalog.admin_products(ctx, storage, page.number, page.size, sort)


@router.post("/ops/products", tags=["产品运营"], status_code=201, response_model=ProductAdminView)
def create_product(ctx: Context, storage: Storage, body: ProductCreate):
    return catalog.write_product(ctx, storage, body)


@router.patch("/ops/products/{product_id}", tags=["产品运营"], response_model=ProductAdminView)
def update_product(ctx: Context, storage: Storage, product_id: Id, body: CatalogPatch):
    return catalog.write_product(ctx, storage, body, product_id)


@router.get(
    "/ops/products/{product_id}/versions",
    tags=["产品运营"],
    response_model=BenefitPage[ProductVersionView],
)
def list_product_versions(
    ctx: Context,
    storage: Storage,
    product_id: Id,
    page: Page,
    sort: VersionSort = "default",
    paginate: PaginateOnly = True,
):
    return catalog.versions(ctx, storage, product_id, page.number, page.size, sort)


@router.post(
    "/ops/products/{product_id}/versions",
    tags=["产品运营"],
    status_code=201,
    response_model=ProductVersionView,
)
def create_product_version(
    ctx: Context, storage: Storage, product_id: Id, body: ProductVersionInput
):
    return catalog.write_version(ctx, storage, product_id, body)


@router.put(
    "/ops/products/{product_id}/versions/{version_id}",
    tags=["产品运营"],
    response_model=ProductVersionView,
)
def replace_product_version(
    ctx: Context,
    storage: Storage,
    product_id: Id,
    version_id: Id,
    body: ProductVersionInput,
):
    return catalog.write_version(ctx, storage, product_id, body, version_id)


@router.post(
    "/ops/products/{product_id}/versions/{version_id}/publish",
    tags=["产品运营"],
    response_model=ProductVersionView,
)
def publish_product_version(ctx: Context, storage: Storage, product_id: Id, version_id: Id):
    return catalog.change_version(ctx, storage, product_id, version_id, "published")


@router.post(
    "/ops/products/{product_id}/versions/{version_id}/retire",
    tags=["产品运营"],
    response_model=ProductVersionView,
)
def retire_product_version(ctx: Context, storage: Storage, product_id: Id, version_id: Id):
    return catalog.change_version(ctx, storage, product_id, version_id, "retired")


@router.post(
    "/ops/product-images/upload-authorizations",
    tags=["产品运营"],
    status_code=201,
    response_model=ImageAuthorization,
)
def authorize_catalog_image(ctx: Context, storage: Storage, body: ImageUpload):
    return catalog.image_authorization(ctx, storage, body)


@router.post(
    "/ops/product-images/{asset_id}/complete", tags=["产品运营"], response_model=ImageConfirmed
)
def complete_catalog_image(ctx: Context, storage: Storage, asset_id: Id):
    return catalog.image_completion(ctx, storage, asset_id)
