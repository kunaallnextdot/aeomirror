"""Request/response schemas for auth + org endpoints (Phase 5)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ------------------------------- auth requests -------------------------------
class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    organization_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    remember: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=1, max_length=200)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


# ------------------------------- profile -------------------------------
class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar: str | None = Field(default=None, max_length=500_000)  # URL or data-URI
    email: EmailStr | None = None
    notification_prefs: dict | None = None
    # To change the password the current password must be supplied.
    current_password: str | None = None
    new_password: str | None = Field(default=None, max_length=200)


# ------------------------------- responses -------------------------------
class UserOut(BaseModel):
    id: str
    name: str
    email: str
    avatar: str | None = None
    email_verified: bool
    role: str
    status: str
    is_platform_admin: bool = False
    notification_prefs: dict | None = None
    created_at: datetime | None = None
    last_login: datetime | None = None


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    created_at: datetime | None = None


class MeOut(BaseModel):
    user: UserOut
    organization: OrganizationOut | None = None
    role: str


class TokenOut(BaseModel):
    """Login/register/refresh response. The refresh token lives in an httpOnly
    cookie and is NOT included here."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    organization: OrganizationOut | None = None
    # dev-only: verification token when email isn't configured (never in prod)
    dev_verification_token: str | None = None


class SessionOut(BaseModel):
    id: str
    user_agent: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    current: bool = False


# ------------------------------- org / team -------------------------------
class UpdateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberOut(BaseModel):
    id: str            # membership id
    user_id: str
    name: str
    email: str
    avatar: str | None = None
    role: str
    status: str
    is_owner: bool = False
    created_at: datetime | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "member"


class InvitationOut(BaseModel):
    id: str
    email: str
    role: str
    status: str
    created_at: datetime | None = None
    expires_at: datetime | None = None


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=120)   # required for new users
    password: str | None = Field(default=None, max_length=200)


class UpdateMemberRoleRequest(BaseModel):
    role: str
