"""P0-5 regression: analysing a template loads ONE page (the representative), not every
page of the template — memory no longer grows with the template's size. The output is
unchanged: the earliest-crawled page is the representative, the affected count is the
template's size, and every page of the template is marked analysed."""
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import event

from app.db.models import CRAWL_ANALYZING, CrawlJob, CrawlUrl
from app.db.session import SessionLocal
from app.services.url_intelligence import ai_analysis
from app.services.url_intelligence import orchestrator as o

N = 2500


def _job_with_template(db, n=N):
    j = CrawlJob(organization_id="org-mem", monitor_id="mon-mem", seed_url="https://mem.example.com/",
                 normalized_seed_url="mem.example.com", max_urls=100000, phase=CRAWL_ANALYZING)
    db.add(j)
    db.commit()
    t0 = datetime(2026, 11, 1)
    ev = {"title": "Product", "word_count": 400, "notes": "x" * 2000}   # a realistic-sized evidence blob
    db.bulk_insert_mappings(CrawlUrl, [
        {"id": f"mem-{j.id[:8]}-{i:05d}", "crawl_job_id": j.id, "organization_id": "org-mem",
         "url": f"https://mem.example.com/p/{i}", "normalized_key": f"mem.example.com/p/{i}",
         "discovery_source": "sitemap", "status": "crawled", "template_id": "tpl-big",
         "page_type": "product", "evidence": ev, "analyzed": False,
         "crawled_at": t0 + timedelta(seconds=(i * 7919) % n)}   # crawl order differs from insert order
        for i in range(n)])
    db.commit()
    return j


def test_a_large_template_loads_only_its_representative(monkeypatch):
    db = SessionLocal()
    loaded = []
    listener = lambda target, context: loaded.append(target.id)   # noqa: E731
    try:
        job = _job_with_template(db)
        expected = (db.query(CrawlUrl.id).filter(CrawlUrl.crawl_job_id == job.id)
                    .order_by(CrawlUrl.crawled_at.asc(), CrawlUrl.id.asc()).first()[0])
        seen = {}
        monkeypatch.setattr(ai_analysis, "analyze_template",
                            lambda db, job, rep, affected_url_count: seen.update(rep=rep.id, n=affected_url_count))
        event.listen(CrawlUrl, "load", listener)
        done = asyncio.run(o._run_analyze_chunk(db, job))
        event.remove(CrawlUrl, "load", listener)
        assert len(loaded) == 1                                     # was N (every member, with evidence)
        assert seen == {"rep": expected, "n": N}
        assert done is True
        db.expire_all()
        assert db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == job.id, CrawlUrl.analyzed.is_(False)).count() == 0
        assert db.get(CrawlJob, job.id).analyzed_count == N
    finally:
        if event.contains(CrawlUrl, "load", listener):
            event.remove(CrawlUrl, "load", listener)
        db.close()


def test_other_jobs_and_templates_are_untouched(monkeypatch):
    db = SessionLocal()
    try:
        job = _job_with_template(db, n=5)
        other = _job_with_template(db, n=3)
        monkeypatch.setattr(ai_analysis, "analyze_template", lambda *a, **k: None)
        asyncio.run(o._run_analyze_chunk(db, job))
        db.expire_all()
        assert db.query(CrawlUrl).filter(CrawlUrl.crawl_job_id == other.id, CrawlUrl.analyzed.is_(True)).count() == 0
    finally:
        db.close()
