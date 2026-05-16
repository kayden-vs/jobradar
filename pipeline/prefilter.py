import yaml
import re
import logging
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)


def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────
# DATE PARSING — handles ISO strings AND relative strings
# ─────────────────────────────────────────────────────────────────

def _parse_posted_at(posted_at: str) -> datetime | None:
    """
    Parse a posted_at string into a timezone-aware datetime.

    Handles:
      - ISO 8601 / RFC 2822 strings  (dateutil)
      - Relative strings:
          "2 days ago", "3 weeks ago", "1 month ago", "2 months ago",
          "Posted 3 days ago", "about 2 weeks ago", "an hour ago"
      - Unix epoch integers stored as strings (Lever uses ms)
    """
    if not posted_at:
        return None

    s = str(posted_at).strip()

    # --- Relative date strings ---
    # normalise: lowercase, strip leading "posted", "about", "over", "almost"
    s_lower = re.sub(r'^(posted|about|over|almost|around)\s+', '', s.lower()).strip()

    # "an hour ago" / "a day ago" / "a week ago" / "a month ago"
    s_lower = re.sub(r'\ban?\b', '1', s_lower)

    patterns = [
        # "X minutes ago"
        (r'(\d+)\s*minute[s]?\s+ago', lambda m: timedelta(minutes=int(m.group(1)))),
        # "X hours ago"
        (r'(\d+)\s*hour[s]?\s+ago',   lambda m: timedelta(hours=int(m.group(1)))),
        # "X days ago"
        (r'(\d+)\s*day[s]?\s+ago',    lambda m: timedelta(days=int(m.group(1)))),
        # "X weeks ago"
        (r'(\d+)\s*week[s]?\s+ago',   lambda m: timedelta(weeks=int(m.group(1)))),
        # "X months ago"  — approximate as 30 days each
        (r'(\d+)\s*month[s]?\s+ago',  lambda m: timedelta(days=int(m.group(1)) * 30)),
        # "X years ago"
        (r'(\d+)\s*year[s]?\s+ago',   lambda m: timedelta(days=int(m.group(1)) * 365)),
    ]
    now = datetime.now(timezone.utc)
    for pattern, delta_fn in patterns:
        m = re.search(pattern, s_lower)
        if m:
            return now - delta_fn(m)

    # --- Unix epoch (ms or s) ---
    if re.fullmatch(r'\d{10,13}', s):
        ts = int(s)
        if ts > 1e12:   # milliseconds
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass

    # --- ISO / RFC / anything dateutil can handle ---
    try:
        dt = dateutil_parser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        logger.debug(f"Could not parse posted_at: '{s}'")
        return None


# ─────────────────────────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ─────────────────────────────────────────────────────────────────

# Phrases that appear verbatim in post titles or body when a job is closed.
_CLOSED_PHRASES = re.compile(
    r'application[s]?\s*(is\s*)?(now\s*)?closed'
    r'|hiring\s*(is\s*)?(now\s*)?closed'
    r'|recruitment\s+closed'
    r'|position[s]?\s*(has\s+been\s*|is\s*|have\s+been\s*)?filled'
    r'|no\s+longer\s+accepting'
    r'|vacancy\s+closed'
    r'|this\s+(job|position|role)\s+is\s+(no\s+longer|closed|filled)'
    r'|we\s+are\s+no\s+longer\s+hiring'
    r'|drive\s+(is\s+)?over'
    r'|hiring\s+(is\s+)?closed',
    re.IGNORECASE,
)

# Context words that precede a deadline date.
_DEADLINE_CONTEXT_RE = re.compile(
    r'(?:last\s+date(?:\s+to\s+apply)?'
    r'|apply\s+before'
    r'|application\s+(?:last\s+)?deadline'
    r'|closing\s+date'
    r'|application\s+close[sd]?\s+(?:on|date)'
    r')[:\s]+([A-Za-z]+\s+\d{1,2},?\s+20\d{2}'
    r'|\d{1,2}[\s/-][A-Za-z]+[\s/-]20\d{2}'
    r'|\d{1,2}[/-]\d{1,2}[/-]20\d{2}'
    r'|20\d{2}-\d{2}-\d{2})',
    re.IGNORECASE,
)


def check_expiry_signals(job: dict) -> tuple[bool, str]:
    """
    Reject jobs where the title or RSS summary contains clear evidence
    that the opening is already closed or the application deadline has passed.

    This catches the majority of stale freshers blog posts without any HTTP
    fetch or AI token spend.

    Two signals checked:
      1. Hard closure phrases: "application closed", "position filled", etc.
      2. Explicit deadline dates: "Last Date: March 15, 2025" — extracted
         and compared to today.
    """
    title = job.get("title", "")
    desc  = job.get("description", "")
    text  = title + " " + desc

    # ── Hard closure phrases ───────────────────────────────────────────────
    m = _CLOSED_PHRASES.search(text)
    if m:
        return True, f"Application closed signal: '{m.group(0).strip()}'"

    # ── Explicit past deadline ────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    for m in _DEADLINE_CONTEXT_RE.finditer(text):
        date_str = m.group(1).strip()
        try:
            deadline = dateutil_parser.parse(date_str, dayfirst=False)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            if deadline < now:
                return True, f"Application deadline passed: '{date_str}'"
        except Exception:
            pass  # Unparseable date — don't reject, let AI judge

    return False, ""


def check_experience(description: str, title: str, profile: dict) -> tuple[bool, str]:
    """
    Reject if any hard-reject experience keyword is found, or if a regex
    pattern detects 2+ years experience requirement.
    """
    text = (description + " " + title).lower()

    for kw in (profile["hard_reject"].get("experience_keywords") or []):
        if kw.lower() in text:
            return True, f"Experience keyword: '{kw}'"

    year_patterns = [
        r'\b([2-9]|\d{2,})\+?\s*years?\s*(of\s+)?(experience|exp)\b',
        r'experience[:\s]+([2-9]|\d{2,})\+?\s*years?',
        r'minimum\s+([2-9]|\d{2,})\s*years?',
        r'([2-9]|\d{2,})\+\s*yrs?\b',
    ]
    for pat in year_patterns:
        m = re.search(pat, text)
        if m:
            return True, f"Experience regex: {m.group(0)}"

    return False, ""


def check_location(description: str, title: str, profile: dict) -> tuple[bool, str]:
    """Reject jobs that are explicitly outside India in-office only."""
    text = (description + " " + title).lower()
    for loc_kw in (profile["candidate"]["location"].get("hard_reject") or []):
        if loc_kw.lower() in text:
            return True, f"Location rejected: '{loc_kw}'"
    return False, ""


def check_company_blacklist(company: str, profile: dict) -> tuple[bool, str]:
    blacklist = [c.lower() for c in (profile["hard_reject"].get("company_blacklist") or [])]
    if company.lower() in blacklist:
        return True, f"Company blacklisted: {company}"
    return False, ""


def check_role_blacklist(title: str, profile: dict) -> tuple[bool, str]:
    title_lower = title.lower()
    for role in (profile["hard_reject"].get("role_blacklist") or []):
        if role.lower() in title_lower:
            return True, f"Role blacklisted: {role}"
    return False, ""


def check_candidate_post(job: dict) -> tuple[bool, str]:
    """Filter out candidate posts (people looking for work, not companies hiring)."""
    if job.get("company", "") == "CANDIDATE_POST":
        return True, "Candidate post"
    title = job.get("title", "").lower()
    # Common patterns for people seeking work (not job postings)
    candidate_signals = [
        "[for hire]", "seeking", "looking for work", "open to work",
        "available for", "hire me", "i am looking", "i'm looking",
        "[seeking]", "need a job", "job seeker",
    ]
    for sig in candidate_signals:
        if title.startswith(sig) or sig in title:
            return True, f"Candidate post signal: '{sig}'"
    return False, ""


def check_has_meaningful_title(job: dict) -> tuple[bool, str]:
    """Reject jobs with no or empty title."""
    title = job.get("title", "").strip()
    if len(title) < 3:
        return True, "Missing or empty title"
    return False, ""


def check_title_relevance(title: str) -> tuple[bool, str]:
    """
    Tighter positive filter — job title must contain at least one
    signal word indicating it could be a backend/software role.
    This blocks obvious non-tech roles (HR, marketing, sales, designer)
    before they reach the AI scorer.
    """
    title_lower = title.lower()

    # ── Block Glassdoor/job-board aggregate listing page titles ────────────
    # Pattern: starts with a number followed by job-count phrasing
    if re.match(r'^\d[\d,]* ', title_lower):
        return True, "Aggregate listing page title (starts with count)"

    # ── Block YC navigation category pages ────────────────────────────────
    # Pattern: "Jobs in [City]", "[Category] Jobs", "Remote [Category] Jobs"
    yc_nav_patterns = [
        r'^jobs in ',              # "Jobs in Los Angeles"
        r'^remote .+ jobs$',       # "Remote Design & UI/UX Jobs"
        r'^software engineer jobs in ',  # "Software Engineer Jobs in Los Angeles"
        r'^recruiting jobs in ',
    ]
    for pat in yc_nav_patterns:
        if re.match(pat, title_lower):
            return True, f"YC navigation/category page title: '{title}'"

    # Any of these in the title = keep it
    keep_signals = [
        "backend", "software", "engineer", "developer", "dev",
        "intern", "fresher", "full stack", "fullstack", "sde",
        "golang", "go ", " go,", "python", "api", "server",
        "platform", "infrastructure", "data engineer", "typescript",
        "node", "node.js", "nodejs", "systems", "cloud", "devops",
        "ml engineer", "swe",
    ]
    for signal in keep_signals:
        if signal in title_lower:
            return False, ""  # Keep

    # Common non-tech roles that waste scoring budget
    reject_roles = [
        "sales", "marketing", "hr ", "human resources", "recruiter",
        "accountant", "finance", "business analyst", "graphic",
        "content", "seo", "social media", "operations",
        "customer success", "account manager", "manager", "senior",
        "sr.", "sr ", "lead", "director", "vp", "president", "head of",
        "principal", "staff", "architect", "associate", "chief of staff",
        "deputy", "writer", "editor", "research", "iii", "iv", " am/"
    ]
    for role in reject_roles:
        if role in title_lower:
            return True, f"Non-tech role title: '{role}'"

    return False, ""


def check_no_description(job: dict) -> tuple[bool, str]:
    """
    If a job has absolutely no description AND no meaningful title
    context, there's nothing for the AI to score — skip it.
    """
    desc = job.get("description", "").strip()
    title = job.get("title", "").strip()
    if len(desc) == 0 and len(title) < 10:
        return True, "No description and no meaningful title"
    return False, ""


def check_rss_tags(job: dict) -> tuple[bool, str]:
    """
    Zero-cost tag-based filter for jobs from freshers_blogs RSS feeds.

    WordPress blogs expose category tags in the RSS feed (entry.tags).
    These are structured as lists in the job dict:
      - experience_tags: tags mentioning "year", "0-1" (e.g. "0-2 Years Experience")
      - batch_tags:      tags with batch year (e.g. "2026 Batch Off Campus")
      - location_tags:   tags mentioning Indian cities or "remote"

    Runs BEFORE experience keyword regex — pure list intersection, near-zero cost.
    Only applies to freshers_blogs source jobs (others won't have these fields).
    """
    if not job.get("source", "").startswith("freshers_blogs"):
        return False, ""  # Not a blog-source job; skip

    # ── Experience tag check ───────────────────────────────────────────────
    exp_tags = job.get("experience_tags", [])
    if exp_tags:
        # Tags are present: check if any signal fresher/entry-level
        fresher_signals = ("fresher", "0-1", "0 - 1", "entry", "0-2", "0 - 2")
        if not any(
            any(sig in t.lower() for sig in fresher_signals)
            for t in exp_tags
        ):
            return True, f"RSS exp tags suggest senior role: {exp_tags[:3]}"

    # Also check role_tags for explicit "Experienced" tags (common on these blogs)
    role_tags = job.get("role_tags", [])
    role_tags_lower = [t.lower() for t in role_tags]
    if "experienced jobs" in role_tags_lower or "experienced" in role_tags_lower:
        # Only reject if there's no fresher tag to balance it
        fresher_in_roles = any(
            any(s in t for s in ("fresher", "fresh", "0-1", "intern"))
            for t in role_tags_lower
        )
        if not fresher_in_roles:
            return True, f"RSS role tags: experienced-only (no fresher tag)"

    # ── Location tag check ───────────────────────────────────────────────
    # These blogs are India-focused; non-India location tags = very unusual.
    # Only reject when ALL location tags are explicit non-India locations.
    loc_tags = job.get("location_tags", [])
    if loc_tags:
        india_signals = {
            "bangalore", "bengaluru", "mumbai", "hyderabad", "delhi",
            "pune", "chennai", "remote", "work from home", "wfh",
            "pan india", "noida", "gurgaon", "india",
        }
        any_india = any(
            any(city in t.lower() for city in india_signals)
            for t in loc_tags
        )
        non_india_explicit = ["usa", "united states", "london", "singapore",
                              "dubai", "canada", "australia", "uk", "europe"]
        all_non_india = all(
            any(loc in t.lower() for loc in non_india_explicit)
            for t in loc_tags
        )
        if all_non_india and not any_india:
            return True, f"RSS location tags: non-India only {loc_tags[:2]}"

    return False, ""


def check_is_old_post(job: dict, profile: dict) -> tuple[bool, str]:
    """
    Reject jobs that are older than the max_job_age_days threshold.
    Uses the smart _parse_posted_at() which handles:
      - ISO dates, RFC dates (dateutil)
      - Relative strings: "3 days ago", "2 months ago", "Posted last week"
      - Unix epoch timestamps
    If posted_at is empty/unparseable, the job passes (benefit of the doubt).
    """
    max_days = profile.get("hard_reject", {}).get("max_job_age_days", 60)
    posted_at = job.get("posted_at")

    if not posted_at:
        return False, ""   # No date = benefit of the doubt

    # Quick-reject obvious stale signals before parsing
    s_lower = str(posted_at).lower()
    stale_patterns = [
        r'\b([2-9]|\d{2,})\s*month[s]?\s*ago',   # "3 months ago" etc.
        r'\b([2-9]|\d{2,})\s*year[s]?\s*ago',    # "2 years ago"
    ]
    for pat in stale_patterns:
        m = re.search(pat, s_lower)
        if m:
            return True, f"Stale relative date: '{posted_at}'"

    dt = _parse_posted_at(posted_at)
    if dt is None:
        logger.debug(f"Unparseable posted_at '{posted_at}' — passing job through")
        return False, ""

    now = datetime.now(timezone.utc)
    age = (now - dt).days

    if age > max_days:
        return True, f"Job posted {age} days ago (max {max_days})"

    return False, ""


# ─────────────────────────────────────────────────────────────────
# MAIN PRE-FILTER
# ─────────────────────────────────────────────────────────────────

def prefilter(jobs: list[dict], profile: dict) -> list[dict]:
    """
    Hard filters run BEFORE the AI scorer to reduce API cost.
    Order matters — cheapest checks first, most expensive last.

    Goal: push <30 jobs/day to the AI scorer to stay within Groq's
    1,000 req/day and 500k token/day limits.
    """

    passed = []

    for job in jobs:
        title       = job.get("title", "")
        company     = job.get("company", "")
        description = job.get("description", "")

        checks = [
            # Cheapest first
            check_has_meaningful_title(job),           # no title = skip
            check_no_description(job),                 # no text = skip
            check_candidate_post(job),                 # Not a job posting
            check_is_old_post(job, profile),           # old post (smart date parsing)
            check_expiry_signals(job),                 # closed/deadline-passed signals in text
            check_company_blacklist(company, profile), # Blacklisted company
            check_role_blacklist(title, profile),      # Blacklisted role type
            check_rss_tags(job),                       # Zero-cost: RSS tag exp/location filter
            check_title_relevance(title),              # obvious non-tech role
            check_experience(description, title, profile),  # Overqualified
            check_location(description, title, profile),    # Wrong geography
        ]

        rejected = False
        for should_reject, reason in checks:
            if should_reject:
                logger.debug(f"REJECTED '{title}' @ '{company}': {reason}")
                rejected = True
                break

        if not rejected:
            passed.append(job)

    logger.info(f"Pre-filter: {len(jobs)} jobs -> {len(passed)} passed (sent to AI scorer)")
    return passed
