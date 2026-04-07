"""
LinkedIn Scraper
Strategy: JobSpy library (handles TLS fingerprinting + anti-bot internally).
Difficulty: 5/5 — Very Hard
Proxy: Residential required.
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
    proxy_configuration: Any,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    """
    Scrape LinkedIn Jobs via JobSpy.
    JobSpy runs synchronously, so we run it in a thread executor.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _jobspy_linkedin_sync,
        keyword, location, max_results, proxy_configuration,
    )


def _jobspy_linkedin_sync(
    keyword: str,
    location: str,
    max_results: int,
    proxy_configuration: Any,
) -> list[dict]:
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError:
        log.error("jobspy not installed. Run: pip install jobspy")
        return []

    proxies = None
    if proxy_configuration:
        try:
            proxy_url = proxy_configuration.new_url()
            proxies = {"http": proxy_url, "https": proxy_url}
        except Exception as e:
            log.warning("Could not get proxy URL: %s", e)

    try:
        results_limit = max_results if max_results > 0 else 100
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=keyword,
            location=location,
            results_wanted=results_limit,
            proxies=list(proxies.values()) if proxies else None,
            linkedin_fetch_description=True,
        )

        jobs = []
        for _, row in df.iterrows():
            jobs.append({
                "title":       _safe(row, "title"),
                "company":     _safe(row, "company"),
                "location":    _safe(row, "location"),
                "jobType":     _safe(row, "job_type"),
                "salary":      _safe(row, "min_amount") or _safe(row, "max_amount"),
                "salaryMin":   _safe(row, "min_amount"),
                "salaryMax":   _safe(row, "max_amount"),
                "salaryCurrency": _safe(row, "currency"),
                "description": _safe(row, "description"),
                "requirements": None,
                "postedDate":  str(_safe(row, "date_posted") or ""),
                "url":         _safe(row, "job_url"),
                "isRemote":    _safe(row, "is_remote"),
            })
        log.info("LinkedIn JobSpy: found %d jobs", len(jobs))
        return jobs

    except Exception as e:
        log.error("LinkedIn JobSpy scrape failed: %s", e, exc_info=True)
        return []


def _safe(row: Any, col: str) -> Any:
    try:
        val = row[col]
        import pandas as pd  # type: ignore
        if pd.isna(val):
            return None
        return val
    except Exception:
        return None
