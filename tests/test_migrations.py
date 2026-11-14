"""在临时 SQLite 数据库验证真实迁移流程，不访问 PostgreSQL。"""

from io import StringIO
from pathlib import Path
from shutil import copyfile

from alembic import command
from alembic.config import Config
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
