"""
Jobscout24.ch Scraper
Strategy: requests + BeautifulSoup (server-rendered HTML).

CONFIRMED URL: https://www.jobscout24.ch/de/jobs/?query=<kw>&location=<loc>
CONFIRMED HTML: job cards are <li> items containing <a href="/de/job/UUID/">
"""

import asyncio
import json
import logging
import time
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)
_ua = UserAgent()
BASE_URL = "https://www.jobscout24.ch"


async def scrape_jobscout24(
    url: str, keyword: str, location: str, max_results: int,
    proxy_url: str | None, delay_ms: int, languages: list[str], **kwargs,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _scrape_sync, keyword, location, max_results, proxy_url, delay_ms
    )


def _scrape_sync(keyword, location, max_results, proxy_url, delay_ms) -> list[dict]:
    from ..utils.proxy import get_proxy_for_requests
    proxies = get_proxy_for_requests(proxy_url)
    limit = max_results if max_results > 0 else 200
    jobs: list[dict] = []

    for lang in ["de", "en", "fr"]:
        if jobs:
            break
        page = 1
        while len(jobs) < limit:
            search_url = f"{BASE_URL}/{lang}/jobs/?query={urllib.parse.quote_plus(keyword)}&location={urllib.parse.quote_plus(location)}"
            if page > 1:
                search_url += f"&page={page}"

            log.info("Jobscout24 GET %s", search_url)
            html = _get(search_url, proxies)
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")
            page_jobs = _parse(soup, lang)
            log.info("Jobscout24 [%s] page %d → %d jobs parsed", lang, page, len(page_jobs))
            if not page_jobs:
                break

            for job in page_jobs:
                if len(jobs) >= limit:
                    break
                if job.get("url"):
                    time.sleep(0.4)
                    detail = _detail(job["url"], proxies)
                    if detail:
                        job.update(detail)
                jobs.append(job)

            if not soup.select_one("a[rel='next'], .pagination-next, li.next a"):
                break
            page += 1
            time.sleep(delay_ms / 1000)

    log.info("Jobscout24: total %d jobs", len(jobs))
    return jobs


def _parse(soup: BeautifulSoup, lang: str) -> list[dict]:
    jobs = []

    # ── JSON-LD first (cleanest) ──────────────────────────────────────────
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            for item in (data if isinstance(data, list) else [data]):
                if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")):
                    jobs.append(_from_jsonld(item))
        except Exception:
            continue
    if jobs:
        return jobs

    # ── Confirmed selector: <a href="/{lang}/job/UUID/"> ─────────────────
    # Each job is a link whose href matches the job detail URL pattern
    job_links = soup.select(f'a[href*="/{lang}/job/"]')
    seen = set()
    for link in job_links:
        href = link.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)

        job_url = href if href.startswith("http") else BASE_URL + href
        title = link.get_text(strip=True) or link.get("title", "")
        if not title:
            continue  # skip nav links

        # Walk up to the parent list item to get company + location
        parent = link.find_parent("li") or link.find_parent("div") or link.parent
        full_text = parent.get_text(" | ", strip=True) if parent else ""
        # Remove the title itself from the remaining text
        remaining = full_text.replace(title, "").strip(" |")
        parts = [p.strip() for p in remaining.split("|") if p.strip()]

        # Heuristic: first non-badge part is company, second is location
        company = _first_non_badge(parts)
        location_text = _first_non_badge([p for p in parts if p != company])

        jobs.append({
            "title": title, "company": company, "location": location_text,
            "jobType": None, "salary": None, "salaryMin": None, "salaryMax": None,
            "salaryCurrency": "CHF", "description": None, "requirements": None,
            "postedDate": None, "url": job_url, "isRemote": None,
        })

    return jobs


def _first_non_badge(parts: list[str]) -> str | None:
    """Skip known badge-like strings (Top Listing, 100%, KMU, etc.)."""
    skip = {"top listing", "new", "sponsored", "kmu", "grossunternehmen", "personaldienstleister",
            "führungsposition", "fachverantwortung", "home office", "remote work"}
    for p in parts:
        low = p.lower()
        if any(s in low for s in skip):
            continue
        if "%" in p and len(p) <= 12:
            continue  # skip workload badges like "80% - 100%"
        return p
    return None


def _detail(job_url: str, proxies) -> dict | None:
    try:
        resp = requests.get(job_url, headers=_hdrs(), proxies=proxies, timeout=20)
        if resp.status_code != 200:
            return None
        s = BeautifulSoup(resp.text, "lxml")
        # JSON-LD on detail page is most reliable
        for script in s.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                for item in (data if isinstance(data, list) else [data]):
                    if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")):
                        d = _from_jsonld(item)
                        return {k: v for k, v in d.items() if v is not None}
            except Exception:
                continue
        desc = s.select_one("[class*='description'], [itemprop='description'], main")
        return {"description": desc.get_text("\n", strip=True) if desc else None}
    except Exception as e:
        log.debug("Jobscout24 detail error: %s", e)
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
def _get(url, proxies) -> str | None:
    try:
        r = requests.get(url, headers=_hdrs(), proxies=proxies, timeout=25, allow_redirects=True)
        log.info("Jobscout24 HTTP %d", r.status_code)
        return r.text if r.status_code == 200 else None
    except Exception as e:
        log.warning("Jobscout24 fetch error: %s", e)
        raise


def _hdrs() -> dict:
    return {
        "User-Agent": _ua.random,
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def _from_jsonld(item: dict) -> dict:
    org = item.get("hiringOrganization") or {}
    loc = item.get("jobLocation") or {}
    addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    sal = item.get("baseSalary") or {}
    sal_val = (sal.get("value") or {}) if isinstance(sal, dict) else {}
    return {
        "title": item.get("title"),
        "company": org.get("name") if isinstance(org, dict) else org,
        "location": addr.get("addressLocality") if isinstance(addr, dict) else None,
        "jobType": item.get("employmentType"),
        "salary": None,
        "salaryMin": sal_val.get("minValue") if isinstance(sal_val, dict) else None,
        "salaryMax": sal_val.get("maxValue") if isinstance(sal_val, dict) else None,
        "salaryCurrency": sal.get("currency", "CHF") if isinstance(sal, dict) else "CHF",
        "description": item.get("description"),
        "requirements": item.get("qualifications"),
        "postedDate": item.get("datePosted"),
        "url": item.get("url"),
        "isRemote": item.get("jobLocationType") == "TELECOMMUTE",
    }
