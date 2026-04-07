"""
Proxy utilities — converts Apify proxy configuration into
format needed by each scraping library.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)


def get_proxy_for_playwright(proxy_configuration: Any) -> dict | None:
    """
    Returns a Playwright-compatible proxy dict:
    { "server": "http://...", "username": "...", "password": "..." }
    """
    if not proxy_configuration:
        return None
    try:
        proxy_url = proxy_configuration.new_url()
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy["username"] = parsed.username
        if parsed.password:
            proxy["password"] = parsed.password
        return proxy
    except Exception as e:
        log.warning("Could not build Playwright proxy dict: %s", e)
        return None


def get_proxy_for_requests(proxy_configuration: Any) -> dict | None:
    """
    Returns a requests-compatible proxy dict:
    { "http": "http://...", "https": "http://..." }
    """
    if not proxy_configuration:
        return None
    try:
        proxy_url = proxy_configuration.new_url()
        return {"http": proxy_url, "https": proxy_url}
    except Exception as e:
        log.warning("Could not build requests proxy dict: %s", e)
        return None
