"""[新增] 校验企业权益模型注册与 PostgreSQL 迁移边界。"""

import re
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.models import Base

BASE_REVISION = "20261122_01"
BENEFIT_REVISION = "20261122_02"
BENEFIT_TABLES = {
    "t_enterprise",
    "t_enterprise_member",
    "t_benefit_staff",
    "t_benefit_service",
    "t_benefit_service_version",
    "t_benefit_package",
    "t_benefit_package_item",
    "t_benefit_purchase_batch",
    "t_benefit_batch_contact",
    "t_benefit_issue_request",
    "t_benefit_grant",
    "t_benefit_balance",
    "t_benefit_service_order",
    "t_benefit_order_event",
}
AUDIT_COLUMNS = {"created_by", "created_at", "updated_by", "updated_at", "is_del"}


def _migration_sql(*, upgrade: bool) -> str:
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    if upgrade:
        command.upgrade(config, f"{BASE_REVISION}:{BENEFIT_REVISION}", sql=True)
    else:
        command.downgrade(config, f"{BENEFIT_REVISION}:{BASE_REVISION}", sql=True)
    return output.getvalue()


def test_benefit_model_registration_comments_and_reference_chain():
    tables = Base.metadata.tables
    assert {name for name in tables if name.startswith(("t_benefit_", "t_enterprise"))} == (
        BENEFIT_TABLES
    )
    for name in BENEFIT_TABLES:
        table = tables[name]
        assert table.comment and AUDIT_COLUMNS <= set(table.c.keys())
        for column in table.columns:
            assert column.comment and re.search(r"[\u4e00-\u9fff]", column.comment)
        assert all(fk.ondelete == "RESTRICT" for fk in table.foreign_keys)
        assert not table.c.is_del.nullable and str(table.c.is_del.server_default.arg) == "false"
        assert not table.c.created_at.nullable and not table.c.updated_at.nullable

    assert {fk.target_fullname for fk in tables["t_benefit_package_item"].foreign_keys} == {
        "t_benefit_package.id",
        "t_benefit_service_version.id",
    }
    assert {fk.target_fullname for fk in tables["t_benefit_grant"].foreign_keys} == {
        "t_benefit_issue_request.id",
        "t_user.id",
    }
    assert {fk.target_fullname for fk in tables["t_benefit_order_event"].foreign_keys} == {
        "t_benefit_service_order.id",
    }


def test_benefit_migration_adds_only_new_tables_and_integrity_checks():
    sql = _migration_sql(upgrade=True)
    assert set(re.findall(r"^CREATE TABLE (\w+) \(", sql, re.M)) == BENEFIT_TABLES
    assert not re.search(r"^(ALTER TABLE|DROP TABLE|INSERT INTO|DELETE FROM)\b", sql, re.M)
    assert re.findall(r"^UPDATE (\w+)", sql, re.M) == ["alembic_version"]

    for name in BENEFIT_TABLES:
        table = Base.metadata.tables[name]
        assert f"COMMENT ON TABLE {name} IS '{table.comment}'" in sql
        for column in table.columns:
            assert f"COMMENT ON COLUMN {name}.{column.name} IS '{column.comment}'" in sql
        for constraint in table.constraints:
            if constraint.name is not None:
                assert f"CONSTRAINT {constraint.name} " in sql
        for index in table.indexes:
            assert f"INDEX {index.name} ON {name} " in sql

    assert "code_digest BYTEA NOT NULL" in sql
    assert "idempotency_key UUID NOT NULL" in sql
    assert "request_data JSONB DEFAULT '{}'::jsonb NOT NULL" in sql
    assert "reserved_count <= granted_count - used_count" in sql
    assert "UNIQUE (service_order_id, action_key)" in sql
    assert "WHERE status = 'issued' AND is_del = false" in sql


def test_benefit_downgrade_drops_only_new_tables_in_dependency_order():
    sql = _migration_sql(upgrade=False)
    dropped = re.findall(r"^DROP TABLE (\w+);", sql, re.M)
    assert len(dropped) == len(BENEFIT_TABLES) and set(dropped) == BENEFIT_TABLES
    position = {name: index for index, name in enumerate(dropped)}
    for name in dropped:
        for fk in Base.metadata.tables[name].foreign_keys:
            parent = fk.column.table.name
            if parent in BENEFIT_TABLES:
                assert position[name] < position[parent]
