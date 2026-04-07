"""
LinkedIn Scraper
Strategy: JobSpy library (handles TLS fingerprinting + anti-bot internally).
Difficulty: 5/5 — Very Hard
"""

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


async def scrape_linkedin(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_url: str | None,      # plain string e.g. "http://user:pass@host:port"
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _jobspy_linkedin_sync, keyword, location, max_results, proxy_url,
    )


def _jobspy_linkedin_sync(
    keyword: str,
    location: str,
    max_results: int,
    proxy_url: str | None,
) -> list[dict]:
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError:
        log.error("python-jobspy not installed.")
        return []

    # JobSpy expects a list of plain proxy URL strings
    proxies = [proxy_url] if proxy_url else None

    try:
        results_limit = max_results if max_results > 0 else 100
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=keyword,
            location=location,
            results_wanted=results_limit,
            proxies=proxies,
            linkedin_fetch_description=True,
        )

        jobs = []
        for _, row in df.iterrows():
            jobs.append({
                "title":          _safe(row, "title"),
                "company":        _safe(row, "company"),
                "location":       _safe(row, "location"),
                "jobType":        _safe(row, "job_type"),
                "salary":         None,
                "salaryMin":      _safe(row, "min_amount"),
                "salaryMax":      _safe(row, "max_amount"),
                "salaryCurrency": _safe(row, "currency"),
                "description":    _safe(row, "description"),
                "requirements":   None,
                "postedDate":     str(_safe(row, "date_posted") or ""),
                "url":            _safe(row, "job_url"),
                "isRemote":       _safe(row, "is_remote"),
            })
        log.info("LinkedIn JobSpy: found %d jobs", len(jobs))
        return jobs

    except Exception as e:
        log.error("LinkedIn JobSpy scrape failed: %s", e, exc_info=True)
        return []


def _safe(row: Any, col: str) -> Any:
    try:
        import pandas as pd  # type: ignore
        val = row[col]
        if pd.isna(val):
            return None
        return val
    except Exception:
        return None
