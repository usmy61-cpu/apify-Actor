"""
Router — maps a site URL to its dedicated scraper coroutine.
Falls back to the generic scraper for unknown sites.
"""

from urllib.parse import urlparse
from typing import Callable, Coroutine, Any

from .scrapers.linkedin   import scrape_linkedin
from .scrapers.indeed     import scrape_indeed
from .scrapers.jobs_ch    import scrape_jobs_ch
from .scrapers.jobscout24 import scrape_jobscout24
from .scrapers.topjobs    import scrape_topjobs
from .scrapers.alpha_ch   import scrape_alpha_ch
from .scrapers.generic    import scrape_generic

# Domain → scraper function mapping
_DOMAIN_MAP: dict[str, Callable[..., Coroutine[Any, Any, list[dict]]]] = {
    "linkedin.com":    scrape_linkedin,
    "indeed.com":      scrape_indeed,
    "jobs.ch":         scrape_jobs_ch,
    "jobscout24.ch":   scrape_jobscout24,
    "topjobs.ch":      scrape_topjobs,
    "alpha.ch":        scrape_alpha_ch,
}


def route_scraper(url: str) -> Callable:
    """
    Given a site URL, return the appropriate scraper function.
    Matches on domain suffix so both 'ch.linkedin.com' and 'linkedin.com' work.
    """
    try:
        hostname = urlparse(url).hostname or ""
        hostname = hostname.lower().lstrip("www.").lstrip("ch.")
    except Exception:
        hostname = ""

    for domain, fn in _DOMAIN_MAP.items():
        if hostname == domain or hostname.endswith("." + domain):
            return fn

    # No match → generic auto-detect scraper
    return scrape_generic
