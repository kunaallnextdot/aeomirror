"""Request schemas for the URL Intelligence Engine API. Responses are plain dicts
(built in routes_url_intelligence) to keep nested evidence/finding payloads flexible,
same convention as app.schemas.monitor."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models import FINDING_STATUSES


class CreateCrawlJobRequest(BaseModel):
    # max_urls is optional — omitted means "use this org's plan ceiling". When given,
    # it is still clamped server-side to that ceiling (routes_url_intelligence), never
    # trusted as-is.
    max_urls: int | None = Field(default=None, ge=1, le=100_000)


class UpdateFindingStatusRequest(BaseModel):
    status: str = Field(min_length=1)

    def validate_status(self) -> bool:
        return self.status in FINDING_STATUSES
