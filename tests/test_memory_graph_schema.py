"""Metadata checks for the AI memory graph schema."""

from app.models import Base


TABLE_NAMES = {
    "t_ai_processing_job",
    "t_memory_element",
    "t_life_entity",
    "t_life_entity_alias",
    "t_memory_element_entity",
    "t_memory_conflict",
}


def test_memory_graph_tables_are_registered_with_audit_fields():
    assert TABLE_NAMES <= Base.metadata.tables.keys()

    audit_fields = {"created_by", "created_at", "updated_by", "updated_at", "is_del"}
    for table_name in TABLE_NAMES:
        table = Base.metadata.tables[table_name]
        assert audit_fields <= set(table.columns.keys())
        assert table.comment


def test_memory_graph_foreign_keys_and_partial_unique_indexes():
    expected_foreign_keys = {
        "t_ai_processing_job": {"t_memory_session.id", "t_memory_fragment.id", "t_user.id"},
        "t_memory_element": {
            "t_memory_session.id",
            "t_memory_fragment.id",
            "t_ai_processing_job.id",
            "t_user.id",
        },
        "t_life_entity": {"t_user.id", "t_memory_fragment.id"},
        "t_life_entity_alias": {"t_life_entity.id", "t_memory_fragment.id"},
        "t_memory_element_entity": {"t_memory_element.id", "t_life_entity.id"},
        "t_memory_conflict": {"t_memory_element.id", "t_ai_processing_job.id", "t_user.id"},
    }

    for table_name, expected_targets in expected_foreign_keys.items():
        table = Base.metadata.tables[table_name]
        actual_targets = {
            fk.target_fullname
            for column in table.columns
            for fk in column.foreign_keys
        }
        assert expected_targets <= actual_targets

    for table_name in (
        "t_ai_processing_job",
        "t_memory_element",
        "t_life_entity_alias",
        "t_memory_element_entity",
        "t_memory_conflict",
    ):
        indexes = Base.metadata.tables[table_name].indexes
        assert any(index.unique and index.dialect_options["postgresql"].get("where") is not None for index in indexes)
