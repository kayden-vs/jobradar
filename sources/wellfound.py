import logging
from scrapling.fetchers import DynamicFetcher  # JS-heavy, needs full browser

logger = logging.getLogger(__name__)

SEARCH_URLS = [
    "https://wellfound.com/jobs?q=golang+backend+intern&l=India&remote=true",
    "https://wellfound.com/jobs?q=backend+engineer+intern&l=India&remote=true",
    "https://wellfound.com/jobs?q=go+developer+fresher&remote=true",
]


def fetch_wellfound() -> list[dict]:
    """
    Scrapes Wellfound using DynamicFetcher (Playwright).
    Wellfound requires JavaScript rendering and has aggressive bot detection.
    """
    all_jobs = []
    seen = set()

    fetcher = DynamicFetcher()

    for url in SEARCH_URLS:
        try:
            page = fetcher.fetch(url, headless=True, network_idle=True)
            
            # Wellfound job cards
            cards = page.css("[data-test='StartupResult'], .job-listing, [class*='JobResult']")
            
            for card in cards:
                link = card.css("a[href*='/jobs/']")
                if not link:
                    continue
                job_url = "https://wellfound.com" + link[0].attrib.get("href", "")
                if job_url in seen:
                    continue
                seen.add(job_url)
                
                title_el    = card.css("[data-test='job-title'], h2, .job-title")
                company_el  = card.css("[data-test='company-name'], .company-name")
                loc_el      = card.css("[data-test='location'], .location")
                salary_el   = card.css("[data-test='compensation'], .compensation")
                
                all_jobs.append({
                    "title":       title_el[0].text.strip() if title_el else "",
                    "company":     company_el[0].text.strip() if company_el else "",
                    "location":    loc_el[0].text.strip() if loc_el else "Remote",
                    "description": "",
                    "url":         job_url,
                    "source":      "wellfound",
                    "salary":      salary_el[0].text.strip() if salary_el else "",
                    "posted_at":   "",
                })
        except Exception as e:
            logger.warning(f"Wellfound scrape failed for {url}: {e}")

    logger.info(f"Wellfound: {len(all_jobs)} jobs found")
    return all_jobs
