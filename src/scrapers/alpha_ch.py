"""
Alpha.ch Scraper
Strategy: Check RSS/Atom feed first (cleanest data), then requests + BeautifulSoup.
Difficulty: 2/5 — Easy
Anti-bot: Basic — no major WAF. Server-rendered or light JS.
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

BASE_URL = "https://www.alpha.ch"

# Possible RSS feed paths on alpha.ch
RSS_CANDIDATES = [
    "/de/jobs/rss",
    "/en/jobs/rss",
    "/fr/jobs/rss",
    "/jobs/feed",
    "/rss/jobs",
]


async def scrape_alpha_ch(
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

    # ── Try RSS feed first ───────────────────────────────────────────────────
    rss_jobs = _try_rss(keyword, location, proxies, results_limit)
    if rss_jobs:
        log.info("Alpha.ch: using RSS feed — found %d items", len(rss_jobs))
        return rss_jobs[:results_limit]

    # ── Fall back to HTML scraping ───────────────────────────────────────────
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

            # Check for RSS link in <head> and try it
            rss_link = soup.select_one("link[type='application/rss+xml'], link[type='application/atom+xml']")
            if rss_link and rss_link.get("href"):
                rss_url = rss_link["href"]
                if not rss_url.startswith("http"):
                    rss_url = BASE_URL + rss_url
                rss_jobs = _parse_rss(rss_url, proxies)
                if rss_jobs:
                    log.info("Alpha.ch: RSS found in page <head>")
                    return rss_jobs[:results_limit]

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

    log.info("Alpha.ch: found %d jobs", len(jobs))
    return jobs


def _try_rss(keyword: str, location: str, proxies: dict | None, limit: int) -> list[dict]:
    """Try known RSS paths. Return parsed jobs if any feed works."""
    for path in RSS_CANDIDATES:
        q = urllib.parse.quote_plus(keyword)
        l = urllib.parse.quote_plus(location)
        rss_url = f"{BASE_URL}{path}?q={q}&location={l}"
        jobs = _parse_rss(rss_url, proxies)
        if jobs:
            return jobs[:limit]
    return []


def _parse_rss(rss_url: str, proxies: dict | None) -> list[dict]:
    try:
        resp = requests.get(rss_url, headers=_build_headers(), proxies=proxies, timeout=15)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        jobs = []

        # RSS 2.0
        for item in root.iter("item"):
            title   = item.findtext("title")
            link    = item.findtext("link")
            desc    = item.findtext("description")
            pub_date = item.findtext("pubDate")
            company = item.findtext("author") or None

            jobs.append({
                "title":          title,
                "company":        company,
                "location":       None,
                "jobType":        None,
                "salary":         None,
                "salaryMin":      None,
                "salaryMax":      None,
                "salaryCurrency": "CHF",
                "description":    desc,
                "requirements":   None,
                "postedDate":     pub_date,
                "url":            link,
                "isRemote":       None,
            })

        # Atom feed
        for entry in root.findall("atom:entry", ns):
            title   = entry.findtext("atom:title", namespaces=ns)
            link_el = entry.find("atom:link", ns)
            link    = link_el.get("href") if link_el is not None else None
            desc    = entry.findtext("atom:summary", namespaces=ns)
            pub     = entry.findtext("atom:updated", namespaces=ns)
            jobs.append({
                "title": title, "company": None, "location": None,
                "jobType": None, "salary": None, "salaryMin": None, "salaryMax": None,
                "salaryCurrency": "CHF", "description": desc, "requirements": None,
                "postedDate": pub, "url": link, "isRemote": None,
            })

        return jobs
    except Exception as e:
        log.debug("RSS parse failed for %s: %s", rss_url, e)
        return []


def _build_search_url(keyword: str, location: str, lang: str, page: int) -> str:
    q = urllib.parse.quote_plus(keyword)
    l = urllib.parse.quote_plus(location)
    params = f"?query={q}&location={l}"
    if page > 1:
        params += f"&page={page}"
    return f"{BASE_URL}/{lang}/jobs{params}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _fetch_page(url: str, proxies: dict | None) -> str | None:
    try:
        resp = requests.get(url, headers=_build_headers(), proxies=proxies, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning("Alpha.ch fetch failed %s: %s", url, e)
        return None


def _parse_listing_page(soup: BeautifulSoup, lang: str) -> list[dict]:
    jobs = []

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

    cards = (
        soup.select("div.job-list-item, article.job, li.job, div[class*='JobCard'], .job-result, .job-item")
    )
    for card in cards:
        title_el   = card.select_one("h2, h3, .job-title, [class*='title']")
        company_el = card.select_one(".company, [class*='company']")
        loc_el     = card.select_one(".location, [class*='location']")
        link_el    = card.select_one("a[href]")
        salary_el  = card.select_one(".salary, [class*='salary']")
        date_el    = card.select_one("time, .date, [class*='date']")

        href = link_el["href"] if link_el else None
        url  = f"{BASE_URL}{href}" if href and href.startswith("/") else href

        jobs.append({
            "title":          title_el.get_text(strip=True)   if title_el   else None,
            "company":        company_el.get_text(strip=True) if company_el else None,
            "location":       loc_el.get_text(strip=True)     if loc_el     else None,
            "jobType":        None,
            "salary":         salary_el.get_text(strip=True)  if salary_el  else None,
            "salaryMin":      None,
            "salaryMax":      None,
            "salaryCurrency": "CHF",
            "description":    None,
            "requirements":   None,
            "postedDate":     date_el.get("datetime") if date_el else None,
            "url":            url,
            "isRemote":       None,
        })
    return jobs


def _fetch_job_detail(job_url: str, proxies: dict | None) -> dict | None:
    try:
        resp = requests.get(job_url, headers=_build_headers(), proxies=proxies, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        desc_el   = soup.select_one("[class*='description'], [itemprop='description'], .job-content")
        req_el    = soup.select_one("[class*='requirement'], [class*='qualification']")
        salary_el = soup.select_one("[class*='salary'], [itemprop='baseSalary']")
        type_el   = soup.select_one("[class*='employment'], [itemprop='employmentType']")
        date_el   = soup.select_one("time[datetime], [itemprop='datePosted']")
        return {
            "description":  desc_el.get_text(separator="\n", strip=True) if desc_el else None,
            "requirements": req_el.get_text(separator="\n", strip=True)  if req_el  else None,
            "salary":       salary_el.get_text(strip=True)                if salary_el else None,
            "jobType":      type_el.get_text(strip=True)                  if type_el   else None,
            "postedDate":   date_el.get("datetime") if date_el else None,
        }
    except Exception as e:
        log.debug("Alpha.ch detail failed: %s", e)
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
