"""
Topjobs.ch Scraper
Strategy: requests + BeautifulSoup (mostly server-rendered HTML).
Difficulty: 2/5 — Easy

Correct search URL: https://www.topjobs.ch/de/stellenangebote/?query=...&region=...
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

BASE_URL = "https://www.topjobs.ch"

# Topjobs uses different search URL patterns per language
SEARCH_PATHS = {
    "de": "/de/stellenangebote/",
    "en": "/en/jobs/",
    "fr": "/fr/offres-demploi/",
}


async def scrape_topjobs(
    url: str,
    keyword: str,
    location: str,
    max_results: int,
    proxy_url: str | None,
    delay_ms: int,
    languages: list[str],
    **kwargs,
) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _scrape_sync, keyword, location, max_results, proxy_url, delay_ms, languages
    )


def _scrape_sync(keyword, location, max_results, proxy_url, delay_ms, languages) -> list[dict]:
    from ..utils.proxy import get_proxy_for_requests
    proxies = get_proxy_for_requests(proxy_url)
    results_limit = max_results if max_results > 0 else 200
    jobs: list[dict] = []

    for lang in ["de", "en", "fr"]:
        if jobs:
            break
        page = 1
        while len(jobs) < results_limit:
            search_url = _build_search_url(keyword, location, lang, page)
            log.info("Topjobs fetching: %s", search_url)
            html = _fetch_page(search_url, proxies)
            if not html:
                log.warning("Topjobs: empty response for %s", search_url)
                break

            soup = BeautifulSoup(html, "lxml")
            page_jobs = _parse_listing_page(soup)
            log.info("Topjobs [%s] page %d: parsed %d jobs", lang, page, len(page_jobs))

            if not page_jobs:
                break

            for job in page_jobs:
                if len(jobs) >= results_limit:
                    break
                if job.get("url"):
                    time.sleep(0.5)
                    detail = _fetch_job_detail(job["url"], proxies)
                    if detail:
                        job.update(detail)
                jobs.append(job)

            if not _has_next_page(soup):
                break
            page += 1
            time.sleep(delay_ms / 1000)

    log.info("Topjobs: total found %d jobs", len(jobs))
    return jobs


def _build_search_url(keyword: str, location: str, lang: str, page: int) -> str:
    path = SEARCH_PATHS.get(lang, "/de/stellenangebote/")
    q = urllib.parse.quote_plus(keyword)
    l = urllib.parse.quote_plus(location)
    url = f"{BASE_URL}{path}?query={q}&location={l}"
    if page > 1:
        url += f"&page={page}"
    return url


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _fetch_page(url: str, proxies: dict | None) -> str | None:
    try:
        resp = requests.get(
            url, headers=_build_headers(), proxies=proxies, timeout=25, allow_redirects=True
        )
        log.info("Topjobs HTTP %d for %s", resp.status_code, url)
        return resp.text if resp.status_code == 200 else None
    except Exception as e:
        log.warning("Topjobs fetch error %s: %s", url, e)
        raise


def _parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    jobs = []

    # JSON-LD first
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ("JobPosting", "jobPosting"):
                    jobs.append(_jsonld_to_job(item))
        except Exception:
            continue
    if jobs:
        return jobs

    selectors = [
        "div.job-listing-item", "article.job", "li.job",
        "div[class*='JobCard']", "div[class*='job-card']",
        "div[class*='result']", "div[class*='stellenangebot']",
        "tr[class*='job']", "div[class*='vacancy']",
    ]
    cards = []
    for sel in selectors:
        cards = soup.select(sel)
        if cards:
            log.info("Topjobs: matched selector '%s' → %d cards", sel, len(cards))
            break

    for card in cards:
        title_el   = card.select_one("h2, h3, h4, [class*='title'], [class*='position']")
        company_el = card.select_one("[class*='company'], [class*='employer'], [class*='firm']")
        loc_el     = card.select_one("[class*='location'], [class*='place'], [class*='region']")
        link_el    = card.select_one("a[href]")
        date_el    = card.select_one("time, [class*='date']")

        href = link_el["href"] if link_el and link_el.get("href") else None
        job_url = f"{BASE_URL}{href}" if href and href.startswith("/") else href

        jobs.append({
            "title":          title_el.get_text(strip=True)   if title_el   else None,
            "company":        company_el.get_text(strip=True) if company_el else None,
            "location":       loc_el.get_text(strip=True)     if loc_el     else None,
            "jobType":        None, "salary": None,
            "salaryMin":      None, "salaryMax": None,
            "salaryCurrency": "CHF", "description": None, "requirements": None,
            "postedDate":     date_el.get("datetime") if date_el else None,
            "url":            job_url, "isRemote": None,
        })
    return jobs


def _fetch_job_detail(job_url: str, proxies: dict | None) -> dict | None:
    try:
        resp = requests.get(job_url, headers=_build_headers(), proxies=proxies, timeout=20)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        desc_el   = soup.select_one("[class*='description'], [itemprop='description'], .content, main")
        req_el    = soup.select_one("[class*='requirement'], [class*='qualification']")
        salary_el = soup.select_one("[class*='salary'], [itemprop='baseSalary']")
        type_el   = soup.select_one("[class*='employment'], [class*='workload'], [itemprop='employmentType']")
        date_el   = soup.select_one("time[datetime], [itemprop='datePosted']")
        return {
            "description":  desc_el.get_text(separator="\n", strip=True) if desc_el else None,
            "requirements": req_el.get_text(separator="\n", strip=True)  if req_el  else None,
            "salary":       salary_el.get_text(strip=True)                if salary_el else None,
            "jobType":      type_el.get_text(strip=True)                  if type_el   else None,
            "postedDate":   date_el.get("datetime") if date_el else None,
        }
    except Exception as e:
        log.debug("Topjobs detail fetch failed: %s", e)
        return None


def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one("a[rel='next'], .pagination-next, li.next a, a[aria-label*='next' i]"))


def _build_headers() -> dict:
    return {
        "User-Agent":      _ua.random,
        "Accept-Language": "de-CH,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    }


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
        "requirements":   item.get("qualifications"),
        "postedDate":     item.get("datePosted"),
        "url":            item.get("url"),
        "isRemote":       item.get("jobLocationType") == "TELECOMMUTE",
    }
