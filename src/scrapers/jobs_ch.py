"""
Jobs.ch Scraper
Strategy:
  1. Direct HTTP to internal REST API (discovered from XHR)
  2. Playwright + XHR interception fallback
  3. DOM / JSON-LD fallback
Difficulty: 3/5 — Medium

Internal API base: https://www.jobs.ch/api/v1/public/search/
"""

import asyncio
import json
import logging
import urllib.parse
from typing import Any

import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)
_ua = UserAgent()

BASE_URL  = "https://www.jobs.ch"
# Known internal REST API endpoints (try in order)
API_URLS = [
    "https://www.jobs.ch/api/v1/public/search/",
    "https://www.jobs.ch/api/v2/public/search/",
    "https://www.jobs.ch/api/public/jobs/",
]


async def scrape_jobs_ch(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_url: str | None,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    from ..utils.proxy import get_proxy_for_requests

    proxies = get_proxy_for_requests(proxy_url)
    results_limit = max_results if max_results > 0 else 200

    # ── Strategy 1: Direct REST API call ──────────────────────────────────────
    loop = asyncio.get_event_loop()
    jobs = await loop.run_in_executor(
        None, _try_rest_api, keyword, location, results_limit, proxies
    )
    if jobs:
        log.info("Jobs.ch REST API: found %d jobs", len(jobs))
        return jobs

    # ── Strategy 2: Playwright + XHR interception ─────────────────────────────
    log.info("Jobs.ch: REST API returned 0 — trying Playwright XHR interception")
    jobs = await _playwright_scrape(keyword, location, results_limit, proxy_url, delay_ms)
    if jobs:
        log.info("Jobs.ch Playwright: found %d jobs", len(jobs))
        return jobs

    log.warning("Jobs.ch: all strategies returned 0 jobs")
    return []


# ── REST API path ─────────────────────────────────────────────────────────────

def _try_rest_api(keyword: str, location: str, limit: int, proxies: dict | None) -> list[dict]:
    """Try Jobs.ch internal REST API directly."""
    headers = {
        "User-Agent":  _ua.random,
        "Accept":      "application/json, text/plain, */*",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        "Referer":     "https://www.jobs.ch/de/stellenangebote/",
        "Origin":      "https://www.jobs.ch",
    }

    params = {
        "query":    keyword,
        "location": location,
        "page":     1,
        "per_page": min(limit, 100),
        "term":     keyword,
        "language": "de",
    }

    for api_url in API_URLS:
        try:
            resp = requests.get(
                api_url,
                params=params,
                headers=headers,
                proxies=proxies,
                timeout=20,
            )
            log.info("Jobs.ch REST API %s → HTTP %d", api_url, resp.status_code)

            if resp.status_code != 200:
                continue

            try:
                data = resp.json()
            except Exception:
                log.warning("Jobs.ch REST API: non-JSON response from %s", api_url)
                continue

            jobs = _parse_jobs_ch_api(data)
            if jobs:
                log.info("Jobs.ch REST API success: %d jobs from %s", len(jobs), api_url)
                return jobs

        except Exception as e:
            log.warning("Jobs.ch REST API error for %s: %s", api_url, e)
            continue

    return []


# ── Playwright XHR interception path ──────────────────────────────────────────

async def _playwright_scrape(keyword, location, limit, proxy_url, delay_ms) -> list[dict]:
    from playwright.async_api import async_playwright, Response
    from ..utils.stealth import apply_stealth_scripts
    from ..utils.proxy import get_proxy_for_playwright

    jobs: list[dict] = []
    api_responses: list = []
    proxy = get_proxy_for_playwright(proxy_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy=proxy,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=_ua.random,
            locale="de-CH",
        )
        await apply_stealth_scripts(context)

        async def handle_response(response: Response):
            try:
                ct = response.headers.get("content-type", "")
                if ("jobs.ch" in response.url) and ("json" in ct) and response.status == 200:
                    body = await response.json()
                    log.info("Jobs.ch XHR captured: %s (type=%s)", response.url[:80], type(body).__name__)
                    api_responses.append(body)
            except Exception:
                pass

        page = await context.new_page()
        page.on("response", handle_response)

        q = urllib.parse.quote_plus(keyword)
        l = urllib.parse.quote_plus(location)
        search_url = f"{BASE_URL}/de/stellenangebote/?term={q}&location={l}"

        try:
            await page.goto(search_url, wait_until="networkidle", timeout=40000)
            await asyncio.sleep(delay_ms / 1000)

            for api_resp in api_responses:
                extracted = _parse_jobs_ch_api(api_resp)
                jobs.extend(extracted)
                if len(jobs) >= limit:
                    break

            if not jobs:
                log.info("Jobs.ch XHR: no API responses captured — trying DOM parsing")
                jobs = await _parse_jobs_ch_dom(page, limit)

        except Exception as e:
            log.error("Jobs.ch Playwright error: %s", e, exc_info=True)
        finally:
            await browser.close()

    return jobs[:limit]


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_jobs_ch_api(data) -> list[dict]:
    """Handle both dict (wrapped) and list (direct array) API responses."""
    jobs = []

    if isinstance(data, dict):
        documents = (
            data.get("documents") or data.get("jobs") or data.get("results")
            or data.get("items") or data.get("data") or []
        )
        # If still empty, check nested under "hits" (Elasticsearch-style)
        if not documents:
            hits = data.get("hits") or {}
            if isinstance(hits, dict):
                documents = hits.get("hits") or hits.get("items") or []
            elif isinstance(hits, list):
                documents = hits
    elif isinstance(data, list):
        documents = data
    else:
        return []

    for item in documents:
        if not isinstance(item, dict):
            continue

        # Handle Elasticsearch _source wrapping
        if "_source" in item:
            item = item["_source"]

        position      = item.get("position") or {}
        company_data  = item.get("company") or {}
        salary_data   = item.get("salary") or {}
        location_data = item.get("place") or item.get("location") or {}

        title     = (position.get("title") if isinstance(position, dict) else None) or item.get("title") or item.get("name")
        comp_name = (company_data.get("name") if isinstance(company_data, dict) else company_data) or item.get("companyName")
        loc_name  = (location_data.get("city") or location_data.get("name") if isinstance(location_data, dict) else location_data) or item.get("location")
        job_url   = item.get("url") or item.get("jobUrl") or item.get("externalUrl")
        if job_url and isinstance(job_url, str) and not job_url.startswith("http"):
            job_url = f"{BASE_URL}{job_url}"

        sal_text = sal_min = sal_max = sal_currency = None
        if isinstance(salary_data, dict):
            sal_text     = salary_data.get("text") or salary_data.get("description")
            sal_min      = salary_data.get("min")
            sal_max      = salary_data.get("max")
            sal_currency = salary_data.get("currency", "CHF")

        jobs.append({
            "title":          title,
            "company":        comp_name,
            "location":       loc_name,
            "jobType":        item.get("workload") or item.get("employmentType"),
            "salary":         sal_text,
            "salaryMin":      sal_min,
            "salaryMax":      sal_max,
            "salaryCurrency": sal_currency or "CHF",
            "description":    item.get("description") or (position.get("description") if isinstance(position, dict) else None),
            "requirements":   item.get("requirements"),
            "postedDate":     item.get("publicationDate") or item.get("createdAt"),
            "url":            job_url,
            "isRemote":       item.get("homeOffice") or item.get("remote"),
        })
    return jobs


async def _parse_jobs_ch_dom(page, limit: int) -> list[dict]:
    """DOM fallback — JSON-LD then CSS selectors."""
    jobs = []
    html = await page.content()

    try:
        import extruct  # type: ignore
        data = extruct.extract(html, syntaxes=["json-ld"])
        for item in data.get("json-ld", []):
            if item.get("@type") in ("JobPosting", "jobPosting"):
                jobs.append(_jsonld_to_job(item))
    except Exception as e:
        log.warning("Jobs.ch JSON-LD extraction failed: %s", e)

    if not jobs:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        selectors = [
            "article[data-cy='job-ad-list-item']",
            "div[class*='JobAdListItem']",
            "div[class*='job-card']",
            "div[class*='result-item']",
            "li[class*='job']",
        ]
        cards = []
        for sel in selectors:
            cards = soup.select(sel)
            if cards:
                log.info("Jobs.ch DOM: matched '%s' → %d cards", sel, len(cards))
                break

        for card in cards[:limit]:
            try:
                title_el   = card.select_one("h2, h3, [class*='title']")
                company_el = card.select_one("[class*='company'], [class*='employer']")
                loc_el     = card.select_one("[class*='location'], [class*='place']")
                link_el    = card.select_one("a[href]")
                title    = title_el.get_text(strip=True)   if title_el   else None
                company  = company_el.get_text(strip=True) if company_el else None
                location = loc_el.get_text(strip=True)     if loc_el     else None
                href     = link_el["href"]                 if link_el    else None
                url      = f"{BASE_URL}{href}" if href and href.startswith("/") else href
                jobs.append({
                    "title": title, "company": company, "location": location,
                    "jobType": None, "salary": None, "salaryMin": None, "salaryMax": None,
                    "salaryCurrency": "CHF", "description": None, "requirements": None,
                    "postedDate": None, "url": url, "isRemote": None,
                })
            except Exception:
                continue
    return jobs


def _jsonld_to_job(item: dict) -> dict:
    org     = item.get("hiringOrganization") or {}
    loc     = item.get("jobLocation") or {}
    addr    = loc.get("address") or {} if isinstance(loc, dict) else {}
    sal     = item.get("baseSalary") or {}
    sal_val = sal.get("value") or {} if isinstance(sal, dict) else {}
    return {
        "title":          item.get("title"),
        "company":        org.get("name") if isinstance(org, dict) else org,
        "location":       addr.get("addressLocality") if isinstance(addr, dict) else None,
        "jobType":        item.get("employmentType"),
        "salary":         None,
        "salaryMin":      sal_val.get("minValue") if isinstance(sal_val, dict) else None,
        "salaryMax":      sal_val.get("maxValue") if isinstance(sal_val, dict) else None,
        "salaryCurrency": sal.get("currency", "CHF") if isinstance(sal, dict) else "CHF",
        "description":    item.get("description"),
        "requirements":   item.get("qualifications") or item.get("experienceRequirements"),
        "postedDate":     item.get("datePosted"),
        "url":            item.get("url"),
        "isRemote":       item.get("jobLocationType") == "TELECOMMUTE",
    }
