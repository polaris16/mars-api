"""[新增] 验证站内聊天迁移和历史消息约束。"""

import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError

from app.models import Base, ChatConversation, ChatConversationMember, ChatMessage

PREVIOUS = "20261125_02"
REVISION = "20261125_03"
TABLES = tuple(model.__table__ for model in (ChatConversation, ChatConversationMember, ChatMessage))
AUDIT = {"created_by", "created_at", "updated_by", "updated_at", "is_del"}


def _sql(*, downgrade: bool = False) -> str:
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    if downgrade:
        command.downgrade(config, f"{REVISION}:{PREVIOUS}", sql=True)
    else:
        command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
    return output.getvalue()


def test_chat_migration_creates_schema_without_rewriting_existing_business_data():
    sql = _sql()
    assert re.findall(r"^CREATE TABLE (\w+) \(", sql, re.M) == [table.name for table in TABLES]
    assert not re.search(r"^(DROP TABLE|ALTER TABLE|INSERT INTO|DELETE FROM)\b", sql, re.M)
    assert re.findall(r"^UPDATE (\w+)", sql, re.M) == ["alembic_version"]
    for table in TABLES:
        assert table.comment and AUDIT <= set(table.c.keys())
        assert f"COMMENT ON TABLE {table.name} IS '{table.comment}'" in sql
        for column in table.c:
            assert column.comment and re.search(r"[\u4e00-\u9fff]", column.comment)
            assert f"COMMENT ON COLUMN {table.name}.{column.name} IS '{column.comment}'" in sql
        for index in table.indexes:
            assert f"INDEX {index.name} ON {table.name}" in sql
    assert (
        "FOREIGN KEY(conversation_id, sender_user_id) REFERENCES "
        "t_chat_conversation_member (conversation_id, user_id)" in sql
    )
    assert "CONSTRAINT uq_chat_message_sequence UNIQUE (conversation_id, sequence_no)" in sql
    assert "UNIQUE (conversation_id, sender_user_id, client_message_id)" in sql


def test_chat_downgrade_protects_existing_history_and_removes_dependents_first():
    sql = _sql(downgrade=True)
    assert (
        sql.index("LOCK TABLE")
        < sql.index("Chat history must be preserved")
        < sql.index("DROP TABLE")
    )
    assert re.findall(r"^DROP TABLE (\w+);", sql, re.M) == [
        table.name for table in reversed(TABLES)
    ]


def test_chat_schema_enforces_membership_sequence_idempotency_and_content():
    conversation, member, message = TABLES
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.exec_driver_sql("PRAGMA foreign_keys = ON")
        sqlite = db.connection.driver_connection
        sqlite.create_function("char_length", 1, lambda value: len(value) if value else 0)
        sqlite.create_function("btrim", 1, lambda value: value.strip(" ") if value else value)
        db.exec_driver_sql("CREATE TABLE t_user (id BIGINT PRIMARY KEY)")
        db.exec_driver_sql("CREATE TABLE t_youth_partner_profile (id BIGINT PRIMARY KEY)")
        db.exec_driver_sql("INSERT INTO t_user VALUES (1), (2), (3)")
        db.exec_driver_sql("INSERT INTO t_youth_partner_profile VALUES (1)")
        Base.metadata.create_all(db, tables=TABLES)

        pair = uuid4()
        db.execute(conversation.insert().values(id=1, kind="friend", relationship_pair_id=pair))
        db.execute(
            member.insert(),
            [
                {"conversation_id": 1, "user_id": 1},
                {"conversation_id": 1, "user_id": 2},
            ],
        )
        first = {
            "id": 1,
            "conversation_id": 1,
            "sequence_no": 1,
            "sender_user_id": 1,
            "client_message_id": uuid4(),
            "message_type": "text",
            "body_text": "您好",
        }
        db.execute(message.insert().values(**first))
        second = {**first, "id": 2, "sequence_no": 2, "client_message_id": uuid4()}
        for overrides in (
            {"sender_user_id": 3},
            {"sequence_no": 1},
            {"client_message_id": first["client_message_id"]},
            {"body_text": " "},
            {"body_text": "字" * 2001},
            {"partner_id": 1},
            {"message_type": "partner_card", "body_text": None},
        ):
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(message.insert().values(**{**second, **overrides}))
        db.execute(
            message.insert().values(
                **{
                    **second,
                    "message_type": "partner_card",
                    "body_text": None,
                    "partner_id": 1,
                }
            )
        )
        db.execute(
            conversation.update()
            .where(conversation.c.id == 1)
            .values(
                last_message_seq=2,
                last_message_at=datetime.now(timezone.utc),
            )
        )
        assert db.scalars(
            select(message.c.sequence_no)
            .where(
                message.c.conversation_id == 1,
                message.c.sequence_no < 2,
            )
            .order_by(message.c.sequence_no.desc())
        ).all() == [1]
        assert db.scalar(select(member.c.last_read_seq).where(member.c.user_id == 1)) == 0
    engine.dispose()
