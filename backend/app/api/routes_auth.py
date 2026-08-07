"""Authentication + account endpoints (Phase 5).

Register / login / refresh / logout, password reset, email verification, and the
/me profile + session-management endpoints.

Token model:
- Access token: short-lived JWT (Bearer), returned in the JSON body. The client
  keeps it in memory and sends it as `Authorization: Bearer <token>`.
- Refresh token: opaque, high-entropy, stored only as a sha256 hash, delivered in
  an httpOnly + SameSite=strict cookie scoped to /auth. Rotated on every refresh
  (refresh-token rotation) and revoked on logout / password reset.

Security: login is rate-limited per email+IP; password strength is enforced;
forgot-password never reveals whether an email exists; cookies are Secure in
production; SameSite=strict + Bearer-auth mutations cover CSRF.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_context, get_current_user
from app.api.routes_scan import _client_ip, _ip_hash
from app.config import settings
from app.core.cache import login_limiter
from app.core.security import (
    create_access_token, generate_token, hash_password, hash_token,
    password_problem, refresh_expiry, slugify, token_expiry, verify_password,
)
from app.db.models import (
    ROLE_OWNER, STATUS_ACTIVE, EmailVerification, Organization,
    OrganizationMember, PasswordReset, Session as SessionModel, User,
)
from app.db.session import get_db
from app.schemas.auth import (
    ForgotPasswordRequest, LoginRequest, MeOut, RegisterRequest,
    ResendVerificationRequest, ResetPasswordRequest, SessionOut, TokenOut,
    UpdateMeRequest, VerifyEmailRequest,
)
from app.services.auth_email import (
    send_password_reset_email, send_verification_email,
)

logger = logging.getLogger("aeomirror.auth")

router = APIRouter(tags=["auth"])


# ------------------------------- serializers -------------------------------
def _user_out(user: User) -> dict:
    from app.api.deps import is_platform_admin
    return {
        "id": user.id, "name": user.name, "email": user.email,
        "avatar": user.avatar, "email_verified": user.email_verified,
        "role": user.role, "status": user.status,
        "is_platform_admin": is_platform_admin(user),
        "notification_prefs": user.notification_prefs,
        "created_at": user.created_at, "last_login": user.last_login,
    }


def _org_out(org: Organization | None) -> dict | None:
    if not org:
        return None
    return {"id": org.id, "name": org.name, "slug": org.slug,
            "owner_id": org.owner_id, "created_at": org.created_at}


# ------------------------------- cookie + session helpers -------------------------------
# Cookie path "/" so the session-management endpoints (/me/sessions) can identify
# the current session. httpOnly + SameSite=strict (+ Secure in prod) keep it safe;
# only /auth/refresh ever rotates it.
_COOKIE_PATH = "/"


def _set_refresh_cookie(response: Response, token: str, remember: bool) -> None:
    max_age = (settings.refresh_token_remember_seconds if remember
               else settings.refresh_token_ttl_seconds)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path=_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name, path=_COOKIE_PATH,
        domain=settings.auth_cookie_domain,
    )


def _issue_session(db: Session, user: User, request: Request,
                   remember: bool) -> str:
    """Create a refresh session and return the RAW token (stored only as a hash)."""
    raw = generate_token()
    row = SessionModel(
        user_id=user.id,
        refresh_token_hash=hash_token(raw),
        user_agent=(request.headers.get("user-agent") or "")[:400] or None,
        ip_hash=_ip_hash(_client_ip(request)),
        remember=remember,
        expires_at=refresh_expiry(remember),
    )
    db.add(row)
    db.commit()
    return raw


def _membership(db: Session, user: User):
    return (db.query(OrganizationMember)
            .filter(OrganizationMember.user_id == user.id).first())


def _token_response(db: Session, user: User, request: Request, response: Response,
                    remember: bool, *, dev_token: str | None = None) -> dict:
    raw = _issue_session(db, user, request, remember)
    _set_refresh_cookie(response, raw, remember)
    member = _membership(db, user)
    org = db.get(Organization, member.organization_id) if member else None
    access = create_access_token(
        user_id=user.id, org_id=(org.id if org else None),
        role=(member.role if member else user.role),
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "user": _user_out(user),
        "organization": _org_out(org),
        "dev_verification_token": dev_token if not settings.is_production else None,
    }


def _unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, n = base, 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


# ================================ register ================================
@router.post("/auth/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, response: Response,
             db: Session = Depends(get_db)):
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    email = str(body.email).strip().lower()
    if db.query(User).filter(User.email == email).first():
        # Do not reveal much, but a clear message here is standard for signup.
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    # First user of a new org is its Owner.
    user = User(name=body.name.strip(), email=email,
                password_hash=hash_password(body.password),
                role=ROLE_OWNER, status=STATUS_ACTIVE, email_verified=False,
                notification_prefs={"product_updates": True, "scan_reports": True})
    db.add(user)
    db.flush()

    org_name = (body.organization_name or f"{body.name.strip()}'s Team").strip()
    org = Organization(name=org_name, slug=_unique_slug(db, org_name), owner_id=user.id)
    db.add(org)
    db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=ROLE_OWNER))
    db.commit()
    db.refresh(user)

    # Email verification (best-effort send).
    raw_verify = generate_token()
    db.add(EmailVerification(user_id=user.id, token_hash=hash_token(raw_verify),
                             expires_at=token_expiry(settings.verify_token_ttl_seconds)))
    db.commit()
    send_verification_email(email, raw_verify)

    return _token_response(db, user, request, response, remember=False,
                           dev_token=raw_verify)


# ================================ login ================================
@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db)):
    email = str(body.email).strip().lower()
    ip = _client_ip(request)
    limiter_key = _ip_hash(f"{email}|{ip}")
    allowed, _remaining, retry_after = login_limiter.check(limiter_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please wait a few minutes and try again.",
            headers={"Retry-After": str(retry_after)})

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(body.password, user.password_hash):
        # Generic message — never reveal whether the email exists.
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if user.status != STATUS_ACTIVE:
        raise HTTPException(status_code=403, detail="This account is not active.")

    user.last_login = token_expiry(0)  # = utcnow()
    db.commit()

    # Phase 8: audit platform-admin logins.
    from app.api.deps import is_platform_admin
    if is_platform_admin(user):
        from app.admin import audit
        audit.record(db, actor=user, action=audit.ADMIN_LOGIN, target_type="user",
                     target_id=user.id, ip_hash=_ip_hash(ip))
    return _token_response(db, user, request, response, remember=body.remember)


# ================================ refresh ================================
@router.post("/auth/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(settings.auth_cookie_name)
    if not raw:
        raise HTTPException(status_code=401, detail="No active session.")
    row = (db.query(SessionModel)
           .filter(SessionModel.refresh_token_hash == hash_token(raw))
           .first())
    if not row or row.revoked or row.expires_at < token_expiry(0):
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")

    user = db.get(User, row.user_id)
    if not user or user.status != STATUS_ACTIVE:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Session is no longer valid.")

    # Rotate: revoke the presented token and issue a fresh one (same remember).
    row.revoked = True
    db.commit()
    return _token_response(db, user, request, response, remember=row.remember)


# ================================ logout ================================
@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(settings.auth_cookie_name)
    if raw:
        row = (db.query(SessionModel)
               .filter(SessionModel.refresh_token_hash == hash_token(raw))
               .first())
        if row and not row.revoked:
            row.revoked = True
            db.commit()
    _clear_refresh_cookie(response)
    return {"ok": True}


@router.post("/auth/logout-all")
def logout_all(request: Request, response: Response,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revoke every session for the current user ("log out everywhere")."""
    (db.query(SessionModel)
     .filter(SessionModel.user_id == user.id, SessionModel.revoked == False)  # noqa: E712
     .update({SessionModel.revoked: True}))
    db.commit()
    _clear_refresh_cookie(response)
    return {"ok": True}


# ================================ password reset ================================
@router.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = str(body.email).strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        raw = generate_token()
        db.add(PasswordReset(user_id=user.id, token_hash=hash_token(raw),
                             expires_at=token_expiry(settings.reset_token_ttl_seconds)))
        db.commit()
        send_password_reset_email(email, raw)
    # Always the same response, whether or not the email exists.
    return {"ok": True, "message": "If that email has an account, a reset link is on its way."}


@router.post("/auth/reset-password")
def reset_password(body: ResetPasswordRequest, response: Response,
                   db: Session = Depends(get_db)):
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    row = (db.query(PasswordReset)
           .filter(PasswordReset.token_hash == hash_token(body.token))
           .first())
    if not row or row.used_at is not None or row.expires_at < token_expiry(0):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")

    user.password_hash = hash_password(body.password)
    row.used_at = token_expiry(0)
    # Revoke all sessions — a password reset logs the user out everywhere.
    (db.query(SessionModel)
     .filter(SessionModel.user_id == user.id, SessionModel.revoked == False)  # noqa: E712
     .update({SessionModel.revoked: True}))
    db.commit()
    _clear_refresh_cookie(response)
    return {"ok": True}


# ================================ email verification ================================
@router.post("/auth/verify-email")
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
    row = (db.query(EmailVerification)
           .filter(EmailVerification.token_hash == hash_token(body.token))
           .first())
    if not row or row.used_at is not None or row.expires_at < token_expiry(0):
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")
    user.email_verified = True
    row.used_at = token_expiry(0)
    db.commit()
    return {"ok": True}


@router.post("/auth/resend-verification")
def resend_verification(body: ResendVerificationRequest, db: Session = Depends(get_db)):
    email = str(body.email).strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user and not user.email_verified:
        raw = generate_token()
        db.add(EmailVerification(user_id=user.id, token_hash=hash_token(raw),
                                 expires_at=token_expiry(settings.verify_token_ttl_seconds)))
        db.commit()
        send_verification_email(email, raw)
    return {"ok": True}


# ================================ /me ================================
@router.get("/me", response_model=MeOut)
def get_me(ctx: AuthContext = Depends(get_context)):
    return {"user": _user_out(ctx.user), "organization": _org_out(ctx.org), "role": ctx.role}


@router.patch("/me", response_model=MeOut)
def update_me(body: UpdateMeRequest, ctx: AuthContext = Depends(get_context),
              db: Session = Depends(get_db)):
    user = ctx.user

    if body.name is not None:
        user.name = body.name.strip()
    if body.avatar is not None:
        user.avatar = body.avatar or None
    if body.notification_prefs is not None:
        merged = dict(user.notification_prefs or {})
        merged.update(body.notification_prefs)
        user.notification_prefs = merged

    # Email change → re-verify.
    if body.email is not None:
        new_email = str(body.email).strip().lower()
        if new_email != user.email:
            if db.query(User).filter(User.email == new_email).first():
                raise HTTPException(status_code=409, detail="That email is already in use.")
            user.email = new_email
            user.email_verified = False
            raw = generate_token()
            db.add(EmailVerification(user_id=user.id, token_hash=hash_token(raw),
                                     expires_at=token_expiry(settings.verify_token_ttl_seconds)))
            send_verification_email(new_email, raw)

    # Password change → require the current password.
    if body.new_password is not None:
        if not verify_password(body.current_password or "", user.password_hash):
            raise HTTPException(status_code=403, detail="Your current password is incorrect.")
        problem = password_problem(body.new_password)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        user.password_hash = hash_password(body.new_password)

    db.commit()
    db.refresh(user)
    return {"user": _user_out(user), "organization": _org_out(ctx.org), "role": ctx.role}


# ================================ sessions ================================
@router.get("/me/sessions", response_model=list[SessionOut])
def list_sessions(request: Request, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    current_hash = None
    raw = request.cookies.get(settings.auth_cookie_name)
    if raw:
        current_hash = hash_token(raw)
    rows = (db.query(SessionModel)
            .filter(SessionModel.user_id == user.id, SessionModel.revoked == False,  # noqa: E712
                    SessionModel.expires_at > token_expiry(0))
            .order_by(SessionModel.last_used_at.desc())
            .all())
    return [{
        "id": r.id, "user_agent": r.user_agent, "created_at": r.created_at,
        "last_used_at": r.last_used_at, "expires_at": r.expires_at,
        "current": r.refresh_token_hash == current_hash,
    } for r in rows]


@router.delete("/me/sessions/{session_id}")
def revoke_session(session_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    row = db.get(SessionModel, session_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    row.revoked = True
    db.commit()
    return {"ok": True}
