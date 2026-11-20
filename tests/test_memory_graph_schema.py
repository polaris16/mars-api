"""Metadata checks for the AI memory graph schema."""

import re

from app.models import Base

TABLE_NAMES = {
    "t_ai_processing_job",
    "t_memory_element",
    "t_life_entity",
    "t_life_entity_alias",
    "t_memory_element_entity",
    "t_memory_conflict",
    # [新增] 第四阶段的冲突、固定版本问答及核验队列。
    "t_memory_conflict_group",
    "t_memory_conflict_evidence",
    "t_memory_review_question",
    "t_memory_review_answer",
    "t_memory_validation_job",
}


def test_memory_graph_tables_are_registered_with_audit_fields():
    assert TABLE_NAMES <= Base.metadata.tables.keys()

    audit_fields = {"created_by", "created_at", "updated_by", "updated_at", "is_del"}
    for table_name in TABLE_NAMES:
        table = Base.metadata.tables[table_name]
        assert audit_fields <= set(table.columns.keys())
        assert table.comment


def test_business_tables_and_columns_have_chinese_comments():
    chinese = re.compile(r"[\u3400-\u9fff]")
    missing = []
    for table in Base.metadata.sorted_tables:
        if not chinese.search(table.comment or ""):
            missing.append(table.name)
        missing.extend(
            f"{table.name}.{column.name}"
            for column in table.columns
            if not chinese.search(column.comment or "")
        )
    assert not missing, missing


def test_memory_graph_foreign_keys_and_partial_unique_indexes():
    expected_foreign_keys = {
        "t_ai_processing_job": {"t_memory_session.id", "t_memory_fragment.id", "t_user.id"},
        "t_memory_element": {
            "t_memory_session.id",
            "t_memory_fragment.id",
            "t_ai_processing_job.id",
            "t_user.id",
            "t_memory_validation_job.id",
        },
        "t_life_entity": {"t_user.id", "t_memory_fragment.id"},
        "t_life_entity_alias": {"t_life_entity.id", "t_memory_fragment.id"},
        "t_memory_element_entity": {"t_memory_element.id", "t_life_entity.id"},
        "t_memory_conflict": {"t_memory_element.id", "t_ai_processing_job.id", "t_user.id"},
        # [新增] 检查引用完整性，包括两处延后创建的循环外键。
        "t_memory_conflict_group": {"t_user.id", "t_memory_review_answer.id"},
        "t_memory_conflict_evidence": {
            "t_memory_conflict_group.id",
            "t_memory_element.id",
            "t_memory_source_revision.id",
            "t_memory_draft_version.id",
            "t_memory_review_answer.id",
        },
        "t_memory_review_question": {"t_memory_conflict_group.id", "t_memory_validation_job.id"},
        "t_memory_review_answer": {
            "t_user.id", "t_memory_review_question.id", "t_memory_validation_job.id",
        },
        "t_memory_validation_job": {"t_user.id", "t_memory_element.id", "t_memory_validation_job.id"},
    }

    for table_name, expected_targets in expected_foreign_keys.items():
        table = Base.metadata.tables[table_name]
        actual_targets = {
            fk.target_fullname for column in table.columns for fk in column.foreign_keys
        }
        assert expected_targets <= actual_targets

    for table_name in (
        "t_ai_processing_job",
        "t_memory_element",
        "t_life_entity_alias",
        "t_memory_element_entity",
        "t_memory_conflict",
        "t_memory_conflict_group",
        "t_memory_conflict_evidence",
        "t_memory_review_question",
        "t_memory_validation_job",
    ):
        indexes = Base.metadata.tables[table_name].indexes
        assert any(
            index.unique and index.dialect_options["postgresql"].get("where") is not None
            for index in indexes
        )
