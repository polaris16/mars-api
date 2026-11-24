"""[新增] 验证产品体验 ORM 和 PostgreSQL 迁移契约。"""

import re
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.models import Base

BASE_REVISION = "20261124_01"
PRODUCT_REVISION = "20261124_02"
PRODUCT_TABLES = {
    "t_product_category",
    "t_product",
    "t_product_version",
    "t_product_image",
    "t_product_experience_round",
    "t_product_experience_preview",
    "t_product_experience_result",
    "t_product_experience_share",
}
AUDIT_COLUMNS = {"created_by", "created_at", "updated_by", "updated_at", "is_del"}


def _migration_sql(*, upgrade: bool) -> str:
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    if upgrade:
        command.upgrade(config, f"{BASE_REVISION}:{PRODUCT_REVISION}", sql=True)
    else:
        command.downgrade(config, f"{PRODUCT_REVISION}:{BASE_REVISION}", sql=True)
    return output.getvalue()


def test_product_model_columns_have_comments_and_protect_reference_chain():
    tables = Base.metadata.tables
    assert {name for name in tables if name.startswith("t_product")} == PRODUCT_TABLES
    for name in PRODUCT_TABLES:
        table = tables[name]
        assert table.comment and AUDIT_COLUMNS <= set(table.c.keys())
        for column in table.columns:
            assert column.comment and re.search(r"[\u4e00-\u9fff]", column.comment)
        assert all(foreign_key.ondelete == "RESTRICT" for foreign_key in table.foreign_keys)
        assert not table.c.is_del.nullable
        assert str(table.c.is_del.server_default.arg) == "false"
    round_table = tables["t_product_experience_round"]
    assert str(round_table.c.preview_limit.server_default.arg) == "20"
    preview_fk = next(
        fk
        for fk in tables["t_product_experience_result"].foreign_key_constraints
        if fk.name == "fk_product_result_round_preview"
    )
    assert list(preview_fk.column_keys) == ["round_id", "preview_id"]
    assert [element.target_fullname for element in preview_fk.elements] == [
        "t_product_experience_preview.round_id",
        "t_product_experience_preview.id",
    ]


def test_product_upgrade_matches_metadata_without_writing_existing_business_data():
    sql = _migration_sql(upgrade=True)
    assert set(re.findall(r"^CREATE TABLE (\w+) \(", sql, re.M)) == PRODUCT_TABLES
    assert not re.search(r"^(DROP TABLE|INSERT INTO|DELETE FROM)\b", sql, re.M)
    assert re.findall(r"^UPDATE (\w+)", sql, re.M) == ["alembic_version"]
    tables = Base.metadata.tables
    for name in PRODUCT_TABLES:
        table = tables[name]
        assert f"COMMENT ON TABLE {name} IS '{table.comment}'" in sql
        for column in table.columns:
            assert f"COMMENT ON COLUMN {name}.{column.name} IS '{column.comment}'" in sql
        for constraint in table.constraints:
            if constraint.name is not None:
                assert f"CONSTRAINT {constraint.name} " in sql
        for index in table.indexes:
            assert f"INDEX {index.name} ON {name} " in sql

    for table_name, column_names in (
        ("t_media_asset", ("width_px", "height_px", "purpose", "upload_expires_at")),
        ("t_benefit_service_version", ("online_handler",)),
    ):
        for field in column_names:
            column = tables[table_name].c[field]
            assert f"COMMENT ON COLUMN {table_name}.{field} IS '{column.comment}'" in sql
    assert "DROP CONSTRAINT ck_t_media_asset_purpose" in sql
    assert "ADD CONSTRAINT ck_benefit_service_version_online_handler" in sql
    assert "ADD CONSTRAINT ck_t_media_asset_product_dimensions" in sql
    assert "AND purpose <> 'experience_generated'" in sql
    assert "ALTER COLUMN upload_expires_at DROP NOT NULL" in sql


def test_product_downgrade_guards_business_data_and_restores_old_media_rules():
    sql = _migration_sql(upgrade=False)
    dropped = re.findall(r"^DROP TABLE (\w+);", sql, re.M)
    assert set(dropped) == PRODUCT_TABLES
    assert sql.index("Product experience data prevents downgrade") < sql.index("DROP TABLE")
    assert "WHERE online_handler IS NOT NULL" in sql
    assert "purpose IN ('recording', 'source', 'attachment')" in sql
    assert "purpose <> 'experience_generated'" not in sql
    assert "ALTER COLUMN upload_expires_at SET NOT NULL" in sql
    position = {name: index for index, name in enumerate(dropped)}
    for name in dropped:
        for foreign_key in Base.metadata.tables[name].foreign_keys:
            parent_name = foreign_key.column.table.name
            if parent_name in PRODUCT_TABLES:
                assert position[name] < position[parent_name]
