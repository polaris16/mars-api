"""[新增] 验证青年伙伴模型与 PostgreSQL 迁移契约。"""

import re
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.models import Base

BASE_REVISION = "20261124_02"
YOUTH_REVISION = "20261125_01"
TABLES = {
    "t_benefit_grant_service_location",
    "t_youth_partner_availability",
    "t_youth_partner_booking",
    "t_youth_partner_check_in_code",
    "t_youth_partner_identity_access",
    "t_youth_partner_identity_document",
    "t_youth_partner_profile",
    "t_youth_partner_rating",
    "t_youth_partner_service",
}


def migration_sql(*, upgrade: bool) -> str:
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    if upgrade:
        command.upgrade(config, f"{BASE_REVISION}:{YOUTH_REVISION}", sql=True)
    else:
        command.downgrade(config, f"{YOUTH_REVISION}:{BASE_REVISION}", sql=True)
    return output.getvalue()


def test_youth_upgrade_adds_documented_tables_without_changing_business_rows():
    sql = migration_sql(upgrade=True)
    assert set(re.findall(r"^CREATE TABLE (\w+) \(", sql, re.M)) == TABLES
    assert not re.search(r"^(DROP TABLE|DELETE FROM|INSERT INTO)\b", sql, re.M)
    assert re.findall(r"^UPDATE (\w+)", sql, re.M) == ["alembic_version"]
    for table_name in TABLES:
        table = Base.metadata.tables[table_name]
        assert table.comment and not table.c.is_del.nullable
        assert {"created_by", "created_at", "updated_by", "updated_at", "is_del"} <= set(
            table.c.keys()
        )
        for column in table.columns:
            assert column.comment and re.search(r"[\u4e00-\u9fff]", column.comment)
            assert f"COMMENT ON COLUMN {table_name}.{column.name}" in sql
        for fk in table.foreign_keys:
            assert fk.ondelete == "RESTRICT"
        for index in table.indexes:
            assert f"INDEX {index.name} ON {table_name}" in sql
    assert "ADD CONSTRAINT ck_benefit_service_version_durations" in sql
    assert "ADD CONSTRAINT ck_t_media_asset_partner_image" in sql
    assert "(action IN ('complete', 'check_in')) = (used_delta = 1)" in sql
    assert "(action <> 'finish_partner_service' OR reserved_delta = 0)" in sql
    for table_name, columns in {
        "t_benefit_staff": ("role",),
        "t_media_asset": ("purpose",),
        "t_benefit_order_event": ("action",),
        "t_benefit_service_version": (
            "online_duration_minutes",
            "offline_duration_minutes",
            "appointment_handler",
        ),
    }.items():
        table = Base.metadata.tables[table_name]
        for name in columns:
            assert f"COMMENT ON COLUMN {table_name}.{name} IS '{table.c[name].comment}'" in sql


def test_youth_downgrade_guards_business_data_and_drops_dependents_first():
    sql = migration_sql(upgrade=False)
    dropped = re.findall(r"^DROP TABLE (\w+);", sql, re.M)
    assert set(dropped) == TABLES
    assert sql.index("Youth partner data must be preserved") < sql.index("DROP TABLE")
    for name in dropped:
        for fk in Base.metadata.tables[name].foreign_keys:
            parent = fk.column.table.name
            if parent in TABLES:
                assert dropped.index(name) < dropped.index(parent)
    assert "(action = 'complete') = (used_delta = 1)" in sql
