"""
Proxy utilities — resolves Apify proxy URL synchronously by
calling new_url() in the async context (main.py) and passing
the already-resolved string URL to all scrapers.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)


async def resolve_proxy_url(proxy_configuration: Any) -> str | None:
    """
    Await proxy_configuration.new_url() in async context.
    Returns a plain string like 'http://user:pass@proxy.apify.com:8000'
    Call this ONCE in main.py and pass the string to all scrapers.
    """
    if not proxy_configuration:
        return None
    try:
        url = await proxy_configuration.new_url()
        return url
    except Exception as e:
        log.warning("Could not resolve proxy URL: %s", e)
        return None


def get_proxy_for_playwright(proxy_url: str | None) -> dict | None:
    """
    Converts a plain proxy URL string into Playwright proxy dict.
    { "server": "http://...", "username": "...", "password": "..." }
    """
    if not proxy_url:
        return None
    try:
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


def get_proxy_for_requests(proxy_url: str | None) -> dict | None:
    """
    Converts a plain proxy URL string into requests proxy dict.
    { "http": "http://...", "https": "http://..." }
    """
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}
