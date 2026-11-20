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
