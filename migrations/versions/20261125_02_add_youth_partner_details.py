"""[新增] 青年伙伴详情、服务时长和收藏。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "20261125_02"
down_revision = "20261125_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_youth_partner_profile",
        sa.Column(
            "specialties",
            ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="按展示顺序排列的擅长领域",
        ),
    )
    op.add_column(
        "t_youth_partner_profile",
        sa.Column("message_to_users", sa.String(500), comment="公开展示的伙伴寄语"),
    )
    op.add_column(
        "t_youth_partner_booking",
        sa.Column(
            "service_duration_seconds", sa.Integer(), comment="完成的服务在预约时段内实际履约的秒数"
        ),
    )
    op.create_check_constraint(
        "ck_youth_partner_booking_service_duration",
        "t_youth_partner_booking",
        "service_duration_seconds IS NULL OR service_duration_seconds >= 0",
    )
    # 仅补算时间齐全的已完成服务。原预约、流水及权益余额不参与更新。
    op.execute("""
        UPDATE t_youth_partner_booking AS booking
        SET service_duration_seconds =
            GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (
                LEAST(orders.finished_at, orders.scheduled_end_at)
                - GREATEST(booking.checked_in_at, orders.scheduled_start_at)
            ))))::integer
        FROM t_benefit_service_order AS orders
        WHERE booking.service_order_id = orders.id
          AND booking.is_del = false
          AND orders.is_del = false
          AND orders.status = 'completed'
          AND booking.checked_in_at IS NOT NULL
          AND orders.finished_at IS NOT NULL
          AND orders.scheduled_start_at IS NOT NULL
          AND orders.scheduled_end_at IS NOT NULL
    """)
    op.create_table(
        "t_youth_partner_mark",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("t_user.id", ondelete="RESTRICT"),
            primary_key=True,
            comment="收藏所属用户账号 ID",
        ),
        sa.Column(
            "partner_id",
            sa.BigInteger(),
            sa.ForeignKey("t_youth_partner_profile.id", ondelete="RESTRICT"),
            primary_key=True,
            comment="收藏的青年伙伴资料 ID",
        ),
        sa.Column("created_by", sa.BigInteger(), comment="创建人 ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        sa.Column("updated_by", sa.BigInteger(), comment="更新人 ID"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="更新时间",
        ),
        sa.Column(
            "is_del",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否软删除",
        ),
        comment="用户个人青年伙伴收藏",
    )
    op.create_index(
        "idx_youth_partner_mark_active_user",
        "t_youth_partner_mark",
        ["user_id", "updated_at", "partner_id"],
        postgresql_where=sa.text("is_del = false"),
    )


def downgrade() -> None:
    # 时长可从原有服务单重新生成，新保存的收藏和公开资料需先保留。
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM t_youth_partner_mark)
                OR EXISTS (
                    SELECT 1 FROM t_youth_partner_profile
                    WHERE cardinality(specialties) > 0 OR message_to_users IS NOT NULL
                )
            THEN
                RAISE EXCEPTION 'Youth partner details must be preserved before downgrade';
            END IF;
        END $$;
    """)
    op.drop_table("t_youth_partner_mark")
    op.drop_constraint(
        "ck_youth_partner_booking_service_duration", "t_youth_partner_booking", type_="check"
    )
    op.drop_column("t_youth_partner_booking", "service_duration_seconds")
    op.drop_column("t_youth_partner_profile", "message_to_users")
    op.drop_column("t_youth_partner_profile", "specialties")
