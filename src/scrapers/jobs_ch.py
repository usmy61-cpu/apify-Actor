"""
Jobs.ch Scraper
Strategy:
  1. Direct HTTP to internal REST API
  2. Playwright + XHR interception fallback
  3. DOM / JSON-LD fallback

Key fixes:
  - Exhaustive field mapping for company/url/jobType (API uses non-obvious keys)
  - URL constructed from id/slug if no direct url field
  - Raw key logging on first record so mismatches are immediately visible
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

BASE_URL = "https://www.jobs.ch"
API_URL  = "https://www.jobs.ch/api/v1/public/search/"


async def scrape_jobs_ch(
    url: str, keyword: str, location: str, max_results: int,
    proxy_url: str | None, delay_ms: int, languages: list[str], **kwargs,
) -> list[dict]:
    from ..utils.proxy import get_proxy_for_requests
    proxies  = get_proxy_for_requests(proxy_url)
    limit    = max_results if max_results > 0 else 200
    loop     = asyncio.get_event_loop()

    # ── Strategy 1: REST API ───────────────────────────────────────────────
    jobs = await loop.run_in_executor(None, _rest_api, keyword, location, limit, proxies)
    if jobs:
        log.info("Jobs.ch REST API: %d jobs", len(jobs))
        return jobs

    # ── Strategy 2: Playwright XHR interception ────────────────────────────
    log.info("Jobs.ch REST returned 0 — trying Playwright")
    jobs = await _playwright_scrape(keyword, location, limit, proxy_url, delay_ms)
    if jobs:
        log.info("Jobs.ch Playwright: %d jobs", len(jobs))
        return jobs

    log.warning("Jobs.ch: all strategies returned 0")
    return []


# ── REST API ──────────────────────────────────────────────────────────────────

def _rest_api(keyword: str, location: str, limit: int, proxies) -> list[dict]:
    headers = {
        "User-Agent":     _ua.random,
        "Accept":         "application/json, text/plain, */*",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        "Referer":        f"{BASE_URL}/de/stellenangebote/",
        "Origin":         BASE_URL,
    }
    # Try multiple param combinations — we don't know the exact API contract
    param_variants = [
        {"term": keyword, "location": location, "page": 1, "num_results": min(limit, 25), "language": "de"},
        {"query": keyword, "location": location, "page": 1, "per_page": min(limit, 25)},
        {"q": keyword, "location": location, "page": 1, "size": min(limit, 25)},
        {"term": keyword, "page": 1, "num_results": min(limit, 25)},  # no location
    ]
    for params in param_variants:
        try:
            resp = requests.get(API_URL, params=params, headers=headers, proxies=proxies, timeout=20)
            log.info("Jobs.ch REST %s → HTTP %d", params, resp.status_code)
            if resp.status_code != 200:
                continue
            data = resp.json()
            jobs = _parse_api(data)
            if jobs:
                return jobs
        except Exception as e:
            log.warning("Jobs.ch REST error: %s", e)
    return []


def _parse_api(data) -> list[dict]:
    """
    Exhaustively handle all known Jobs.ch / JobCloud API response shapes.
    Logs all keys of the first raw record for debugging.
    """
    # Unwrap envelope
    if isinstance(data, dict):
        docs = (
            data.get("documents") or data.get("jobs") or data.get("results")
            or data.get("items") or data.get("data") or data.get("hits")
            or []
        )
        if isinstance(docs, dict):          # Elasticsearch hits wrapper
            docs = docs.get("hits") or docs.get("items") or []
    elif isinstance(data, list):
        docs = data
    else:
        return []

    if not docs:
        return []

    # Log raw keys on first item for debugging
    first = docs[0]
    if isinstance(first, dict):
        log.info("Jobs.ch API first-item keys: %s", list(first.keys())[:30])
        # Handle Elasticsearch _source wrapping
        if "_source" in first:
            first = first["_source"]
            log.info("Jobs.ch API _source keys: %s", list(first.keys())[:30])

    jobs = []
    for raw in docs:
        if not isinstance(raw, dict):
            continue
        # Unwrap _source
        item = raw.get("_source", raw)

        title    = _pick(item, "title", "jobTitle", "positionTitle", "name", "heading")
        if not title:
            continue

        # Company — try nested object then flat string fields
        company  = _nested_name(item, "company", "advertiser", "employer",
                                 "hiringOrganization", "companyInfo", "recruiter")
        company  = company or _pick(item, "companyName", "company_name",
                                     "advertiser_name", "employerName", "firm")

        # Location — try nested then flat
        loc_obj  = _nested_locality(item, "place", "location", "jobLocation",
                                     "address", "city", "workPlace")
        location = loc_obj or _pick(item, "locationText", "cityName", "region",
                                     "canton", "workLocation")

        # URL — try direct, then construct from slug or id
        job_url  = _pick(item, "url", "jobUrl", "link", "href",
                          "externalUrl", "applyUrl", "detailUrl")
        if not job_url:
            slug = _pick(item, "slug", "urlSlug", "jobSlug", "permalink")
            uid  = _pick(item, "id", "jobId", "uid", "uuid", "externalId")
            if slug:
                job_url = f"{BASE_URL}/de/stellenangebote/{slug}/"
            elif uid:
                job_url = f"{BASE_URL}/de/stellenangebote/{uid}/"

        # Job type
        jtype    = _pick(item, "employmentType", "contractType", "workload",
                          "jobType", "employmentGrade", "workloadText",
                          "contractTypeLabel", "pensum")

        # Salary
        sal_obj  = item.get("salary") or item.get("compensation") or item.get("wage") or {}
        sal_text = None
        sal_min  = sal_max = sal_cur = None
        if isinstance(sal_obj, dict):
            sal_text = _pick(sal_obj, "text", "description", "display", "label")
            sal_min  = sal_obj.get("min") or sal_obj.get("minValue") or sal_obj.get("from")
            sal_max  = sal_obj.get("max") or sal_obj.get("maxValue") or sal_obj.get("to")
            sal_cur  = sal_obj.get("currency", "CHF")
        elif isinstance(sal_obj, str):
            sal_text = sal_obj

        # Date
        date     = _pick(item, "publicationDate", "datePosted", "createdAt",
                          "postedDate", "publishedAt", "publishDate", "date")

        # Description
        desc     = _pick(item, "description", "jobDescription", "content",
                          "bodyText", "fullDescription", "details")

        # Remote
        remote   = item.get("homeOffice") or item.get("remote") or item.get("isRemote")

        jobs.append({
            "title":          title,
            "company":        company,
            "location":       location,
            "jobType":        jtype,
            "salary":         sal_text,
            "salaryMin":      sal_min,
            "salaryMax":      sal_max,
            "salaryCurrency": sal_cur or "CHF",
            "description":    desc,
            "requirements":   item.get("requirements") or item.get("qualifications"),
            "postedDate":     date,
            "url":            job_url,
            "isRemote":       remote,
        })

    log.info("Jobs.ch _parse_api: parsed %d/%d items (company=%d, url=%d)",
             len(jobs), len(docs),
             sum(1 for j in jobs if j.get("company")),
             sum(1 for j in jobs if j.get("url")))
    return jobs


# ── Playwright XHR fallback ───────────────────────────────────────────────────

async def _playwright_scrape(keyword, location, limit, proxy_url, delay_ms) -> list[dict]:
    from playwright.async_api import async_playwright, Response
    from ..utils.stealth import apply_stealth_scripts
    from ..utils.proxy import get_proxy_for_playwright

    import asyncio
    jobs: list[dict] = []
    captured: list   = []
    proxy = get_proxy_for_playwright(proxy_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, proxy=proxy,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=_ua.random, locale="de-CH",
        )
        await apply_stealth_scripts(context)

        async def capture(response: Response):
            try:
                ct = response.headers.get("content-type", "")
                if "jobs.ch" in response.url and "json" in ct and response.status == 200:
                    body = await response.json()
                    log.info("Jobs.ch XHR captured: %s", response.url[:100])
                    captured.append(body)
            except Exception:
                pass

        page = await context.new_page()
        page.on("response", capture)

        q = urllib.parse.quote_plus(keyword)
        l = urllib.parse.quote_plus(location)
        await page.goto(
            f"{BASE_URL}/de/stellenangebote/?term={q}&location={l}",
            wait_until="networkidle", timeout=40000,
        )
        await asyncio.sleep(delay_ms / 1000)

        for payload in captured:
            jobs.extend(_parse_api(payload))
            if len(jobs) >= limit:
                break

        if not jobs:
            html = await page.content()
            jobs = _dom_fallback(html)

        await browser.close()

    return jobs[:limit]


def _dom_fallback(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    import extruct  # type: ignore
    jobs = []
    try:
        data = extruct.extract(html, syntaxes=["json-ld"])
        for item in data.get("json-ld", []):
            if "JobPosting" in str(item.get("@type", "")):
                jobs.append(_from_jsonld(item))
    except Exception:
        pass
    if not jobs:
        soup = BeautifulSoup(html, "lxml")
        for card in soup.select("article[data-cy='job-ad-list-item'], div[class*='job-card'], li[class*='job']"):
            title_el = card.select_one("h2, h3, [class*='title']")
            link_el  = card.select_one("a[href]")
            if not title_el:
                continue
            href = link_el["href"] if link_el else None
            jobs.append({
                "title":    title_el.get_text(strip=True),
                "company":  None, "location": None, "jobType": None,
                "salary":   None, "salaryMin": None, "salaryMax": None,
                "salaryCurrency": "CHF", "description": None, "requirements": None,
                "postedDate": None,
                "url":      (BASE_URL + href if href and href.startswith("/") else href),
                "isRemote": None,
            })
    return jobs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick(d: dict, *keys) -> str | None:
    """Return first non-empty string value among candidate keys."""
    for k in keys:
        v = d.get(k)
        if v and isinstance(v, str):
            return v.strip() or None
        if v and isinstance(v, (int, float)):
            return str(v)
    return None


def _nested_name(item: dict, *keys) -> str | None:
    """Extract .name from first matching nested object."""
    for k in keys:
        obj = item.get(k)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("displayName") or obj.get("title")
            if name:
                return str(name).strip()
        elif isinstance(obj, str) and obj.strip():
            return obj.strip()
    return None


def _nested_locality(item: dict, *keys) -> str | None:
    """Extract city/locality text from first matching nested location object."""
    for k in keys:
        obj = item.get(k)
        if isinstance(obj, dict):
            city = (obj.get("city") or obj.get("addressLocality")
                    or obj.get("name") or obj.get("label") or obj.get("text"))
            if city:
                return str(city).strip()
        elif isinstance(obj, str) and obj.strip():
            return obj.strip()
    return None


def _from_jsonld(item: dict) -> dict:
    org  = item.get("hiringOrganization") or {}
    loc  = item.get("jobLocation") or {}
    addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    sal  = item.get("baseSalary") or {}
    sv   = (sal.get("value") or {}) if isinstance(sal, dict) else {}
    return {
        "title":          item.get("title"),
        "company":        org.get("name") if isinstance(org, dict) else org,
        "location":       addr.get("addressLocality") if isinstance(addr, dict) else None,
        "jobType":        item.get("employmentType"),
        "salary":         None,
        "salaryMin":      sv.get("minValue") if isinstance(sv, dict) else None,
        "salaryMax":      sv.get("maxValue") if isinstance(sv, dict) else None,
        "salaryCurrency": sal.get("currency", "CHF") if isinstance(sal, dict) else "CHF",
        "description":    item.get("description"),
        "requirements":   item.get("qualifications"),
        "postedDate":     item.get("datePosted"),
        "url":            item.get("url"),
        "isRemote":       item.get("jobLocationType") == "TELECOMMUTE",
    }
