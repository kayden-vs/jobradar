import os
import requests
import logging
from scrapling.fetchers import StealthyFetcher, Fetcher

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"

# --- Dork templates ---
# These are tuned specifically for:
# Backend intern/fresher + Golang + India/Remote + Fintech preferred
# Rotate through all of them in a single morning run

DORK_QUERIES = [
    # Core role dorks
    '"backend intern" OR "backend fresher" "golang" OR "go" india',
    '"software engineer intern" "go" OR "golang" "bangalore" OR "remote"',
    '"backend developer" "0-1 years" OR "fresher" "golang" india',
    '"backend engineering intern" india -site:linkedin.com -site:naukri.com',
    '"junior backend developer" "go" OR "golang" india 2025',
    
    # Fintech/crypto specific — your crypto exchange project is directly relevant
    '"backend intern" "fintech" OR "payments" OR "crypto" india',
    '"software intern" "crypto" OR "blockchain" OR "defi" "golang" OR "go"',
    '"backend engineer" "fresher" "payments" india -site:linkedin.com',
    '"go developer" intern OR fresher "india" OR "remote"',
    
    # Google Form job applications (hidden from all aggregators)
    '"docs.google.com/forms" "backend intern" "golang" OR "go"',
    '"forms.gle" "apply" "software engineer" "intern" india',
    '"google form" "backend developer" "fresher" OR "intern" india 2025',
    
    # Company career pages directly
    'intitle:"careers" "backend intern" "golang" site:*.in',
    'intitle:"join us" "backend engineer" "fresher" site:*.io',
    '"we are hiring" "backend intern" "go" OR "golang" -site:linkedin.com',
    
    # Job posted on company blog/about page
    '"now hiring" "backend" "intern" "golang" india',
    '"open position" "backend engineer" "0-1" years india',
]


def search_serper(query: str) -> list[dict]:
    """Run a single Serper.dev Google search, return list of results."""
    headers = {
        "X-API-KEY":    SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q":      query,
        "gl":     "in",           # Google India results
        "hl":     "en",
        "num":    10,
    }
    try:
        r = requests.post(SERPER_URL, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("organic", [])
    except Exception as e:
        logger.warning(f"Serper query failed '{query[:50]}': {e}")
        return []


def is_job_related_url(url: str) -> bool:
    """Quick check to avoid wasting Scrapling fetches on irrelevant pages."""
    skip_domains = ["naukri.com", "linkedin.com", "indeed.com", "glassdoor.com",
                    "shine.com", "timesjobs.com", "monsterindia.com"]
    job_signals  = ["careers", "jobs", "hiring", "apply", "forms.gle",
                    "docs.google.com/forms", "greenhouse.io", "lever.co",
                    "job", "opening", "position"]
    
    url_lower = url.lower()
    if any(d in url_lower for d in skip_domains):
        return False   # Already covered by dedicated scrapers
    return any(s in url_lower for s in job_signals)


def extract_job_from_page(url: str, title_hint: str, company_hint: str) -> dict | None:
    """
    Uses Scrapling to fetch a discovered URL and extract job details.
    Falls back to plain Fetcher for non-JS pages, uses StealthyFetcher for the rest.
    """
    try:
        # Try fast plain fetch first
        fetcher = Fetcher()
        page = fetcher.fetch(url, timeout=15)
        
        # Look for job-related content signals
        body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])
        if len(body_text) < 200:
            # Page might need JS — retry with stealthy browser
            fetcher2 = StealthyFetcher()
            page = fetcher2.fetch(url, headless=True, network_idle=True)
            body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])
        
        # If it's a Google Form, extract the form title and description
        if "docs.google.com/forms" in url or "forms.gle" in url:
            return {
                "title":       title_hint,
                "company":     company_hint,
                "location":    "India (Google Form)",
                "description": body_text[:3000],
                "url":         url,
                "source":      "serper_google_form",
                "salary":      "",
                "posted_at":   "",
            }
        
        return {
            "title":       title_hint,
            "company":     company_hint,
            "location":    _extract_location(body_text),
            "description": body_text[:5000],  # First 5000 chars for AI
            "url":         url,
            "source":      "serper",
            "salary":      _extract_salary(body_text),
            "posted_at":   "",
        }
    except Exception as e:
        logger.warning(f"Failed to extract job from {url}: {e}")
        return None


def _extract_location(text: str) -> str:
    """Simple heuristic to find location in job description."""
    keywords = ["remote", "bangalore", "bengaluru", "mumbai", "hyderabad",
                "delhi", "ncr", "pune", "chennai", "kolkata", "india"]
    text_lower = text.lower()
    found = [k.title() for k in keywords if k in text_lower]
    return " / ".join(found[:3]) if found else "Not specified"


def _extract_salary(text: str) -> str:
    """Simple heuristic to extract salary info."""
    import re
    patterns = [
        r'\₹[\d,]+\s*[-–]\s*₹[\d,]+',
        r'[\d]+\s*[-–]\s*[\d]+\s*LPA',
        r'[\d]+k\s*[-–]\s*[\d]+k\s*per\s*month',
        r'stipend.*?₹[\d,]+',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return ""


def fetch_serper_jobs() -> list[dict]:
    """Main function: runs all dork queries and extracts jobs from discovered pages."""
    all_jobs = []
    seen_urls = set()
    
    for query in DORK_QUERIES:
        results = search_serper(query)
        
        for result in results:
            url   = result.get("link", "")
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            
            if not url or url in seen_urls:
                continue
            if not is_job_related_url(url):
                continue
                
            seen_urls.add(url)
            
            # Extract company name heuristic from URL or title
            company = _guess_company(url, title)
            
            job = extract_job_from_page(url, title, company)
            if job:
                all_jobs.append(job)
    
    logger.info(f"Serper discovery: {len(all_jobs)} jobs extracted")
    return all_jobs


def _guess_company(url: str, title: str) -> str:
    """Best-effort company name extraction from URL."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    domain = domain.replace("www.", "").split(".")[0]
    return domain.title()
