"""Add publisher calendars, templates and recipient schedule index."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261126_01"
down_revision = "20261125_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_youth_partner_profile",
        sa.Column(
            "calendar_time_zone",
            sa.String(length=64),
            nullable=True,
            comment="青年伙伴发布时间表所用的 IANA 时区",
        ),
    )
    op.create_table(
        "t_youth_partner_availability_template",
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="模板所属青年伙伴资料 ID"),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            comment="模板类别：workday 工作日、restday 休息日",
        ),
        sa.Column(
            "windows",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
            comment="模板开放区间，起止为当地午夜起的分钟数",
        ),
        sa.Column(
            "booking_start_minutes",
            postgresql.ARRAY(sa.SmallInteger()),
            server_default=sa.text("'{}'"),
            nullable=False,
            comment="模板预约开始时间，当地午夜起的分钟数",
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
            "jsonb_typeof(windows) = 'array'", name="ck_youth_partner_template_windows"
        ),
        sa.CheckConstraint("kind IN ('workday', 'restday')", name="ck_youth_partner_template_kind"),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("partner_id", "kind"),
        comment="青年伙伴的工作日与休息日预约模板",
    )
    op.create_table(
        "t_youth_partner_calendar_day",
        sa.Column("partner_id", sa.BigInteger(), nullable=False, comment="日历所属青年伙伴资料 ID"),
        sa.Column("schedule_date", sa.Date(), nullable=False, comment="伙伴日历时区下的发布日期"),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="日期的发布版本",
        ),
        sa.Column(
            "booking_starts",
            postgresql.ARRAY(sa.DateTime(timezone=True)),
            server_default=sa.text("'{}'"),
            nullable=False,
            comment="当天发布的预约开始绝对时间，空数组表示无可约开始时间",
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
        sa.CheckConstraint("version > 0", name="ck_youth_partner_calendar_day_version"),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["t_youth_partner_profile.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("partner_id", "schedule_date"),
        comment="青年伙伴已发布的每日预约开始时间",
    )
    op.create_index(
        "idx_benefit_order_recipient_schedule",
        "t_benefit_service_order",
        ["requested_by_user_id", "scheduled_start_at", "id"],
        unique=False,
        postgresql_where=sa.text("is_del = false AND status IN ('reserved', 'in_progress')"),
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE t_youth_partner_profile, t_youth_partner_availability_template, "
        "t_youth_partner_calendar_day IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM t_youth_partner_availability_template)
                OR EXISTS (SELECT 1 FROM t_youth_partner_calendar_day)
                OR EXISTS (SELECT 1 FROM t_youth_partner_profile WHERE calendar_time_zone IS NOT NULL)
            THEN
                RAISE EXCEPTION 'Published calendars and templates must be preserved before downgrade';
            END IF;
        END $$;
    """)
    op.drop_index("idx_benefit_order_recipient_schedule", table_name="t_benefit_service_order")
    op.drop_table("t_youth_partner_calendar_day")
    op.drop_table("t_youth_partner_availability_template")
    op.drop_column("t_youth_partner_profile", "calendar_time_zone")
