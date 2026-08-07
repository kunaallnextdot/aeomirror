"""Auth dependencies + RBAC (Phase 5).

`get_current_user` authenticates the Bearer access token and loads the account.
`get_context` additionally resolves the org membership (the RBAC source of truth).
`require_permission` / `require_role` are dependency factories that protect an
endpoint. Every failure is a 401 (not authenticated) or 403 (authenticated but
not allowed) — never a 500, and never leaks whether a resource exists.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import decode_access_token
from app.db.models import (
    ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, ROLE_VIEWER, STATUS_ACTIVE,
    Organization, OrganizationMember, User,
)
from app.db.session import get_db

# --------------------------- permission matrix ---------------------------
# Action → the set of roles allowed to perform it. Owner is granted everything
# implicitly (see has_permission). Keep this table the single source of truth.
PERMISSIONS: dict[str, set[str]] = {
    "report:view": {ROLE_VIEWER, ROLE_MEMBER, ROLE_ADMIN, ROLE_OWNER},
    "scan:run": {ROLE_MEMBER, ROLE_ADMIN, ROLE_OWNER},
    "scan:delete": {ROLE_ADMIN, ROLE_OWNER},
    "user:invite": {ROLE_ADMIN, ROLE_OWNER},
    "member:manage": {ROLE_OWNER},   # change roles / remove members
    "org:update": {ROLE_OWNER, ROLE_ADMIN},
    "org:delete": {ROLE_OWNER},
}


def has_permission(role: str, action: str) -> bool:
    if role == ROLE_OWNER:
        return True
    return role in PERMISSIONS.get(action, set())


@dataclass
class AuthContext:
    user: User
    org: Organization | None
    role: str

    @property
    def org_id(self) -> str | None:
        return self.org.id if self.org else None


_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Require a valid access token for an active account."""
    token = _bearer(authorization)
    if not token:
        raise _UNAUTH
    claims = decode_access_token(token)
    if not claims:
        raise _UNAUTH
    user = db.get(User, claims["sub"])
    if not user or user.status != STATUS_ACTIVE:
        raise _UNAUTH
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """Authenticate if a token is present; otherwise return None (never raises).
    Used by the public scanner so signed-in scans are attributed to their org."""
    token = _bearer(authorization)
    if not token:
        return None
    claims = decode_access_token(token)
    if not claims:
        return None
    user = db.get(User, claims["sub"])
    if not user or user.status != STATUS_ACTIVE:
        return None
    return user


def get_context(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthContext:
    """Resolve the caller's organization + effective role from the membership row
    (authoritative), falling back to the user's stored role."""
    member = (db.query(OrganizationMember)
              .filter(OrganizationMember.user_id == user.id)
              .first())
    org = db.get(Organization, member.organization_id) if member else None
    role = member.role if member else user.role
    return AuthContext(user=user, org=org, role=role)


def require_permission(action: str):
    """Dependency factory: 403 unless the caller's role grants `action`."""
    def _dep(ctx: AuthContext = Depends(get_context)) -> AuthContext:
        if not has_permission(ctx.role, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return ctx
    return _dep


def is_platform_admin(user: User) -> bool:
    """True for platform admins: the DB flag or an allowlisted email (bootstrap)."""
    return bool(user.is_platform_admin) or user.email in settings.admin_email_list()


def get_admin(user: User = Depends(get_current_user)) -> User:
    """Protect an admin endpoint: 403 unless the caller is a platform admin."""
    if not is_platform_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


def require_role(*roles: str):
    """Dependency factory: 403 unless the caller has one of `roles`."""
    allowed = set(roles)

    def _dep(ctx: AuthContext = Depends(get_context)) -> AuthContext:
        if ctx.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return ctx
    return _dep
