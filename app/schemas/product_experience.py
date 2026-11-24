"""[新增] 产品目录、图片直传、体验轮次和分享的接口契约。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, StringConstraints, model_validator

from app.schemas.benefits import BenefitInput, Code, Id, Name, OrderIndex

Mode = Literal["try_on", "scene_composite"]
Angle = Literal["unspecified", "front", "side", "back", "detail"]
ImageType = Literal["image/jpeg", "image/png"]
RoundStatus = Literal["open", "completed", "expired"]
PreviewStatus = Literal["queued", "processing", "succeeded", "failed", "cancelled"]
VersionStatus = Literal["draft", "published", "retired"]
Direction = Literal["incoming", "outgoing"]
NameSort = Literal["default", "name_asc", "name_desc"]
CreatedSort = Literal["default", "created_at_asc", "created_at_desc"]
IdSort = Literal["default", "id_asc", "id_desc"]
VersionSort = Literal["default", "version_asc", "version_desc"]
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class CategoryCreate(BenefitInput):
    code: Code
    name: Name
    sort_order: OrderIndex = 0


class CatalogPatch(BenefitInput):
    sort_order: OrderIndex | None = None
    is_enabled: bool | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("至少提供一个非空的更新字段")
        return self


class CategoryPatch(CatalogPatch):
    name: Name | None = None


class ProductCreate(BenefitInput):
    code: Code
    sort_order: OrderIndex = 0


class ProductImageInput(BenefitInput):
    media_asset_id: Id
    angle: Angle = "unspecified"
    is_cover: bool = False
    is_reference: bool = True
    sort_order: OrderIndex = 0

    @model_validator(mode="after")
    def validate_usage(self):
        if not (self.is_cover or self.is_reference):
            raise ValueError("产品图片至少需要一种用途")
        return self


class ProductVersionInput(BenefitInput):
    category_id: Id
    name: Annotated[str, StringConstraints(min_length=1, max_length=150)]
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    generation_modes: list[Mode] = Field(default_factory=list, max_length=2)
    images: list[ProductImageInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_entries(self):
        if len(set(self.generation_modes)) != len(self.generation_modes):
            raise ValueError("生成方式不能重复")
        if len({image.media_asset_id for image in self.images}) != len(self.images):
            raise ValueError("同一版本不能重复引用图片")
        if sum(image.is_cover for image in self.images) > 1:
            raise ValueError("产品版本最多一张封面")
        return self


class ImageUpload(BenefitInput):
    filename: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    content_type: ImageType
    size_bytes: Annotated[int, Field(gt=0, le=MAX_UPLOAD_BYTES)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def validate_filename(self):
        if any(ord(character) < 32 for character in self.filename):
            raise ValueError("文件名包含无效字符")
        return self


class PhotoUpload(ImageUpload):
    round_id: Id


class PreviewInput(BenefitInput):
    photo_id: Id
    product_id: Id
    product_version_id: Id
    mode: Mode


class ConfirmInput(BenefitInput):
    preview_id: Id


class ShareInput(BenefitInput):
    recipient_user_id: Id


class CategoryView(BaseModel):
    id: int
    code: str
    name: str
    sort_order: int
    is_enabled: bool


class CategoriesView(BaseModel):
    items: list[CategoryView]


class ImageLink(BaseModel):
    image_url: str
    url_expires_at: datetime
    width_px: int
    height_px: int


class ProductImageView(ImageLink):
    media_asset_id: int
    angle: Angle
    is_cover: bool
    is_reference: bool
    sort_order: int


class ProductCard(BaseModel):
    product_id: int
    product_version_id: int
    name: str
    category_id: int
    generation_modes: list[Mode]
    cover: ProductImageView | None


class ProductVersionView(ProductCard):
    version_no: int
    status: VersionStatus
    description: str | None
    attributes: dict[str, JsonValue]
    images: list[ProductImageView]


class ProductAdminView(BaseModel):
    id: int
    code: str
    sort_order: int
    is_enabled: bool
    current_version: ProductCard | None


class ImageAuthorization(BaseModel):
    asset_id: int
    upload_url: str
    required_headers: dict[str, str]
    url_expires_at: datetime


class PhotoAuthorization(BaseModel):
    photo_id: int
    upload_url: str
    required_headers: dict[str, str]
    url_expires_at: datetime


class ImageConfirmed(ImageLink):
    asset_id: int
    status: Literal["uploaded"] = "uploaded"


class PhotoConfirmed(ImageLink):
    photo_id: int
    status: Literal["uploaded"] = "uploaded"


class PreviewView(BaseModel):
    preview_id: int
    status: PreviewStatus
    preview_remaining: int
    preview_url: str | None = None
    url_expires_at: datetime | None = None
    width_px: int | None = None
    height_px: int | None = None
    error_code: str | None = None


class RoundView(BaseModel):
    round_id: int
    status: RoundStatus
    expires_at: datetime
    preview_limit: int
    preview_remaining: int


class RoundDetail(RoundView):
    previews: list[PreviewView]
    result_id: int | None


class ConfirmView(BaseModel):
    result_id: int
    service_order_id: int
    round_id: int
    remaining_benefit_count: int


class ResultCard(BaseModel):
    result_id: int
    product_id: int
    product_name: str
    thumbnail: ImageLink
    created_at: datetime


class ResultView(ResultCard):
    product_version_id: int
    mode: Mode
    image: ImageLink


class ShareCard(BaseModel):
    share_id: int
    result_id: int
    sender_user_id: int
    recipient_user_id: int
    created_at: datetime
    status: Literal["active", "revoked", "unavailable"]


class ShareView(ShareCard):
    image: ImageLink
