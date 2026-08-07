"""Phase 9: billing + subscriptions.

Adds plans, subscriptions, payments, payment_events, invoices and seeds the three
plans (free, report, pro). Additive and reversible.

Revision ID: c9e5a71f4b28
Revises: b8d3f1a20c47
Create Date: 2026-07-25 22:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9e5a71f4b28"
down_revision: Union[str, None] = "b8d3f1a20c47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("code", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="usd"),
        sa.Column("interval", sa.String(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("plan_code", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("provider", sa.String(), nullable=False, server_default="stripe"),
        sa.Column("provider_subscription_id", sa.String(), nullable=True),
        sa.Column("provider_customer_id", sa.String(), nullable=True),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_subscriptions_organization_id", "subscriptions", ["organization_id"])
    op.create_index("ix_subscriptions_provider_subscription_id", "subscriptions", ["provider_subscription_id"])
    op.create_index("ix_subs_org_status", "subscriptions", ["organization_id", "status"])

    op.create_table(
        "payments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="stripe"),
        sa.Column("provider_payment_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("plan_code", sa.String(), nullable=True),
        sa.Column("scan_id", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="usd"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payments_organization_id", "payments", ["organization_id"])
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_provider_payment_id", "payments", ["provider_payment_id"])
    op.create_index("ix_payments_scan_id", "payments", ["scan_id"])
    op.create_index("ix_payments_reference", "payments", ["reference"])
    op.create_index("ix_payments_org_created", "payments", ["organization_id", "created_at"])

    op.create_table(
        "payment_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False, server_default="stripe"),
        sa.Column("provider_event_id", sa.String(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payment_events_provider_event_id", "payment_events",
                    ["provider_event_id"], unique=True)

    op.create_table(
        "invoices",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("subscription_id", sa.String(), nullable=True),
        sa.Column("payment_id", sa.String(), nullable=True),
        sa.Column("number", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="usd"),
        sa.Column("status", sa.String(), nullable=False, server_default="paid"),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_invoices_organization_id", "invoices", ["organization_id"])
    op.create_index("ix_invoices_subscription_id", "invoices", ["subscription_id"])
    op.create_index("ix_invoices_payment_id", "invoices", ["payment_id"])
    op.create_index("ix_invoices_number", "invoices", ["number"], unique=True)
    op.create_index("ix_invoices_org_created", "invoices", ["organization_id", "created_at"])

    # Seed the three plans from the code definitions (idempotent).
    from app.billing.plans import PLAN_DEFS
    conn = op.get_bind()
    plans = sa.table(
        "plans",
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.String), sa.column("price_cents", sa.Integer),
        sa.column("currency", sa.String), sa.column("interval", sa.String),
        sa.column("features", sa.JSON), sa.column("active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    for p in PLAN_DEFS:
        conn.execute(plans.insert().values(
            code=p["code"], name=p["name"], description=p["description"],
            price_cents=p["price_cents"], currency=p["currency"], interval=p["interval"],
            features=p["features"], active=True, sort_order=p["sort_order"],
        ))


def downgrade() -> None:
    op.drop_table("invoices")
    op.drop_table("payment_events")
    op.drop_table("payments")
    op.drop_table("subscriptions")
    op.drop_table("plans")
