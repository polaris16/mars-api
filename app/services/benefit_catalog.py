"""[新增] 企业岗位、版本化权益商品和采购配置。"""

import hashlib
import re
from collections import defaultdict

from sqlalchemy import func, select

from app.models import (
    BenefitBatchContact,
    BenefitGrant,
    BenefitIssueRequest,
    BenefitPackage,
    BenefitPackageItem,
    BenefitPurchaseBatch,
    BenefitService,
    BenefitServiceVersion,
    BenefitStaff,
    Enterprise,
    EnterpriseMember,
    User,
)
from app.schemas.benefits import (
    BenefitNameSort,
    BenefitPackageSort,
    BenefitPurchaseSort,
    BenefitServiceSort,
    BenefitStaffSort,
    BenefitVersionSort,
)
from app.services.benefit_common import (
    BenefitError,
    active_row,
    conflict,
    display,
    flush_unique,
    missing,
    new_row,
    now_utc,
    page_data,
    paginate,
    select_page,
    stamp,
    utc,
)

ENTERPRISE_FIELDS = "id code display_name icon_url status"
MEMBER_FIELDS = "id enterprise_id user_id role status work_display_name work_title work_phone"
VERSION_FIELDS = (
    "id service_id version_no name summary description_ref terms_ref delivery_terms "
    "fulfillment_mode status published_at"
)
PACKAGE_FIELDS = "id package_code version_no title description icon_url status published_at"
ITEM_FIELDS = "id service_version_id quota_per_unit icon_key sort_order"
PURCHASE_FIELDS = (
    "id enterprise_id package_id purchase_reference purchased_units currency status activated_at "
    "closed_at issue_until claim_until service_valid_from service_valid_until display_timezone"
)
DOCUMENT_LIMIT = 512 * 1024


def read_document(storage, ref: str) -> str:
    match = re.fullmatch(r"benefits/documents/([a-f0-9]{64})\.txt", ref)
    if match is None:
        raise conflict("benefit_document_mismatch", "权益文档引用无效")
    data = storage.read_text_source(ref, DOCUMENT_LIMIT, 10)
    if not data or len(data) > DOCUMENT_LIMIT or hashlib.sha256(data).hexdigest() != match[1]:
        raise conflict("benefit_document_mismatch", "权益文档内容与固定版本引用不匹配")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise conflict("benefit_document_mismatch", "权益文档必须为 UTF-8 文本") from None


def purchase_grants(batch_id: int):
    return (
        select(BenefitGrant)
        .join(BenefitIssueRequest)
        .where(BenefitIssueRequest.purchase_batch_id == batch_id)
    )


def purchase_view(db, batch, *, private: bool = False) -> dict:
    counts = dict(
        db.execute(
            purchase_grants(batch.id)
            .with_only_columns(BenefitGrant.status, func.count())
            .where(
                (BenefitGrant.status == "claimed")
                | (
                    (BenefitGrant.status == "issued")
                    & BenefitGrant.is_del.is_(False)
                    & (BenefitGrant.claim_expires_at > now_utc())
                )
            )
            .group_by(BenefitGrant.status)
        ).all()
    )
    view = display(batch, PURCHASE_FIELDS)
    view.update(
        available_units=batch.purchased_units - counts.get("claimed", 0) - counts.get("issued", 0),
        pending_units=counts.get("issued", 0),
        claimed_units=counts.get("claimed", 0),
    )
    if private:
        view.update(display(batch, "paid_at paid_amount_minor purchase_contract_ref"))
    return view


def contacts(db, batch_id: int) -> list[dict]:
    rows = db.scalars(
        select(EnterpriseMember)
        .join(BenefitBatchContact)
        .join(
            BenefitPurchaseBatch, BenefitPurchaseBatch.id == BenefitBatchContact.purchase_batch_id
        )
        .join(Enterprise, Enterprise.id == EnterpriseMember.enterprise_id)
        .join(User, User.id == EnterpriseMember.user_id)
        .where(
            BenefitBatchContact.purchase_batch_id == batch_id,
            BenefitBatchContact.is_del.is_(False),
            BenefitPurchaseBatch.enterprise_id == EnterpriseMember.enterprise_id,
            Enterprise.is_del.is_(False),
            Enterprise.status == "active",
            EnterpriseMember.is_del.is_(False),
            EnterpriseMember.status == "active",
            EnterpriseMember.work_phone.is_not(None),
            User.is_del.is_(False),
        )
        .order_by(BenefitBatchContact.sort_order, EnterpriseMember.id)
    )
    return [display(row, "id work_display_name work_title work_phone") for row in rows]


def scoped_purchase(db, batch_id: int, enterprise_id: int, *, lock: bool = False):
    stmt = select(BenefitPurchaseBatch).where(
        BenefitPurchaseBatch.id == batch_id,
        BenefitPurchaseBatch.enterprise_id == enterprise_id,
        BenefitPurchaseBatch.is_del.is_(False),
    )
    row = db.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt
    )
    if row is None:
        raise missing()
    return row


class BenefitCatalogService:
    def enterprises(
        self,
        ctx,
        page,
        size,
        *,
        own=False,
        sort: BenefitNameSort = "default",
        paginate_results: bool = True,
    ):
        with ctx.db.begin():
            # [删除-旧] stmt = select(Enterprise).where(Enterprise.is_del.is_(False)).order_by(Enterprise.id)
            # [新增] 只对调用者可见的企业排序，按 ID 稳定处理同名企业。
            name = func.lower(Enterprise.display_name)
            order = {
                "default": (Enterprise.id,),
                "name_asc": (name.asc(), Enterprise.id),
                "name_desc": (name.desc(), Enterprise.id),
            }[sort]
            stmt = select(Enterprise).where(Enterprise.is_del.is_(False)).order_by(*order)
            render = lambda row: display(row, ENTERPRISE_FIELDS)
            if own:
                roles = dict(
                    ctx.db.execute(
                        select(EnterpriseMember.enterprise_id, EnterpriseMember.role).where(
                            EnterpriseMember.user_id == ctx.actor_id,
                            EnterpriseMember.status == "active",
                            EnterpriseMember.is_del.is_(False),
                        )
                    ).all()
                )
                stmt = stmt.where(Enterprise.id.in_(roles), Enterprise.status == "active")

                def render(row):
                    return {**display(row, ENTERPRISE_FIELDS), "role": roles[row.id]}
            else:
                ctx.require_platform("admin", "ops")
            # [删除-旧] return paginate(ctx.db, stmt, page, size, render)
            return paginate(ctx.db, stmt, page, size, render, paginate_results=paginate_results)

    def write_enterprise(self, ctx, body, enterprise_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            if enterprise_id is None:
                active_row(ctx.db, User, body.admin_user_id)
                row = new_row(
                    Enterprise, ctx.actor_id, **body.model_dump(exclude={"admin_user_id"})
                )
                ctx.db.add(row)
                flush_unique(ctx.db)
                ctx.db.add(
                    new_row(
                        EnterpriseMember,
                        ctx.actor_id,
                        enterprise_id=row.id,
                        user_id=body.admin_user_id,
                        role="admin",
                    )
                )
            else:
                row = active_row(ctx.db, Enterprise, enterprise_id, lock=True)
                for key, value in body.model_dump(exclude_unset=True).items():
                    if value is None and key != "icon_url":
                        raise BenefitError(422, "benefit_invalid_field", "名称及状态不能清空")
                    setattr(row, key, value)
                stamp(row, ctx.actor_id)
            return display(row, ENTERPRISE_FIELDS)

    def members(self, ctx, enterprise_id, page, size, *, sort: BenefitNameSort = "default"):
        with ctx.db.begin():
            ctx.enterprise(enterprise_id, "admin")
            name = func.lower(func.nullif(func.trim(EnterpriseMember.work_display_name), ""))
            order = {
                "default": (EnterpriseMember.id,),
                "name_asc": (name.asc().nulls_last(), EnterpriseMember.id),
                "name_desc": (name.desc().nulls_last(), EnterpriseMember.id),
            }[sort]
            stmt = (
                select(EnterpriseMember)
                .where(
                    EnterpriseMember.enterprise_id == enterprise_id,
                    EnterpriseMember.is_del.is_(False),
                )
                # [删除-旧] .order_by(EnterpriseMember.id)
                .order_by(*order)
            )
            return paginate(ctx.db, stmt, page, size, lambda row: display(row, MEMBER_FIELDS))

    def write_member(self, ctx, enterprise_id, body, member_id=None):
        with ctx.db.begin():
            ctx.enterprise(enterprise_id, "admin", lock=True)
            if member_id is None:
                active_row(ctx.db, User, body.user_id)
                row = ctx.db.scalar(
                    select(EnterpriseMember).where(
                        EnterpriseMember.enterprise_id == enterprise_id,
                        EnterpriseMember.user_id == body.user_id,
                    )
                )
                if row is not None and row.status == "active" and not row.is_del:
                    raise conflict("benefit_duplicate", "该账号已是企业成员")
                if row is None:
                    row = new_row(EnterpriseMember, ctx.actor_id, enterprise_id=enterprise_id)
                    ctx.db.add(row)
                row.is_del = False
            else:
                row = active_row(ctx.db, EnterpriseMember, member_id)
                if row.enterprise_id != enterprise_id:
                    raise missing()
            values = body.model_dump(exclude_unset=member_id is not None)
            if any(key in values and values[key] is None for key in ("role", "status")):
                raise BenefitError(422, "benefit_invalid_field", "角色及状态不能清空")
            if (
                row.role == "admin"
                and row.status == "active"
                and (
                    values.get("role", "admin") != "admin"
                    or values.get("status", "active") != "active"
                )
            ):
                another_admin = ctx.db.scalar(
                    select(EnterpriseMember.id)
                    .join(User)
                    .where(
                        EnterpriseMember.enterprise_id == enterprise_id,
                        EnterpriseMember.id != row.id,
                        EnterpriseMember.is_del.is_(False),
                        EnterpriseMember.role == "admin",
                        EnterpriseMember.status == "active",
                        User.is_del.is_(False),
                    )
                    .limit(1)
                )
                if another_admin is None:
                    raise conflict("last_enterprise_admin", "企业至少保留一位有效管理员")
            for key, value in values.items():
                setattr(row, key, value)
            stamp(row, ctx.actor_id)
            flush_unique(ctx.db)
            return display(row, MEMBER_FIELDS)

    def staff(self, ctx, page, size, *, sort: BenefitStaffSort = "default"):
        with ctx.db.begin():
            ctx.require_platform("admin")
            # [新增] 显式岗位排序按岗位编码，同岗位按记录 ID。
            order = {
                "default": (BenefitStaff.id,),
                "role_asc": (BenefitStaff.role.asc(), BenefitStaff.id),
                "role_desc": (BenefitStaff.role.desc(), BenefitStaff.id),
            }[sort]
            stmt = (
                # [删除-旧] select(BenefitStaff).where(BenefitStaff.is_del.is_(False)).order_by(BenefitStaff.id)
                select(BenefitStaff).where(BenefitStaff.is_del.is_(False)).order_by(*order)
            )
            return paginate(
                ctx.db, stmt, page, size, lambda row: display(row, "id user_id role status")
            )

    def replace_staff(self, ctx, user_id, body):
        with ctx.db.begin():
            ctx.require_platform("admin")
            active_row(ctx.db, User, user_id, lock=True)
            rows = {
                row.role: row
                for row in ctx.db.scalars(
                    select(BenefitStaff)
                    .where(BenefitStaff.user_id == user_id)
                    .order_by(BenefitStaff.role)
                    .with_for_update()
                )
            }
            for role, row in rows.items():
                row.status = "active" if role in body.roles else "disabled"
                row.is_del = False
                stamp(row, ctx.actor_id)
            for role in sorted(body.roles - rows.keys()):
                ctx.db.add(new_row(BenefitStaff, ctx.actor_id, user_id=user_id, role=role))
            flush_unique(ctx.db)
            return {"user_id": user_id, "roles": sorted(body.roles)}

    @staticmethod
    def service_view(db, row):
        latest = db.scalar(
            select(BenefitServiceVersion)
            .where(
                BenefitServiceVersion.service_id == row.id,
                BenefitServiceVersion.status == "published",
                BenefitServiceVersion.is_del.is_(False),
            )
            .order_by(BenefitServiceVersion.version_no.desc())
            .limit(1)
        )
        return BenefitCatalogService._service_data(row, latest)

    # [新增] 写入响应和列表共用服务目录的展示字段。
    @staticmethod
    def _service_data(row, latest):
        return {
            **display(row, "id code status"),
            "latest_version": display(latest, VERSION_FIELDS) if latest else None,
        }

    def services(
        self,
        ctx,
        page,
        size,
        *,
        paginate_results: bool = True,
        sort: BenefitServiceSort = "default",
    ):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            # [删除-旧] stmt = (
            #     select(BenefitService)
            #     .where(BenefitService.is_del.is_(False))
            #     .order_by(BenefitService.id)
            # )
            # return paginate(ctx.db, stmt, page, size, lambda row: self.service_view(ctx.db, row))
            # [新增] 先排序切页，再一次查询当前结果中的最新已发布版本。
            service = BenefitService
            version = BenefitServiceVersion
            order = {
                "default": (service.id,),
                "code_asc": (service.code.asc(), service.id),
                "code_desc": (service.code.desc(), service.id),
            }[sort]
            visible = select(service).where(service.is_del.is_(False))
            rows, page, size, total = select_page(
                ctx.db, visible.order_by(*order), page, size, paginate_results=paginate_results
            )
            if not rows:
                return page_data([], page, size, total)
            latest_id = (
                select(version.id)
                .where(
                    version.service_id == service.id,
                    version.status == "published",
                    version.is_del.is_(False),
                )
                .order_by(version.version_no.desc())
                .limit(1)
                .correlate(service)
                .scalar_subquery()
            )
            service_ids = (
                [row.id for row in rows]
                if paginate_results
                else visible.with_only_columns(service.id)
            )
            latest = ctx.db.scalars(
                select(version)
                .join(service, version.id == latest_id)
                .where(service.id.in_(service_ids))
            )
            versions = {row.service_id: row for row in latest}
            return page_data(
                [self._service_data(row, versions.get(row.id)) for row in rows], page, size, total
            )

    def write_service(self, ctx, body, service_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            if service_id is None:
                row = new_row(BenefitService, ctx.actor_id, code=body.code)
                ctx.db.add(row)
            else:
                row = active_row(ctx.db, BenefitService, service_id, lock=True)
                row.status = body.status
                stamp(row, ctx.actor_id)
            flush_unique(ctx.db)
            return self.service_view(ctx.db, row)

    def versions(
        self,
        ctx,
        service_id,
        page,
        size,
        *,
        paginate_results: bool = True,
        sort: BenefitVersionSort = "default",
    ):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            active_row(ctx.db, BenefitService, service_id)
            stmt = (
                select(BenefitServiceVersion)
                .where(
                    BenefitServiceVersion.service_id == service_id,
                    BenefitServiceVersion.is_del.is_(False),
                )
                # [删除-旧] .order_by(BenefitServiceVersion.version_no.desc())
                .order_by(
                    (
                        BenefitServiceVersion.version_no.asc()
                        if sort == "version_asc"
                        else BenefitServiceVersion.version_no.desc()
                    ),
                    BenefitServiceVersion.id,
                )
            )
            # [删除-旧] return paginate(ctx.db, stmt, page, size, lambda row: display(row, VERSION_FIELDS))
            return paginate(
                ctx.db,
                stmt,
                page,
                size,
                lambda row: display(row, VERSION_FIELDS),
                paginate_results=paginate_results,
            )

    @staticmethod
    def scoped_version(db, service_id, version_id, *, lock=False):
        row = active_row(db, BenefitServiceVersion, version_id, lock=lock)
        if row.service_id != service_id:
            raise missing()
        return row

    def write_version(self, ctx, service_id, body, version_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            active_row(ctx.db, BenefitService, service_id, lock=True)
            if version_id is None:
                version = ctx.db.scalar(
                    select(func.max(BenefitServiceVersion.version_no)).where(
                        BenefitServiceVersion.service_id == service_id
                    )
                )
                row = new_row(
                    BenefitServiceVersion,
                    ctx.actor_id,
                    service_id=service_id,
                    version_no=(version or 0) + 1,
                    **body.model_dump(),
                )
                ctx.db.add(row)
            else:
                row = self.scoped_version(ctx.db, service_id, version_id, lock=True)
                self.require_draft(row)
                for key, value in body.model_dump().items():
                    setattr(row, key, value)
                stamp(row, ctx.actor_id)
            flush_unique(ctx.db)
            return display(row, VERSION_FIELDS)

    def change_version_state(self, ctx, service_id, version_id, state, storage=None):
        if state == "published":
            with ctx.db.begin():
                ctx.require_platform("admin", "ops")
                row = self.scoped_version(ctx.db, service_id, version_id)
                if row.status == state:
                    return display(row, VERSION_FIELDS)
                self.require_draft(row)
                refs = (row.description_ref, row.terms_ref)
            for ref in dict.fromkeys(refs):
                read_document(storage, ref)
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            service = active_row(ctx.db, BenefitService, service_id, lock=True)
            row = self.scoped_version(ctx.db, service_id, version_id, lock=True)
            if row.status == state:
                return display(row, VERSION_FIELDS)
            if state == "published":
                self.require_draft(row)
                if service.status != "active" or refs != (row.description_ref, row.terms_ref):
                    raise conflict("benefit_version_conflict", "目录或文档引用已变更，请刷新后发布")
                row.published_at = now_utc()
            elif row.status != "published":
                raise conflict("benefit_version_conflict", "仅已发布版本可退役")
            row.status = state
            stamp(row, ctx.actor_id)
            return display(row, VERSION_FIELDS)

    @staticmethod
    def require_draft(row):
        if row.status != "draft":
            raise conflict("benefit_not_draft", "仅草稿可以修改")

    @staticmethod
    def package_items(db, package_id):
        return db.scalars(
            select(BenefitPackageItem)
            .where(
                BenefitPackageItem.package_id == package_id,
                BenefitPackageItem.is_del.is_(False),
            )
            .order_by(BenefitPackageItem.sort_order, BenefitPackageItem.id)
        ).all()

    def package_view(self, db, row):
        return {
            **display(row, PACKAGE_FIELDS),
            "items": [display(item, ITEM_FIELDS) for item in self.package_items(db, row.id)],
        }

    def packages(
        self,
        ctx,
        page,
        size,
        package_id=None,
        *,
        paginate_results: bool = True,
        sort: BenefitPackageSort = "default",
    ):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            if package_id is not None:
                return self.package_view(ctx.db, active_row(ctx.db, BenefitPackage, package_id))
            # [删除-旧] stmt = (
            #     select(BenefitPackage)
            #     .where(BenefitPackage.is_del.is_(False))
            #     .order_by(BenefitPackage.id.desc())
            # )
            # return paginate(ctx.db, stmt, page, size, lambda row: self.package_view(ctx.db, row))
            # [新增] 全量结果也只批量查询一次权益包明细。
            package = BenefitPackage
            item = BenefitPackageItem
            order = {
                "default": (package.id.desc(),),
                "title_asc": (func.lower(package.title).asc(), package.id.desc()),
                "title_desc": (func.lower(package.title).desc(), package.id.desc()),
            }[sort]
            visible = select(package).where(package.is_del.is_(False))
            rows, page, size, total = select_page(
                ctx.db, visible.order_by(*order), page, size, paginate_results=paginate_results
            )
            if not rows:
                return page_data([], page, size, total)
            package_ids = (
                [row.id for row in rows]
                if paginate_results
                else visible.with_only_columns(package.id)
            )
            items = defaultdict(list)
            for row in ctx.db.scalars(
                select(item)
                .where(item.package_id.in_(package_ids), item.is_del.is_(False))
                .order_by(item.package_id, item.sort_order, item.id)
            ):
                items[row.package_id].append(display(row, ITEM_FIELDS))
            return page_data(
                [{**display(row, PACKAGE_FIELDS), "items": items[row.id]} for row in rows],
                page,
                size,
                total,
            )

    def sellable_items(self, db, package_id, *, supported=False):
        items = self.package_items(db, package_id)
        if not items:
            raise conflict("benefit_package_empty", "权益包至少需要一个服务项")
        version_ids = {item.service_version_id for item in items}
        service_ids = set(
            db.scalars(
                select(BenefitServiceVersion.service_id).where(
                    BenefitServiceVersion.id.in_(version_ids)
                )
            )
        )
        # 与服务版本发布和退役一致，先锁主档再锁版本。
        services = {id: active_row(db, BenefitService, id, lock=True) for id in sorted(service_ids)}
        for id in sorted(version_ids):
            version = active_row(db, BenefitServiceVersion, id, lock=True)
            service = services[version.service_id]
            if version.status != "published" or service.status != "active":
                raise conflict("benefit_service_unavailable", "权益包含未发布或已停用的服务版本")
            if supported and version.fulfillment_mode != "appointment":
                raise conflict(
                    "benefit_fulfillment_unavailable", "该计划包含暂未接入的线上履约服务"
                )

    def write_package(self, ctx, body, package_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            fields = body.model_dump(exclude={"items", "package_code"})
            if package_id is None:
                version = ctx.db.scalar(
                    select(func.max(BenefitPackage.version_no)).where(
                        BenefitPackage.package_code == body.package_code
                    )
                )
                row = new_row(
                    BenefitPackage,
                    ctx.actor_id,
                    package_code=body.package_code,
                    version_no=(version or 0) + 1,
                    **fields,
                )
                ctx.db.add(row)
                flush_unique(ctx.db)
            else:
                row = active_row(ctx.db, BenefitPackage, package_id, lock=True)
                self.require_draft(row)
                for key, value in fields.items():
                    setattr(row, key, value)
                stamp(row, ctx.actor_id)
            existing = {
                item.service_version_id: item
                for item in ctx.db.scalars(
                    select(BenefitPackageItem).where(BenefitPackageItem.package_id == row.id)
                )
            }
            for item in body.items:
                active_row(ctx.db, BenefitServiceVersion, item.service_version_id)
                current = existing.pop(item.service_version_id, None)
                if current is None:
                    current = new_row(BenefitPackageItem, ctx.actor_id, package_id=row.id)
                    ctx.db.add(current)
                for key, value in item.model_dump().items():
                    setattr(current, key, value)
                current.is_del = False
                stamp(current, ctx.actor_id)
            for removed in existing.values():
                removed.is_del = True
                stamp(removed, ctx.actor_id)
            flush_unique(ctx.db)
            return self.package_view(ctx.db, row)

    def change_package_state(self, ctx, package_id, state):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            row = active_row(ctx.db, BenefitPackage, package_id, lock=True)
            if row.status != state:
                if state == "published":
                    self.require_draft(row)
                    self.sellable_items(ctx.db, row.id)
                    row.published_at = now_utc()
                elif row.status != "published":
                    raise conflict("benefit_version_conflict", "仅已发布版本可退役")
                row.status = state
                stamp(row, ctx.actor_id)
            return self.package_view(ctx.db, row)

    def purchases(
        self,
        ctx,
        page,
        size,
        enterprise_id=None,
        batch_id=None,
        status=None,
        *,
        sort: BenefitPurchaseSort = "default",
    ):
        with ctx.db.begin():
            if enterprise_id is None:
                ctx.require_platform("admin", "ops")
                private = True
            else:
                _, member = ctx.enterprise(enterprise_id, "admin", "issuer")
                private = member.role == "admin"
            if batch_id is not None:
                row = (
                    scoped_purchase(ctx.db, batch_id, enterprise_id)
                    if enterprise_id
                    else active_row(ctx.db, BenefitPurchaseBatch, batch_id)
                )
                return {
                    **purchase_view(ctx.db, row, private=private),
                    "contacts": contacts(ctx.db, row.id),
                }
            stmt = select(BenefitPurchaseBatch).where(BenefitPurchaseBatch.is_del.is_(False))
            if enterprise_id is not None:
                stmt = stmt.where(BenefitPurchaseBatch.enterprise_id == enterprise_id)
            if status:
                stmt = stmt.where(BenefitPurchaseBatch.status == status)
            # [新增] 保留默认 ID 倒序，显式排序以采购批次创建时间为准。
            batch = BenefitPurchaseBatch
            order = {
                "default": (batch.id.desc(),),
                "created_at_asc": (batch.created_at.asc(), batch.id.desc()),
                "created_at_desc": (batch.created_at.desc(), batch.id.desc()),
            }[sort]
            return paginate(
                ctx.db,
                # [删除-旧] stmt.order_by(BenefitPurchaseBatch.id.desc()),
                stmt.order_by(*order),
                page,
                size,
                lambda row: purchase_view(ctx.db, row, private=private),
            )

    def purchase_contract(self, ctx, batch_id, storage, enterprise_id=None):
        with ctx.db.begin():
            if enterprise_id is None:
                ctx.require_platform("admin", "ops")
                row = active_row(ctx.db, BenefitPurchaseBatch, batch_id)
            else:
                ctx.enterprise(enterprise_id, "admin")
                row = scoped_purchase(ctx.db, batch_id, enterprise_id)
            ref = row.purchase_contract_ref
            if ref is None:
                raise missing()
        return {"purchase_batch_id": batch_id, "content": read_document(storage, ref)}

    def write_purchase(self, ctx, body, batch_id=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            active_row(ctx.db, Enterprise, body.enterprise_id)
            if active_row(ctx.db, BenefitPackage, body.package_id).status != "published":
                raise conflict("benefit_package_unavailable", "仅已发布权益包可采购")
            if batch_id is None:
                row = new_row(BenefitPurchaseBatch, ctx.actor_id, **body.model_dump())
                ctx.db.add(row)
            else:
                row = active_row(ctx.db, BenefitPurchaseBatch, batch_id, lock=True)
                self.require_draft(row)
                different_enterprise = row.enterprise_id != body.enterprise_id
                if (
                    different_enterprise
                    and ctx.db.scalar(
                        select(BenefitBatchContact.enterprise_member_id)
                        .where(
                            BenefitBatchContact.purchase_batch_id == row.id,
                            BenefitBatchContact.is_del.is_(False),
                        )
                        .limit(1)
                    )
                    is not None
                ):
                    raise conflict("benefit_purchase_conflict", "已有联系人配置，不能变更采购企业")
                for key, value in body.model_dump().items():
                    setattr(row, key, value)
                stamp(row, ctx.actor_id)
            flush_unique(ctx.db)
            return purchase_view(ctx.db, row, private=True)

    def change_purchase_state(self, ctx, batch_id, state, paid_at=None):
        with ctx.db.begin():
            ctx.require_platform("admin", "ops")
            row = active_row(ctx.db, BenefitPurchaseBatch, batch_id, lock=True)
            if row.status != state:
                if state == "active":
                    self.require_draft(row)
                    now = now_utc()
                    org = active_row(ctx.db, Enterprise, row.enterprise_id)
                    package = active_row(ctx.db, BenefitPackage, row.package_id, lock=True)
                    if org.status != "active" or package.status != "published":
                        raise conflict("benefit_purchase_conflict", "企业和权益包须有效")
                    if not utc(paid_at) <= now < utc(row.issue_until):
                        raise conflict(
                            "benefit_purchase_window", "付款或企业发放时间不满足生效条件"
                        )
                    self.sellable_items(ctx.db, package.id, supported=True)
                    row.paid_at, row.activated_at = paid_at, now
                else:
                    if row.status != "active":
                        raise conflict("benefit_purchase_conflict", "仅已生效采购可停止发放")
                    row.closed_at = now_utc()
                row.status = state
                stamp(row, ctx.actor_id)
            return purchase_view(ctx.db, row, private=True)

    def replace_contacts(self, ctx, enterprise_id, batch_id, body):
        with ctx.db.begin():
            ctx.enterprise(enterprise_id, "admin", lock=True)
            scoped_purchase(ctx.db, batch_id, enterprise_id, lock=True)
            valid_ids = set(
                ctx.db.scalars(
                    select(EnterpriseMember.id)
                    .join(User)
                    .where(
                        EnterpriseMember.enterprise_id == enterprise_id,
                        EnterpriseMember.id.in_(body.member_ids),
                        EnterpriseMember.is_del.is_(False),
                        EnterpriseMember.status == "active",
                        EnterpriseMember.work_phone.is_not(None),
                        User.is_del.is_(False),
                    )
                )
            )
            if valid_ids != set(body.member_ids):
                raise BenefitError(
                    422, "benefit_contact_unavailable", "联系人须是有工作电话的有效企业成员"
                )
            existing = {
                row.enterprise_member_id: row
                for row in ctx.db.scalars(
                    select(BenefitBatchContact).where(
                        BenefitBatchContact.purchase_batch_id == batch_id
                    )
                )
            }
            for position, member_id in enumerate(body.member_ids):
                row = existing.pop(member_id, None)
                if row is None:
                    row = new_row(
                        BenefitBatchContact,
                        ctx.actor_id,
                        purchase_batch_id=batch_id,
                        enterprise_member_id=member_id,
                    )
                    ctx.db.add(row)
                row.is_del = False
                row.sort_order = position
                stamp(row, ctx.actor_id)
            for removed in existing.values():
                removed.is_del = True
                stamp(removed, ctx.actor_id)
            ctx.db.flush()
            return {"items": contacts(ctx.db, batch_id)}
