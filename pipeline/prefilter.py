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

# Non-job content — exam prep, govt notifications, question papers that leak
# through from broad RSS feeds (particularly freshersnow.com root /feed/).
_NON_JOB_CONTENT_RE = re.compile(
    r'question\s+paper[s]?'
    r'|previous\s+paper[s]?'
    r'|model\s+paper[s]?'
    r'|answer\s+key'
    r'|admit\s+card'
    r'|hall\s+ticket'
    r'|exam\s+pattern'
    r'|\bsyllabus\s+20\d\d'
    r'|\bresult[s]?\s+20\d\d'
    r'|\bcut\s*off\s+mark'
    r'|police\s+constable'
    r'|\bssc\s+'
    r'|\bupsc\s+'
    r'|\bibps\s+'
    r'|railway\s+(?:group|recruitment|loco)'
    r'|fireman\s+'
    r'|\bpsc\s+'
    r'|horticulture\s+officer',
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


def check_non_job_content(job: dict) -> tuple[bool, str]:
    """
    Reject non-job content — exam prep posts, govt notifications, question
    papers, etc. that leak through broad RSS feeds (e.g. freshersnow root /feed/).

    These pass the tech-role title filter (no explicit reject signal) but are
    clearly not job postings and would score 1/10, wasting Groq tokens.
    """
    title = job.get("title", "")
    m = _NON_JOB_CONTENT_RE.search(title)
    if m:
        return True, f"Non-job content: '{m.group(0).strip()}'"
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


# ATS sources that return structured, machine-readable job data.
# Used to apply stricter filters that don't make sense for RSS/blog sources.
_ATS_SOURCES = {"greenhouse", "greenhouse_eu", "lever", "ashby", "workable", "workday"}

# Sources with freeform but curated titles (not ATS but not open blog text either).
# These get their own strict mode — see _INTERNSHALA_TITLE_RE below.
_INTERNSHALA_SOURCES = {"internshala"}

# Comprehensive tech-role positive signals for ATS title allow-listing.
# NOTE: these are raw substrings, which is safe for clean ATS titles like
# "Backend Engineer" but NOT for Internshala-style titles like
# "Backend Development" (where "dev" would match "Business Development").
_ATS_TITLE_KEEP_SIGNALS = [
    # Role types
    "engineer", "developer", "dev", "programmer", "sde", "swe", "intern",
    "fresher", "graduate", "trainee", "associate engineer",
    # Specialisations
    "backend", "back-end", "back end", "full stack", "fullstack", "full-stack",
    "frontend", "front-end", "front end",  # keep FE in case they mention backend too
    "platform", "infrastructure", "devops", "sre", "cloud", "systems",
    "data engineer", "data engineering",
    # Languages / frameworks
    "golang", "go ", " go,", " go)", "python", "typescript", "javascript",
    "java ", "java,", "java)", "kotlin", "rust", "c++", "scala",
    "node", "node.js", "nodejs", "django", "fastapi", "spring",
    # Generic tech signals
    "api", "server", "microservice", "ml engineer", "software",
]

# Internshala-specific strict allow-list using WORD-BOUNDARY regex.
#
# Why a separate list?
# Internshala titles are freeform strings like "Backend Development" or
# "Business Development (Sales)".  The ATS substring list is unsafe here:
#   • "dev"    matches "Business De**v**elopment" — false positive
#   • "intern" matches "Medical Writing With **Intern**ship Opportunity" — false positive
#   • "node"   matches "Zara**node**" — unlikely but possible
#
# These patterns use \b word boundaries so "engineer" only matches as a whole
# word, not as part of "engineering intern" would still match "engineer".
# The list is intentionally narrower than the ATS list — we only want roles
# that are explicitly software/backend oriented.
_INTERNSHALA_TITLE_RE = re.compile(
    r'\b(engineer|developer|programmer|sde|swe)\b'
    r'|\b(backend|back.end|fullstack|full.stack|mern|mean|node\.?js|nodejs|golang|typescript|python|java|rust|c\+\+|scala|kotlin|django|fastapi|spring|devops|microservice|api|software)\b'
    r'|\bbackend\s+development\b'
    r'|\bfull.?stack\s+development\b'
    r'|\bweb\s+development\b'    # keep for now but lower-signal
    r'|\bdata\s+engineer'
    r'|\bplatform\b|\binfrastructure\b|\bcloud\b|\bsre\b',
    re.IGNORECASE,
)

# Internshala titles that match the above but are NOT tech engineering roles.
# Applied as a secondary reject AFTER the allow-list match to block false positives.
_INTERNSHALA_TITLE_REJECT_RE = re.compile(
    r'\bbusiness\s+development\b'    # "Business Development (Sales)" — not SWE
    r'|\bproduct\s+development\b'    # "Product Development" — ambiguous, not SWE
    r'|\bno.code\b'                  # "No Code Development"
    r'|\bphp\b'                      # PHP not in candidate stack
    r'|\bfront.?end\b(?!.*back)'     # Pure frontend with no backend mention
    r'|\banimation\b|\bgame\s+dev'   # Creative roles
    r'|\bai\s+(blog|data\s+analytic|content|writing)\b'  # AI content, not engineering
    r'|\bweb\s+development\s*$'      # Bare "Web Development" with no tech qualifier — too generic
    r'|\bai\s+web\s+development\b',  # Broad AI web scope, not backend
    re.IGNORECASE,
)

# Universal hard-reject for clearly non-SWE roles that can still slip through
# source-specific filters (e.g. "Revenue Enablement Intern" passes the ATS allowlist
# via the "intern" signal, "GTM Systems Specialist" via "systems").
#
# Applied BEFORE source-specific logic in check_title_relevance() — zero exceptions.
# All patterns use \b word boundaries to prevent false positives:
#   \bsales\b     → matches "Sales Exec" but NOT "Salesforce Engineer"
#   \bfinance\b   → matches "Finance Intern" but NOT "Financial Software Engineer"
#   \btalent\b    → matches "Talent Acquisition" but NOT "talented developer"
#   \bhr\b        → matches "HR Manager" but NOT "Chrome" or "Thread"
#
# Intentionally NOT included (too risky for false positives):
#   "solutions engineer" — legitimate SWE title at many companies (Stripe, Cloudflare)
#   "customer engineer"  — legitimate at Google, Databricks, etc.
#   "forward deployed"   — legitimate at Palantir, others
#   "security"           — "Security Engineer" is a valid SWE target
_NON_SWE_TITLE_RE = re.compile(
    r'\b(sales|account\s+executive|business\s+development|revenue|marketing|audit)\b'
    r'|\b(finance|finops|gtm|go[\s\-]+to[\s\-]+market)\b'
    r'|\b(customer\s+success|devrel|developer\s+relations)\b'
    r'|\b(solutions?\s+consulting|presales|pre[\s\-]sales)\b'
    r'|\b(recruiting|talent\s+acquisition|talent\s+management)\b'
    r'|\b(hr\s+|human\s+resources)'
    r'|\b(platformops|techops|it\s+operations|sysadmin|system\s+administrator)\b'
    r'|\b(network\s+engineer|content\s+engineer|technical\s+writer)\b',
    re.IGNORECASE,
)


def check_title_relevance(title: str, source: str = "") -> tuple[bool, str]:
    """
    Positive / negative title filter before the AI scorer.

    Four modes in sequence:

    0. Universal non-SWE hard-reject (ALL sources):
       Rejects roles that are explicitly non-engineering regardless of source —
       e.g. "Revenue Enablement Intern" (passes ATS 'intern' allowlist but is
       clearly non-SWE), "GTM Systems Specialist" (passes via 'systems').
       Applied first with word-boundary regex to prevent false positives.

    1. ATS sources (greenhouse, lever, ashby, workable, workday):
       Strict POSITIVE allow-list via substring matching — safe because ATS
       titles are clean and short (e.g. "Backend Engineer", "SDE Intern").

    2. Internshala:
       Strict POSITIVE allow-list via word-boundary REGEX — necessary because
       Internshala titles are freeform strings like "Backend Development" or
       "Business Development (Sales)". Raw substring "dev" would match the
       latter; \b word boundaries prevent those false positives.
       A secondary reject regex further blocks known false-positive patterns
       (Business Development, PHP, pure frontend, No Code, etc.).

    3. All other sources (RSS blogs, HN, Serper, etc.):
       Lenient blocklist-only mode — rejects obvious non-tech roles but
       passes ambiguous titles through to the AI scorer.
    """
    title_lower = title.lower()

    # ── 0. Universal non-SWE hard-reject (all sources) ────────────────────
    # Must run BEFORE source-specific logic — catches non-SWE roles that
    # slip through positive allowlists (e.g. "Revenue Enablement Intern"
    # passes ATS allowlist via "intern" but is clearly non-engineering).
    m = _NON_SWE_TITLE_RE.search(title)
    if m:
        return True, f"Non-SWE role title: '{m.group(0).strip()}'"

    # ── Block Glassdoor/job-board aggregate listing page titles ────────────
    if re.match(r'^\d[\d,]* ', title_lower):
        return True, "Aggregate listing page title (starts with count)"

    # ── Block YC navigation category pages ────────────────────────────────
    yc_nav_patterns = [
        r'^jobs in ',
        r'^remote .+ jobs$',
        r'^software engineer jobs in ',
        r'^recruiting jobs in ',
    ]
    for pat in yc_nav_patterns:
        if re.match(pat, title_lower):
            return True, f"YC navigation/category page title: '{title}'"

    # ── ATS strict mode: positive allow-list only ─────────────────────────
    if source in _ATS_SOURCES:
        for signal in _ATS_TITLE_KEEP_SIGNALS:
            if signal in title_lower:
                return False, ""  # Tech signal found — keep
        # No tech signal found — reject (safe because ATS titles are clean)
        return True, f"ATS title has no tech signal: '{title}'"

    # ── Internshala strict mode: word-boundary regex allow-list ──────────
    # Uses regex \b boundaries to avoid "dev" matching "Business Development",
    # "intern" matching "Internship Opportunity", etc.
    if source in _INTERNSHALA_SOURCES:
        m = _INTERNSHALA_TITLE_RE.search(title)
        if not m:
            return True, f"Internshala title has no tech signal: '{title}'"
        # Tech signal found — but check secondary reject patterns (false positives)
        reject_m = _INTERNSHALA_TITLE_REJECT_RE.search(title)
        if reject_m:
            return True, f"Internshala title rejected (false positive pattern): '{reject_m.group(0).strip()}'"
        return False, ""

    # ── Non-ATS lenient mode (original logic) ────────────────────────────
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
            return False, ""

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


# Hard-reject location strings for ATS jobs.
# ATS location fields are structured (e.g. "New York, NY") — safe to match.
_ATS_REJECT_LOCATION_PATTERNS = re.compile(
    r'\busa\b|\bunited states\b|\bus only\b|\bus-only\b'
    r'|\bsan francisco\b|\bsf,\b|\bseattle\b|\bnew york\b|\bnyc\b'
    r'|\blos angeles\b|\bchicago\b|\baustin\b|\bboston\b|\bdenver\b'
    r'|\batlanta\b|\bdallas\b|\bphoenix\b|\bminneapolis\b'
    r'|\bportland\b|\bsalt lake\b|\bsan jose\b|\bsan diego\b'
    r'|\b[a-z]+,\s*[A-Z]{2}\b'  # city, STATE abbreviation (e.g. "Austin, TX")
    r'|\bunited kingdom\b|\blondon\b|\buk only\b'
    # EU countries — remote-but-EU-only roles that won't hire India candidates
    r'|\bgermany\b|\bireland\b|\bspain\b|\bsweden\b|\bpoland\b'
    r'|\bnetherlands\b|\bfrancefr\b|\bfrance\b|\bdenmark\b|\bfinland\b'
    r'|\bnorway\b|\bczech\b|\baustralia\b'
    r'|\beurope\b|\bberlin\b|\bamsterdam\b|\bparis\b|\bstockholm\b'
    r'|\bsingapore\b|\bdubai\b|\bcairo\b|\bsydney\b|\bmelbourne\b'
    r'|\bcanada\b|\btoronto\b|\bvancouver\b|\blatin america\b|\blatam\b',
    re.IGNORECASE,
)

# Signals that the ATS location IS acceptable (India/Remote pass-through).
_ATS_ACCEPT_LOCATION_PATTERNS = re.compile(
    r'india|remote|bangalore|bengaluru|mumbai|hyderabad|delhi|pune'
    r'|chennai|kolkata|noida|gurgaon|gurugram|work from home|wfh|pan india'
    r'|not specified|worldwide|global|anywhere',
    re.IGNORECASE,
)


def check_ats_location(job: dict) -> tuple[bool, str]:
    """
    Zero-cost location filter for ATS jobs only.

    ATS sources (Greenhouse, Lever, Ashby, Workable) populate the `location`
    field with clean, structured strings — safe to pattern-match without
    reading the description.

    Logic:
      1. Skip non-ATS jobs (handled by check_location in description).
      2. If the location contains an India/Remote accept signal → pass.
      3. If the location contains a known non-India reject pattern → reject.
      4. Ambiguous / empty locations → pass (benefit of the doubt).
    """
    source = job.get("source", "")
    if source not in _ATS_SOURCES:
        return False, ""  # Not an ATS job

    location = job.get("location", "").strip()
    if not location or location.lower() == "not specified":
        return False, ""  # No location info — benefit of the doubt

    # Accept signals take priority
    if _ATS_ACCEPT_LOCATION_PATTERNS.search(location):
        return False, ""

    # Reject signals
    if _ATS_REJECT_LOCATION_PATTERNS.search(location):
        return True, f"ATS location outside India: '{location}'"

    return False, ""  # Ambiguous — pass


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

    Filters applied in order:
      1. Structural checks (title, description presence)
      2. Age / expiry signals
      3. Blacklists (company, role)
      4. ATS-specific: strict title allow-list + location metadata
      5. RSS tag checks (blog sources only)
      6. General title relevance (non-ATS lenient mode)
      7. Experience / location keyword scan in description
      8. Per-company cap (ATS sources) — applied LAST so all other
         filters have already narrowed the pool.
    """
    hard_reject_cfg = profile.get("hard_reject", {})
    # Use a high safety ceiling here — the real per-company cap (ats_per_company_cap)
    # runs POST-ranking in scorer.py so we keep the TOP-ranked jobs per company,
    # not the first-fetched ones. This value only prevents runaway single-company floods.
    ats_prefilter_safety_cap = hard_reject_cfg.get("ats_prefilter_safety_cap", 100)

    passed = []
    company_counts: dict[str, int] = {}  # tracks ATS jobs per company

    for job in jobs:
        title       = job.get("title", "")
        company     = job.get("company", "")
        description = job.get("description", "")
        source      = job.get("source", "")

        checks = [
            # ── Structural (cheapest) ─────────────────────────────────────
            check_has_meaningful_title(job),           # no title = skip
            check_no_description(job),                 # no text = skip
            check_candidate_post(job),                 # Not a job posting
            check_non_job_content(job),                # Exam prep / govt noise from RSS
            # ── Temporal ─────────────────────────────────────────────────
            check_is_old_post(job, profile),           # old post (smart date parsing)
            check_expiry_signals(job),                 # closed/deadline-passed signals in text
            # ── Blacklists ────────────────────────────────────────────────
            check_company_blacklist(company, profile), # Blacklisted company
            check_role_blacklist(title, profile),      # Blacklisted role type
            # ── ATS-specific cheap filters ────────────────────────────────
            check_ats_location(job),                   # ATS: location field (US/UK/EU reject)
            check_title_relevance(title, source),      # ATS: strict allow-list; non-ATS: lenient
            # ── RSS tag checks ────────────────────────────────────────────
            check_rss_tags(job),                       # Zero-cost: RSS tag exp/location filter
            # ── Description keyword scans (slower) ───────────────────────
            check_experience(description, title, profile),  # Overqualified
            check_location(description, title, profile),    # Wrong geography (description)
        ]

        rejected = False
        for should_reject, reason in checks:
            if should_reject:
                logger.debug(f"REJECTED '{title}' @ '{company}': {reason}")
                rejected = True
                break

        if rejected:
            continue

        # ── Per-company safety ceiling (ATS only) ────────────────────────────
        # High ceiling (default 100) — only prevents extreme single-company floods.
        # The real per-company cap (ats_per_company_cap=25) runs POST-ranking
        # in scorer.py so the TOP-ranked jobs per company are kept, not random ones.
        if source in _ATS_SOURCES:
            count = company_counts.get(company, 0)
            if count >= ats_prefilter_safety_cap:
                logger.debug(
                    f"REJECTED '{title}' @ '{company}': prefilter safety cap reached "
                    f"({ats_prefilter_safety_cap} ATS jobs/company)"
                )
                continue
            company_counts[company] = count + 1

        passed.append(job)

    # Summarise safety-cap hits
    capped_companies = [c for c, n in company_counts.items() if n >= ats_prefilter_safety_cap]
    if capped_companies:
        logger.info(
            f"ATS prefilter safety cap ({ats_prefilter_safety_cap}): capped companies — "
            + ", ".join(capped_companies)
        )

    logger.info(f"Pre-filter: {len(jobs)} jobs -> {len(passed)} passed (sent to AI scorer)")
    return passed
