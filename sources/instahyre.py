import requests
import logging

logger = logging.getLogger(__name__)

# Instahyre's internal API (found via DevTools Network tab)
# This is a public, unauthenticated endpoint
INSTAHYRE_API = "https://www.instahyre.com/api/v1/opportunity/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer":    "https://www.instahyre.com/jobs/",
    "Accept":     "application/json",
}

# Skills to search for (maps to Instahyre's skill filter)
SKILL_IDS = {
    "golang": "go",
    "python": "python",
    "backend": "backend",
}

def fetch_instahyre() -> list[dict]:
    """
    Fetches job listings from Instahyre's internal API.
    If this API changes, fall back to the Scrapling approach below.
    """
    all_jobs = []
    
    params = {
        "format":        "json",
        "skills":        "golang,go,backend",
        "experience":    "0,1",          # 0 to 1 year
        "locations":     "work-from-home,bangalore,mumbai,hyderabad",
        "limit":         50,
        "offset":        0,
    }
    
    try:
        r = requests.get(INSTAHYRE_API, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        for job in data.get("results", []):
            company = job.get("company", {})
            role = job.get("role", {})
            
            all_jobs.append({
                "title":       role.get("title", ""),
                "company":     company.get("name", ""),
                "location":    job.get("location_display", "India"),
                "description": job.get("description", "") or role.get("description", ""),
                "url":         f"https://www.instahyre.com/jobs/{job.get('id', '')}",
                "source":      "instahyre",
                "salary":      f"{job.get('salary_min', '')} - {job.get('salary_max', '')} LPA",
                "posted_at":   job.get("created", ""),
            })
    except Exception as e:
        logger.warning(f"Instahyre API fetch failed: {e}")
        # Fallback: use Scrapling if the API changes
        return _fetch_instahyre_scrapling()
    
    logger.info(f"Instahyre: {len(all_jobs)} jobs found")
    return all_jobs


def _fetch_instahyre_scrapling() -> list[dict]:
    """Fallback: scrape Instahyre with Scrapling if the API breaks"""
    from scrapling.fetchers import StealthyFetcher
    jobs = []
    try:
        fetcher = StealthyFetcher()
        fetcher.configure(headless=True, network_idle=True)
        page = fetcher.fetch(
            "https://www.instahyre.com/jobs/?skills=golang&exp=0-1"
        )
        cards = page.css(".job-card, .opportunity-card")
        for card in cards:
            title_el = card.css("h2, h3, .job-title")
            company_el = card.css(".company-name")
            jobs.append({
                "title":       title_el[0].text if title_el else "",
                "company":     company_el[0].text if company_el else "",
                "location":    "India",
                "description": "",
                "url":         "",
                "source":      "instahyre",
                "salary":      "",
                "posted_at":   "",
            })
    except Exception as e:
        logger.error(f"Instahyre Scrapling fallback also failed: {e}")
    return jobs
