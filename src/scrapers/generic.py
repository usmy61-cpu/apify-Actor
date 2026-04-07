"""
Generic Scraper — Auto-detect for any unknown/custom website.
Strategy pipeline:
  1. JSON-LD structured data (schema.org/JobPosting)
  2. Internal XHR/REST API interception (Playwright)
  3. Common job card CSS patterns (BeautifulSoup)
  4. robots.txt sitemap → find job listing URLs
  5. Full-page Playwright scrape as last resort
"""

import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

log = logging.getLogger(__name__)
_ua = UserAgent()

# Common CSS patterns for job listings across generic sites
JOB_CARD_SELECTORS = [
    "[itemtype*='JobPosting']",
    "article[class*='job']",
    "div[class*='job-card']",
    "div[class*='JobCard']",
    "li[class*='job']",
    "div[class*='vacancy']",
    "div[class*='position']",
    ".job-listing",
    ".job-result",
    ".job-item",
    ".career-item",
]

TITLE_SELECTORS   = ["h1", "h2", "h3", "[class*='title']", "[class*='position']", "[itemprop='title']"]
COMPANY_SELECTORS = ["[class*='company']", "[class*='employer']", "[class*='organization']", "[itemprop='hiringOrganization']"]
LOCATION_SELECTORS = ["[class*='location']", "[class*='place']", "[class*='city']", "[itemprop='jobLocation']"]
SALARY_SELECTORS  = ["[class*='salary']", "[class*='wage']", "[class*='compensation']", "[itemprop='baseSalary']"]
DATE_SELECTORS    = ["time[datetime]", "[class*='date']", "[class*='posted']", "[itemprop='datePosted']"]
DESC_SELECTORS    = ["[class*='description']", "[class*='content']", "[class*='details']", "[itemprop='description']", "main", ".job-body"]


async def scrape_generic(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_url: str | None,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    """
    Auto-detect scraping strategy for unknown sites.
    Tries static first (fast), falls back to Playwright (reliable).
    """
    results_limit = max_results if max_results > 0 else 50
    domain = urlparse(url).netloc

    # Build search URL (try common patterns)
    search_url = _build_generic_search_url(url, keyword, location)

    # ── Step 1: Try fast static scrape ──────────────────────────────────────
    log.info("Generic scraper [%s]: trying static requests", domain)
    jobs = await asyncio.get_event_loop().run_in_executor(
        None, _static_scrape, search_url, keyword, location, results_limit, proxy_url
    )

    if jobs:
        log.info("Generic scraper [%s]: static found %d jobs", domain, len(jobs))
        return jobs

    # ── Step 2: Playwright with XHR interception ─────────────────────────────
    log.info("Generic scraper [%s]: static failed — trying Playwright", domain)
    jobs = await _playwright_scrape(
        search_url, keyword, location, results_limit, proxy_url, delay_ms
    )

    log.info("Generic scraper [%s]: Playwright found %d jobs", domain, len(jobs))
    return jobs


# ── Static path ──────────────────────────────────────────────────────────────

def _static_scrape(url, keyword, location, limit, proxy_url) -> list[dict]:
    from ..utils.proxy import get_proxy_for_requests
    proxies = get_proxy_for_requests(proxy_url)
    try:
        resp = requests.get(url, headers=_build_headers(), proxies=proxies, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        log.debug("Generic static fetch failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # JSON-LD first
    jobs = _extract_jsonld_jobs(soup)
    if jobs:
        return jobs[:limit]

    # CSS pattern matching
    jobs = _extract_css_jobs(soup, resp.url)
    return jobs[:limit]


def _extract_jsonld_jobs(soup: BeautifulSoup) -> list[dict]:
    jobs = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ("JobPosting", "jobPosting"):
                    jobs.append(_jsonld_to_job(item))
        except Exception:
            continue
    return jobs


def _extract_css_jobs(soup: BeautifulSoup, base_url: str) -> list[dict]:
    cards = []
    for sel in JOB_CARD_SELECTORS:
        cards = soup.select(sel)
        if len(cards) > 1:
            break

    if not cards:
        return []

    jobs = []
    for card in cards:
        title   = _first_text(card, TITLE_SELECTORS)
        company = _first_text(card, COMPANY_SELECTORS)
        loc     = _first_text(card, LOCATION_SELECTORS)
        salary  = _first_text(card, SALARY_SELECTORS)
        date_el = card.select_one(", ".join(DATE_SELECTORS))
        date    = date_el.get("datetime") or date_el.get_text(strip=True) if date_el else None
        link    = card.select_one("a[href]")
        href    = link["href"] if link else None
        job_url = urljoin(base_url, href) if href else None

        # Infer remote
        is_remote = None
        if title and re.search(r'\bremote\b', title, re.I):
            is_remote = True

        jobs.append({
            "title":          title,
            "company":        company,
            "location":       loc,
            "jobType":        None,
            "salary":         salary,
            "salaryMin":      None,
            "salaryMax":      None,
            "salaryCurrency": None,
            "description":    None,
            "requirements":   None,
            "postedDate":     date,
            "url":            job_url,
            "isRemote":       is_remote,
        })
    return jobs


# ── Playwright path ───────────────────────────────────────────────────────────

async def _playwright_scrape(url, keyword, location, limit, proxy_url, delay_ms) -> list[dict]:
    from playwright.async_api import async_playwright, Response
    from ..utils.stealth import apply_stealth_scripts
    from ..utils.proxy import get_proxy_for_playwright

    jobs: list[dict] = []
    api_data_collected: list = []
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
            locale="en-US",
        )
        await apply_stealth_scripts(context)

        # Intercept JSON API responses
        async def capture_response(response: Response):
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct and response.status == 200:
                    body = await response.json()
                    if isinstance(body, (dict, list)):
                        api_data_collected.append(body)
            except Exception:
                pass

        page = await context.new_page()
        page.on("response", capture_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=35000)
            await asyncio.sleep(delay_ms / 1000)

            # Parse intercepted API data
            for payload in api_data_collected:
                extracted = _extract_from_api_payload(payload)
                jobs.extend(extracted)
                if len(jobs) >= limit:
                    break

            # If no API data, parse DOM
            if not jobs:
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")
                jobs = _extract_jsonld_jobs(soup)
                if not jobs:
                    jobs = _extract_css_jobs(soup, url)

        except Exception as e:
            log.error("Generic Playwright error: %s", e, exc_info=True)
        finally:
            await browser.close()

    return jobs[:limit]


def _extract_from_api_payload(payload) -> list[dict]:
    """Try to find job-like objects in arbitrary JSON API responses."""
    jobs = []

    candidates = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        # Look for array fields that likely contain job listings
        for key in ("jobs", "results", "items", "data", "listings", "vacancies", "positions", "offers"):
            if isinstance(payload.get(key), list):
                candidates = payload[key]
                break
        if not candidates and "total" in payload:
            # Might be paginated wrapper
            for v in payload.values():
                if isinstance(v, list) and v:
                    candidates = v
                    break

    for item in candidates:
        if not isinstance(item, dict):
            continue
        # Check if it looks like a job
        has_title = any(k in item for k in ("title", "jobTitle", "positionName", "name"))
        has_company = any(k in item for k in ("company", "employer", "companyName", "organization"))
        if not (has_title or has_company):
            continue

        jobs.append({
            "title":          item.get("title") or item.get("jobTitle") or item.get("positionName"),
            "company":        item.get("company") or item.get("employer") or item.get("companyName"),
            "location":       item.get("location") or item.get("city") or item.get("place"),
            "jobType":        item.get("employmentType") or item.get("jobType") or item.get("workType"),
            "salary":         item.get("salary") or item.get("compensation"),
            "salaryMin":      item.get("salaryMin") or item.get("minSalary"),
            "salaryMax":      item.get("salaryMax") or item.get("maxSalary"),
            "salaryCurrency": item.get("currency") or item.get("salaryCurrency"),
            "description":    item.get("description") or item.get("jobDescription"),
            "requirements":   item.get("requirements") or item.get("qualifications"),
            "postedDate":     item.get("datePosted") or item.get("postedDate") or item.get("createdAt"),
            "url":            item.get("url") or item.get("jobUrl") or item.get("link"),
            "isRemote":       item.get("remote") or item.get("isRemote") or item.get("homeOffice"),
        })
    return jobs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_generic_search_url(base_url: str, keyword: str, location: str) -> str:
    """Append keyword/location as query params if not already a search URL."""
    parsed = urlparse(base_url)
    if parsed.query:
        return base_url  # Already has params

    q = urllib.parse.quote_plus(keyword)
    l = urllib.parse.quote_plus(location)

    # Try common search path patterns
    for path in ["/jobs/search", "/search/jobs", "/jobs", "/vacancies", "/careers"]:
        return f"{parsed.scheme}://{parsed.netloc}{path}?q={q}&location={l}"

    return f"{base_url}?q={q}&location={l}"


def _first_text(element, selectors: list[str]) -> str | None:
    for sel in selectors:
        el = element.select_one(sel)
        if el:
            return el.get_text(strip=True)
    return None


def _build_headers() -> dict:
    return {
        "User-Agent":      _ua.random,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    }



def _jsonld_to_job(item: dict) -> dict:
    org  = item.get("hiringOrganization") or {}
    loc  = item.get("jobLocation") or {}
    addr = loc.get("address") or {} if isinstance(loc, dict) else {}
    sal  = item.get("baseSalary") or {}
    sal_val = sal.get("value") or {}
    return {
        "title":          item.get("title"),
        "company":        org.get("name") if isinstance(org, dict) else org,
        "location":       addr.get("addressLocality") if isinstance(addr, dict) else None,
        "jobType":        item.get("employmentType"),
        "salary":         None,
        "salaryMin":      sal_val.get("minValue") if isinstance(sal_val, dict) else None,
        "salaryMax":      sal_val.get("maxValue") if isinstance(sal_val, dict) else None,
        "salaryCurrency": sal.get("currency") if isinstance(sal, dict) else None,
        "description":    item.get("description"),
        "requirements":   item.get("qualifications") or item.get("experienceRequirements"),
        "postedDate":     item.get("datePosted"),
        "url":            item.get("url"),
        "isRemote":       item.get("jobLocationType") == "TELECOMMUTE",
    }
