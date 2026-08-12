"""AEOMirror API entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    routes_admin, routes_auth, routes_billing, routes_contact, routes_dashboard,
    routes_digest, routes_misc, routes_monitors, routes_org, routes_public,
    routes_reports, routes_scan,
)
from app.config import settings
from app.core.observability import (
    RequestLoggingMiddleware, SecurityHeadersMiddleware, configure_logging,
    init_error_tracking,
)
from app.monitoring import worker

configure_logging()
init_error_tracking()   # Sentry, only when SENTRY_DSN is set
logger = logging.getLogger("aeomirror")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. Uvicorn handles SIGTERM and drains in-flight requests
    # (see --timeout-graceful-shutdown in the production entrypoint).
    logger.info("startup env=%s", settings.environment)
    # In-process monitoring worker (Phase 7). Disabled under tests and when
    # SCHEDULER_ENABLED=false (e.g. running scans from a separate worker process).
    worker_task = None
    if worker.should_start():
        worker_task = asyncio.create_task(worker.run_worker_loop())
        logger.info("monitoring worker enabled")
    yield
    worker.stop_worker()
    if worker_task:
        try:
            await asyncio.wait_for(worker_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            worker_task.cancel()
    logger.info("shutdown complete")


# Interactive docs are hidden in production (unless EXPOSE_DOCS=true) so the schema
# isn't publicly browsable.
_docs_on = settings.expose_docs or not settings.is_production
app = FastAPI(
    title=settings.app_name, version="1.0.0", lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

# Order: RequestLogging is added last so it is the OUTERMOST layer (it times the
# whole request and stamps X-Request-ID on the final response).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list(),   # explicit origins only, never "*"
    allow_credentials=True,               # required for the refresh cookie
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Admin-Token"],
    # Let the browser read the download filename + request id on cross-origin responses.
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(routes_misc.router)
app.include_router(routes_scan.router)
app.include_router(routes_contact.router)   # POST /api/contact (public)
app.include_router(routes_dashboard.router)
app.include_router(routes_auth.router)   # /auth/*, /me, /me/sessions
app.include_router(routes_org.router)    # /org/*
app.include_router(routes_reports.router)  # /reports/*
app.include_router(routes_monitors.router)  # /monitors/*, /alerts, /history/*
app.include_router(routes_admin.router)     # /admin/* (platform admins only)
app.include_router(routes_billing.router)   # /billing/* (plans, checkout, webhooks, subscriptions)
app.include_router(routes_public.router)    # /public/* (unauthenticated shared-report read path)
app.include_router(routes_digest.router)    # /digest/* (unauthenticated unsubscribe)

# NOTE: schema creation is intentionally NOT done at startup. Alembic migrations
# own the schema — run `alembic upgrade head` (or `npm run db:upgrade`) before
# starting the app in production. See INTEGRATIONS.md A2 and PHASE_1_*.md.


def _error_cors_headers(request: Request) -> dict[str, str]:
    """CORS headers for an error response. This handler runs at Starlette's
    ServerErrorMiddleware, which sits OUTSIDE CORSMiddleware, so the 500 it produces
    never passes back through CORS and would otherwise carry no
    Access-Control-Allow-Origin — causing a cross-origin browser to block the response
    and report a false "backend unreachable". We mirror what CORSMiddleware would emit
    for our credentialed, explicit-origin config (echo the allowed origin; never '*')."""
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_list():
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace to the client. Log it server-side with the
    request id and return a generic error (with CORS headers so a cross-origin browser
    actually receives the 500 instead of seeing a network failure)."""
    rid = getattr(request.state, "request_id", None)
    logger.exception("unhandled_exception request_id=%s path=%s", rid, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "request_id": rid},
        headers=_error_cors_headers(request),
    )


@app.get("/")
def root():
    return {"service": settings.app_name, "docs": "/docs"}
