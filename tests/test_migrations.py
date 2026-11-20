"""在临时 SQLite 数据库验证真实迁移流程，不访问 PostgreSQL。"""

from io import StringIO
from pathlib import Path
from shutil import copyfile

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect

from app.models import Base


def test_autogenerate_upgrade_downgrade_and_offline_sql(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parents[1]
    script_directory = tmp_path / "migrations"
    (script_directory / "versions").mkdir(parents=True)
    for filename in ("env.py", "script.py.mako"):
        copyfile(project_root / "migrations" / filename, script_directory / filename)

    metadata = MetaData()
    Table("migration_probe", metadata, Column("id", Integer, primary_key=True))
    monkeypatch.setattr(Base, "metadata", metadata)

    output = StringIO()
    config = Config(str(project_root / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(script_directory))

    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    try:
        unmanaged_metadata = MetaData()
        Table("unmanaged", unmanaged_metadata, Column("id", Integer, primary_key=True))
        unmanaged_metadata.create_all(engine)
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            revision = command.revision(config, message="migration probe", autogenerate=True)
            assert revision is not None
            migration_script = Path(revision.path).read_text(encoding="utf-8")
            assert "op.create_table(" in migration_script
            assert "op.drop_table('unmanaged')" not in migration_script

            command.upgrade(config, "head")
            assert inspect(connection).has_table("migration_probe")

            command.downgrade(config, "base")
            assert not inspect(connection).has_table("migration_probe")
            assert inspect(connection).has_table("unmanaged")

        config.attributes.pop("connection")
        command.upgrade(config, "head", sql=True)
        assert "CREATE TABLE migration_probe" in output.getvalue()
        assert "SERIAL" in output.getvalue()
    finally:
        engine.dispose()


def test_reliability_and_schema_comments_generate_offline_postgresql_sql():
    project_root = Path(__file__).resolve().parents[1]
    output = StringIO()
    config = Config(str(project_root / "alembic.ini"), output_buffer=output)
    comments = ScriptDirectory.from_config(config).get_revision("20261119_12").module
    updated_by_pipeline = {("t_life_entity", "entity_type"), ("t_memory_element_entity", "relation_type")}
    for table_name, (_, current) in comments.TABLE_COMMENTS.items():
        assert Base.metadata.tables[table_name].comment == current
    for table_name, columns in comments.COLUMN_COMMENTS.items():
        for field, _, current in columns:
            if (table_name, field) not in updated_by_pipeline:
                assert Base.metadata.tables[table_name].c[field].comment == current

    command.upgrade(config, "20261119_10:20261119_12", sql=True)
    sql = output.getvalue()
    assert "ADD COLUMN client_request_key UUID" in sql
    assert "UNIQUE (session_id, created_by, client_request_key)" in sql
    assert "invitation_expires_at = LEAST" in sql
    assert "last_material_at IS DISTINCT FROM latest.recorded_at" in sql
    assert "COMMENT ON TABLE t_auth_session IS '用户登录认证会话'" in sql
    assert "COMMENT ON COLUMN t_memory_element.summary" in sql

    output.seek(0)
    output.truncate()
    command.downgrade(config, "20261119_12:20261119_10", sql=True)
    sql = output.getvalue()
    assert "COMMENT ON TABLE t_auth_session IS NULL" in sql
    assert "DROP COLUMN invitation_expires_at" in sql


def test_versioned_memory_migration_creates_current_tables_and_constraints():
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20261119_12:20261120_01", sql=True)
    sql = output.getvalue()
    tables = (
        "t_memory_source_revision", "t_memory_draft_version", "t_memory_entity_candidate",
        "t_memory_entity_resolution", "t_memory_entity_evidence",
    )
    for name in tables:
        assert f"CREATE TABLE {name}" in sql
        assert f"COMMENT ON TABLE {name}" in sql
    assert "FOREIGN KEY(draft_version_id) REFERENCES t_memory_draft_version (id)" in sql
    for table, column in (("t_life_entity", "entity_type"), ("t_memory_element_entity", "relation_type")):
        assert f"COMMENT ON COLUMN {table}.{column} IS '{Base.metadata.tables[table].c[column].comment}'" in sql
    assert "time_period" in sql

    output.seek(0)
    output.truncate()
    command.downgrade(config, "20261120_01:20261119_12", sql=True)
    sql = output.getvalue()
    assert "DROP CONSTRAINT fk_memory_element_draft" in sql
    for name in tables:
        assert f"DROP TABLE {name}" in sql


def test_memory_conflict_review_migration_orders_foreign_keys_and_keeps_history():
    """[新增] 检查冲突表迁移及循环外键的升级、回退顺序。"""
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20261120_01:20261120_02", sql=True)
    upgrade_sql = output.getvalue()
    new_tables = (
        "t_memory_conflict_group", "t_memory_conflict_evidence", "t_memory_review_question",
        "t_memory_review_answer", "t_memory_validation_job",
    )
    for table in new_tables:
        assert f"CREATE TABLE {table} (" in upgrade_sql
        assert f"COMMENT ON TABLE {table} IS" in upgrade_sql
    assert upgrade_sql.index("CREATE TABLE t_memory_review_answer (") < upgrade_sql.index(
        "ADD CONSTRAINT fk_memory_group_resolution_answer"
    )
    assert upgrade_sql.index("CREATE TABLE t_memory_validation_job (") < upgrade_sql.index(
        "ADD CONSTRAINT fk_memory_element_validation_job"
    )
    assert "ADD CONSTRAINT ck_memory_conflict_group_job" in upgrade_sql
    assert "UNIQUE (question_id)" in upgrade_sql
    assert "CONSTRAINT ck_memory_review_answer_action_payload CHECK" in upgrade_sql
    assert "CONSTRAINT ck_memory_conflict_evidence_source CHECK" in upgrade_sql
    assert "CONSTRAINT ck_memory_validation_job_lease CHECK" in upgrade_sql
    assert "CREATE UNIQUE INDEX uq_memory_review_question_pending" in upgrade_sql
    assert "CREATE UNIQUE INDEX uq_memory_validation_job_processing" in upgrade_sql
    assert "UPDATE t_memory_" not in upgrade_sql
    assert "DELETE FROM t_memory_" not in upgrade_sql
    assert "DROP " not in upgrade_sql

    output.seek(0)
    output.truncate()
    command.downgrade(config, "20261120_02:20261120_01", sql=True)
    downgrade_sql = output.getvalue()
    assert downgrade_sql.index("DROP CONSTRAINT fk_memory_group_resolution_answer") < (
        downgrade_sql.index("DROP TABLE t_memory_review_answer;")
    )
    assert downgrade_sql.index("DROP CONSTRAINT fk_memory_element_validation_job") < (
        downgrade_sql.index("DROP TABLE t_memory_validation_job;")
    )
    assert downgrade_sql.index("DROP TABLE t_memory_conflict_evidence;") < (
        downgrade_sql.index("DROP TABLE t_memory_review_answer;")
    )
    assert "DROP TABLE t_memory_element;" not in downgrade_sql
    assert "DROP TABLE t_memory_conflict;" not in downgrade_sql


def test_graph_event_migration_creates_only_nodes_and_versioned_members():
    """[新增] 核对第五阶段的离线升级、归组约束和回退依赖顺序。"""
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20261120_02:20261121_01", sql=True)
    upgrade_sql = output.getvalue()
    event = "t_memory_graph_event"
    member = "t_memory_graph_event_element"
    assert upgrade_sql.count("CREATE TABLE ") == 2
    assert upgrade_sql.index(f"CREATE TABLE {event} (") < upgrade_sql.index(f"CREATE TABLE {member} (")
    assert "CONSTRAINT uq_memory_graph_event_owner_anchor UNIQUE (owner_user_id, anchor_key)" in upgrade_sql
    assert (
        f"CREATE UNIQUE INDEX uq_memory_graph_member_active_element ON {member} (element_id) "
        "WHERE is_del = false AND status = 'active'"
    ) in upgrade_sql
    assert "CONSTRAINT ck_memory_graph_member_identity_basis CHECK" in upgrade_sql
    assert "membership_basis = 'source' AND identity_group_id IS NULL" in upgrade_sql
    assert "membership_basis = 'confirmed_same_event' AND identity_group_id IS NOT NULL" in upgrade_sql
    assert "FOREIGN KEY(draft_version_id) REFERENCES t_memory_draft_version (id) ON DELETE RESTRICT" in upgrade_sql
    for table in (event, member):
        assert f"COMMENT ON TABLE {table} IS" in upgrade_sql
        for column in Base.metadata.tables[table].c:
            assert f"COMMENT ON COLUMN {table}.{column.name} IS '{column.comment}'" in upgrade_sql
    assert "ALTER TABLE" not in upgrade_sql
    assert "INSERT INTO" not in upgrade_sql
    assert "DELETE FROM" not in upgrade_sql
    assert [line for line in upgrade_sql.splitlines() if line.startswith("UPDATE ")] == [
        "UPDATE alembic_version SET version_num='20261121_01' WHERE alembic_version.version_num = '20261120_02';"
    ]

    output.seek(0)
    output.truncate()
    command.downgrade(config, "20261121_01:20261120_02", sql=True)
    assert [line for line in output.getvalue().splitlines() if line.startswith("DROP TABLE ")] == [
        f"DROP TABLE {member};",
        f"DROP TABLE {event};",
    ]
