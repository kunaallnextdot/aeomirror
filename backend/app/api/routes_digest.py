"""Public weekly-digest unsubscribe (no auth).

Security (C5): GET renders a confirmation page and NEVER mutates state — email clients
and security scanners pre-fetch links, so a GET-based opt-out would silently unsubscribe
users who never clicked. The opt-out happens on POST only (which also backs the email's
`List-Unsubscribe-Post` one-click header). An invalid/unknown token returns 404 without
revealing whether the token ever existed.
"""
from __future__ import annotations

import html as _html

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import digest as digest_svc

router = APIRouter(prefix="/digest", tags=["digest"])


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta name='robots' content='noindex'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{_html.escape(title)}</title></head>"
            f"<body style='font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;"
            f"padding:0 20px;color:#0B0F14'>{body}</body></html>")


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, db: Session = Depends(get_db)):
    """Render the confirmation page. Does NOT opt the user out (GET is safe)."""
    user = digest_svc.user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found.")   # no existence leak
    body = (f"<h2>Unsubscribe from the weekly digest?</h2>"
            f"<p><strong>{_html.escape(user.email)}</strong> will stop receiving AEOMirror "
            f"weekly digest emails.</p>"
            f"<form method='post' action='/digest/unsubscribe/{_html.escape(token)}'>"
            f"<button type='submit' style='padding:10px 16px;font-size:15px;cursor:pointer'>"
            f"Confirm unsubscribe</button></form>")
    return _page("Unsubscribe", body)


@router.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_confirm(token: str, db: Session = Depends(get_db)):
    """Perform the opt-out (idempotent). Backs both the confirmation form and the email's
    List-Unsubscribe-Post one-click header."""
    user = digest_svc.user_by_token(db, token)
    if not user:
        raise HTTPException(status_code=404, detail="Not found.")
    user.digest_opt_out = True
    db.commit()
    return _page("Unsubscribed",
                 "<h2>You're unsubscribed.</h2><p>You will no longer receive AEOMirror "
                 "weekly digest emails. You can re-enable them anytime from your dashboard.</p>")
