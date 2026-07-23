"""AEOMirror API entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import routes_misc, routes_scan
from app.config import settings
from app.core.observability import (
    RequestLoggingMiddleware, SecurityHeadersMiddleware, configure_logging,
)

configure_logging()
logger = logging.getLogger("aeomirror")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup. Uvicorn handles SIGTERM and drains in-flight requests
    # (see --timeout-graceful-shutdown in the production entrypoint).
    logger.info("startup env=%s", settings.environment)
    yield
    logger.info("shutdown complete")


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

# Order: RequestLogging is added last so it is the OUTERMOST layer (it times the
# whole request and stamps X-Request-ID on the final response).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list(),   # explicit origins only, never "*"
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID", "X-Admin-Token"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(routes_misc.router)
app.include_router(routes_scan.router)

# NOTE: schema creation is intentionally NOT done at startup. Alembic migrations
# own the schema — run `alembic upgrade head` (or `npm run db:upgrade`) before
# starting the app in production. See INTEGRATIONS.md A2 and PHASE_1_*.md.


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace to the client. Log it server-side with the
    request id and return a generic error."""
    rid = getattr(request.state, "request_id", None)
    logger.exception("unhandled_exception request_id=%s path=%s", rid, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "request_id": rid},
    )


@app.get("/")
def root():
    return {"service": settings.app_name, "docs": "/docs"}
