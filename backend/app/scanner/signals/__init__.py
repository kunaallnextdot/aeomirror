"""Modular AI-visibility signal services (Phase 3).

Each signal module exposes `analyze(ctx: SignalContext) -> SignalResult` and is a
pure function over an already-fetched page (no network, no LLM, no paid API).
`aggregate.run_signals()` runs them all and builds the weighted report.
"""
