"""Provider interface for answer tracking.

Every provider exposes one coroutine: `query(prompt, *, timeout) -> ProviderResult`.
Providers translate their own SDK/HTTP errors into `ProviderError` so the runner has
one uniform retry decision (`transient`): retry once on timeout / connection / 429 /
5xx, never on a 4xx auth/validation error.

CITATIONS CONTRACT (do not violate):
  - citations = None  -> the provider CANNOT report citations at all.
  - citations = []    -> the provider searched and cited nothing.
These are different signals; conflating them shows a citation failure where there is
none. A provider declares its capability via the class attribute `supports_citations`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderResult:
    text: str
    citations: list[dict] | None      # [{"url", "title"}] | [] | None (see contract above)
    model: str                        # exact model string used (never a floating alias)
    tokens: dict | None               # {"input", "output", ...} when the provider reports it
    latency_ms: int


class ProviderError(Exception):
    """A provider call failed. `status_code` is the upstream HTTP status when known;
    `transient` marks failures a single retry can plausibly fix (timeout / connection /
    429 / 5xx). Auth/validation (401/403/400/422) is NOT transient."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


def is_transient(err: ProviderError) -> bool:
    """Whether the runner should retry after this error. Explicit `transient` wins;
    otherwise infer from the status code (429 or any 5xx)."""
    if err.transient:
        return True
    code = err.status_code
    return isinstance(code, int) and (code == 429 or code >= 500)


async def post_json(url: str, *, headers: dict, json: dict, timeout: int) -> dict:
    """POST JSON and return the parsed body, translating transport/HTTP errors into
    ProviderError. httpx is imported lazily so the package need not be present unless a
    real provider call is actually made (tests stub `query` and never reach here)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=json)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise ProviderError(f"transport error: {type(exc).__name__}", transient=True) from exc
    if resp.status_code >= 400:
        # 429/5xx are transient (inferred in is_transient); 4xx auth/validation is not.
        raise ProviderError(f"http {resp.status_code}", status_code=resp.status_code)
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — a malformed body is a hard failure, no retry
        raise ProviderError("invalid json body") from exc


class BaseProvider:
    """Subclasses set `name` + `supports_citations` and implement `query`."""
    name: str = ""
    supports_citations: bool = False

    def __init__(self, *, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def query(self, prompt: str, *, timeout: int) -> ProviderResult:
        raise NotImplementedError
