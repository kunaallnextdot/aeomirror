"""Domain errors for answer tracking (mapped to HTTP status in the routes)."""
from __future__ import annotations

from datetime import datetime


class RunRefused(Exception):
    """A run was refused by a cost guard (dedup interval or monthly limit). Carries a
    user-facing `detail` and, for the interval case, the `next_eligible` timestamp."""

    def __init__(self, detail: str, *, reason: str, next_eligible: datetime | None = None):
        super().__init__(detail)
        self.detail = detail
        self.reason = reason                    # "interval" | "monthly_limit"
        self.next_eligible = next_eligible
