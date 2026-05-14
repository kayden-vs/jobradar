import os
import requests
import logging
from scrapling.fetchers import StealthyFetcher, Fetcher

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"

# Hard cap: never spend more than this many Serper API credits per run.
# Free tier = 2,500/month. At 20/day × 30 days = 600/month. Well within limit.
MAX_SERPER_CALLS = 20

# --- Dork templates ---
# Tuned for: Backend intern/fresher + Go OR TypeScript/Node.js + India/Remote
# NOT just Golang — broad backend coverage now.
DORK_QUERIES = [
    # ── General Backend (broad) ──────────────────────────────────────────────
    '"backend intern" OR "backend fresher" india site:*.in OR site:*.io OR site:*.co',
    '"backend engineering intern" india -site:linkedin.com -site:naukri.com',
    '"backend developer" "0-1 years" OR "fresher" OR "intern" india 2026',
    '"software engineer intern" "backend" OR "api" india -site:linkedin.com',
    '"junior backend developer" india 2026',
    '"SDE intern" OR "software developer intern" "backend" india',

    # ── Golang specific ──────────────────────────────────────────────────────
    '"backend intern" OR "backend fresher" "golang" OR "go" india',
    '"go developer" intern OR fresher india OR remote',
    '"software engineer intern" "go" OR "golang" "bangalore" OR "remote"',

    # ── TypeScript / Node.js specific ────────────────────────────────────────
    '"backend intern" OR "backend fresher" "typescript" OR "node.js" OR "nodejs" india',
    '"node.js intern" OR "nodejs intern" india OR remote',
    '"typescript backend" intern OR fresher india',
    '"backend developer" "typescript" OR "node" "0-1 years" OR "fresher" india',

    # ── Fintech / Crypto (your strongest project signal) ─────────────────────
    '"backend intern" "fintech" OR "payments" OR "crypto" india',
    '"software intern" "crypto" OR "blockchain" OR "defi" india',
    '"backend engineer" "fresher" "payments" OR "fintech" india -site:linkedin.com',

    # ── Google Form applications (hidden from aggregators) ───────────────────
    '"docs.google.com/forms" "backend intern" india',
    '"forms.gle" "apply" "software engineer" "intern" india',

    # ── Company career pages directly ────────────────────────────────────────
    'intitle:"careers" "backend intern" OR "backend fresher" site:*.in',
    '"we are hiring" "backend" "intern" OR "fresher" india -site:linkedin.com',
    'intitle:"join us" "backend engineer" "fresher" site:*.io',
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
                    "shine.com", "timesjobs.com", "monsterindia.com",
                    "internshala.com",  # covered by dedicated source
                    "hirist.tech",      # covered by dedicated source (if enabled)
                    ]
    job_signals  = ["careers", "jobs", "hiring", "apply", "forms.gle",
                    "docs.google.com/forms", "greenhouse.io", "lever.co",
                    "job", "opening", "position", "vacancy"]

    url_lower = url.lower()
    if any(d in url_lower for d in skip_domains):
        return False   # Already covered by dedicated scrapers
    return any(s in url_lower for s in job_signals)


def extract_job_from_page(url: str, title_hint: str, company_hint: str) -> dict | None:
    """
    Uses Scrapling to fetch a discovered URL and extract job details.
    - Plain Fetcher: fast HTTP-only, works for static pages
    - StealthyFetcher: headless browser, for JS-rendered / bot-protected pages
    """
    import time
    time.sleep(1)  # Rate limit: 1 req/sec

    try:
        page = Fetcher().get(url, timeout=15)
        body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])

        if len(body_text) < 200:
            time.sleep(2)
            page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
            body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])

        # Google Form: just return title + description text
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
                "delhi", "ncr", "pune", "chennai", "kolkata", "india",
                "work from home", "wfh", "hybrid"]
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
    extracts the full job description from each discovered page.

    Serper budget: each query = 1 credit.
    20 queries/day × 30 days = 600 credits/month (free tier: 2,500/month).
    """
    all_jobs = []
    seen_urls = set()
    queries_run = 0

    for query in DORK_QUERIES[:MAX_SERPER_CALLS]:
        results = search_serper(query)
        queries_run += 1

        for result in results:
            url   = result.get("link", "")
            title = result.get("title", "")

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
