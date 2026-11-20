"""[新增] 整理稿版本、来源证据、实体确认和会话可见性。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261120_01"
down_revision = "20261119_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('t_memory_session', sa.Column('visibility', sa.String(length=10), nullable=True, comment='记忆可见性，空值表示存量会话'))
    op.create_check_constraint('ck_t_memory_session_visibility', 't_memory_session', "visibility IN ('public', 'private', 'secret')")
    op.create_table('t_memory_source_revision',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='主键 ID'),
    sa.Column('fragment_id', sa.BigInteger(), nullable=False, comment='来源素材片段 ID'),
    sa.Column('source_kind', sa.String(length=24), nullable=False, comment='直接文字、文本文件或 App/服务端 ASR'),
    sa.Column('source_asset_id', sa.BigInteger(), nullable=True, comment='原始文件对象 ID'),
    sa.Column('source_text', sa.Text(), nullable=False, comment='原始文字或未整理的 ASR 全文'),
    sa.Column('transcript_segments', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False, comment='字符与原声区间的对应数组'),
    sa.Column('source_fingerprint', sa.String(length=64), nullable=False, comment='原文、定位及来源的 SHA-256'),
    sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建人 ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('updated_by', sa.BigInteger(), nullable=True, comment='更新人 ID'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
    sa.Column('is_del', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='是否软删除'),
    sa.CheckConstraint("jsonb_typeof(transcript_segments) = 'array'", name='ck_memory_source_segments'),
    sa.CheckConstraint("source_kind IN ('content_text', 'text_file', 'app_asr', 'server_asr')", name='ck_memory_source_kind'),
    sa.CheckConstraint('length(trim(source_text)) > 0', name='ck_memory_source_nonblank'),
    sa.ForeignKeyConstraint(['fragment_id'], ['t_memory_fragment.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_asset_id'], ['t_media_asset.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fragment_id', 'source_fingerprint', name='uq_memory_source_fingerprint'),
    comment='记忆片段原文及转写版本'
    )
    op.create_index('idx_memory_source_fragment', 't_memory_source_revision', ['fragment_id', 'id'], unique=False)
    op.create_table('t_memory_draft_version',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='主键 ID'),
    sa.Column('source_revision_id', sa.BigInteger(), nullable=False, comment='所用的原文及定位版本 ID'),
    sa.Column('base_version_id', sa.BigInteger(), nullable=True, comment='人工编辑使用的基准版本 ID'),
    sa.Column('origin', sa.String(length=16), nullable=False, comment='model 自动整理或 owner 人工校订'),
    sa.Column('paragraphs', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='有序段落、来源位置和人工编辑标记'),
    sa.Column('request_key', sa.Uuid(), nullable=True, comment='人工保存幂等请求键'),
    sa.Column('request_hash', sa.String(length=64), nullable=True, comment='人工保存请求内容指纹'),
    sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建人 ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('updated_by', sa.BigInteger(), nullable=True, comment='更新人 ID'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
    sa.Column('is_del', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='是否软删除'),
    sa.CheckConstraint("jsonb_typeof(paragraphs) = 'array' AND jsonb_array_length(paragraphs) > 0", name='ck_memory_draft_paragraphs'),
    sa.CheckConstraint("origin IN ('model', 'owner')", name='ck_memory_draft_origin'),
    sa.ForeignKeyConstraint(['base_version_id'], ['t_memory_draft_version.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_revision_id'], ['t_memory_source_revision.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_revision_id', 'request_key', name='uq_memory_draft_request'),
    comment='带段落来源的不可变记忆整理稿版本'
    )
    op.create_index('idx_memory_draft_source', 't_memory_draft_version', ['source_revision_id', 'id'], unique=False)
    op.add_column('t_memory_element', sa.Column('draft_version_id', sa.BigInteger(), nullable=True, comment='抽取时生效的整理稿版本 ID'))
    op.create_foreign_key(
        'fk_memory_element_draft', 't_memory_element', 't_memory_draft_version',
        ['draft_version_id'], ['id'], ondelete='CASCADE',
    )
    op.add_column('t_memory_element', sa.Column('draft_references', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True, comment='整理稿段落及段内引用位置'))
    op.add_column('t_memory_element', sa.Column('verification_status', sa.String(length=20), nullable=True, comment='系统验证状态，待核对、已验证或冲突，独立于人工核对'))
    op.create_check_constraint('ck_memory_element_verification', 't_memory_element', "verification_status IN ('pending', 'verified', 'conflict')")
    op.create_check_constraint('ck_memory_element_draft_references', 't_memory_element', "draft_references IS NULL OR jsonb_typeof(draft_references) = 'array'")
    op.create_index('idx_memory_element_draft', 't_memory_element', ['draft_version_id'], unique=False)
    op.create_index('idx_memory_element_verification', 't_memory_element', ['session_id', 'verification_status'], unique=False)
    op.create_table('t_memory_entity_candidate',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='主键 ID'),
    sa.Column('element_id', sa.BigInteger(), nullable=False, comment='所属记忆元素 ID'),
    sa.Column('entity_type', sa.String(length=24), nullable=False, comment='提及的人物、地点、机构或时间段'),
    sa.Column('mention_text', sa.String(length=200), nullable=False, comment='叙述者所用的原文称谓'),
    sa.Column('narrator_user_id', sa.BigInteger(), nullable=True, comment='已知的实际叙述者账号 ID'),
    sa.Column('relation_type', sa.String(length=24), nullable=False, comment='实体提及在元素中的角色'),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='提及的整理版本、段落与原文位置'),
    sa.Column('evidence_key', sa.String(length=64), nullable=False, comment='来源、含义及叙述者的证据指纹'),
    sa.Column('candidate_entity_ids', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False, comment='可能关联的实体 ID'),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False, comment='候选待确认或已处理'),
    sa.Column('revision', sa.Integer(), server_default=sa.text('1'), nullable=False, comment='用于并发确认的版本'),
    sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建人 ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('updated_by', sa.BigInteger(), nullable=True, comment='更新人 ID'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
    sa.Column('is_del', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='是否软删除'),
    sa.CheckConstraint("jsonb_typeof(candidate_entity_ids) = 'array'", name='ck_memory_candidate_ids'),
    sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name='ck_memory_candidate_evidence'),
    sa.CheckConstraint("status IN ('pending', 'resolved')", name='ck_memory_candidate_status'),
    sa.CheckConstraint('revision > 0', name='ck_memory_candidate_revision'),
    sa.ForeignKeyConstraint(['element_id'], ['t_memory_element.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['narrator_user_id'], ['t_user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    comment='来自记忆元素的实体提及和待确认匹配'
    )
    op.create_index('idx_memory_entity_candidate_element', 't_memory_entity_candidate', ['element_id', 'status', 'id'], unique=False)
    op.create_index('idx_memory_entity_candidate_evidence', 't_memory_entity_candidate', ['evidence_key'], unique=False)
    op.create_table('t_memory_entity_resolution',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='主键 ID'),
    sa.Column('candidate_id', sa.BigInteger(), nullable=False, comment='被确认的实体候选 ID'),
    sa.Column('candidate_revision', sa.Integer(), nullable=False, comment='提交确认时的候选版本'),
    sa.Column('action', sa.String(length=24), nullable=False, comment='关联已有实体、新建实体或排除匹配'),
    sa.Column('entity_id', sa.BigInteger(), nullable=False, comment='确认或排除的实体 ID'),
    sa.Column('request_hash', sa.String(length=64), nullable=False, comment='同一候选版本的幂等请求指纹'),
    sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建人 ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('updated_by', sa.BigInteger(), nullable=True, comment='更新人 ID'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
    sa.Column('is_del', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='是否软删除'),
    sa.CheckConstraint("action IN ('link_existing', 'create_new', 'reject_match')", name='ck_memory_resolution_action'),
    sa.ForeignKeyConstraint(['candidate_id'], ['t_memory_entity_candidate.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['entity_id'], ['t_life_entity.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('candidate_id', 'candidate_revision', name='uq_memory_entity_resolution'),
    comment='记忆所有者处理实体候选的历史记录'
    )
    op.create_table('t_memory_entity_evidence',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='主键 ID'),
    sa.Column('element_entity_id', sa.BigInteger(), nullable=False, comment='对应的记忆元素实体关联 ID'),
    sa.Column('alias_id', sa.BigInteger(), nullable=True, comment='同一条证据支持的别名 ID'),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='整理稿段落及原文位置'),
    sa.Column('evidence_key', sa.String(length=64), nullable=False, comment='证据和称谓的去重指纹'),
    sa.Column('created_by', sa.BigInteger(), nullable=True, comment='创建人 ID'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
    sa.Column('updated_by', sa.BigInteger(), nullable=True, comment='更新人 ID'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
    sa.Column('is_del', sa.Boolean(), server_default=sa.text('false'), nullable=False, comment='是否软删除'),
    sa.CheckConstraint("jsonb_typeof(evidence) = 'object'", name='ck_memory_entity_evidence'),
    sa.ForeignKeyConstraint(['alias_id'], ['t_life_entity_alias.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['element_entity_id'], ['t_memory_element_entity.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('element_entity_id', 'evidence_key', name='uq_memory_entity_evidence'),
    comment='实体关联及别名的逐项来源证据'
    )
    op.create_index('idx_memory_entity_evidence_alias', 't_memory_entity_evidence', ['alias_id'], unique=False)
    op.drop_constraint('ck_t_life_entity_type', 't_life_entity', type_='check')
    op.create_check_constraint('ck_t_life_entity_type', 't_life_entity', "entity_type IN ('person', 'place', 'organization', 'time_period')")
    op.alter_column('t_life_entity', 'entity_type',
               comment='实体类型：person 人物、place 地点、organization 机构、time_period 时间段',
               existing_comment='实体类型：person 人物、place 地点、organization 机构')
    op.drop_constraint('ck_t_memory_element_entity_relation_type', 't_memory_element_entity', type_='check')
    op.create_check_constraint('ck_t_memory_element_entity_relation_type', 't_memory_element_entity', "relation_type IN ('subject', 'participant', 'location', 'mentioned', 'other', 'time_period')")
    op.alter_column('t_memory_element_entity', 'relation_type',
               comment='实体角色：主体、参与者、地点、提及、其他或 time_period 时间段',
               existing_comment='实体角色：subject 主体、participant 参与者、location 地点、mentioned 提及、other 其他')

def downgrade() -> None:
    op.alter_column('t_memory_element_entity', 'relation_type',
                    comment='实体角色：subject 主体、participant 参与者、location 地点、mentioned 提及、other 其他')
    op.drop_constraint('ck_t_memory_element_entity_relation_type', 't_memory_element_entity', type_='check')
    op.create_check_constraint('ck_t_memory_element_entity_relation_type', 't_memory_element_entity',
                               "relation_type IN ('subject', 'participant', 'location', 'mentioned', 'other')")
    op.alter_column('t_life_entity', 'entity_type',
                    comment='实体类型：person 人物、place 地点、organization 机构')
    op.drop_constraint('ck_t_life_entity_type', 't_life_entity', type_='check')
    op.create_check_constraint('ck_t_life_entity_type', 't_life_entity',
                               "entity_type IN ('person', 'place', 'organization')")
    op.drop_table('t_memory_entity_evidence')
    op.drop_table('t_memory_entity_resolution')
    op.drop_table('t_memory_entity_candidate')
    op.drop_index('idx_memory_element_draft', table_name='t_memory_element')
    op.drop_index('idx_memory_element_verification', table_name='t_memory_element')
    op.drop_constraint('ck_memory_element_draft_references', 't_memory_element', type_='check')
    op.drop_constraint('ck_memory_element_verification', 't_memory_element', type_='check')
    op.drop_constraint('fk_memory_element_draft', 't_memory_element', type_='foreignkey')
    op.drop_column('t_memory_element', 'verification_status')
    op.drop_column('t_memory_element', 'draft_references')
    op.drop_column('t_memory_element', 'draft_version_id')
    op.drop_table('t_memory_draft_version')
    op.drop_table('t_memory_source_revision')
    op.drop_constraint('ck_t_memory_session_visibility', 't_memory_session', type_='check')
    op.drop_column('t_memory_session', 'visibility')
