import os
import requests
import logging
from scrapling.fetchers import StealthyFetcher, Fetcher

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"

# Hard cap: never spend more than this many Serper API credits per run.
# Free tier = 2,500/month. At 15/day × 30 days = 450/month. Well within limit.
# Each query costs exactly 1 credit regardless of how many results it returns.
MAX_SERPER_CALLS = 15

# --- Dork templates ---
# These are tuned specifically for:
# Backend intern/fresher + Golang + India/Remote + Fintech preferred
# NOTE: We use at most MAX_SERPER_CALLS from this list per run.
DORK_QUERIES = [
    # Core role dorks
    '"backend intern" OR "backend fresher" "golang" OR "go" india',
    '"software engineer intern" "go" OR "golang" "bangalore" OR "remote"',
    '"backend developer" "0-1 years" OR "fresher" "golang" india',
    '"backend engineering intern" india -site:linkedin.com -site:naukri.com',
    '"junior backend developer" "go" OR "golang" india 2026',

    # Fintech/crypto specific — crypto exchange project is directly relevant
    '"backend intern" "fintech" OR "payments" OR "crypto" india',
    '"software intern" "crypto" OR "blockchain" OR "defi" "golang" OR "go"',
    '"backend engineer" "fresher" "payments" india -site:linkedin.com',
    '"go developer" intern OR fresher "india" OR "remote"',

    # Google Form job applications (hidden from all aggregators)
    '"docs.google.com/forms" "backend intern" "golang" OR "go"',
    '"forms.gle" "apply" "software engineer" "intern" india',
    '"google form" "backend developer" "fresher" OR "intern" india 2026',

    # Company career pages directly
    'intitle:"careers" "backend intern" "golang" site:*.in',
    'intitle:"join us" "backend engineer" "fresher" site:*.io',
    '"we are hiring" "backend intern" "go" OR "golang" -site:linkedin.com',
]
# Trim to cap at runtime (not here) so list stays readable


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
    - Plain Fetcher uses .get() (HTTP only, fast, for static pages)
    - StealthyFetcher uses .fetch() (browser, for JS/protected pages)
    Adds a small delay between fetches to avoid rate-limiting.
    """
    import time
    time.sleep(1)  # Rate limit: 1 req/sec to avoid getting blocked

    try:
        # Try fast plain HTTP request first (Fetcher uses .get(), NOT .fetch())
        fetcher = Fetcher()
        page = fetcher.get(url, timeout=15)

        # Look for job-related content signals
        body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])
        if len(body_text) < 200:
            # Page might need JS rendering — retry with stealthy browser
            time.sleep(2)  # Extra delay before browser fetch
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
            "description": body_text[:5000],
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
    """
    Main function: runs dork queries (capped at MAX_SERPER_CALLS) and
    extracts the full job description from each discovered page using Scrapling.
    
    Serper budget: each query = 1 API credit. With MAX_SERPER_CALLS=15 and
    30 days/month that's 450 credits/month, well within the 2,500 free tier.
    
    JD fetching: after Serper returns URLs, we visit each one with Scrapling
    and extract all visible text. This IS the job description that gets scored.
    """
    all_jobs = []
    seen_urls = set()
    queries_run = 0

    for query in DORK_QUERIES[:MAX_SERPER_CALLS]:  # Hard cap
        results = search_serper(query)
        queries_run += 1

        for result in results:
            url     = result.get("link", "")
            title   = result.get("title", "")

            if not url or url in seen_urls:
                continue
            if not is_job_related_url(url):
                continue

            seen_urls.add(url)

            company = _guess_company(url, title)
            job = extract_job_from_page(url, title, company)
            if job:
                all_jobs.append(job)

    logger.info(f"Serper discovery: {queries_run} queries, {len(all_jobs)} jobs extracted")
    return all_jobs


def _guess_company(url: str, title: str) -> str:
    """Best-effort company name extraction from URL."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    domain = domain.replace("www.", "").split(".")[0]
    return domain.title()
