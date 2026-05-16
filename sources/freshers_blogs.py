"""
sources/freshers_blogs.py — Indian fresher job blog aggregator

Strategy (lazy-fetch / listing-only):
  These sites are high-volume WordPress blogs that cross-post the same jobs.
  We scrape ONLY the RSS feed for each site — no individual post fetching upfront.
  - RSS entries give us: title, url, published date, and summary/excerpt.
  - title_parser() extracts company, location, role from the standard blog title format.
  - fetch_full_description(url) is a helper the scorer calls when description is empty.
  - Deduplication is handled downstream by pipeline/dedup.py (hash of title+company+location).

Concurrency:
  feedparser calls are blocking (sync HTTP). We run all RSS sources concurrently
  using ThreadPoolExecutor, then collect results into a single list.

Sites (all RSS):
  offcampusjobs4u, freshershunt, freshersnow, freshersdunia,
  fresheropenings, tnpofficer, freshers-job, freshersarea

NOTE — Cuvette.tech removed: /app/student/find-jobs requires a student session
  cookie. Without auth, the SPA returns a login redirect (connection-level timeout).
"""

import re
import logging
import feedparser
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# RSS FEED REGISTRY
# ─────────────────────────────────────────────────────────────────

# Validated feed URLs — live-tested May 2026. Category slugs may differ per site;
# always verify with feedparser before adding.
RSS_FEEDS: list[tuple[str, str]] = [
    # (source_label, feed_url)

    # offcampusjobs4u: category/offcampus-jobs covers both off-campus + internship posts.
    # category/internship/feed/ confirmed 0 entries — removed.
    ("offcampusjobs4u", "https://offcampusjobs4u.com/category/offcampus-jobs/feed/"),

    # freshershunt: category feeds /off-campus-drives/ and /internship-for-freshers/
    # both return 0 entries. Root /feed/ works (1-few entries, slow site).
    ("freshershunt",    "https://freshershunt.in/feed/"),

    # freshersnow: /jobs/it-software/ and /jobs/internships/ return HTTP 404.
    # Root /feed/ returns 20 entries (mix of IT + exam prep noise; prefilter handles).
    ("freshersnow",     "https://freshersnow.com/feed/"),

    ("freshersdunia",   "https://freshersdunia.in/feed/"),
    ("fresheropenings", "https://fresheropenings.com/feed/"),
    ("tnpofficer",      "https://tnpofficer.com/category/off-campus/feed/"),
    ("freshers-job",    "https://freshers-job.com/feed/"),
    ("freshersarea",    "https://freshersarea.in/feed/"),

    # placementninja.in: HTTP 404 / bozo, 0 entries — confirmed dead, excluded.
    # Re-test: curl -o /dev/null -s -w "%{http_code}" https://placementninja.in/feed/
]

# FEEDPARSER USER-AGENT
# WordPress and Cloudflare-protected blogs block the default Python/feedparser UA.
# A browser-like UA is needed to get the actual RSS XML (without it, some sites
# return an HTML 403/404 page which feedparser fails to parse — "syntax error at line 2").
_RSS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# NOTE: Cuvette.tech (/app/student/find-jobs) is an auth-gated React SPA.
# The URL requires a valid student session cookie — headless browsers without
# credentials will hit a login redirect or connection hang. Removed from sources.
# TODO: If Cuvette adds a public job listing API, re-add it here.

# ─────────────────────────────────────────────────────────────────
# TITLE PARSER
# ─────────────────────────────────────────────────────────────────

# These blogs follow patterns like:
#   "Razorpay Recruitment 2025: Backend Engineer | Bangalore | Batch 2025"
#   "Juspay Internship 2024 | Golang Developer | Hyderabad | 2024 Batch"
#   "TCS Off Campus Drive 2025 | Software Engineer | Pan India"
#   "Infosys Freshers Jobs 2025 – Backend Developer – Pune"
_TITLE_RE = re.compile(
    r'^(?P<company>[A-Za-z0-9][\w\s&./,-]+?)'        # Company name (leading)
    r'\s*(?:'
        r'(?:recruitment|hiring|off\s*campus|drive|'
        r'freshers?\s*jobs?|jobs?|internship|intern\b)'
        r'[\s\d:–-]*'
    r')'
    r'(?:[|:–-]\s*)?'
    r'(?P<role>[^|–-]+?)?'                             # Role (optional)
    r'(?:\s*[|–-]\s*(?P<location>[^|–-]+?))?'         # Location (optional)
    r'(?:\s*[|–-]\s*.+)?$',                            # remainder (batch, etc.)
    re.IGNORECASE,
)

# Words that are NOT company names — strip from the parsed company token
_COMPANY_BLACKWORDS = {
    "recruitment", "off campus", "offcampus", "hiring", "drive",
    "freshers", "fresher", "jobs", "job", "internship", "intern",
    "batch", "2024", "2025", "2026", "it", "limited", "ltd",
}

_LOCATION_HINTS = [
    # Major Indian tech cities
    "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "mumbai",
    "delhi", "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad",
    "indore", "coimbatore", "kochi", "jaipur", "bhubaneswar", "nagpur",
    # Generic
    "pan india", "india", "remote", "work from home", "wfh",
]


def title_parser(title: str) -> dict:
    """
    Parse an Indian fresher blog post title into {company, role, location}.

    Handles the common formats:
      "Company Recruitment YEAR: Role | City | Batch YEAR"
      "Company Internship | Role | City"
      "Company Off Campus Drive | Role – City"

    Returns a dict with lowercase-stripped values. Empty strings if not found.
    """
    result = {"company": "", "role": "", "location": ""}
    if not title:
        return result

    cleaned = title.strip()

    # ── Try regex match first ──────────────────────────────────────
    m = _TITLE_RE.match(cleaned)
    if m:
        company  = (m.group("company")  or "").strip()
        role     = (m.group("role")     or "").strip()
        location = (m.group("location") or "").strip()

        # Trim trailing noise from company
        company = re.sub(
            r'\s*(recruitment|hiring|off\s*campus|drive|jobs?|internship|intern).*$',
            '', company, flags=re.IGNORECASE
        ).strip(" –-|:")

        result["company"]  = company.lower().strip()
        result["role"]     = role.lower().strip()
        result["location"] = location.lower().strip()

    # ── Fallback: scan title for known city names ──────────────────
    if not result["location"]:
        title_lower = cleaned.lower()
        for hint in _LOCATION_HINTS:
            if hint in title_lower:
                result["location"] = hint
                break

    # ── Normalise "work from home" / "wfh" → "remote" ─────────────
    loc = result["location"]
    if loc in ("work from home", "wfh"):
        result["location"] = "remote"

    return result


# ─────────────────────────────────────────────────────────────────
# LAZY DESCRIPTION FETCH (called by scorer when description is empty)
# ─────────────────────────────────────────────────────────────────

def fetch_full_description(url: str) -> str:
    """
    Fetch the full body text of a single WordPress blog post.

    Called by the scorer ONLY for jobs that survived pre-filter and have
    description under 100 chars. Uses Scrapling's plain Fetcher (sync HTTP)
    since all these sites are standard WordPress (server-side rendered).

    Targets .entry-content / .post-content — the main article body div —
    to avoid pulling in nav/sidebar/footer noise.
    Returns extracted text truncated to 4000 chars, or "" on failure.
    """
    from scrapling.fetchers import Fetcher
    try:
        page = Fetcher().get(url, timeout=10)

        # Try focused selectors first — avoids nav/sidebar/footer noise
        for selector in (".entry-content", ".post-content", ".article-content",
                         "article", ".job-description", "main"):
            els = page.css(selector)
            if els:
                text = els[0].get_all_text() if hasattr(els[0], "get_all_text") else els[0].text
                text = re.sub(r'\s+', ' ', text or "").strip()
                if len(text) > 200:
                    return text[:4000]

        # Fallback: full page minus boilerplate tags
        text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer", "header"])
        text = re.sub(r'\s+', ' ', text or "").strip()
        return text[:4000] if len(text) > 200 else ""

    except Exception as e:
        logger.debug(f"fetch_full_description failed for {url}: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────
# DATE HELPER
# ─────────────────────────────────────────────────────────────────

def _parse_rss_date(entry) -> str:
    """
    Extract an ISO8601 date string from a feedparser entry.
    Tries published_parsed → published → updated_parsed → updated → "".
    Returns "" if nothing is parseable (prefilter gives benefit-of-the-doubt).
    """
    # feedparser gives us a time.struct_time in *_parsed fields
    if getattr(entry, "published_parsed", None):
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    # Raw RFC 2822 string fallback
    raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt.isoformat()
        except Exception:
            pass

    if getattr(entry, "updated_parsed", None):
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    return ""


# ─────────────────────────────────────────────────────────────────
# RSS FEED FETCHER
# ─────────────────────────────────────────────────────────────────

def _fetch_rss(source_label: str, feed_url: str) -> list[dict]:
    """
    Fetch a single RSS feed and return a list of partial job dicts.
    Only listing-level data is extracted — no individual post fetching.

    Tags extracted from entry.tags (WordPress category metadata) are stored
    as structured lists so the prefilter can do zero-cost rejection before
    any HTTP fetch or AI call.
    """
    jobs: list[dict] = []

    try:
        feed = feedparser.parse(feed_url, agent=_RSS_UA)

        if feed.bozo and not feed.entries:
            # True bozo with zero entries = completely unparseable feed
            logger.warning(f"[{source_label}] RSS unparseable for {feed_url}: {feed.get('bozo_exception')}")
            return jobs
        if feed.bozo:
            # Partially broken XML — feedparser still recovered some entries, log and continue
            logger.debug(f"[{source_label}] RSS bozo (partial parse) for {feed_url}: {feed.get('bozo_exception')}")

        for entry in feed.entries:
            raw_title = (getattr(entry, "title", "") or "").strip()
            if not raw_title:
                continue

            url = (getattr(entry, "link", "") or "").strip()

            # Grab any available excerpt / summary — do NOT fetch the full post
            summary = ""
            if hasattr(entry, "summary"):
                summary = re.sub(r'<[^>]+>', ' ', entry.summary or "")
                summary = re.sub(r'\s+', ' ', summary).strip()[:500]
            elif hasattr(entry, "content") and entry.content:
                raw_content = entry.content[0].get("value", "")
                summary = re.sub(r'<[^>]+>', ' ', raw_content)
                summary = re.sub(r'\s+', ' ', summary).strip()[:500]

            posted_at = _parse_rss_date(entry)

            # ── WordPress category tags ───────────────────────────────────────
            # feedparser exposes WordPress categories as entry.tags — a list of
            # dicts with a "term" key. Rich metadata at zero extra HTTP cost.
            tags: list[str] = [t.get("term", "") for t in getattr(entry, "tags", [])]
            tags_lower = [t.lower() for t in tags]

            _INDIA_CITIES = {
                "bangalore", "bengaluru", "mumbai", "hyderabad", "delhi",
                "remote", "work from home", "pan india", "wfh",
            }

            experience_tags = [t for t in tags if
                               any(x in t.lower() for x in ("year", "0-1", "0 - 1"))]
            batch_tags      = [t for t in tags if
                               any(x in t.lower() for x in ("batch", "2024", "2025", "2026"))]
            location_tags   = [t for t in tags if
                               any(city in t.lower() for city in _INDIA_CITIES)]
            # role_tags = all tags (full list for prefilter inspection)
            role_tags = tags

            # Parse company / location / role from title
            parsed = title_parser(raw_title)
            company  = parsed["company"]  or source_label
            location = parsed["location"] or "india"

            # Prefer tag-derived location if title parser didn't find one
            if location == "india" and location_tags:
                location = location_tags[0].lower()

            jobs.append({
                "title":           raw_title,
                "company":         company,
                "location":        location,
                "description":     summary,   # empty/partial — scorer lazy-fetches if needed
                "url":             url,
                "source":          f"freshers_blogs/{source_label}",
                "salary":          "",
                "posted_at":       posted_at,
                # Tag metadata for zero-cost prefilter checks
                "experience_tags": experience_tags,
                "batch_tags":      batch_tags,
                "location_tags":   location_tags,
                "role_tags":       role_tags,
            })

    except Exception as e:
        logger.warning(f"[{source_label}] RSS fetch failed ({feed_url}): {e}")

    return jobs



# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_freshers_blogs() -> list[dict]:
    """
    Aggregate all Indian fresher job blog sources concurrently.

    Strategy:
      - All RSS feeds run concurrently via ThreadPoolExecutor (feedparser is blocking).
      - Individual source failures are caught silently (logger.warning).
      - Within-batch deduplication by URL to collapse exact cross-posts.
      - Final deduplication by title+company+location handled by pipeline/dedup.py.

    Returns:
      Combined list of partial job dicts (description may be empty —
      scorer calls fetch_full_description(url) for jobs that pass pre-filter).
    """
    all_jobs: list[dict] = []
    seen_urls: set[str]  = set()

    def _rss_task(label: str, url: str) -> list[dict]:
        result = _fetch_rss(label, url)
        logger.info(f"[{label}] {len(result)} entries from {url}")
        return result

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {
            executor.submit(_rss_task, label, url): label
            for label, url in RSS_FEEDS
        }

        for future in as_completed(future_map):
            label = future_map[future]
            try:
                batch = future.result()
                for job in batch:
                    job_url = job.get("url", "")
                    if job_url and job_url in seen_urls:
                        continue  # Exact URL duplicate (same post, different feed)
                    if job_url:
                        seen_urls.add(job_url)
                    all_jobs.append(job)
            except Exception as e:
                logger.warning(f"[{label}] Source future raised: {e}")

    logger.info(
        f"freshers_blogs: {len(all_jobs)} total listings from {len(RSS_FEEDS)} RSS feeds"
    )
    return all_jobs
