import logging
from scrapling.fetchers import StealthyFetcher

logger = logging.getLogger(__name__)

# Search queries tuned for backend intern/fresher in India
CUTSHORT_QUERIES = [
    "golang backend intern",
    "backend developer intern",
    "software engineer intern golang",
    "backend engineer fresher",
    "go developer fresher india",
    "backend intern fintech",
    "full stack intern golang",
]

BASE_URL = "https://cutshort.io/jobs"


def fetch_cutshort() -> list[dict]:
    """
    Scrapes Cutshort job search results.
    Cutshort uses client-side rendering, so we need StealthyFetcher.
    """
    all_jobs = []
    seen_urls = set()
    
    for query in CUTSHORT_QUERIES:
        try:
            search_url = f"{BASE_URL}?q={query.replace(' ', '+')}&remote=true"
            page = StealthyFetcher.fetch(search_url, headless=True, network_idle=True)
            
            # Job cards on Cutshort
            job_cards = page.css(".job-card") or page.css("[data-testid='job-card']")
            
            for card in job_cards:
                url_el = card.css("a[href*='/jobs/']")
                if not url_el:
                    continue
                    
                url = "https://cutshort.io" + url_el[0].attrib.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                title = card.css(".job-title, h3, [data-testid='job-title']")
                company = card.css(".company-name, [data-testid='company-name']")
                location = card.css(".location, [data-testid='location']")
                salary = card.css(".salary, [data-testid='salary']")
                
                all_jobs.append({
                    "title":       title[0].text if title else "",
                    "company":     company[0].text if company else "",
                    "location":    location[0].text if location else "India",
                    "description": "",   # fetch full JD separately if passes pre-filter
                    "url":         url,
                    "source":      "cutshort",
                    "salary":      salary[0].text if salary else "",
                    "posted_at":   "",
                })
        except Exception as e:
            logger.warning(f"Cutshort scrape failed for query '{query}': {e}")
    
    logger.info(f"Cutshort: {len(all_jobs)} jobs found")
    return all_jobs


def fetch_job_description(url: str) -> str:
    """
    Fetches the full JD for a Cutshort job listing.
    Called only for jobs that pass the pre-filter.
    """
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        desc = page.css(".job-description, [data-testid='job-description']")
        return desc[0].text if desc else ""
    except Exception as e:
        logger.warning(f"Failed to fetch Cutshort JD for {url}: {e}")
        return ""
