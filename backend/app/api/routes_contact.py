"""Contact & Support controller.

Public endpoint:
  POST /api/contact   -> validate, rate-limit (per IP), store, email (best-effort)

The admin-facing Support Inbox lives in routes_admin.py (the platform-admin
surface). This module owns the public submission path only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.routes_scan import _client_ip, _ip_hash
from app.core.cache import contact_limiter
from app.db.session import get_db
from app.schemas.contact import ContactRequest, ContactResponse
from app.services.contact_service import create_contact, schedule_email_delivery

router = APIRouter(prefix="/api", tags=["contact"])

_SUCCESS = ("Thank you. We've received your message and will reply within 24 hours.")


@router.post("/contact", response_model=ContactResponse)
def submit_contact(body: ContactRequest, request: Request,
                   db: Session = Depends(get_db)):
    # Anti-spam: fixed-window rate limit per client IP (shared across workers via
    # Redis when configured, in-memory otherwise).
    ip = _client_ip(request)
    allowed, _remaining, retry_after = contact_limiter.check(_ip_hash(ip))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="You've sent several messages recently. Please try again a little later.",
            headers={"Retry-After": str(retry_after)},
        )

    contact = create_contact(db, body, ip_hash=_ip_hash(ip))
    # Emails go out on a daemon thread — SMTP must never block or slow the request.
    schedule_email_delivery(contact)
    return ContactResponse(ok=True, message=_SUCCESS)
