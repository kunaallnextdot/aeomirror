"""Thin, safe wrapper around the Anthropic Messages API (server-side only).

Every AI feature in the app goes through `complete()`. It is deliberately minimal:
- The API key comes ONLY from settings (env). It is never logged and never returned.
- A short request timeout plus ONE retry on transient errors (429 / 5xx / connection).
- On ANY failure it returns None — callers MUST have a non-AI fallback. Failures are
  logged by exception TYPE only (never the message, key, prompt, or response body).
- Every system prompt is suffixed with a strict "respond with only the requested
  output" instruction so downstream JSON/text parsing stays robust.

The `anthropic` SDK is imported lazily inside the call so the package is only required
when AI is actually configured (tests mock this module and never import the SDK).
"""
from __future__ import annotations

import json
import logging
import time

from app.config import settings

log = logging.getLogger("app.ai")

# Short pause before the single retry on a transient error (rate limit / 5xx /
# connection). Kept small so a request is never blocked for long.
_RETRY_BACKOFF_SECONDS = 0.75

# Appended to every system prompt. Keeps the model from wrapping output in prose or
# code fences, which would break the callers' defensive JSON/text parsing.
_STRICT = (
    "\n\nIMPORTANT: Respond with ONLY the exact output requested — no preamble, no "
    "explanation, and no markdown code fences. When JSON is requested, return a single "
    "valid JSON object and nothing else."
)


def _is_transient(exc: Exception) -> bool:
    """Retry only on conditions that a second attempt can plausibly fix."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code == 429 or code >= 500
    return type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}


def complete(system: str, user_content: str, *, max_tokens: int | None = None,
             timeout: int | None = None) -> str | None:
    """Send one prompt to Claude and return the plain-text response, or None on any
    failure (SDK missing, no key, timeout, rate limit, bad response). Never raises.

    Retries ONCE after a short backoff on a transient error (429 / 5xx / connection /
    timeout); any other error, or a second failure, returns None so callers fall back.

    `timeout` overrides the per-request client timeout for this call (the interactive
    content-insight path passes a shorter one); None keeps settings.ai_timeout_seconds."""
    if not settings.ai_enabled:
        return None

    system_prompt = (system or "").strip() + _STRICT
    tokens = max_tokens or settings.ai_max_tokens
    call_timeout = timeout or settings.ai_timeout_seconds
    last_exc: Exception | None = None

    for attempt in range(2):  # initial try + one retry
        try:
            import anthropic  # lazy: only needed when AI is configured

            client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key,
                timeout=call_timeout,
            )
            msg = client.messages.create(
                model=settings.ai_model,
                max_tokens=tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            text = "".join(
                getattr(b, "text", "") for b in msg.content
                if getattr(b, "type", None) == "text"
            ).strip()
            return text or None
        except Exception as exc:  # noqa: BLE001 — must never propagate to callers
            last_exc = exc
            if attempt == 0 and _is_transient(exc):
                time.sleep(_RETRY_BACKOFF_SECONDS)   # brief backoff, then one retry
                continue
            break

    # Log the exception TYPE only — never the message/key/prompt/response.
    log.warning("AI completion failed (%s)", type(last_exc).__name__ if last_exc else "unknown")
    return None


def parse_json(raw: str | None) -> dict | None:
    """Best-effort extraction of a single JSON object from a model response.

    Strips ``` fences and any leading/trailing prose, then json.loads. Returns the
    parsed dict, or None if nothing valid parses (never raises). Non-object JSON
    (a list, a bare string) is treated as a failure and returns None."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()

    def _loads(s: str) -> dict | None:
        try:
            obj = json.loads(s)
        except (json.JSONDecodeError, TypeError):
            return None
        return obj if isinstance(obj, dict) else None

    obj = _loads(text)
    if obj is not None:
        return obj
    # Fall back to the outermost {...} span if there is stray prose around it.
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        return _loads(text[i:j + 1])
    return None
