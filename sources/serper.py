"""
sources/serper.py — Google dork-based job discovery via Serper.dev

Strategy: hunt what NO other source can find
────────────────────────────────────────────
  sources/ats.py already polls Greenhouse, Lever, Ashby, Workable, SmartRecruiters,
  Rippling, BambooHR, Recruitee, and Personio via structured APIs — all listed companies
  get full, structured, 100% coverage.  Serper site: queries on those same platforms
  find a random Google-indexed sample of the same jobs.  That's waste.

  Serper's actual edge is finding jobs that ZERO other sources can:
    1. Company-owned career pages not on any ATS (startups with custom /careers pages)
    2. Indian ATS platforms not in companies.yaml (Keka, Freshteam, Zoho Recruit)
    3. Hidden applications (Google Forms, Notion, Typeform, Airtable)
    4. Language-specific Go/TS searches on company career pages
    5. Remote-first global startups with India presence

Query tiers (enforced in fetch_serper_jobs)
────────────────────────────────────────────
  Tier 1 — Unique/non-overlapping (GUARANTEED TIER_1_BUDGET=10 slots):
    • _OWNED_CAREER_TEMPLATES  — company career pages with explicit ATS exclusions
    • _ALT_ATS_TEMPLATES       — Keka, Freshteam, Zoho (not in ats.py at all)
    • _HIDDEN_APPLY_TEMPLATES  — Google Forms, Notion, Typeform

  Tier 2 — High-value, language-specific (fill after Tier 1):
    • _GOLANG_TEMPLATES, _TYPESCRIPT_NODE_TEMPLATES
    • _FINTECH_CRYPTO_TEMPLATES, _REMOTE_GLOBAL_TEMPLATES
    • _CAREER_PAGE_TEMPLATES   (improved with ATS exclusion operators)

  Tier 3 — Low-priority filler, high ATS overlap (remaining slots):
    • _ATS_SITE_TEMPLATES      — KEPT but demoted: Google occasionally surfaces
      companies NOT in companies.yaml. Only 3–5 of 25 slots used here.
    • _GENERAL_BACKEND_TEMPLATES — broad, lower precision

Budget
──────
  MAX_SERPER_CALLS = 25/run × 2 runs/day × 30 days = 1,500 credits/month
  (60% of Serper free tier 2,500/month — still safe)
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
# 25/run × 2 runs/day × 30 days = 1,500/month (60% of free-tier 2,500).
MAX_SERPER_CALLS = 25

# Guaranteed slots reserved for Tier-1 queries (unique, non-overlapping sources).
# Remaining MAX_SERPER_CALLS - TIER_1_BUDGET slots go to Tier-2 then Tier-3.
TIER_1_BUDGET = 10

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

# ── Bucket 1: ATS site-targeted (TIER 3 — low priority filler) ────────────
# IMPORTANT: ats.py already polls Greenhouse/Lever/Ashby/Workable/SmartRecruiters/
# Rippling/BambooHR/Recruitee/Personio via structured API for all companies in
# companies.yaml.  These site: queries mostly find the same jobs again.
# KEPT but demoted to Tier 3 (only 3–5 of 25 slots): Google occasionally surfaces
# companies NOT in companies.yaml — so there's marginal discovery value.
# Ashby (JS-rendered) and Workable (JS-rendered) REMOVED — scraping fails on those.
_ATS_SITE_TEMPLATES = [
    # Lever — HTML pages render with plain HTTP (scrapeable)
    'site:lever.co "{skill}" "intern" OR "fresher" "india" OR "remote" OR "bangalore"',
    'site:lever.co "backend" "{skill}" "india" OR "remote" OR "bengaluru"',

    # Greenhouse — HTML pages render with plain HTTP (scrapeable)
    'site:boards.greenhouse.io "{skill}" "intern" OR "fresher" "india" OR "remote"',
    'site:boards.greenhouse.io "backend" "{skill}" "india" OR "bangalore" 2026',

    # Combined — one query, two boards
    '(site:lever.co OR site:boards.greenhouse.io) "{skill}" "intern" OR "fresher" "india" OR "remote"',
    # REMOVED: site:jobs.ashbyhq.com — JS-rendered, scraping returns < 200 chars
    # REMOVED: site:apply.workable.com — JS-rendered, scraping returns < 200 chars
    # REMOVED: site:jobs.smartrecruiters.com — already in companies.yaml + ats.py
    # REMOVED: site:job.rippling.com — already in companies.yaml + ats.py
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

# ── Bucket 8: Company career pages (TIER 2 — with ATS exclusion operators) ──
# Adding -site: exclusions prevents hitting Greenhouse/Lever pages already
# covered by ats.py, forcing Google to return company-specific career pages.
_CAREER_PAGE_TEMPLATES = [
    'intitle:"careers" "backend intern" OR "backend fresher" site:*.in -site:internshala.com',
    '"we are hiring" "backend" "intern" OR "fresher" india -site:linkedin.com -site:naukri.com -site:greenhouse.io -site:lever.co',
    'intitle:"join us" "backend engineer" "fresher" site:*.io -site:greenhouse.io -site:ashbyhq.com',
    '"open positions" "backend" "golang" OR "typescript" "india" OR "remote" -site:linkedin.com -site:naukri.com',
    '"job openings" "backend engineer" "fresher" OR "intern" "india" 2026 -site:linkedin.com',
    '"currently hiring" "backend" OR "software" "intern" OR "fresher" india 2026 -site:linkedin.com -site:greenhouse.io -site:lever.co',
    '"engineering roles" "intern" OR "fresher" "backend" india startup 2026 -site:linkedin.com',
    '"hiring" "golang" OR "typescript" "backend" "intern" OR "fresher" site:*.io -site:greenhouse.io -site:ashbyhq.com -site:lever.co',
]


# ── Bucket 9: Company-owned career pages with ATS exclusions (TIER 1) ────────
# The KEY INSIGHT: by excluding all major ATS domains we force Google to return
# results from companies with their OWN /careers pages — exactly the companies
# NOT covered by ats.py.  This is Serper's unique, irreplaceable value-add.
_OWNED_CAREER_TEMPLATES = [
    '"backend intern" india 2026 -site:linkedin.com -site:naukri.com -site:greenhouse.io -site:lever.co -site:ashbyhq.com -site:workable.com -site:internshala.com',
    '"backend fresher" OR "software intern" india 2026 -site:linkedin.com -site:naukri.com -site:greenhouse.io -site:lever.co -site:smartrecruiters.com',
    '"golang developer" OR "go developer" "intern" OR "fresher" india -site:linkedin.com -site:naukri.com -site:lever.co -site:greenhouse.io -site:ashbyhq.com',
    '"typescript backend" OR "node.js backend" "intern" OR "fresher" india -site:linkedin.com -site:greenhouse.io -site:lever.co -site:smartrecruiters.com',
    'intitle:careers "backend" "intern" OR "fresher" site:*.in 2026 -site:internshala.com -site:hirist.tech -site:naukri.com',
    'intitle:careers "backend" "golang" OR "typescript" "india" OR "remote" site:*.io -site:greenhouse.io -site:ashbyhq.com -site:lever.co',
    '"we are hiring" "backend engineer" "fresher" OR "intern" india 2026 -site:linkedin.com -site:greenhouse.io -site:lever.co',
    '"open to applications" OR "accepting applications" "backend" "intern" OR "fresher" india 2026',
    '"engineering intern" OR "software intern" "golang" OR "typescript" india startup -site:linkedin.com -site:naukri.com -site:greenhouse.io',
    '"fintech" OR "payments" "backend" "intern" OR "fresher" india site:*.io -site:greenhouse.io -site:lever.co -site:linkedin.com',
]


# ── Bucket 10: Alternative ATS platforms not in companies.yaml (TIER 1) ──────
# These platforms are NOT handled by ats.py and NOT listed in companies.yaml.
# Keka, Freshteam (Freshworks ATS), and Zoho Recruit are widely used by Indian
# companies but absent from our structured API polling entirely.
_ALT_ATS_TEMPLATES = [
    # Keka — popular ATS among mid-size Indian tech companies
    'site:jobs.keka.com "backend" OR "software engineer" "intern" OR "fresher" india',
    'site:jobs.keka.com "golang" OR "typescript" OR "node" india',
    # Freshteam (Freshworks' own ATS — used by many Indian companies)
    'site:jobs.freshteam.com "backend" OR "software" "intern" OR "fresher" india',
    'site:jobs.freshteam.com "golang" OR "typescript" india 2026',
    # Zoho Recruit — extremely common in Indian mid-market tech
    'site:careers.zoho.com "backend" OR "software engineer" "intern" OR "fresher" india',
    'site:recruit.zohocloud.com "backend" OR "software" "intern" india',
    # Instahyre — not fully covered by instahyre.py
    'site:instahyre.com "backend" "golang" OR "typescript" "intern" OR "fresher"',
    # Cutshort — not reliably covered by cutshort.py (API issues)
    'site:cutshort.io "backend" "golang" OR "typescript" OR "node" "intern" OR "fresher" india',
    # Wellfound/AngelList — discoverable via Google even if direct scraping is blocked
    'site:wellfound.com/jobs "backend" "intern" OR "fresher" india OR remote',
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


def _expand_templates(
    templates: list[str],
    skills: list[str],
    locs: list[str],
    year: int,
) -> list[str]:
    """
    Expand a template list via Cartesian product over skills (and optionally locations).
    Module-level so it can be shared by _build_tiered_queries and build_dork_queries.
    """
    out = []
    for tpl in templates:
        if "{skill}" in tpl and "{city}" in tpl:
            for skill, city in product(skills[:2], locs[:2]):
                out.append(tpl.format(skill=skill, city=city, year=year))
        elif "{skill}" in tpl:
            for skill in skills:
                out.append(tpl.format(skill=skill, year=year))
        elif "{city}" in tpl:
            for city in locs[:2]:
                out.append(tpl.format(city=city, year=year))
        else:
            out.append(tpl.format(year=year) if "{year}" in tpl else tpl)
    return out


def _build_tiered_queries(profile: dict) -> tuple[list[str], list[str], list[str]]:
    """
    Generate query lists split into three priority tiers.

    Tier 1 — Unique / non-overlapping (guaranteed TIER_1_BUDGET slots):
      Jobs that NO other pipeline source can find:
      • _OWNED_CAREER_TEMPLATES : company-owned career pages (ATS exclusion operators)
      • _ALT_ATS_TEMPLATES      : Keka, Freshteam, Zoho, Cutshort, Wellfound
      • _HIDDEN_APPLY_TEMPLATES : Google Forms, Notion, Typeform, Airtable

    Tier 2 — High-value, language/domain-specific:
      • Golang, TypeScript, Fintech/Crypto, Remote-global, Career-page queries.

    Tier 3 — Low-priority filler (high ATS.py overlap):
      • _ATS_SITE_TEMPLATES     : Greenhouse/Lever site: queries
        (marginal value: can find companies NOT in companies.yaml)
      • _GENERAL_BACKEND_TEMPLATES: broad, lower precision.
    """
    import datetime
    year = datetime.datetime.now().year

    skill_tokens    = _build_skill_variants(profile)
    primary_skills  = skill_tokens[:4]
    broad_skills    = skill_tokens[:6]
    location_tokens = _build_location_variants(profile)
    primary_locs    = location_tokens[:2]

    def _dedup(queries: list[str], exclude: set[str] = None) -> list[str]:
        seen = set(exclude or [])
        result = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                result.append(q)
        return result

    # ── Tier 1: Guaranteed unique budget ──────────────────────────────────────
    t1_raw = []
    t1_raw.extend(_expand_templates(_OWNED_CAREER_TEMPLATES, primary_skills, primary_locs, year))
    t1_raw.extend(_expand_templates(_ALT_ATS_TEMPLATES,      primary_skills, primary_locs, year))
    t1_raw.extend(_expand_templates(_HIDDEN_APPLY_TEMPLATES, primary_skills, primary_locs, year))
    tier1 = _dedup(t1_raw)

    # ── Tier 2: High-value, language-specific ─────────────────────────────────
    t2_raw = []
    t2_raw.extend(_expand_templates(_GOLANG_TEMPLATES,          primary_skills, primary_locs, year))
    t2_raw.extend(_expand_templates(_TYPESCRIPT_NODE_TEMPLATES, primary_skills, primary_locs, year))
    t2_raw.extend(_expand_templates(_FINTECH_CRYPTO_TEMPLATES,  primary_skills, primary_locs, year))
    t2_raw.extend(_expand_templates(_REMOTE_GLOBAL_TEMPLATES,   primary_skills, primary_locs, year))
    t2_raw.extend(_expand_templates(_CAREER_PAGE_TEMPLATES,     primary_skills, primary_locs, year))
    tier2 = _dedup(t2_raw, exclude=set(tier1))

    # ── Tier 3: Filler — high ATS.py overlap ──────────────────────────────────
    t3_raw = []
    t3_raw.extend(_expand_templates(_ATS_SITE_TEMPLATES,        primary_skills, primary_locs, year))
    t3_raw.extend(_expand_templates(_GENERAL_BACKEND_TEMPLATES, broad_skills,   primary_locs, year))
    tier3 = _dedup(t3_raw, exclude=set(tier1) | set(tier2))

    return tier1, tier2, tier3


def build_dork_queries(profile: dict) -> list[str]:
    """
    Returns the full flat query pool across all tiers (for testing / inspection).
    The actual run uses _build_tiered_queries() for priority-aware selection.
    """
    t1, t2, t3 = _build_tiered_queries(profile)
    return t1 + t2 + t3


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
        # New: alt ATS platforms (Tier 1 targets)
        "keka.com", "freshteam.com", "zoho.com", "zohocloud.com",
        "instahyre.com", "cutshort.io", "wellfound.com",
        "notion.so", "notion.site",
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
    Main function: builds tiered dork queries, selects up to MAX_SERPER_CALLS
    with priority given to unique, non-overlapping sources, then runs them.

    Selection (25 calls total):
      • TIER_1_BUDGET (10) slots: guaranteed for Tier 1 (owned career pages,
        alt ATS, hidden apply).  These find jobs NO other source can.
      • Remaining 15 slots: Tier 2 first (Go/TS/fintech, shuffled), then
        Tier 3 filler (Greenhouse/Lever site:, general backend).

    Shuffling within each tier ensures full-pool coverage over many runs.

    Budget: 25/run × 2 runs/day × 30 days = 1,500 credits/month
            (60% of Serper free tier 2,500/month)
    """
    if profile is None:
        try:
            import yaml
            with open("profile.yaml") as f:
                profile = yaml.safe_load(f)
        except Exception:
            profile = {}

    tier1, tier2, tier3 = _build_tiered_queries(profile)

    # Shuffle within each tier for coverage diversity across runs
    random.shuffle(tier1)
    random.shuffle(tier2)
    random.shuffle(tier3)

    # Guarantee Tier-1 budget, then fill from Tier-2 → Tier-3
    t1_selected   = tier1[:TIER_1_BUDGET]
    remaining     = MAX_SERPER_CALLS - len(t1_selected)
    fill_pool     = tier2 + tier3   # tier2 comes first (higher priority)
    fill_selected = fill_pool[:remaining]
    selected_queries = t1_selected + fill_selected

    t2_count = min(len(tier2), remaining)
    t3_count = max(0, remaining - len(tier2))
    logger.info(
        f"Serper pool: {len(tier1)} tier-1 | {len(tier2)} tier-2 | {len(tier3)} tier-3. "
        f"Running: {len(t1_selected)} (unique) + {t2_count} (priority) + "
        f"{t3_count} (filler) = {len(selected_queries)} queries"
    )

    all_jobs  : list[dict] = []
    seen_urls : set[str]   = set()
    queries_run            = 0

    for query in selected_queries:
        logger.debug(f"Serper [{queries_run+1}/{len(selected_queries)}]: {query[:80]}")
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
