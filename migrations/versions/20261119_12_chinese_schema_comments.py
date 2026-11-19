"""统一表和字段的中文说明。"""

from alembic import op


revision = "20261119_12"
down_revision = "20261119_11"
branch_labels = None
depends_on = None


# 使用生成迁移时的静态快照，避免未来的模型更改影响旧迁移。
TABLE_COMMENTS = {
    't_ai_processing_job': ('AI processing jobs', 'AI 素材处理任务'),
    't_auth_session': (None, '用户登录认证会话'),
    't_life_entity': ('People, places, and organizations in a user life graph', '人生图谱中的人物、地点和机构'),
    't_life_entity_alias': ('Aliases of normalized life entities', '人生图谱实体的别名'),
    't_memory_conflict': ('Detected conflicts between structured memory elements', '记忆元素间的冲突及用户确认结果'),
    't_memory_element': ('Structured memory elements extracted from source fragments', '结构化记忆元素及抽取失败保留的原文兜底'),
    't_memory_element_entity': ('Links between memory elements and life entities', '记忆元素与人生图谱实体的关联'),
}

COLUMN_COMMENTS = {
    't_ai_processing_job': (
        ('id', 'Id', '主键 ID'),
        ('session_id', 'Session id', '所属记忆会话 ID'),
        ('source_fragment_id', 'Source fragment id', '触发任务的来源片段 ID'),
        ('requested_by_user_id', 'Requested by user id', '提交处理任务的用户 ID'),
        ('job_type', 'Job type', '任务类型，如 extract_memory_elements 表示抽取记忆元素'),
        ('status', 'Status', '任务状态：queued 待执行、processing 执行中、succeeded 成功、failed 失败、cancelled 取消'),
        ('priority', 'Priority', '任务优先级，取值 0 至 10，值越大越优先'),
        ('input_payload', 'Input payload', '任务输入引用、内容指纹和处理版本，JSON 对象'),
        ('result_payload', 'Result payload', '任务统计、原文定位和失败记录，JSON 对象'),
        ('attempt_count', 'Attempt count', '已领取的执行次数，包含首次同步尝试'),
        ('max_attempts', 'Max attempts', '任务允许尝试的总次数'),
        ('available_at', 'Available at', '任务最早可领取的时间'),
        ('started_at', 'Started at', '首次领取任务的时间'),
        ('finished_at', 'Finished at', '任务达到终态的时间'),
        ('worker_id', 'Worker id', '本次领取的租约令牌，用于核对提交者'),
        ('lease_expires_at', 'Lease expires at', '当前执行租约到期时间'),
        ('idempotency_key', 'Idempotency key', '同一会话内的任务幂等键'),
        ('error_code', 'Error code', '最近一次失败或取消的错误码'),
        ('error_message', 'Error message', '可选的安全错误说明'),
        ('created_by', 'Created by', '创建人 ID'),
        ('created_at', 'Created at', '创建时间'),
        ('updated_by', 'Updated by', '更新人 ID'),
        ('updated_at', 'Updated at', '更新时间'),
        ('is_del', 'Is del', '是否软删除'),
    ),
    't_auth_session': (
        ('id', 'Primary key identifier', '主键 ID'),
        ('user_id', 'Related user account ID', '持有登录会话的用户 ID'),
        ('token_hash', 'SHA-256 hash of the bearer token; raw tokens are never stored', 'Bearer Token 的 SHA-256 摘要，原文不入库'),
        ('expires_at', 'Authentication token expiration timestamp', '登录会话到期时间'),
        ('created_by', 'User ID that created this row', '创建人 ID'),
        ('created_at', 'Timestamp when this row was created', '创建时间'),
        ('updated_by', 'User ID that last updated this row', '更新人 ID'),
        ('updated_at', 'Timestamp when this row was last updated', '更新时间'),
        ('is_del', 'Soft-delete flag', '是否软删除'),
    ),
    't_life_entity': (
        ('id', 'Id', '主键 ID'),
        ('owner_user_id', 'Owner user id', '人生图谱所有者的用户 ID'),
        ('entity_type', 'Entity type', '实体类型：person 人物、place 地点、organization 机构'),
        ('canonical_name', 'Canonical name', '实体归一化后的标准名称'),
        ('relationship_to_author', 'Relationship to author', '实体与回忆录主人之间的关系说明'),
        ('summary_profile', 'Summary profile', '用于回忆录整理的实体简介'),
        ('attributes', 'Attributes', '按实体类型补充的结构化属性，JSON 对象'),
        ('first_mentioned_fragment_id', 'First mentioned fragment id', '最早提及该实体的来源片段 ID'),
        ('created_by', 'Created by', '创建人 ID'),
        ('created_at', 'Created at', '创建时间'),
        ('updated_by', 'Updated by', '更新人 ID'),
        ('updated_at', 'Updated at', '更新时间'),
        ('is_del', 'Is del', '是否软删除'),
    ),
    't_life_entity_alias': (
        ('id', 'Id', '主键 ID'),
        ('entity_id', 'Entity id', '关联的人生实体 ID'),
        ('alias_name', 'Alias name', '原素材中的实体别名'),
        ('alias_type', 'Alias type', '别名类型，other 表示未细分'),
        ('source_fragment_id', 'Source fragment id', '提供此别名的来源片段 ID'),
        ('confidence', 'Confidence', '该别名指向目标实体的置信度，取值 0 至 1'),
        ('created_by', 'Created by', '创建人 ID'),
        ('created_at', 'Created at', '创建时间'),
        ('updated_by', 'Updated by', '更新人 ID'),
        ('updated_at', 'Updated at', '更新时间'),
        ('is_del', 'Is del', '是否软删除'),
    ),
    't_memory_conflict': (
        ('id', 'Id', '主键 ID'),
        ('element_a_id', 'Element a id', '冲突中的第一个记忆元素 ID，按 ID 升序保存'),
        ('element_b_id', 'Element b id', '冲突中的第二个记忆元素 ID'),
        ('detected_by_job_id', 'Detected by job id', '发现冲突的 AI 任务 ID'),
        ('conflict_type', 'Conflict type', '冲突涉及的属性或事实类型'),
        ('description', 'Description', '冲突的内容及差异说明'),
        ('question_text', 'Question text', '发给用户的核对问题'),
        ('conflict_payload', 'Conflict payload', '检测冲突时保留的来源证据，JSON 对象'),
        ('status', 'Status', '处理状态：pending 待处理、awaiting_user 待确认、resolved 已解决、cannot_remember 记不清、dismissed 已忽略'),
        ('user_response', 'User response', '用户对冲突的原始回答'),
        ('resolution_payload', 'Resolution payload', '结构化的确认或处理结果，JSON 对象'),
        ('resolved_by_user_id', 'Resolved by user id', '确认或处理冲突的用户 ID'),
        ('resolved_at', 'Resolved at', '记录最终处理结果的时间'),
        ('created_by', 'Created by', '创建人 ID'),
        ('created_at', 'Created at', '创建时间'),
        ('updated_by', 'Updated by', '更新人 ID'),
        ('updated_at', 'Updated at', '更新时间'),
        ('is_del', 'Is del', '是否软删除'),
    ),
    't_memory_contributor': (
        ('id', 'Primary key identifier', '主键 ID'),
        ('session_id', 'Memory session ID', '所属记忆会话 ID'),
        ('contributor_type', 'Provider category: owner, registered user, or guest', '提供者类型：owner 会话主人、registered_user 注册用户、guest 访客'),
        ('account_user_id', 'Registered account representing this contributor', '素材提供者对应的注册用户 ID，访客为空'),
        ('session_member_id', 'Session membership authorizing this contributor', '赋予贡献资格的会话成员授权 ID'),
        ('relationship_id', 'Relationship associated with this contributor', '提供者资格关联的亲属关系 ID'),
        ('display_name', 'Name displayed for the material contributor', '素材提供者的会话内显示名'),
        ('relationship_label', 'Free-form relationship label', '提供者的自定义关系称谓'),
        ('identity_status', 'Whether this contributor identity has been verified', '身份核验状态：unverified 未核验、verified 已核验'),
        ('consent_status', 'Contributor consent lifecycle status', '提供者同意状态：pending 待同意、accepted 已同意、withdrawn 已撤回'),
        ('consented_at', 'Timestamp when the contributor consented', '最近一次同意提供素材的时间'),
        ('created_by', 'User ID that created this row', '创建人 ID'),
        ('created_at', 'Timestamp when this row was created', '创建时间'),
        ('updated_by', 'User ID that last updated this row', '更新人 ID'),
        ('updated_at', 'Timestamp when this row was last updated', '更新时间'),
        ('is_del', 'Soft-delete flag', '是否软删除'),
    ),
    't_memory_element': (
        ('id', 'Id', '主键 ID'),
        ('session_id', 'Session id', '所属记忆会话 ID'),
        ('source_fragment_id', 'Source fragment id', '来源片段 ID'),
        ('processing_job_id', 'Processing job id', '产生该记忆元素的 AI 任务 ID'),
        ('sequence_no', 'Sequence no', '同一来源片段内的元素顺序'),
        ('element_type', 'Element type', '元素类型：event 事件、fact 事实、life_stage 人生阶段、other 其他'),
        ('summary', 'Summary', '记忆内容摘要，原文兜底时仅保存预览'),
        ('source_quote', 'Source quote', '可在来源文本中逐字核对的引句'),
        ('exact_at', 'Exact at', '有明确依据的精确时间，带时区'),
        ('time_start', 'Time start', '推定日期范围的起始日期'),
        ('time_end', 'Time end', '推定日期范围的结束日期'),
        ('time_description', 'Time description', '用户对时间的原始或模糊表述'),
        ('location_description', 'Location description', '根据原文归纳的地点描述'),
        ('attribute_confidence', 'Attribute confidence', '按属性记录的置信度，JSON 对象'),
        ('overall_confidence', 'Overall confidence', '整个元素的模型置信度，取值 0 至 1'),
        ('review_status', 'Review status', '核对状态：pending 待核对、confirmed 确认、corrected 已修正、cannot_remember 记不清、rejected 已拒绝'),
        ('reviewed_by_user_id', 'Reviewed by user id', '核对记忆元素的用户 ID'),
        ('reviewed_at', 'Reviewed at', '记录核对结论的时间'),
        ('created_by', 'Created by', '创建人 ID'),
        ('created_at', 'Created at', '创建时间'),
        ('updated_by', 'Updated by', '更新人 ID'),
        ('updated_at', 'Updated at', '更新时间'),
        ('is_del', 'Is del', '是否软删除'),
    ),
    't_memory_element_entity': (
        ('id', 'Id', '主键 ID'),
        ('memory_element_id', 'Memory element id', '关联的记忆元素 ID'),
        ('entity_id', 'Entity id', '关联的人生实体 ID'),
        ('relation_type', 'Relation type', '实体角色：subject 主体、participant 参与者、location 地点、mentioned 提及、other 其他'),
        ('confidence', 'Confidence', '元素与实体关联的置信度，取值 0 至 1'),
        ('evidence_text', 'Evidence text', '支持此关联的原文证据'),
        ('created_by', 'Created by', '创建人 ID'),
        ('created_at', 'Created at', '创建时间'),
        ('updated_by', 'Updated by', '更新人 ID'),
        ('updated_at', 'Updated at', '更新时间'),
        ('is_del', 'Is del', '是否软删除'),
    ),
    't_memory_fragment': (
        ('session_id', '所属录音会话ID', '所属记忆会话 ID'),
        ('content_text', '直接输入或文本文件内容', '用户直接提供的文字内容，文件对象通过来源字段关联'),
        ('transcript_raw', 'ASR原始转写文本', '原始 ASR 转写文本，可由原素材提供者补交'),
        ('recorded_at', '录音发生时间', '素材记录时间，支持客户端补录历史时间'),
    ),
    't_memory_session': (
        ('last_material_at', '最近新增素材时间', '未删除片段中最晚的记录时间，无片段时为空'),
    ),
    't_memory_session_member': (
        ('id', 'Primary key identifier', '主键 ID'),
        ('session_id', 'Memory session ID', '所属记忆会话 ID'),
        ('user_id', 'Related user account ID', '获得会话授权的用户 ID'),
        ('role', 'Member role and access level in the session', '会话角色：viewer 查看者、contributor 贡献者、editor 编辑者、delegate 代理'),
        ('source_relationship_id', 'Relationship supporting the session access grant', '会话授权所依据的用户关系 ID'),
        ('can_manage_members', 'Whether this member may manage other session members', '是否允许代理管理普通会话成员'),
        ('status', 'Lifecycle status', '授权状态：active 有效、revoked 已撤销、expired 已过期'),
        ('granted_by_user_id', 'User who granted this session membership', '授予会话权限的用户 ID'),
        ('granted_at', 'Timestamp when session access was granted', '最近一次授予会话权限的时间'),
        ('valid_until', 'End of the validity period', '会话授权截止时间，空值表示无单独期限'),
        ('revoked_at', 'Timestamp when the relationship was revoked', '撤销会话授权的时间'),
        ('revocation_reason', 'Reason the relationship was revoked', '撤销会话授权的原因'),
        ('created_by', 'User ID that created this row', '创建人 ID'),
        ('created_at', 'Timestamp when this row was created', '创建时间'),
        ('updated_by', 'User ID that last updated this row', '更新人 ID'),
        ('updated_at', 'Timestamp when this row was last updated', '更新时间'),
        ('is_del', 'Soft-delete flag', '是否软删除'),
    ),
    't_user_relationship': (
        ('id', 'Primary key identifier', '主键 ID'),
        ('relationship_pair_id', 'Shared identifier for reciprocal relationship rows', '关系组 ID，双向亲属记录共用'),
        ('subject_user_id', 'User represented as the subject of this directional relation', '定向关系的主体用户 ID'),
        ('related_user_id', 'Other user referenced by this directional relation', '定向关系指向的用户 ID'),
        ('relationship_type', 'Relationship category: family, agent, caregiver, or other', '关系类型：family 亲属、agent 代理、caregiver 照护、other 其他'),
        ('relation_code', 'Directional relation code; reciprocal family codes are stored in paired rows', '主体对目标的关系码，如 parent_of 的反向为 child_of'),
        ('relationship_label', 'Free-form relationship label', '用户填写的自定义关系称谓'),
        ('status', 'Lifecycle status', '关系状态：pending 待确认、active 有效、rejected 已拒绝、revoked 已撤销、expired 已过期'),
        ('authorization_method', 'Method used to establish or verify authorization', '关系确认方式：in_app 应用内、offline_agreement 线下约定、admin_verified 人工核验'),
        ('authorization_reference', 'Reference to supporting authorization evidence', '关系授权的凭据或外部记录引用'),
        ('initiated_by_user_id', 'User who initiated the relationship invitation', '发起关系邀请的用户 ID'),
        ('verified_by_user_id', 'User who verified the relationship', '核验关系的用户 ID'),
        ('valid_from', 'Beginning of the validity period', '关系生效时间'),
        ('valid_until', 'End of the validity period', '关系有效期截止时间，空值表示无单独期限'),
        ('accepted_at', 'Timestamp when the relationship was accepted', '接收方确认关系的时间'),
        ('revoked_at', 'Timestamp when the relationship was revoked', '撤回邀请或撤销关系的时间'),
        ('revocation_reason', 'Reason the relationship was revoked', '撤回邀请或撤销关系的原因'),
        ('created_by', 'User ID that created this row', '创建人 ID'),
        ('created_at', 'Timestamp when this row was created', '创建时间'),
        ('updated_by', 'User ID that last updated this row', '更新人 ID'),
        ('updated_at', 'Timestamp when this row was last updated', '更新时间'),
        ('is_del', 'Soft-delete flag', '是否软删除'),
    ),
}


def upgrade() -> None:
    for table_name, (previous, current) in TABLE_COMMENTS.items():
        op.create_table_comment(table_name, current, existing_comment=previous)
    for table_name, columns in COLUMN_COMMENTS.items():
        for column_name, previous, current in columns:
            op.alter_column(table_name, column_name, existing_comment=previous, comment=current)


def downgrade() -> None:
    for table_name, columns in COLUMN_COMMENTS.items():
        for column_name, previous, current in columns:
            op.alter_column(table_name, column_name, existing_comment=current, comment=previous)
    for table_name, (previous, current) in TABLE_COMMENTS.items():
        if previous is None:
            op.drop_table_comment(table_name, existing_comment=current)
        else:
            op.create_table_comment(table_name, previous, existing_comment=current)
