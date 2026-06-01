import logging

logger = logging.getLogger(__name__)

# NOTE: Instahyre's internal API endpoint /api/v1/opportunity/ now returns 404.
# We fall back entirely to the Scrapling scraper path until the correct endpoint
# is identified from the network tab of instahyre.com.
#
# When the API is restored, un-comment the block below and restore fetch_instahyre()
# to try the API first.
#
# INSTAHYRE_API = "https://www.instahyre.com/api/v1/opportunity/"
# API_HEADERS = {
#     "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
#     "Referer":    "https://www.instahyre.com/jobs/",
#     "Accept":     "application/json",
# }
# API_PARAMS = {
#     "format":     "json",
#     "skills":     "golang,go,backend",
#     "experience": "0,1",
#     "locations":  "work-from-home,bangalore,mumbai,hyderabad",
#     "limit":      50,
#     "offset":     0,
# }

# Search URLs for the Scrapling scraper — one per skill/filter combo
_SCRAPE_URLS = [
    "https://www.instahyre.com/jobs/?skills=golang&exp=0-1",
    "https://www.instahyre.com/jobs/?skills=backend&exp=0-1",
    "https://www.instahyre.com/jobs/?skills=typescript&exp=0-1",
]


from sources.utils import is_playwright_available

def fetch_instahyre() -> list[dict]:
    """
    Scrape Instahyre job listings via Scrapling (StealthyFetcher).

    The internal API (/api/v1/opportunity/) currently returns 404.
    This function goes straight to the Scrapling path.  When the API is fixed,
    restore the API-first approach from the commented block above.
    """
    if not is_playwright_available():
        logger.info("Instahyre: Playwright is unavailable (missing libcups), skipping Instahyre.")
        return []
    return _fetch_instahyre_scrapling()


def _fetch_instahyre_scrapling() -> list[dict]:
    """
    Scrape Instahyre job cards using StealthyFetcher (Playwright).

    Instahyre is a heavily JS-rendered SPA — plain HTTP fetchers won't work.
    We iterate over multiple search URLs to cover different skill filters.
    """
    from scrapling.fetchers import StealthyFetcher
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    for search_url in _SCRAPE_URLS:
        try:
            page = StealthyFetcher.fetch(
                search_url,
                headless=True,
                network_idle=True,
                timeout=30_000,  # ms — give SPA time to fully render
            )

            # Instahyre job card selectors (as of May 2026 layout)
            cards = (
                page.css(".job-card")
                or page.css(".opportunity-card")
                or page.css("[class*='JobCard']")
                or page.css("[class*='OpportunityCard']")
            )

            if not cards:
                logger.debug(f"Instahyre: no cards found at {search_url}")
                continue

            for card in cards:
                # Title
                title_el   = card.css("h2, h3, .job-title, [class*='title']")
                title      = title_el[0].text.strip() if title_el else ""

                # Company
                company_el = card.css(".company-name, [class*='company']")
                company    = company_el[0].text.strip() if company_el else ""

                # Location
                loc_el   = card.css(".location, [class*='location']")
                location = loc_el[0].text.strip() if loc_el else "India"

                # Salary / stipend
                sal_el = card.css(".salary, [class*='salary'], [class*='compensation']")
                salary = sal_el[0].text.strip() if sal_el else ""

                # Application URL — try to find an anchor with /jobs/ in href
                link_el  = card.css("a[href*='/jobs/'], a[href*='/opportunity/']")
                href     = link_el[0].attrib.get("href", "") if link_el else ""
                job_url  = (
                    ("https://www.instahyre.com" + href) if href.startswith("/") else href
                )

                if not job_url or job_url in seen_urls:
                    continue
                seen_urls.add(job_url)

                if not title:
                    continue

                jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    location,
                    "description": "",   # full JD requires a second fetch
                    "url":         job_url,
                    "source":      "instahyre",
                    "salary":      salary,
                    "posted_at":   "",
                })

        except Exception as e:
            logger.warning(f"Instahyre Scrapling failed for {search_url}: {e}")

    logger.info(f"Instahyre: {len(jobs)} jobs found via Scrapling")
    return jobs
