"""[新增] 产品目录、图片引用和发布版本。"""

from collections import defaultdict

from sqlalchemy import any_, func, select, update

from app.models import MediaAsset, Product, ProductCategory, ProductImage, ProductVersion
from app.services.benefit_common import (
    active_row,
    conflict,
    display,
    flush_unique,
    missing,
    new_row,
    now_utc,
    page_data,
    select_page,
    stamp,
)
from app.services.product_media import (
    CONFIRMED,
    PRODUCT_IMAGE,
    authorize_upload,
    complete_upload,
    image_link,
)

CATEGORY_FIELDS = "id code name sort_order is_enabled"
PRODUCT_FIELDS = "id code sort_order is_enabled"
IMAGE_FIELDS = "media_asset_id angle is_cover is_reference sort_order"


def public_products():
    """目录与新预览使用相同的发布和上下架规则。"""
    return (
        select(ProductVersion)
        .join(Product, Product.id == ProductVersion.product_id)
        .join(ProductCategory, ProductCategory.id == ProductVersion.category_id)
        .where(
            ProductVersion.status == "published",
            ProductVersion.is_del.is_(False),
            Product.is_enabled.is_(True),
            Product.is_del.is_(False),
            ProductCategory.is_enabled.is_(True),
            ProductCategory.is_del.is_(False),
        )
    )


def product_images(db, versions, *, covers_only=False):
    query = (
        select(ProductImage, MediaAsset)
        .join(MediaAsset, MediaAsset.id == ProductImage.media_asset_id)
        .where(
            ProductImage.product_version_id.in_(versions),
            ProductImage.is_del.is_(False),
            MediaAsset.is_del.is_(False),
            MediaAsset.purpose == PRODUCT_IMAGE,
            MediaAsset.upload_status.in_(CONFIRMED),
        )
        .order_by(ProductImage.sort_order, ProductImage.id)
    )
    if covers_only:
        query = query.where(ProductImage.is_cover.is_(True))
    images = defaultdict(list)
    for image, asset in db.execute(query):
        images[image.product_version_id].append((image, asset))
    return images


def _version_view(row, images, storage, *, include_details=False):
    refs = [{**display(ref, IMAGE_FIELDS), **image_link(asset, storage)} for ref, asset in images]
    result = {
        "product_version_id": row.id,
        **display(row, "product_id name category_id generation_modes"),
        "cover": next((ref for ref in refs if ref["is_cover"]), None),
    }
    if include_details:
        result.update(display(row, "version_no status description attributes"), images=refs)
    return result


class ProductCatalogService:
    def categories(self, ctx, mode=None, *, operations=False):
        with ctx.db.begin():
            query = select(ProductCategory).where(ProductCategory.is_del.is_(False))
            if operations:
                ctx.require_platform("admin", "ops")
            else:
                query = query.where(ProductCategory.is_enabled.is_(True))
                if mode is not None:
                    eligible = public_products().where(any_(ProductVersion.generation_modes) == mode)
                    query = query.where(
                        ProductCategory.id.in_(
                            eligible.with_only_columns(ProductVersion.category_id)
                        )
                    )
            rows = ctx.db.scalars(query.order_by(ProductCategory.sort_order, ProductCategory.id))
            return {"items": [display(row, CATEGORY_FIELDS) for row in rows]}

    def list_products(self, ctx, storage, page, size, category_id, mode, keyword, sort):
        with ctx.db.begin():
            query = public_products()
            if category_id is not None:
                query = query.where(ProductVersion.category_id == category_id)
            if mode is not None:
                query = query.where(any_(ProductVersion.generation_modes) == mode)
            if keyword and (keyword := keyword.strip()):
                query = query.where(ProductVersion.name.icontains(keyword, autoescape=True))
            name = func.lower(ProductVersion.name)
            order = {
                "default": (Product.sort_order, Product.id),
                "name_asc": (name.asc(), Product.id),
                "name_desc": (name.desc(), Product.id),
            }[sort]
            rows, page, size, total = select_page(
                ctx.db, query.order_by(*order), page, size, paginate_results=True
            )
            images = product_images(ctx.db, [row.id for row in rows], covers_only=True)
            return page_data(
                [_version_view(row, images[row.id], storage) for row in rows], page, size, total
            )

    def detail(self, ctx, storage, product_id):
        with ctx.db.begin():
            row = ctx.db.scalar(public_products().where(Product.id == product_id))
            if row is None:
                raise missing()
            return self._version_detail(ctx, storage, row)

    def create_category(self, ctx, body):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            row = new_row(ProductCategory, ctx.actor_id, **body.model_dump())
            ctx.db.add(row)
            flush_unique(ctx.db)
            return display(row, CATEGORY_FIELDS)

    def update_category(self, ctx, category_id, body):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            row = active_row(ctx.db, ProductCategory, category_id, lock=True)
            self._patch(ctx, row, body)
            return display(row, CATEGORY_FIELDS)

    def admin_products(self, ctx, storage, page, size, sort):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            order = Product.id.desc() if sort == "id_desc" else Product.id
            query = select(Product).where(Product.is_del.is_(False)).order_by(order)
            rows, page, size, total = select_page(ctx.db, query, page, size, paginate_results=True)
            versions = {
                row.product_id: row
                for row in ctx.db.scalars(
                    select(ProductVersion).where(
                        ProductVersion.product_id.in_([product.id for product in rows]),
                        ProductVersion.status == "published",
                        ProductVersion.is_del.is_(False),
                    )
                )
            }
            covers = product_images(
                ctx.db, [version.id for version in versions.values()], covers_only=True
            )
            items = []
            for row in rows:
                version = versions.get(row.id)
                current = _version_view(version, covers[version.id], storage) if version else None
                items.append({**display(row, PRODUCT_FIELDS), "current_version": current})
            return page_data(items, page, size, total)

    def write_product(self, ctx, storage, body, product_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            if product_id is None:
                row = new_row(Product, ctx.actor_id, **body.model_dump())
                ctx.db.add(row)
                flush_unique(ctx.db)
                return {**display(row, PRODUCT_FIELDS), "current_version": None}
            row = active_row(ctx.db, Product, product_id, lock=True)
            self._patch(ctx, row, body)
            version = ctx.db.scalar(
                select(ProductVersion).where(
                    ProductVersion.product_id == row.id,
                    ProductVersion.status == "published",
                    ProductVersion.is_del.is_(False),
                )
            )
            current = None
            if version:
                covers = product_images(ctx.db, [version.id], covers_only=True)
                current = _version_view(version, covers[version.id], storage)
            return {**display(row, PRODUCT_FIELDS), "current_version": current}

    @staticmethod
    def _patch(ctx, row, body):
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        stamp(row, ctx.actor_id)

    @staticmethod
    def _version_detail(ctx, storage, row):
        images = product_images(ctx.db, [row.id])
        return _version_view(row, images[row.id], storage, include_details=True)

    @staticmethod
    def _version(ctx, product_id, version_id):
        row = active_row(ctx.db, ProductVersion, version_id, lock=True)
        if row.product_id != product_id:
            raise missing()
        return row

    def versions(self, ctx, storage, product_id, page, size, sort):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            active_row(ctx.db, Product, product_id)
            number = ProductVersion.version_no
            query = (
                select(ProductVersion)
                .where(
                    ProductVersion.product_id == product_id,
                    ProductVersion.is_del.is_(False),
                )
                .order_by(number.asc() if sort == "version_asc" else number.desc())
            )
            rows, page, size, total = select_page(ctx.db, query, page, size, paginate_results=True)
            images = product_images(ctx.db, [row.id for row in rows])
            return page_data(
                [_version_view(row, images[row.id], storage, include_details=True) for row in rows],
                page,
                size,
                total,
            )

    def write_version(self, ctx, storage, product_id, body, version_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            active_row(ctx.db, Product, product_id, lock=True)
            active_row(ctx.db, ProductCategory, body.category_id)
            for image in body.images:
                asset = active_row(ctx.db, MediaAsset, image.media_asset_id)
                if asset.purpose != PRODUCT_IMAGE or asset.upload_status not in CONFIRMED:
                    raise conflict("product_image_not_ready", "只能引用已确认的产品图片")
            fields = body.model_dump(exclude={"images"})
            if version_id is None:
                latest = ctx.db.scalar(
                    select(func.max(ProductVersion.version_no)).where(
                        ProductVersion.product_id == product_id
                    )
                )
                row = new_row(
                    ProductVersion,
                    ctx.actor_id,
                    product_id=product_id,
                    version_no=(latest or 0) + 1,
                    **fields,
                )
                ctx.db.add(row)
                flush_unique(ctx.db)
            else:
                row = self._version(ctx, product_id, version_id)
                if row.status != "draft":
                    raise conflict("product_not_draft", "仅草稿可以修改")
                for key, value in fields.items():
                    setattr(row, key, value)
                stamp(row, ctx.actor_id)
                ctx.db.execute(
                    update(ProductImage)
                    .where(
                        ProductImage.product_version_id == row.id,
                        ProductImage.is_del.is_(False),
                    )
                    .values(is_del=True, updated_by=ctx.actor_id, updated_at=now_utc())
                )
            ctx.db.add_all(
                [
                    new_row(
                        ProductImage, ctx.actor_id, product_version_id=row.id, **image.model_dump()
                    )
                    for image in body.images
                ]
            )
            flush_unique(ctx.db)
            return self._version_detail(ctx, storage, row)

    def change_version(self, ctx, storage, product_id, version_id, target):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            # 产品行串行化版本发布，保证只有一个版本可用于新任务。
            active_row(ctx.db, Product, product_id, lock=True)
            row = self._version(ctx, product_id, version_id)
            if row.status == target:
                return self._version_detail(ctx, storage, row)
            if target == "published":
                if row.status != "draft":
                    raise conflict("product_not_draft", "仅草稿可以发布")
                category = active_row(ctx.db, ProductCategory, row.category_id)
                images = product_images(ctx.db, [row.id])[row.id]
                if not (
                    category.is_enabled
                    and row.generation_modes
                    and any(ref.is_cover for ref, _ in images)
                    and any(ref.is_reference for ref, _ in images)
                ):
                    raise conflict(
                        "product_not_publishable", "请配置启用分类、封面、参考图及生成方式"
                    )
                ctx.db.execute(
                    update(ProductVersion)
                    .where(
                        ProductVersion.product_id == product_id,
                        ProductVersion.status == "published",
                        ProductVersion.is_del.is_(False),
                    )
                    .values(status="retired", updated_by=ctx.actor_id, updated_at=now_utc())
                )
                row.published_at = now_utc()
            elif row.status != "published":
                raise conflict("product_not_published", "仅已发布版本可以退役")
            row.status = target
            stamp(row, ctx.actor_id)
            flush_unique(ctx.db)
            return self._version_detail(ctx, storage, row)

    def image_authorization(self, ctx, storage, body):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            return authorize_upload(ctx, storage, body, PRODUCT_IMAGE)

    def image_completion(self, ctx, storage, asset_id):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
        return complete_upload(ctx, asset_id, PRODUCT_IMAGE, storage)
