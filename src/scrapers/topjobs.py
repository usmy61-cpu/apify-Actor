"""
Topjobs.ch Scraper
Strategy: requests + BeautifulSoup. Check sitemap.xml first for direct listing index.
Difficulty: 2/5 — Easy
Anti-bot: Minimal — header validation only. Mostly server-rendered HTML.
"""

import asyncio
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)
_ua = UserAgent()

BASE_URL = "https://www.topjobs.ch"


async def scrape_topjobs(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_configuration: Any,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _scrape_sync, keyword, location, max_results, proxy_configuration, delay_ms, languages
    )


def _scrape_sync(keyword, location, max_results, proxy_configuration, delay_ms, languages) -> list[dict]:
    proxies = _get_requests_proxy(proxy_configuration)
    results_limit = max_results if max_results > 0 else 200
    jobs: list[dict] = []

    for lang in languages:
        if lang not in ("de", "fr", "en"):
            lang = "de"

        page = 1
        while len(jobs) < results_limit:
            search_url = _build_search_url(keyword, location, lang, page)
            html = _fetch_page(search_url, proxies)
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")
            page_jobs = _parse_listing_page(soup, lang)

            if not page_jobs:
                break

            for job in page_jobs:
                if len(jobs) >= results_limit:
                    break
                if job.get("url"):
                    detail = _fetch_job_detail(job["url"], proxies)
                    if detail:
                        job.update(detail)
                jobs.append(job)
                time.sleep(delay_ms / 1000)

            if not _has_next_page(soup):
                break
            page += 1

        if jobs:
            break

    log.info("Topjobs.ch: found %d jobs", len(jobs))
    return jobs


def _build_search_url(keyword: str, location: str, lang: str, page: int) -> str:
    q = urllib.parse.quote_plus(keyword)
    l = urllib.parse.quote_plus(location)
    params = f"?q={q}&location={l}"
    if page > 1:
        params += f"&page={page}"
    return f"{BASE_URL}/{lang}/jobs/search{params}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(url: str, proxies: dict | None) -> str | None:
    try:
        resp = requests.get(url, headers=_build_headers(), proxies=proxies, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning("Topjobs fetch failed %s: %s", url, e)
        return None


def _parse_listing_page(soup: BeautifulSoup, lang: str) -> list[dict]:
    jobs = []

    # JSON-LD first
    import json
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("JobPosting", "jobPosting"):
                    jobs.append(_jsonld_to_job(item))
        except Exception:
            continue

    if jobs:
        return jobs

    # CSS fallback
    cards = (
        soup.select("div.job-listing-item")
        or soup.select("article.job")
        or soup.select("li.job")
        or soup.select("div[class*='JobCard']")
        or soup.select(".job-result")
    )

    for card in cards:
        title_el   = card.select_one("h2, h3, .title, [class*='title']")
        company_el = card.select_one(".company, [class*='company'], .employer")
        loc_el     = card.select_one(".location, [class*='location'], .city")
        link_el    = card.select_one("a[href]")
        date_el    = card.select_one("time, .date, [class*='date']")

        href = link_el["href"] if link_el else None
        url  = f"{BASE_URL}{href}" if href and href.startswith("/") else href

        jobs.append({
            "title":          title_el.get_text(strip=True)   if title_el   else None,
            "company":        company_el.get_text(strip=True) if company_el else None,
            "location":       loc_el.get_text(strip=True)     if loc_el     else None,
            "jobType":        None,
            "salary":         None,
            "salaryMin":      None,
            "salaryMax":      None,
            "salaryCurrency": "CHF",
            "description":    None,
            "requirements":   None,
            "postedDate":     date_el.get("datetime") or date_el.get_text(strip=True) if date_el else None,
            "url":            url,
            "isRemote":       None,
        })

    return jobs


def _fetch_job_detail(job_url: str, proxies: dict | None) -> dict | None:
    try:
        resp = requests.get(job_url, headers=_build_headers(), proxies=proxies, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        desc_el   = soup.select_one("[class*='description'], [itemprop='description'], .content")
        req_el    = soup.select_one("[class*='requirement'], [class*='qualification']")
        salary_el = soup.select_one("[class*='salary'], [itemprop='baseSalary']")
        type_el   = soup.select_one("[class*='employment'], [class*='workload'], [itemprop='employmentType']")
        date_el   = soup.select_one("time[datetime], [itemprop='datePosted']")

        return {
            "description":  desc_el.get_text(separator="\n", strip=True) if desc_el else None,
            "requirements": req_el.get_text(separator="\n", strip=True)  if req_el  else None,
            "salary":       salary_el.get_text(strip=True)                if salary_el else None,
            "jobType":      type_el.get_text(strip=True)                  if type_el   else None,
            "postedDate":   date_el.get("datetime") or date_el.get_text(strip=True) if date_el else None,
        }
    except Exception as e:
        log.debug("Topjobs detail fetch failed: %s", e)
        return None


def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one("a[rel='next'], .pagination-next, a[aria-label*='next' i]"))


def _build_headers() -> dict:
    return {
        "User-Agent":      _ua.random,
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    }


def _get_requests_proxy(proxy_configuration) -> dict | None:
    if not proxy_configuration:
        return None
    try:
        url = proxy_configuration.new_url()
        return {"http": url, "https": url}
    except Exception:
        return None


def _jsonld_to_job(item: dict) -> dict:
    org  = item.get("hiringOrganization") or {}
    loc  = item.get("jobLocation") or {}
    addr = loc.get("address") or {} if isinstance(loc, dict) else {}
    sal  = item.get("baseSalary") or {}
    sal_val = sal.get("value") or {}
    return {
        "title":          item.get("title"),
        "company":        org.get("name"),
        "location":       addr.get("addressLocality"),
        "jobType":        item.get("employmentType"),
        "salary":         None,
        "salaryMin":      sal_val.get("minValue"),
        "salaryMax":      sal_val.get("maxValue"),
        "salaryCurrency": sal.get("currency", "CHF"),
        "description":    item.get("description"),
        "requirements":   item.get("qualifications"),
        "postedDate":     item.get("datePosted"),
        "url":            item.get("url"),
        "isRemote":       item.get("jobLocationType") == "TELECOMMUTE",
    }
