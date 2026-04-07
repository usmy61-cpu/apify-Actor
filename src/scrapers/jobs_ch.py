"""
Jobs.ch Scraper
Strategy: Playwright + XHR/fetch interception (intercept internal REST API calls).
Difficulty: 3/5 — Medium
Bug fix: API response may be a list directly — handle both list and dict.
"""

import asyncio
import logging
import urllib.parse
from typing import Any

log = logging.getLogger(__name__)


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
    from playwright.async_api import async_playwright, Response
    from ..utils.stealth import apply_stealth_scripts
    from ..utils.proxy import get_proxy_for_playwright

    jobs: list[dict] = []
    api_responses: list = []
    proxy = get_proxy_for_playwright(proxy_url)
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

            # Parse all intercepted API responses
            for api_resp in api_responses:
                extracted = _parse_jobs_ch_api(api_resp)
                jobs.extend(extracted)
                if len(jobs) >= results_limit:
                    break

            # DOM fallback if API interception yielded nothing
            if not jobs:
                log.info("Jobs.ch: API interception yielded 0 — falling back to DOM parsing")
                jobs = await _parse_jobs_ch_dom(page, results_limit, delay_ms)

            # Paginate
            page_num = 0
            while len(jobs) < results_limit and page_num < 10:
                next_btn = await page.query_selector("button[aria-label='Next page'], a[rel='next']")
                if not next_btn:
                    break
                prev_count = len(api_responses)
                await next_btn.click()
                await asyncio.sleep(delay_ms / 1000)
                for api_resp in api_responses[prev_count:]:
                    jobs.extend(_parse_jobs_ch_api(api_resp))
                page_num += 1

        except Exception as e:
            log.error("Jobs.ch Playwright error: %s", e, exc_info=True)
        finally:
            await browser.close()

    log.info("Jobs.ch: found %d jobs", len(jobs[:results_limit]))
    return jobs[:results_limit]


def _parse_jobs_ch_api(data) -> list[dict]:
    """
    Parse Jobs.ch internal API JSON response.
    Handles both dict (wrapped) and list (direct array) responses.
    """
    jobs = []

    # Unwrap if dict — look for common array keys
    if isinstance(data, dict):
        documents = (
            data.get("documents")
            or data.get("jobs")
            or data.get("results")
            or data.get("items")
            or data.get("data")
            or []
        )
    elif isinstance(data, list):
        documents = data
    else:
        return []

    for item in documents:
        if not isinstance(item, dict):
            continue

        position      = item.get("position") or {}
        company_data  = item.get("company") or {}
        salary        = item.get("salary") or {}
        location_data = item.get("place") or item.get("location") or {}

        title     = position.get("title") or item.get("title") or item.get("name")
        comp_name = company_data.get("name") if isinstance(company_data, dict) else company_data
        comp_name = comp_name or item.get("companyName")
        loc_name  = (location_data.get("city") or location_data.get("name")) if isinstance(location_data, dict) else location_data
        loc_name  = loc_name or item.get("location")
        job_url   = item.get("url") or item.get("jobUrl") or item.get("externalUrl")
        if job_url and isinstance(job_url, str) and not job_url.startswith("http"):
            job_url = f"https://www.jobs.ch{job_url}"

        sal_text = None
        sal_min = sal_max = None
        if isinstance(salary, dict):
            sal_text = salary.get("text") or salary.get("description")
            sal_min  = salary.get("min")
            sal_max  = salary.get("max")

        jobs.append({
            "title":          title,
            "company":        comp_name,
            "location":       loc_name,
            "jobType":        item.get("workload") or item.get("employmentType"),
            "salary":         sal_text,
            "salaryMin":      sal_min,
            "salaryMax":      sal_max,
            "salaryCurrency": salary.get("currency", "CHF") if isinstance(salary, dict) else "CHF",
            "description":    item.get("description") or (position.get("description") if isinstance(position, dict) else None),
            "requirements":   item.get("requirements"),
            "postedDate":     item.get("publicationDate") or item.get("createdAt"),
            "url":            job_url,
            "isRemote":       item.get("homeOffice") or item.get("remote"),
        })
    return jobs


async def _parse_jobs_ch_dom(page, results_limit: int, delay_ms: int) -> list[dict]:
    """DOM fallback — JSON-LD then CSS selectors."""
    import extruct  # type: ignore

    jobs = []
    html = await page.content()

    try:
        data = extruct.extract(html, syntaxes=["json-ld"])
        for item in data.get("json-ld", []):
            if item.get("@type") in ("JobPosting", "jobPosting"):
                jobs.append(_jsonld_to_job(item))
    except Exception as e:
        log.warning("Jobs.ch JSON-LD extraction failed: %s", e)

    if not jobs:
        cards = await page.query_selector_all(
            "article[data-cy='job-ad-list-item'], div[class*='JobAdListItem'], div[class*='job-card']"
        )
        for card in cards[:results_limit]:
            try:
                title_el   = await card.query_selector("h2, h3, [class*='title']")
                company_el = await card.query_selector("[class*='company'], [class*='employer']")
                loc_el     = await card.query_selector("[class*='location'], [class*='place']")
                link_el    = await card.query_selector("a[href*='/job'], a[href*='/stelle']")
                title    = await title_el.inner_text()   if title_el   else None
                company  = await company_el.inner_text() if company_el else None
                location = await loc_el.inner_text()     if loc_el     else None
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
