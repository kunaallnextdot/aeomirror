"""Phase 5: authentication + multi-user accounts.

Creates users, organizations, organization_members, sessions, password_resets,
email_verifications, invitations; and adds organization_id / user_id ownership
columns (+ indexes) to scans so history can be scoped to an organization.

Additive and backward-compatible: existing anonymous scans keep organization_id
NULL and remain reachable via the public /v1/scan flow.

Revision ID: 7a1c9e4b2f60
Revises: b2f4a7c9d1e3
Create Date: 2026-07-25 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a1c9e4b2f60"
down_revision: Union[str, None] = "b2f4a7c9d1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------- users ----------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=True),
        sa.Column("avatar", sa.String(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("notification_prefs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_login", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ---------------- organizations ----------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)
    op.create_index("ix_organizations_owner_id", "organizations", ["owner_id"])

    # ---------------- organization_members ----------------
    op.create_table(
        "organization_members",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_organization_members_organization_id", "organization_members",
                    ["organization_id"])
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])
    op.create_index("ix_org_members_org_user", "organization_members",
                    ["organization_id", "user_id"], unique=True)

    # ---------------- sessions ----------------
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip_hash", sa.String(), nullable=True),
        sa.Column("remember", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_refresh_token_hash", "sessions",
                    ["refresh_token_hash"], unique=True)

    # ---------------- password_resets ----------------
    op.create_table(
        "password_resets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_password_resets_user_id", "password_resets", ["user_id"])
    op.create_index("ix_password_resets_token_hash", "password_resets",
                    ["token_hash"], unique=True)

    # ---------------- email_verifications ----------------
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_email_verifications_user_id", "email_verifications", ["user_id"])
    op.create_index("ix_email_verifications_token_hash", "email_verifications",
                    ["token_hash"], unique=True)

    # ---------------- invitations ----------------
    op.create_table(
        "invitations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("invited_by", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"])
    op.create_index("ix_invitations_email", "invitations", ["email"])
    op.create_index("ix_invitations_token_hash", "invitations", ["token_hash"], unique=True)

    # ---------------- scans: ownership columns ----------------
    op.add_column("scans", sa.Column("organization_id", sa.String(), nullable=True))
    op.add_column("scans", sa.Column("user_id", sa.String(), nullable=True))
    op.create_index("ix_scans_organization_id", "scans", ["organization_id"])
    op.create_index("ix_scans_user_id", "scans", ["user_id"])
    op.create_index("ix_scans_org_created", "scans", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_scans_org_created", table_name="scans")
    op.drop_index("ix_scans_user_id", table_name="scans")
    op.drop_index("ix_scans_organization_id", table_name="scans")
    op.drop_column("scans", "user_id")
    op.drop_column("scans", "organization_id")

    op.drop_table("invitations")
    op.drop_table("email_verifications")
    op.drop_table("password_resets")
    op.drop_table("sessions")
    op.drop_table("organization_members")
    op.drop_table("organizations")
    op.drop_table("users")
