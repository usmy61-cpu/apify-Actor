"""
Indeed Switzerland Scraper
Strategy: JobSpy (primary) → Playwright+stealth fallback.
Difficulty: 3/5 — Medium
"""

import asyncio
import logging
import urllib.parse
from typing import Any

log = logging.getLogger(__name__)


async def scrape_indeed(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_url: str | None,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    # Primary: JobSpy
    loop = asyncio.get_event_loop()
    jobs = await loop.run_in_executor(
        None, _jobspy_indeed_sync, keyword, location, max_results, proxy_url
    )

    if jobs:
        return jobs

    log.warning("Indeed JobSpy returned 0 results — trying Playwright fallback")
    return await _playwright_indeed(keyword, location, max_results, proxy_url, delay_ms)


# ── JobSpy path ──────────────────────────────────────────────────────────────

def _jobspy_indeed_sync(keyword, location, max_results, proxy_url) -> list[dict]:
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError:
        log.error("python-jobspy not installed.")
        return []

    proxies = [proxy_url] if proxy_url else None

    try:
        results_limit = max_results if max_results > 0 else 100
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=keyword,
            location=location,
            country_indeed="Switzerland",
            results_wanted=results_limit,
            proxies=proxies,
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
        log.info("Indeed JobSpy: found %d jobs", len(jobs))
        return jobs
    except Exception as e:
        log.error("Indeed JobSpy failed: %s", e, exc_info=True)
        return []


# ── Playwright fallback ──────────────────────────────────────────────────────

async def _playwright_indeed(keyword, location, max_results, proxy_url, delay_ms) -> list[dict]:
    from playwright.async_api import async_playwright
    from ..utils.stealth import apply_stealth_scripts
    from ..utils.proxy import get_proxy_for_playwright

    jobs = []
    proxy = get_proxy_for_playwright(proxy_url)
    results_limit = max_results if max_results > 0 else 100

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy=proxy,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )
        await apply_stealth_scripts(context)
        page = await context.new_page()

        q = urllib.parse.quote_plus(keyword)
        l = urllib.parse.quote_plus(location)
        search_url = f"https://ch.indeed.com/jobs?q={q}&l={l}"

        try:
            await page.goto(search_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(delay_ms / 1000)

            page_num = 0
            while len(jobs) < results_limit:
                cards = await page.query_selector_all("div.job_seen_beacon")
                if not cards:
                    cards = await page.query_selector_all("[data-testid='slider_item']")

                for card in cards:
                    if len(jobs) >= results_limit:
                        break
                    job = await _parse_indeed_card(card)
                    if job:
                        jobs.append(job)

                next_btn = await page.query_selector("a[data-testid='pagination-page-next']")
                if not next_btn or page_num >= 9:
                    break
                await next_btn.click()
                await asyncio.sleep(delay_ms / 1000)
                page_num += 1

        except Exception as e:
            log.error("Indeed Playwright error: %s", e, exc_info=True)
        finally:
            await browser.close()

    log.info("Indeed Playwright fallback: found %d jobs", len(jobs))
    return jobs


async def _parse_indeed_card(card) -> dict | None:
    try:
        title_el   = await card.query_selector("h2.jobTitle span")
        company_el = await card.query_selector("[data-testid='company-name']")
        loc_el     = await card.query_selector("[data-testid='text-location']")
        salary_el  = await card.query_selector("[data-testid='attribute_snippet_testid']")
        link_el    = await card.query_selector("a.jcs-JobTitle")

        title   = await title_el.inner_text()   if title_el   else None
        company = await company_el.inner_text() if company_el else None
        loc     = await loc_el.inner_text()     if loc_el     else None
        salary  = await salary_el.inner_text()  if salary_el  else None
        href    = await link_el.get_attribute("href") if link_el else None
        url     = f"https://ch.indeed.com{href}" if href and href.startswith("/") else href

        return {
            "title": title, "company": company, "location": loc,
            "jobType": None, "salary": salary, "salaryMin": None, "salaryMax": None,
            "salaryCurrency": "CHF", "description": None, "requirements": None,
            "postedDate": None, "url": url, "isRemote": None,
        }
    except Exception:
        return None


def _safe(row, col):
    try:
        import pandas as pd  # type: ignore
        val = row[col]
        if pd.isna(val):
            return None
        return val
    except Exception:
        return None
