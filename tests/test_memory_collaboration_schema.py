"""Validate the ORM contract for relationships, session access, and contributors."""

from app.models import MemoryContributor, MemorySessionMember, UserRelationship


def test_collaboration_models_register_expected_tables_and_columns():
    assert UserRelationship.__tablename__ == "t_user_relationship"
    assert {
        "principal_user_id", "delegate_user_id", "relationship_type", "status",
        "authorization_method", "valid_from", "valid_until",
    } <= set(UserRelationship.__table__.columns.keys())

    assert MemorySessionMember.__tablename__ == "t_memory_session_member"
    assert {
        "session_id", "user_id", "role", "source_relationship_id",
        "can_manage_members", "status", "granted_by_user_id", "valid_until",
    } <= set(MemorySessionMember.__table__.columns.keys())

    assert MemoryContributor.__tablename__ == "t_memory_contributor"
    assert {
        "session_id", "contributor_type", "account_user_id", "session_member_id",
        "relationship_id", "display_name", "identity_status", "consent_status",
    } <= set(MemoryContributor.__table__.columns.keys())


def test_collaboration_tables_have_audit_and_soft_delete_fields():
    for model in (UserRelationship, MemorySessionMember, MemoryContributor):
        columns = set(model.__table__.columns.keys())
        assert {"created_by", "created_at", "updated_by", "updated_at", "is_del"} <= columns
