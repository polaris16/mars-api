"""Add in-app chat conversations, members and messages."""

import sqlalchemy as sa
from alembic import op

revision = "20261125_03"
down_revision = "20261125_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_chat_conversation",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="会话 ID"),
        sa.Column(
            "kind", sa.String(length=30), nullable=False, comment="会话类型：friend 好友聊天"
        ),
        sa.Column("relationship_pair_id", sa.Uuid(), nullable=False, comment="原双向好友关系组 ID"),
        sa.Column(
            "last_message_seq",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="最后已提交的会话内消息序号",
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最后一条消息的服务端时间",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint("kind = 'friend'", name="ck_chat_conversation_kind"),
        sa.CheckConstraint(
            "last_message_seq >= 0 AND (last_message_seq = 0) = (last_message_at IS NULL)",
            name="ck_chat_conversation_last_message",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relationship_pair_id"),
        comment="站内一对一会话及最近消息位置",
    )
    op.create_index(
        "idx_chat_conversation_inbox",
        "t_chat_conversation",
        ["last_message_at", "id"],
        unique=False,
        postgresql_where=sa.text("is_del = false AND last_message_at IS NOT NULL"),
    )
    op.create_table(
        "t_chat_conversation_member",
        sa.Column("conversation_id", sa.BigInteger(), nullable=False, comment="所属会话 ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="会话成员账号 ID"),
        sa.Column(
            "last_read_seq",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
            comment="本人连续已读的最后消息序号",
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint("last_read_seq >= 0", name="ck_chat_member_last_read_seq"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["t_chat_conversation.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["t_user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("conversation_id", "user_id"),
        comment="站内会话成员及个人已读进度",
    )
    op.create_index(
        "idx_chat_member_user_conversation",
        "t_chat_conversation_member",
        ["user_id", "conversation_id"],
        unique=False,
    )
    op.create_table(
        "t_chat_message",
        sa.Column("id", sa.BigInteger(), nullable=False, comment="消息 ID"),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False, comment="所属会话 ID"),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False, comment="会话内递增的消息序号"),
        sa.Column("sender_user_id", sa.BigInteger(), nullable=False, comment="发送者账号 ID"),
        sa.Column("client_message_id", sa.Uuid(), nullable=False, comment="客户端发送及重试标识"),
        sa.Column(
            "message_type",
            sa.String(length=30),
            nullable=False,
            comment="内容类型：text 文字、partner_card 青年伙伴卡片",
        ),
        sa.Column("body_text", sa.Text(), nullable=True, comment="文字消息正文"),
        sa.Column(
            "partner_id", sa.BigInteger(), nullable=True, comment="分享卡片所引用的青年伙伴资料 ID"
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否软删除",
        ),
        sa.CheckConstraint(
            "(message_type = 'text' AND body_text IS NOT NULL AND char_length(body_text) BETWEEN 1 AND 2000 AND btrim(body_text) <> '' AND partner_id IS NULL) OR (message_type = 'partner_card' AND body_text IS NULL AND partner_id IS NOT NULL)",
            name="ck_chat_message_content",
        ),
        sa.CheckConstraint("sequence_no > 0", name="ck_chat_message_sequence"),
        sa.ForeignKeyConstraint(
            ["conversation_id", "sender_user_id"],
            ["t_chat_conversation_member.conversation_id", "t_chat_conversation_member.user_id"],
            name="fk_chat_message_sender_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "sender_user_id", "client_message_id", name="uq_chat_message_request"
        ),
        sa.UniqueConstraint("conversation_id", "sequence_no", name="uq_chat_message_sequence"),
        comment="站内聊天历史消息",
    )
    op.create_index(
        "idx_chat_message_sender_sequence",
        "t_chat_message",
        ["conversation_id", "sender_user_id", "sequence_no"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE t_chat_conversation, t_chat_conversation_member, t_chat_message "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM t_chat_conversation)
                OR EXISTS (SELECT 1 FROM t_chat_conversation_member)
                OR EXISTS (SELECT 1 FROM t_chat_message)
            THEN
                RAISE EXCEPTION 'Chat history must be preserved before downgrade';
            END IF;
        END $$;
    """)
    op.drop_table("t_chat_message")
    op.drop_table("t_chat_conversation_member")
    op.drop_table("t_chat_conversation")
