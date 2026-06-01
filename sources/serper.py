"""
sources/serper.py — Google dork-based job discovery via Serper.dev

Architecture
────────────
  - DORK_TEMPLATES: 60+ parameterised templates across 8 buckets:
      1. ATS site-targeted  (lever, greenhouse, ashby, workable, rippling, …)
      2. General backend   (broad entry-level India)
      3. Go/Golang specific
      4. TypeScript / Node.js
      5. Fintech / Crypto / Payments
      6. Remote-first / global startups in India
      7. Hidden applications (Google Forms, Notion, Typeform)
      8. Company career pages ("We are hiring" posts)

  - build_dork_queries(profile): generates concrete query strings from the
    templates, substituted with the candidate's actual skills/roles from
    profile.yaml.  This means adding a new skill automatically produces new
    dork variants — no manual update required.

  - fetch_serper_jobs(): shuffles the full query pool so different queries
    run on each execution.  Over a week all 60+ queries get coverage while
    staying inside the 2,500 credit/month free budget.

Budget maths
────────────
  MAX_SERPER_CALLS = 15 queries/run  (tunable)
  15/day × 30 days = 450 credits/month — comfortably within the 2,500 limit.
  To increase coverage: raise MAX_SERPER_CALLS up to ~30 (still < 900/month).
"""

import os
import random
import logging
from itertools import product
from urllib.parse import urlparse

import requests
from scrapling.fetchers import Fetcher

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL     = "https://google.serper.dev/search"

# Hard cap: never spend more than this many Serper API credits per run.
# Free tier = 2,500/month.  15/day × 30 days = 450/month.
MAX_SERPER_CALLS = 15

# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN BLOCKLIST
# Domains (and all subdomains) that should never be scraped via Serper.
# Matching: a URL is blocked if any blocklist entry appears as a domain suffix.
# ─────────────────────────────────────────────────────────────────────────────
DOMAIN_BLOCKLIST: set[str] = {
    # Job aggregators — already covered by dedicated sources or unscrapeable
    "naukri.com",
    "linkedin.com",
    "indeed.com",
    "internshala.com",       # covered by dedicated source
    "hirist.tech",           # covered by dedicated source (if enabled)
    "shine.com",
    "timesjobs.com",
    "monsterindia.com",
    # Glassdoor — all country variants
    "glassdoor.com",
    "glassdoor.co.in",
    "glassdoor.sg",
    "glassdoor.co.uk",
    "glassdoor.de",
    "glassdoor.fr",
    "glassdoor.ca",
    # Other aggregators that return 403 or are redundant
    "ziprecruiter.com",
    "simplyhired.com",
    "reddit.com",
    "wellfound.com",
    # Irrelevant / non-job content
    "facebook.com",
    "scribd.com",
    "prosple.com",           # Southeast Asia
    "bayt.com",              # Middle East
    # BuiltIn city sites (US-specific, return 403 from India)
    "builtin.com",
    "builtinchicago.org",
    "builtinsf.com",
    "builtinboston.org",
    "builtinnyc.com",
    "builtinla.com",
    "builtinseattle.com",
    "builtinaustin.com",
    # Consistently unreachable from the pipeline
    "dailyremote.com",       # 403
    "ambitionbox.com",       # 403
}

# ─────────────────────────────────────────────────────────────────────────────
# ATS DOMAINS — used for smarter company name extraction
# ─────────────────────────────────────────────────────────────────────────────
_ATS_NETLOC_MAP = {
    # netloc pattern → (ats_name, slug_position_in_path)
    "boards.greenhouse.io":    ("greenhouse",  1),   # /boards.greenhouse.io/{slug}/jobs/{id}
    "boards.eu.greenhouse.io": ("greenhouse_eu", 1),
    "api.lever.co":            ("lever",       2),   # /v0/postings/{slug}
    "jobs.lever.co":           ("lever",       1),   # /{slug}/{id}
    "apply.workable.com":      ("workable",    1),   # /{slug}/j/{shortcode}
    "jobs.ashbyhq.com":        ("ashby",       1),   # /{slug}/{id}
    "job.rippling.com":        ("rippling",    0),   # fallback
    "careers.rippling.com":    ("rippling",    0),
    "jobs.smartrecruiters.com":("smartrecruiters", 1),
    "app.dover.com":           ("dover",        2),  # /client/{slug}/…
    "jobs.gusto.com":          ("gusto",        1),
}


# ─────────────────────────────────────────────────────────────────────────────
# DORK TEMPLATE LIBRARY
#
# Placeholders:
#   {skill}       → one of the candidate's strong skills (Go, TypeScript, …)
#   {role}        → one of the candidate's target roles (Backend Intern, …)
#   {city}        → acceptable city (Bangalore, Remote, …)
#   {year}        → current year
#
# Templates are Cartesian-product–expanded against substitution lists at
# runtime by build_dork_queries().  Each concrete query counts as 1 credit.
# ─────────────────────────────────────────────────────────────────────────────

# ── Bucket 1: ATS site-targeted ───────────────────────────────────────────
# High-precision: Google limits results to a specific ATS domain.
# Each site: operator costs 1 credit but returns very relevant results.
_ATS_SITE_TEMPLATES = [
    # Lever
    'site:lever.co "{skill}" "intern" OR "fresher" "india" OR "remote" OR "bangalore"',
    'site:lever.co "{skill}" "0-1 years" OR "entry level" "india" OR "remote"',
    'site:lever.co "backend" "{skill}" "india" OR "remote" OR "bengaluru"',
    'site:lever.co "software engineer" "{skill}" "intern" OR "fresher" "india"',

    # Greenhouse
    'site:boards.greenhouse.io "{skill}" "intern" OR "fresher" "india" OR "remote"',
    'site:boards.greenhouse.io "backend" "{skill}" "india" OR "bangalore" 2025 OR 2026',
    'site:boards.greenhouse.io "software engineer" "{skill}" "india" OR "remote"',
    'site:boards.greenhouse.io "entry level" OR "junior" "{skill}" "india" OR "remote"',

    # Ashby HQ
    'site:jobs.ashbyhq.com "{skill}" "intern" OR "fresher" OR "0-1 years" "india" OR "remote"',
    'site:jobs.ashbyhq.com "backend" "{skill}" "india" OR "bangalore" OR "remote"',
    'site:jobs.ashbyhq.com "software engineer" "{skill}" "india" OR "remote"',

    # Workable
    'site:apply.workable.com "{skill}" "intern" OR "fresher" "india" OR "remote"',
    'site:apply.workable.com "backend" "{skill}" "0-1 years" OR "fresher" "india"',
    'site:apply.workable.com "software engineer" "{skill}" "intern" india',

    # Rippling / SmartRecruiters / Dover (emerging ATS)
    'site:job.rippling.com "{skill}" "intern" OR "fresher" "india" OR "remote"',
    'site:jobs.smartrecruiters.com "{skill}" "intern" OR "fresher" "india" OR "remote" "backend"',

    # Combined ATS OR — one query searches multiple boards
    '(site:lever.co OR site:boards.greenhouse.io) "{skill}" "intern" OR "fresher" "india" OR "remote"',
    '(site:jobs.ashbyhq.com OR site:apply.workable.com) "{skill}" "fresher" OR "intern" "india"',
]

# ── Bucket 2: General backend (broad) ─────────────────────────────────────
_GENERAL_BACKEND_TEMPLATES = [
    '"backend intern" OR "backend fresher" india -site:linkedin.com -site:naukri.com',
    '"backend engineering intern" india -site:linkedin.com -site:naukri.com',
    '"backend developer" "0-1 years" OR "fresher" OR "intern" india 2026',
    '"software engineer intern" "backend" OR "api" india -site:linkedin.com',
    '"junior backend developer" india 2026 -site:linkedin.com',
    '"SDE intern" OR "software developer intern" "backend" india -site:linkedin.com',
    '"backend engineer" "fresher" india 2026 -site:linkedin.com -site:naukri.com',
    '"entry level backend" OR "entry-level backend" india OR remote -site:linkedin.com',
    '"backend engineering" "0 years" OR "fresher" OR "no experience" india',
    '"software development intern" "backend" OR "server" india -site:linkedin.com',
    '"associate software engineer" "backend" OR "golang" OR "typescript" india',
]

# ── Bucket 3: Go / Golang specific ─────────────────────────────────────────
_GOLANG_TEMPLATES = [
    '"backend intern" OR "backend fresher" "golang" OR "go developer" india',
    '"go developer" intern OR fresher india OR remote -site:linkedin.com',
    '"software engineer intern" "go" OR "golang" "bangalore" OR "remote" OR "india"',
    '"golang intern" OR "golang fresher" india 2025 OR 2026',
    '"go backend" "intern" OR "fresher" OR "entry level" india -site:linkedin.com',
    'site:lever.co "golang" OR "go developer" "intern" OR "junior" "india" OR "remote"',
    'site:jobs.ashbyhq.com "golang" OR "go" "fresher" OR "intern" "india" OR "remote"',
    '"hiring" "golang" "backend" "intern" OR "fresher" india -site:linkedin.com',
    '"microservices" "golang" "intern" OR "fresher" india OR bangalore OR remote',
    '"grpc" OR "gRPC" "golang" "intern" OR "fresher" india',
    '"distributed systems" "golang" "fresher" OR "entry level" india OR remote',
]

# ── Bucket 4: TypeScript / Node.js specific ───────────────────────────────
_TYPESCRIPT_NODE_TEMPLATES = [
    '"backend intern" OR "backend fresher" "typescript" OR "node.js" india',
    '"node.js intern" OR "nodejs intern" india OR remote -site:linkedin.com',
    '"typescript backend" intern OR fresher india -site:linkedin.com',
    '"backend developer" "typescript" OR "node" "0-1 years" OR "fresher" india',
    '"typescript" "backend" "intern" OR "fresher" "bangalore" OR "remote" india',
    'site:lever.co "typescript" OR "node.js" "backend" "intern" OR "junior" india OR remote',
    'site:boards.greenhouse.io "typescript" "backend" "intern" OR "fresher" india OR remote',
    '"REST API" "typescript" "intern" OR "fresher" india -site:linkedin.com',
    '"express" OR "nestjs" OR "nest.js" "intern" OR "fresher" india backend 2026',
    '"full stack" "typescript" OR "node.js" "intern" OR "fresher" india 2026',
]

# ── Bucket 5: Fintech / Crypto / Payments ────────────────────────────────
_FINTECH_CRYPTO_TEMPLATES = [
    '"backend intern" "fintech" OR "payments" OR "crypto" india -site:linkedin.com',
    '"software intern" "crypto" OR "blockchain" OR "defi" india -site:linkedin.com',
    '"backend engineer" "fresher" "payments" OR "fintech" india -site:linkedin.com',
    'site:lever.co "fintech" OR "payments" "backend" "intern" OR "fresher" india OR remote',
    'site:boards.greenhouse.io "crypto" OR "blockchain" "backend" "intern" OR "junior" india',
    'site:jobs.ashbyhq.com "fintech" OR "payments" "backend" "intern" OR "fresher"',
    '"order matching" OR "trading engine" "backend" "intern" OR "fresher" india OR remote',
    '"payment gateway" "backend" "intern" OR "fresher" india -site:linkedin.com',
    '"wallet" OR "defi" "backend engineer" "intern" OR "fresher" india 2026',
    '"exchange" OR "crypto exchange" "backend" "intern" OR "fresher" india OR remote',
]

# ── Bucket 6: Remote-first / global startups ─────────────────────────────
_REMOTE_GLOBAL_TEMPLATES = [
    '"backend intern" "remote" "india" -site:linkedin.com -site:naukri.com',
    '"work from anywhere" OR "fully remote" "backend" "intern" OR "fresher" india',
    '"remote backend engineer" "intern" OR "fresher" OR "entry level" india',
    'site:lever.co "remote" "backend" "intern" OR "entry level" "india" OR "IST"',
    'site:boards.greenhouse.io "remote" "backend" "intern" OR "fresher" "india"',
    '"anywhere in india" OR "pan india" "backend" "intern" OR "fresher" -site:linkedin.com',
    '"global remote" "backend" OR "software" "intern" OR "fresher" india 2026',
    '"india time zone" OR "IST" "backend" "intern" OR "fresher" remote',
]

# ── Bucket 7: Hidden applications (Google Forms, Notion, Typeform) ────────
_HIDDEN_APPLY_TEMPLATES = [
    '"docs.google.com/forms" "backend intern" india',
    '"forms.gle" "apply" "software engineer" "intern" india',
    '"typeform.com" "backend" "intern" OR "fresher" india -site:linkedin.com',
    '"airtable.com" "apply" "backend" "intern" OR "fresher" india',
    '"notion.so" "apply" OR "hiring" "backend" "intern" OR "fresher" india',
    'inurl:forms.gle "backend" OR "software" "intern" india 2026',
    '"google form" "backend intern" OR "software intern" "india" 2025 OR 2026',
]

# ── Bucket 8: Company career pages / social hiring posts ─────────────────
_CAREER_PAGE_TEMPLATES = [
    'intitle:"careers" "backend intern" OR "backend fresher" site:*.in',
    '"we are hiring" "backend" "intern" OR "fresher" india -site:linkedin.com',
    'intitle:"join us" "backend engineer" "fresher" site:*.io',
    '"open positions" "backend" "golang" OR "typescript" "india" OR "remote"',
    '"job openings" "backend engineer" "fresher" OR "intern" "india" 2026',
    '"apply now" "backend" "intern" OR "fresher" india -site:linkedin.com site:*.io OR site:*.com',
    '"currently hiring" "backend" OR "software" "intern" OR "fresher" india 2026',
    '"engineering roles" "intern" OR "fresher" "backend" india startup 2026',
]

# ─────────────────────────────────────────────────────────────────────────────
# PROFILE-DRIVEN TEMPLATE INSTANTIATION
# ─────────────────────────────────────────────────────────────────────────────

def _build_skill_variants(profile: dict) -> list[str]:
    """
    Extract skill keywords from profile.yaml for dork substitution.
    Returns a deduplicated, prioritised list of search-friendly skill tokens.

    Only includes skills that make sense as Google dork search terms — generic
    tools like Git, Linux, Protobuf alone tend to produce noisy results and are
    excluded.  Skills absent from the map fall through to a lowercased form but
    are filtered by the SKIP_SKILLS set.
    """
    skills_cfg = profile.get("candidate", {}).get("skills", {})
    strong     = skills_cfg.get("strong", [])
    learning   = skills_cfg.get("learning", [])

    # Skills that are too generic / low-signal for Google dork searches.
    # Adding a skill here prevents it from appearing as a {skill} placeholder.
    SKIP_SKILLS = {
        "git", "linux", "docker", "protobuf", "rest apis",
        "shell", "bash", "vim", "vscode", "figma",
    }

    # Map canonical profile.yaml skill names → effective search token(s).
    # Preference order matters: first entry is used for ATS-targeted dorks.
    skill_token_map: dict[str, list[str]] = {
        "Go":            ["golang", "go developer"],
        "Golang":        ["golang"],
        "TypeScript":    ["typescript"],
        "JavaScript":    ["javascript"],
        "Node.js":       ["node.js", "nodejs"],
        "Python":        ["python"],
        "Java":          ["java"],
        "Kotlin":        ["kotlin"],
        "Rust":          ["rust"],
        "C++":           ["c++"],
        "Scala":         ["scala"],
        "gRPC":          ["grpc"],
        "REST APIs":     ["REST API"],            # special-cased in SKIP_SKILLS via lower
        "PostgreSQL":    ["postgresql", "postgres"],
        "Redis":         ["redis"],
        "Microservices": ["microservices"],
        "Kafka":         ["kafka"],
        "RabbitMQ":      ["rabbitmq"],
        "Elasticsearch": ["elasticsearch"],
        "MongoDB":       ["mongodb"],
        "Kubernetes":    ["kubernetes"],
        "AWS":           ["aws"],
        "GCP":           ["gcp"],
        "Azure":         ["azure"],
        "FastAPI":       ["fastapi"],
        "Django":        ["django"],
        "Spring":        ["spring boot"],
        "GraphQL":       ["graphql"],
        "Docker":        None,   # None = skip
        "Git":           None,
        "Linux":         None,
        "Protobuf":      None,
    }

    tokens: list[str] = []
    for skill in strong + learning:
        mapped = skill_token_map.get(skill)
        if mapped is None:
            # Explicitly skipped
            continue
        if mapped:
            tokens.extend(mapped)
        else:
            # Not in map — use lowercased skill unless it's in the skip set
            lower = skill.lower()
            if lower not in SKIP_SKILLS:
                tokens.append(lower)

    # Remove any that slipped through the skip set
    tokens = [t for t in tokens if t.lower() not in SKIP_SKILLS]

    # Deduplicate while preserving order
    seen:   set[str]  = set()
    result: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _build_location_variants(profile: dict) -> list[str]:
    """Return a short list of location tokens for dork substitution."""
    acceptable = profile.get("candidate", {}).get("location", {}).get("acceptable", [])
    priority   = ["Remote", "Bangalore", "India", "Hyderabad", "Mumbai"]
    # Keep only items from the priority list that appear in the profile
    out = [loc for loc in priority if any(loc.lower() in a.lower() for a in acceptable)]
    return out or ["India", "Remote"]


def build_dork_queries(profile: dict) -> list[str]:
    """
    Generate concrete dork query strings from the template library.

    Strategy:
    - ATS-site templates: substitute {skill} with the top-3 strongest skills
      (precision > recall for these — site: narrows scope enough).
    - General templates: use a broader set but avoid Cartesian explosion.
    - Static queries (no placeholders) are kept as-is.
    - Final list is deduplicated before returning.

    Returns list of query strings, all unique.
    """
    import datetime
    current_year = datetime.datetime.now().year

    skill_tokens   = _build_skill_variants(profile)
    primary_skills = skill_tokens[:4]   # Top skills for ATS-targeted dorks (precision)
    broad_skills   = skill_tokens[:6]   # Broader set for general dorks

    location_tokens = _build_location_variants(profile)
    primary_locs    = location_tokens[:2]

    all_queries: list[str] = []

    def _expand(templates: list[str], skills: list[str], locs: list[str]) -> list[str]:
        """Expand a template list with Cartesian product over skills (and optionally locs)."""
        out = []
        for tpl in templates:
            if "{skill}" in tpl and "{city}" in tpl:
                for skill, city in product(skills[:2], locs[:2]):
                    out.append(tpl.format(skill=skill, city=city, year=current_year))
            elif "{skill}" in tpl:
                for skill in skills:
                    out.append(tpl.format(skill=skill, year=current_year))
            elif "{city}" in tpl:
                for city in locs[:2]:
                    out.append(tpl.format(city=city, year=current_year))
            else:
                # Static template — year substitution only
                out.append(tpl.format(year=current_year) if "{year}" in tpl else tpl)
        return out

    # Bucket 1: ATS site-targeted — most valuable, use primary skills only
    all_queries.extend(_expand(_ATS_SITE_TEMPLATES,        primary_skills, primary_locs))

    # Bucket 2: General backend — mostly static, very few skill slots
    all_queries.extend(_expand(_GENERAL_BACKEND_TEMPLATES, broad_skills,   primary_locs))

    # Bucket 3 & 4: Language-specific (Go, TS) — mostly static
    all_queries.extend(_expand(_GOLANG_TEMPLATES,          primary_skills, primary_locs))
    all_queries.extend(_expand(_TYPESCRIPT_NODE_TEMPLATES, primary_skills, primary_locs))

    # Bucket 5: Fintech/Crypto — largely static
    all_queries.extend(_expand(_FINTECH_CRYPTO_TEMPLATES,  primary_skills, primary_locs))

    # Bucket 6: Remote-global
    all_queries.extend(_expand(_REMOTE_GLOBAL_TEMPLATES,   primary_skills, primary_locs))

    # Bucket 7: Hidden applications — static
    all_queries.extend(_expand(_HIDDEN_APPLY_TEMPLATES,    primary_skills, primary_locs))

    # Bucket 8: Career pages — mostly static
    all_queries.extend(_expand(_CAREER_PAGE_TEMPLATES,     primary_skills, primary_locs))

    # Deduplicate while preserving order
    seen   = set()
    unique = []
    for q in all_queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    return unique


# ─────────────────────────────────────────────────────────────────────────────
# SERPER API
# ─────────────────────────────────────────────────────────────────────────────

def search_serper(query: str) -> list[dict]:
    """Run a single Serper.dev Google search, return list of organic results."""
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not set — skipping Serper query")
        return []

    headers = {
        "X-API-KEY":    SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q":   query,
        "gl":  "in",    # Google India results
        "hl":  "en",
        "num": 10,
    }
    try:
        r = requests.post(SERPER_URL, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("organic", [])
    except Exception as e:
        logger.warning(f"Serper query failed '{query[:60]}': {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# URL FILTERING & COMPANY EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_blocked_domain(url: str) -> bool:
    """
    Return True if the URL's domain matches any entry in DOMAIN_BLOCKLIST.
    Uses suffix-matching on the netloc so e.g. 'glassdoor.com' blocks
    both 'glassdoor.com' and 'www.glassdoor.com'.
    """
    try:
        netloc = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return False
    return any(
        netloc == blocked or netloc.endswith("." + blocked)
        for blocked in DOMAIN_BLOCKLIST
    )


def is_job_related_url(url: str) -> bool:
    """Quick check to avoid wasting Scrapling fetches on irrelevant pages."""
    if _is_blocked_domain(url):
        return False  # On the blocklist — skip

    job_signals = [
        "careers", "jobs", "hiring", "apply", "forms.gle",
        "docs.google.com/forms", "greenhouse.io", "lever.co",
        "ashbyhq.com", "workable.com", "job", "opening",
        "position", "vacancy", "rippling.com", "smartrecruiters",
        "dover.com", "typeform", "airtable",
    ]
    return any(s in url.lower() for s in job_signals)


def _guess_company(url: str, title: str) -> str:
    """
    Best-effort company name extraction from URL.

    For known ATS domains, the company slug lives at a well-known path position:
      - lever.co/v0/postings/{slug}      → slug
      - boards.greenhouse.io/{slug}/...  → slug
      - jobs.ashbyhq.com/{slug}/...      → slug
      - apply.workable.com/{slug}/...    → slug

    For everything else, falls back to the subdomain/second-level domain.
    """
    try:
        parsed  = urlparse(url)
        netloc  = parsed.netloc.lower().replace("www.", "")
        path_parts = [p for p in parsed.path.split("/") if p]  # non-empty segments

        for ats_netloc, (ats_name, slug_idx) in _ATS_NETLOC_MAP.items():
            if netloc == ats_netloc or netloc.endswith("." + ats_netloc):
                if path_parts and slug_idx < len(path_parts):
                    slug = path_parts[slug_idx]
                    # Strip numeric IDs (Greenhouse job IDs are pure digits)
                    if slug.isdigit():
                        # Try the segment before it
                        slug = path_parts[slug_idx - 1] if slug_idx > 0 else slug
                    return slug.replace("-", " ").title()

        # Generic fallback: use the SLD (second-level domain)
        domain = netloc.split(".")[0]
        return domain.title()
    except Exception:
        return "Unknown"


def _detect_ats_source(url: str) -> str:
    """
    Return the ATS name if the URL is hosted on a known ATS platform,
    otherwise return 'serper'.
    """
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        for ats_netloc, (ats_name, _) in _ATS_NETLOC_MAP.items():
            if netloc == ats_netloc or netloc.endswith("." + ats_netloc):
                return f"serper_{ats_name}"
    except Exception:
        pass
    return "serper"


# ─────────────────────────────────────────────────────────────────────────────
# PAGE SCRAPING
# ─────────────────────────────────────────────────────────────────────────────

def extract_job_from_page(url: str, title_hint: str, company_hint: str) -> dict | None:
    """
    Fetches a discovered URL with Scrapling's plain HTTP Fetcher and extracts
    job details from the page text.

    StealthyFetcher (Playwright/Chromium headless) is intentionally NOT used here
    because it requires system libraries (libcups.so.2) that may not be present.
    Plain HTTP is sufficient for the vast majority of ATS job pages.

    If the page returns <200 chars of text it is silently skipped — this is
    typically a JS-only SPA that needs a browser, which we can't run here.

    Hard timeout: 10 s per fetch.
    """
    import time
    time.sleep(1)  # Rate limit: 1 req/sec

    FETCH_TIMEOUT   = 10    # seconds
    MIN_BODY_CHARS  = 200   # skip near-empty pages (JS-rendered, blocked, etc.)

    try:
        page   = Fetcher.get(url, timeout=FETCH_TIMEOUT)
        status = getattr(page, "status", None)
        if status is not None and status != 200:
            logger.debug(f"Serper skip non-200 ({status}): {url}")
            return None

        body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])

        if len(body_text) < MIN_BODY_CHARS:
            # JS-rendered / bot-blocked page — no headless fallback available.
            logger.debug(f"Serper skip sparse page ({len(body_text)} chars): {url}")
            return None

        source_tag = _detect_ats_source(url)

        # Google Form: return title + description text directly
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
            "source":      source_tag,
            "salary":      _extract_salary(body_text),
            "posted_at":   "",
        }
    except Exception as e:
        logger.warning(f"Failed to extract job from {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TEXT EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_location(text: str) -> str:
    """Simple heuristic to find location in job description."""
    keywords = [
        "remote", "bangalore", "bengaluru", "mumbai", "hyderabad",
        "delhi", "ncr", "pune", "chennai", "kolkata", "india",
        "work from home", "wfh", "hybrid", "noida", "gurgaon",
        "gurugram", "pan india",
    ]
    text_lower = text.lower()
    found = [k.title() for k in keywords if k in text_lower]
    return " / ".join(found[:3]) if found else "Not specified"


def _extract_salary(text: str) -> str:
    """Simple heuristic to extract salary/stipend info."""
    import re
    patterns = [
        r'₹[\d,]+\s*[-–]\s*₹[\d,]+',
        r'[\d]+\s*[-–]\s*[\d]+\s*LPA',
        r'[\d]+k\s*[-–]\s*[\d]+k\s*per\s*month',
        r'stipend.*?₹[\d,]+',
        r'[\d]+\s*[-–]\s*[\d]+\s*per\s*month',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def fetch_serper_jobs(profile: dict | None = None) -> list[dict]:
    """
    Main function: generates dork queries (profile-driven), shuffles them,
    runs up to MAX_SERPER_CALLS queries, and extracts jobs from results.

    Shuffling ensures that across multiple runs the full 60+ query pool gets
    coverage even though each run only executes MAX_SERPER_CALLS of them.

    Budget:
        15 queries/run × 30 days = 450 credits/month (free tier: 2,500/month).
    """
    # Load a minimal default profile if none is provided (standalone testing)
    if profile is None:
        try:
            import yaml
            with open("profile.yaml") as f:
                profile = yaml.safe_load(f)
        except Exception:
            profile = {}

    all_queries = build_dork_queries(profile)

    # Shuffle for query diversity across runs
    query_pool = list(all_queries)
    random.shuffle(query_pool)

    # Select up to MAX_SERPER_CALLS queries for this run
    selected_queries = query_pool[:MAX_SERPER_CALLS]

    logger.info(
        f"Serper dork pool: {len(all_queries)} unique queries generated, "
        f"running {len(selected_queries)} this cycle"
    )

    all_jobs  : list[dict] = []
    seen_urls : set[str]   = set()
    queries_run            = 0

    for query in selected_queries:
        logger.debug(f"Serper query [{queries_run+1}/{len(selected_queries)}]: {query[:80]}")
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
            job     = extract_job_from_page(url, title, company)
            if job:
                all_jobs.append(job)

    logger.info(
        f"Serper discovery: {queries_run} queries → "
        f"{len(seen_urls)} unique URLs → {len(all_jobs)} jobs extracted"
    )
    return all_jobs
