"""
Swiss Job Scraper — Apify Actor Entry Point
Orchestrates all scrapers based on user input.
"""

import asyncio
import logging
from datetime import datetime, timezone

from apify import Actor

from .router import route_scraper
from .utils.normalizer import normalize_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


async def main() -> None:
    async with Actor:
        # ── Read input ──────────────────────────────────────────────────────
        inp = await Actor.get_input() or {}

        keywords: list[str]  = inp.get("keywords", ["software engineer"])
        location: str        = inp.get("location", "Switzerland")
        websites: list[dict] = inp.get("websites", [])
        max_per_site: int    = inp.get("maxResultsPerSitePerKeyword", 50)
        proxy_cfg: dict      = inp.get("proxyConfiguration", {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]})
        delay_ms: int        = inp.get("delayBetweenRequestsMs", 2000)
        languages: list[str] = inp.get("languages", ["en", "de"])

        # Filter only enabled websites
        active_sites = [w for w in websites if w.get("enabled", True)]

        if not active_sites:
            log.warning("No websites enabled — nothing to scrape.")
            return

        if not keywords:
            log.warning("No keywords provided — nothing to scrape.")
            return

        log.info(
            "Starting scrape | keywords=%s | location=%s | sites=%d | max_per=%d",
            keywords, location, len(active_sites), max_per_site,
        )

        # ── Build Apify proxy configuration ─────────────────────────────────
        proxy_configuration = None
        if proxy_cfg.get("useApifyProxy"):
            proxy_configuration = await Actor.create_proxy_configuration(
                groups=proxy_cfg.get("apifyProxyGroups", ["RESIDENTIAL"]),
                country_code=proxy_cfg.get("apifyProxyCountry"),
            )

        # ── Open output dataset ──────────────────────────────────────────────
        dataset = await Actor.open_dataset()
        seen_urls: set[str] = set()
        total_saved = 0

        # ── Main scrape loop ─────────────────────────────────────────────────
        for site in active_sites:
            site_name = site.get("name", "Unknown")
            site_url  = site.get("url", "")

            for keyword in keywords:
                log.info("Scraping [%s] keyword='%s' location='%s'", site_name, keyword, location)

                try:
                    scraper_fn = route_scraper(site_url)
                    raw_jobs = await scraper_fn(
                        url=site_url,
                        keyword=keyword,
                        location=location,
                        max_results=max_per_site,
                        proxy_configuration=proxy_configuration,
                        delay_ms=delay_ms,
                        languages=languages,
                    )
                except Exception as exc:
                    log.error("Scraper failed for [%s] keyword='%s': %s", site_name, keyword, exc, exc_info=True)
                    await Actor.set_status_message(f"⚠ Error on {site_name} ({keyword}): {exc}")
                    continue

                count = 0
                for raw in raw_jobs:
                    job = normalize_job(
                        raw=raw,
                        source=site_name,
                        source_url=site_url,
                        keyword=keyword,
                        scraped_at=datetime.now(timezone.utc).isoformat(),
                    )

                    # Deduplicate by URL
                    job_url = job.get("url", "")
                    if job_url and job_url in seen_urls:
                        continue
                    if job_url:
                        seen_urls.add(job_url)

                    await dataset.push_data(job)
                    count += 1
                    total_saved += 1

                log.info("  ↳ Saved %d jobs from [%s] for '%s'", count, site_name, keyword)
                await Actor.set_status_message(
                    f"Scraped {total_saved} jobs so far | Last: {site_name} / {keyword}"
                )

        log.info("✅ Done — %d total jobs saved to dataset.", total_saved)
        await Actor.set_status_message(f"✅ Complete — {total_saved} jobs saved.")


if __name__ == "__main__":
    asyncio.run(main())
