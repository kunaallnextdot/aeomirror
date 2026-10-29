"""The in-process worker must never run jobs on the API's event loop: jobs do
synchronous DB work, DNS and HTML parsing, which stalled every request (even
/health) for the length of a crawl chunk."""
import asyncio
import threading

from app.monitoring import worker


def test_each_tick_runs_on_its_own_loop_in_another_thread(monkeypatch):
    seen = {}

    async def fake_tick():
        seen["thread"] = threading.get_ident()
        seen["loop"] = asyncio.get_running_loop()
        worker.stop_worker()
        return {"processed": 0, "enqueued": 0}
    monkeypatch.setattr(worker, "tick", fake_tick)

    async def main():
        task = asyncio.create_task(worker.run_worker_loop())
        # The request loop keeps running while a tick is in progress.
        beats = 0
        while not task.done() and beats < 200:
            await asyncio.sleep(0.005)
            beats += 1
        await task
        return asyncio.get_running_loop()

    main_loop = asyncio.run(main())
    assert seen["thread"] != threading.get_ident()
    assert seen["loop"] is not main_loop


def test_a_blocking_tick_does_not_stall_the_request_loop(monkeypatch):
    import time

    async def slow_tick():
        time.sleep(0.5)            # stands in for parsing/DB work inside a job
        worker.stop_worker()
        return {"processed": 1, "enqueued": 0}
    monkeypatch.setattr(worker, "tick", slow_tick)

    async def main():
        task = asyncio.create_task(worker.run_worker_loop())
        worst = 0.0
        while not task.done():
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - t - 0.01)
        await task
        return worst

    assert asyncio.run(main()) < 0.2   # was ≥0.5 s with the tick on this loop


def test_a_failing_tick_is_logged_and_the_loop_keeps_going(monkeypatch, caplog):
    from app.config import settings
    monkeypatch.setattr(settings, "scheduler_interval_seconds", 0)
    calls = {"n": 0}

    async def flaky_tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        worker.stop_worker()
        return {"processed": 0, "enqueued": 0}
    monkeypatch.setattr(worker, "tick", flaky_tick)
    with caplog.at_level("ERROR", logger="aeomirror.worker"):
        asyncio.run(worker.run_worker_loop())
    assert calls["n"] == 2                          # survived the failure, ran again
    assert any("worker tick failed" in r.getMessage() for r in caplog.records)


def test_ticks_never_overlap_within_a_process(monkeypatch):
    """One runner per process: the next tick starts only after the previous one ends."""
    import time
    from app.config import settings
    monkeypatch.setattr(settings, "scheduler_interval_seconds", 0)
    active, peak, runs = [0], [0], [0]

    async def tick():
        active[0] += 1; peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        active[0] -= 1; runs[0] += 1
        if runs[0] == 3:
            worker.stop_worker()
        return {"processed": 0, "enqueued": 0}
    monkeypatch.setattr(worker, "tick", tick)
    asyncio.run(worker.run_worker_loop())
    assert runs[0] == 3 and peak[0] == 1
