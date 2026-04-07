"""
Jobs.ch Scraper
Strategy: Playwright + XHR/fetch interception (intercept internal REST API calls).
Difficulty: 3/5 — Medium
Anti-bot: Cloudflare CDN + IP rate limiting.
Structure: React SPA — but exposes internal JSON API via XHR.
"""

import asyncio
import json
import logging
import urllib.parse
from typing import Any

log = logging.getLogger(__name__)

# Jobs.ch internal API base (discovered via XHR interception)
JOBS_CH_API = "https://www.jobs.ch/api/v1/public/search/"


async def scrape_jobs_ch(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_configuration: Any,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    from playwright.async_api import async_playwright, Response
    from ..utils.stealth import apply_stealth_scripts
    from ..utils.proxy import get_proxy_for_playwright

    jobs: list[dict] = []
    api_responses: list[dict] = []
    proxy = get_proxy_for_playwright(proxy_configuration)
    results_limit = max_results if max_results > 0 else 200

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy=proxy,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="de-CH",
        )
        await apply_stealth_scripts(context)

        # ── Intercept XHR / fetch responses from the internal API ──────────
        async def handle_response(response: Response):
            try:
                if "jobs.ch/api" in response.url and response.status == 200:
                    body = await response.json()
                    api_responses.append(body)
            except Exception:
                pass

        page = await context.new_page()
        page.on("response", handle_response)

        q = urllib.parse.quote_plus(keyword)
        l = urllib.parse.quote_plus(location)
        search_url = f"https://www.jobs.ch/de/stellenangebote/?term={q}&location={l}"

        try:
            await page.goto(search_url, wait_until="networkidle", timeout=35000)
            await asyncio.sleep(delay_ms / 1000)

            # Parse intercepted API responses first
            for api_resp in api_responses:
                extracted = _parse_jobs_ch_api(api_resp)
                jobs.extend(extracted)
                if len(jobs) >= results_limit:
                    break

            # If API interception didn't yield results, fall back to DOM parsing
            if not jobs:
                log.info("Jobs.ch: API interception yielded 0 — falling back to DOM parsing")
                jobs = await _parse_jobs_ch_dom(page, results_limit, delay_ms)

            # Paginate if needed
            page_num = 0
            while len(jobs) < results_limit and page_num < 10:
                next_btn = await page.query_selector("button[aria-label='Next page'], a[rel='next']")
                if not next_btn:
                    break
                await next_btn.click()
                await asyncio.sleep(delay_ms / 1000)
                # New API responses captured by handler above
                new_jobs = []
                for api_resp in api_responses[len(jobs):]:
                    new_jobs.extend(_parse_jobs_ch_api(api_resp))
                jobs.extend(new_jobs)
                page_num += 1

        except Exception as e:
            log.error("Jobs.ch Playwright error: %s", e, exc_info=True)
        finally:
            await browser.close()

    log.info("Jobs.ch: found %d jobs", len(jobs[:results_limit]))
    return jobs[:results_limit]


def _parse_jobs_ch_api(data: dict) -> list[dict]:
    """Parse Jobs.ch internal API JSON response."""
    jobs = []
    documents = data.get("documents") or data.get("jobs") or data.get("results") or []
    if isinstance(data, list):
        documents = data

    for item in documents:
        if not isinstance(item, dict):
            continue

        # Handle nested position/company objects
        position = item.get("position") or {}
        company  = item.get("company") or {}
        salary   = item.get("salary") or {}
        location_data = item.get("place") or item.get("location") or {}

        title     = position.get("title") or item.get("title") or item.get("name")
        comp_name = company.get("name") or item.get("companyName")
        loc_name  = location_data.get("city") or location_data.get("name") or item.get("location")
        job_url   = item.get("url") or item.get("jobUrl") or item.get("externalUrl")
        if job_url and not job_url.startswith("http"):
            job_url = f"https://www.jobs.ch{job_url}"

        jobs.append({
            "title":         title,
            "company":       comp_name,
            "location":      loc_name,
            "jobType":       item.get("workload") or item.get("employmentType"),
            "salary":        salary.get("text") or salary.get("description"),
            "salaryMin":     salary.get("min"),
            "salaryMax":     salary.get("max"),
            "salaryCurrency": salary.get("currency", "CHF"),
            "description":   item.get("description") or position.get("description"),
            "requirements":  item.get("requirements"),
            "postedDate":    item.get("publicationDate") or item.get("createdAt"),
            "url":           job_url,
            "isRemote":      item.get("homeOffice") or item.get("remote"),
        })
    return jobs


async def _parse_jobs_ch_dom(page, results_limit: int, delay_ms: int) -> list[dict]:
    """DOM fallback parser for jobs.ch using JSON-LD structured data."""
    import extruct  # type: ignore

    jobs = []
    html = await page.content()

    # Extract JSON-LD structured data
    try:
        data = extruct.extract(html, syntaxes=["json-ld"])
        for item in data.get("json-ld", []):
            if item.get("@type") in ("JobPosting", "jobPosting"):
                jobs.append(_jsonld_to_job(item))
    except Exception as e:
        log.warning("Jobs.ch JSON-LD extraction failed: %s", e)

    if not jobs:
        # Try CSS selectors on job cards
        cards = await page.query_selector_all("article[data-cy='job-ad-list-item'], div[class*='JobAdListItem']")
        for card in cards[:results_limit]:
            try:
                title_el   = await card.query_selector("h2, h3, [class*='title']")
                company_el = await card.query_selector("[class*='company'], [class*='employer']")
                loc_el     = await card.query_selector("[class*='location'], [class*='place']")
                link_el    = await card.query_selector("a[href*='/job']")
                title   = await title_el.inner_text()   if title_el   else None
                company = await company_el.inner_text() if company_el else None
                location = await loc_el.inner_text()    if loc_el     else None
                href     = await link_el.get_attribute("href") if link_el else None
                url      = f"https://www.jobs.ch{href}" if href and href.startswith("/") else href
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
    org = item.get("hiringOrganization") or {}
    loc = item.get("jobLocation") or {}
    addr = loc.get("address") or loc if isinstance(loc, dict) else {}
    sal = item.get("baseSalary") or {}
    sal_val = sal.get("value") or {}

    return {
        "title":          item.get("title"),
        "company":        org.get("name"),
        "location":       addr.get("addressLocality") or addr.get("addressRegion"),
        "jobType":        item.get("employmentType"),
        "salary":         None,
        "salaryMin":      sal_val.get("minValue"),
        "salaryMax":      sal_val.get("maxValue"),
        "salaryCurrency": sal.get("currency", "CHF"),
        "description":    item.get("description"),
        "requirements":   item.get("qualifications") or item.get("experienceRequirements"),
        "postedDate":     item.get("datePosted"),
        "url":            item.get("url"),
        "isRemote":       item.get("jobLocationType") == "TELECOMMUTE",
    }
