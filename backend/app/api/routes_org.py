"""Organization + team management endpoints (Phase 5).

Every endpoint here is protected. Viewing the org/team requires membership;
mutating it requires the appropriate permission (see app/api/deps.PERMISSIONS):
- invite / cancel invitations: Owner or Admin (user:invite)
- change roles / remove members / rename org: Owner (member:manage, org:update)

Accepting an invitation is public (token-authenticated) — it either attaches an
existing account or creates a new one, then signs the user in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_context, require_permission
from app.api.routes_auth import _org_out, _token_response
from app.config import settings
from app.core.security import (
    generate_token, hash_password, hash_token, password_problem, token_expiry,
    verify_password,
)
from app.db.models import (
    ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER, STATUS_ACTIVE,
    Invitation, Organization, OrganizationMember, User,
)
from app.db.session import get_db
from app.schemas.auth import (
    AcceptInviteRequest, InvitationOut, InviteRequest, MemberOut,
    OrganizationOut, TokenOut, UpdateMemberRoleRequest, UpdateOrgRequest,
)
from app.services.auth_email import send_invitation_email

# Roles that can be assigned via invite / role change (never Owner — there is
# exactly one owner, transferred separately).
ASSIGNABLE_ROLES = {ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER}

router = APIRouter(prefix="/org", tags=["organization"])


def _require_org(ctx: AuthContext) -> Organization:
    if not ctx.org:
        raise HTTPException(status_code=404, detail="No organization for this account.")
    return ctx.org


def _member_out(member: OrganizationMember, user: User, owner_id: str) -> dict:
    return {
        "id": member.id, "user_id": user.id, "name": user.name, "email": user.email,
        "avatar": user.avatar, "role": member.role, "status": user.status,
        "is_owner": user.id == owner_id, "created_at": member.created_at,
    }


# ================================ org ================================
@router.get("", response_model=OrganizationOut)
def get_org(ctx: AuthContext = Depends(get_context)):
    return _org_out(_require_org(ctx))


@router.patch("", response_model=OrganizationOut)
def update_org(body: UpdateOrgRequest,
               ctx: AuthContext = Depends(require_permission("org:update")),
               db: Session = Depends(get_db)):
    org = _require_org(ctx)
    org.name = body.name.strip()
    db.commit()
    db.refresh(org)
    return _org_out(org)


# ================================ members ================================
@router.get("/members", response_model=list[MemberOut])
def list_members(ctx: AuthContext = Depends(get_context), db: Session = Depends(get_db)):
    org = _require_org(ctx)
    rows = (db.query(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .filter(OrganizationMember.organization_id == org.id)
            .all())
    # Owner first, then by join time.
    out = [_member_out(m, u, org.owner_id) for m, u in rows]
    out.sort(key=lambda x: (not x["is_owner"], x["created_at"] or ""))
    return out


@router.patch("/members/{membership_id}", response_model=MemberOut)
def update_member_role(membership_id: str, body: UpdateMemberRoleRequest,
                       ctx: AuthContext = Depends(require_permission("member:manage")),
                       db: Session = Depends(get_db)):
    org = _require_org(ctx)
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail="Role must be admin, member, or viewer.")
    member = db.get(OrganizationMember, membership_id)
    if not member or member.organization_id != org.id:
        raise HTTPException(status_code=404, detail="Member not found.")
    if member.user_id == org.owner_id:
        raise HTTPException(status_code=409, detail="The owner's role cannot be changed.")
    member.role = body.role
    user = db.get(User, member.user_id)
    if user:
        user.role = body.role  # keep the mirrored user.role in sync
    db.commit()
    db.refresh(member)
    return _member_out(member, user, org.owner_id)


@router.delete("/members/{membership_id}")
def remove_member(membership_id: str,
                  ctx: AuthContext = Depends(require_permission("member:manage")),
                  db: Session = Depends(get_db)):
    org = _require_org(ctx)
    member = db.get(OrganizationMember, membership_id)
    if not member or member.organization_id != org.id:
        raise HTTPException(status_code=404, detail="Member not found.")
    if member.user_id == org.owner_id:
        raise HTTPException(status_code=409, detail="The owner cannot be removed.")
    db.delete(member)
    db.commit()
    return {"ok": True}


# ================================ invitations ================================
@router.post("/invitations", response_model=InvitationOut, status_code=201)
def create_invitation(body: InviteRequest,
                      ctx: AuthContext = Depends(require_permission("user:invite")),
                      db: Session = Depends(get_db)):
    org = _require_org(ctx)
    # Billing gate: team members are a Pro feature.
    from app.billing import entitlements
    if not entitlements.entitlements(db, org.id).get("team"):
        raise HTTPException(status_code=402,
            detail="Team members are a Pro feature. Upgrade to Pro to invite teammates.")
    role = body.role.strip().lower()
    if role not in ASSIGNABLE_ROLES:
        raise HTTPException(status_code=422, detail="Role must be admin, member, or viewer.")
    email = str(body.email).strip().lower()

    # If the email already belongs to a member of THIS org, nothing to do.
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        member = (db.query(OrganizationMember)
                  .filter(OrganizationMember.user_id == existing_user.id).first())
        if member and member.organization_id == org.id:
            raise HTTPException(status_code=409, detail="That person is already on your team.")
        if member:
            raise HTTPException(status_code=409,
                detail="That account already belongs to another organization.")

    # Reuse/replace any pending invite for the same email in this org.
    (db.query(Invitation)
     .filter(Invitation.organization_id == org.id, Invitation.email == email,
             Invitation.status == "pending")
     .update({Invitation.status: "revoked"}))

    raw = generate_token()
    inv = Invitation(
        organization_id=org.id, email=email, role=role,
        token_hash=hash_token(raw), invited_by=ctx.user.id, status="pending",
        expires_at=token_expiry(settings.invite_token_ttl_seconds),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    send_invitation_email(email, raw, org.name, role, ctx.user.name)
    return {"id": inv.id, "email": inv.email, "role": inv.role, "status": inv.status,
            "created_at": inv.created_at, "expires_at": inv.expires_at}


@router.get("/invitations", response_model=list[InvitationOut])
def list_invitations(ctx: AuthContext = Depends(require_permission("user:invite")),
                     db: Session = Depends(get_db)):
    org = _require_org(ctx)
    rows = (db.query(Invitation)
            .filter(Invitation.organization_id == org.id, Invitation.status == "pending")
            .order_by(Invitation.created_at.desc())
            .all())
    return [{"id": r.id, "email": r.email, "role": r.role, "status": r.status,
             "created_at": r.created_at, "expires_at": r.expires_at} for r in rows]


@router.delete("/invitations/{invitation_id}")
def cancel_invitation(invitation_id: str,
                      ctx: AuthContext = Depends(require_permission("user:invite")),
                      db: Session = Depends(get_db)):
    org = _require_org(ctx)
    inv = db.get(Invitation, invitation_id)
    if not inv or inv.organization_id != org.id:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    inv.status = "revoked"
    db.commit()
    return {"ok": True}


def _valid_invitation(db: Session, token: str) -> Invitation:
    inv = (db.query(Invitation)
           .filter(Invitation.token_hash == hash_token(token))
           .first())
    if not inv or inv.status != "pending" or inv.expires_at < token_expiry(0):
        raise HTTPException(status_code=400, detail="This invitation is invalid or has expired.")
    return inv


@router.get("/invitations/info")
def invitation_info(token: str, db: Session = Depends(get_db)):
    """Public: describe an invitation so the accept page can render correctly.
    Only reveals what the token holder already received in their email."""
    inv = _valid_invitation(db, token)
    org = db.get(Organization, inv.organization_id)
    if not org:
        raise HTTPException(status_code=400, detail="This invitation is no longer valid.")
    existing = db.query(User).filter(User.email == inv.email).first()
    return {
        "email": inv.email, "role": inv.role,
        "organization_name": org.name,
        "requires_signup": existing is None,   # new users must set name + password
    }


# ================================ accept invitation (public) ================================
@router.post("/invitations/accept", response_model=TokenOut)
def accept_invitation(body: AcceptInviteRequest, request: Request, response: Response,
                      db: Session = Depends(get_db)):
    inv = _valid_invitation(db, body.token)
    org = db.get(Organization, inv.organization_id)
    if not org:
        raise HTTPException(status_code=400, detail="This invitation is no longer valid.")

    user = db.query(User).filter(User.email == inv.email).first()
    if user:
        # Existing account: verify the password before granting a session, so
        # possession of the invite link alone can never hijack an existing account.
        if not verify_password(body.password or "", user.password_hash):
            raise HTTPException(status_code=401,
                detail="Enter your existing password to accept this invitation.")
        if db.query(OrganizationMember).filter(OrganizationMember.user_id == user.id).first():
            raise HTTPException(status_code=409,
                detail="This account already belongs to an organization.")
        user.role = inv.role
    else:
        # New account: name + password required.
        if not body.password or not body.name:
            raise HTTPException(status_code=422,
                detail="Set a name and password to create your account.")
        problem = password_problem(body.password)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
        user = User(name=body.name.strip(), email=inv.email,
                    password_hash=hash_password(body.password), role=inv.role,
                    status=STATUS_ACTIVE,
                    email_verified=True,  # inviting proves control of the inbox
                    notification_prefs={"product_updates": True, "scan_reports": True})
        db.add(user)
        db.flush()

    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=inv.role))
    inv.status = "accepted"
    inv.accepted_at = token_expiry(0)
    db.commit()
    db.refresh(user)
    return _token_response(db, user, request, response, remember=False)
