"""
Topjobs.ch Scraper
Strategy: requests + BeautifulSoup (server-rendered HTML).

CONFIRMED URL:  https://topjobs.ch/en/jobs?q=<keyword>
CONFIRMED HTML: <li><h2><a href="/en/job/ID">Title</a></h2> Company, Location, Date</li>

Note: topjobs does NOT support free-text location — uses region IDs.
We search by keyword only and filter location in post-processing.
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
BASE_URL = "https://topjobs.ch"

LANG_PATHS = {"en": "/en/jobs", "de": "/de/jobs", "fr": "/fr/emplois"}


async def scrape_topjobs(
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

    for lang, path in LANG_PATHS.items():
        if jobs:
            break
        page = 1
        while len(jobs) < limit:
            # CONFIRMED: topjobs uses ?q= not ?query=
            search_url = f"{BASE_URL}{path}?q={urllib.parse.quote_plus(keyword)}"
            if page > 1:
                search_url += f"&page={page}"

            log.info("Topjobs GET %s", search_url)
            html = _get(search_url, proxies)
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")
            page_jobs = _parse(soup, lang)
            log.info("Topjobs [%s] page %d → %d jobs", lang, page, len(page_jobs))
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

            # Topjobs pagination: "of N pages" shown; check for next page link
            if not soup.select_one("a[rel='next'], a[href*='page=']"):
                break
            page += 1
            time.sleep(delay_ms / 1000)

    log.info("Topjobs: total %d jobs", len(jobs))
    return jobs


def _parse(soup: BeautifulSoup, lang: str) -> list[dict]:
    jobs = []

    # ── JSON-LD first ────────────────────────────────────────────────────
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

    # ── Confirmed selector: <h2><a href="/{lang}/job/ID"> ───────────────
    # From live HTML: ## [Title](https://topjobs.ch/en/job/1448363321)
    job_links = soup.select(f'a[href*="/{lang}/job/"]')
    seen = set()

    for link in job_links:
        href = link.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)

        job_url = href if href.startswith("http") else BASE_URL + href
        title = link.get_text(strip=True)
        if not title:
            continue

        # Parent <li> contains company + address + date
        parent = link.find_parent("li") or link.parent
        if parent:
            # Remove the title link text and get remaining text chunks
            parts = []
            for child in parent.children:
                text = child.get_text(" ", strip=True) if hasattr(child, "get_text") else str(child).strip()
                if text and text != title:
                    parts.extend([p.strip() for p in text.split("\n") if p.strip()])

            # From live HTML: company is first non-empty, address is second
            company  = parts[0] if len(parts) > 0 else None
            location_text = parts[1] if len(parts) > 1 else None
            # Date is usually last part (e.g. "2W", "6D", "1M")
            date = parts[-1] if parts and len(parts[-1]) <= 5 else None
        else:
            company = location_text = date = None

        jobs.append({
            "title": title, "company": company, "location": location_text,
            "jobType": None, "salary": None, "salaryMin": None, "salaryMax": None,
            "salaryCurrency": "CHF", "description": None, "requirements": None,
            "postedDate": date, "url": job_url, "isRemote": None,
        })

    return jobs


def _detail(job_url: str, proxies) -> dict | None:
    try:
        r = requests.get(job_url, headers=_hdrs(), proxies=proxies, timeout=20)
        if r.status_code != 200:
            return None
        s = BeautifulSoup(r.text, "lxml")
        for script in s.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                for item in (data if isinstance(data, list) else [data]):
                    if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")):
                        d = _from_jsonld(item)
                        return {k: v for k, v in d.items() if v is not None}
            except Exception:
                continue
        desc = s.select_one("[class*='description'], [itemprop='description'], main, article")
        req  = s.select_one("[class*='requirement'], [class*='qualification']")
        return {
            "description":  desc.get_text("\n", strip=True) if desc else None,
            "requirements": req.get_text("\n", strip=True)  if req  else None,
        }
    except Exception as e:
        log.debug("Topjobs detail error: %s", e)
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
def _get(url, proxies) -> str | None:
    try:
        r = requests.get(url, headers=_hdrs(), proxies=proxies, timeout=25, allow_redirects=True)
        log.info("Topjobs HTTP %d for %s", r.status_code, url[:80])
        return r.text if r.status_code == 200 else None
    except Exception as e:
        log.warning("Topjobs fetch error: %s", e)
        raise


def _hdrs() -> dict:
    return {
        "User-Agent": _ua.random,
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
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
