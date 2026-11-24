"""[新增] 将已确认好友的期限与会话访问授权分开。

Revision ID: 20261124_01
Revises: 20261122_02
"""

from alembic import op

revision = "20261124_01"
down_revision = "20261122_02"
branch_labels = None
depends_on = None

# 仅处理完整双向确认的好友。较新的已确认关系（包括已解除）覆盖旧关系，
# 较新的待处理邀请由双方继续处理，避免恢复出重复的好友关系。
_CONTACTS = """
WITH contacts AS (
    SELECT r.id,
           CASE
               WHEN r.valid_until IS NULL THEN inverse.valid_until
               WHEN inverse.valid_until IS NULL THEN r.valid_until
               WHEN r.valid_until < inverse.valid_until THEN r.valid_until
               ELSE inverse.valid_until
           END AS deadline
      FROM t_user_relationship AS r
      JOIN t_user_relationship AS inverse
        ON inverse.relationship_pair_id = r.relationship_pair_id
       AND inverse.subject_user_id = r.related_user_id
       AND inverse.related_user_id = r.subject_user_id
     WHERE r.relationship_type = 'contact' AND inverse.relationship_type = 'contact'
       AND r.relation_code = 'contact_of' AND inverse.relation_code = 'contact_of'
       AND r.status = 'active' AND inverse.status = 'active'
       AND r.accepted_at IS NOT NULL AND inverse.accepted_at IS NOT NULL
       AND NOT r.is_del AND NOT inverse.is_del
       AND (SELECT count(*) FROM t_user_relationship AS member
             WHERE member.relationship_pair_id = r.relationship_pair_id
               AND NOT member.is_del) = 2
       AND NOT EXISTS (
           SELECT 1 FROM t_user_relationship AS newer
            WHERE newer.relationship_type = 'contact'
              AND newer.relationship_pair_id <> r.relationship_pair_id
              AND newer.id > CASE WHEN r.id > inverse.id THEN r.id ELSE inverse.id END
              AND (newer.accepted_at IS NOT NULL OR (
                  newer.status = 'pending' AND NOT newer.is_del
                  AND (newer.valid_until IS NULL OR newer.valid_until > CURRENT_TIMESTAMP)
                  AND (newer.invitation_expires_at IS NULL
                       OR newer.invitation_expires_at > CURRENT_TIMESTAMP)
              ))
              AND ((newer.subject_user_id = r.subject_user_id AND newer.related_user_id = r.related_user_id)
                OR (newer.subject_user_id = r.related_user_id AND newer.related_user_id = r.subject_user_id))
       )
)
"""


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        # 在缩短旧授权与清理好友期限之间阻止并发写入。
        op.execute(
            "LOCK TABLE t_user_relationship, t_memory_session_member IN SHARE ROW EXCLUSIVE MODE"
        )
    # 保留授权实际使用过的更早期限，防止已失效的记忆授权随好友恢复而重新生效。
    op.execute(_CONTACTS + """
        UPDATE t_memory_session_member AS grant_row
           SET valid_until = contacts.deadline
          FROM contacts
         WHERE grant_row.source_relationship_id = contacts.id
           AND grant_row.status = 'active' AND NOT grant_row.is_del
           AND contacts.deadline IS NOT NULL
           AND (grant_row.valid_until IS NULL OR grant_row.valid_until > contacts.deadline)
    """)
    op.execute(_CONTACTS + """
        UPDATE t_user_relationship
           SET invitation_expires_at = NULL, valid_until = NULL
         WHERE id IN (SELECT id FROM contacts)
           AND (invitation_expires_at IS NOT NULL OR valid_until IS NOT NULL)
    """)


def downgrade() -> None:
    # 数据修订无法从现有值重建旧的申请期限，回退版本时保留已分离的期限。
    pass
