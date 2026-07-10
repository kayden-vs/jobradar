"""
pipeline/ranker.py
──────────────────
Pure-Python heuristic relevance ranker — v2.

Purpose
-------
Before sending eligible jobs to the Groq AI scorer, rank them by how
likely they are to be a strong match for the candidate. This serves two
goals:

1. Fix the "missing date = dropped" bug: the old approach sorted by
   posted_at descending, so jobs without a date were always pushed to the
   end and silently dropped by the cap. Now recency is one signal among
   many — a job with no date can still rank highly if it matches keywords.

2. Ensure the token budget (used to stop scoring early) is spent on the
   most promising jobs, not just the newest ones.

Architecture — Layered scoring with spread amplification
────────────────────────────────────────────────────────

  Layer 1 — Positive signal bonuses
    Additive score for stack, role, domain, recency, and project signals.
    All detection patterns are built DYNAMICALLY from profile.yaml, so
    the ranker works correctly for any candidate — not just backend/Go.

  Layer 2 — Skill Density scoring  (NEW in v2)
    Counts how many DISTINCT skills from the candidate's profile appear
    in the job text. A job mentioning Go + gRPC + PostgreSQL + Redis (4
    hits) scores dramatically higher than one mentioning only "Go" (1 hit).

  Layer 3 — Concordance & Multiplicative boosters  (NEW in v2)
    Title-Description Concordance: same skill in BOTH title AND desc.
    Holy Trinity: fresher + primary skill + backend all in title.
    Title Richness: multiple distinct signal types in title.
    These break the flat cluster that forms when many jobs share the
    same basic positive signals.

  Layer 4 — Penalty-augmented scoring
    Negative signals push clearly-weak jobs further down the queue.
    Includes NEW role mismatch penalties (TechOps, IT Ops, etc.)
    and lazy-fetch source exemptions (Workday/Naukri/freshers_blogs).

  Layer 5 — Source-aware adaptive weighting
    Not all sources produce equally reliable signals. Per-source offsets
    are applied last to reflect structural data-quality differences.

  Layer 6 — Location Affinity  (NEW in v2)
    Jobs explicitly mentioning India cities or Remote get a boost.

Fully configurable — zero hardcoded preferences
-------------------------------------------------
  All numeric values live in `ranker_weights` in profile.yaml.
  ALL detection patterns (stack, domain, project, synergy) are derived
  dynamically from the candidate's `skills`, `industries`, and `projects`
  sections in profile.yaml — so any candidate can use this tool by
  editing only profile.yaml, with no code changes needed.

  `_resolve_weights(weights)` merges caller dict with `_DEFAULT_WEIGHTS`.
  `build_profile_patterns(profile)` builds compiled regexes from profile.

Scores can be negative. sort(reverse=True) handles this correctly.
"""

import re
import logging
import statistics
from datetime import datetime, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT WEIGHTS  (numeric config; mirrors profile.yaml)
#
# These are fallbacks when a key is absent from profile.yaml ranker_weights.
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_WEIGHTS: dict = {
    # ── Positive signal bonuses ───────────────────────────────────────────
    "primary_skill_title":    5,   # top-priority skill in title (e.g. Go, Python, Rust)
    "primary_skill_desc":     3,   # top-priority skill in description only
    "secondary_skill_title":  3,   # secondary-priority skill in title (e.g. TypeScript)
    "secondary_skill_desc":   1,   # secondary-priority skill in description only
    "backend_title":          2,   # backend / REST / microservice in title
    "backend_desc":           1,   # backend keyword in description only
    "fresher_title":          2,   # intern / fresher / junior / entry-level in title
    "high_priority_domain":   3,   # high-priority industry domain anywhere in text
    "med_priority_domain":    1,   # medium-priority industry domain anywhere in text
    "project_signal_per_hit": 2,   # per matching project relevance signal
    "project_signal_max":     4,   # cap on total project bonus
    "desc_quality":           1,   # bonus for description ≥ desc_quality_min_chars
    "desc_quality_min_chars": 200,
    "has_date":               1,   # job has any posted_at value
    # ── Recency bonuses ───────────────────────────────────────────────────
    # Day bucket thresholds are structural (7/14/30 days) and not
    # configurable; only the bonus *values* are.
    "recency_7d":             4,
    "recency_14d":            2,
    "recency_30d":            1,
    # ── Skill Density (NEW v2) ────────────────────────────────────────────
    # Counts distinct skills found in job text. More matches = stronger signal.
    "skill_density_per_hit":  1,   # bonus per distinct skill matched (beyond first)
    "skill_density_max":      6,   # cap on total density bonus
    # ── Concordance & Multiplicative Boosters (NEW v2) ────────────────────
    "concordance_bonus":      3,   # same primary skill found in BOTH title AND desc
    "holy_trinity_bonus":     4,   # fresher + primary skill + backend ALL in title
    "title_richness_2":       1,   # 2 distinct signal types in title
    "title_richness_3":       2,   # 3+ distinct signal types in title
    # ── Location Affinity (NEW v2) ────────────────────────────────────────
    "location_india_bonus":   2,   # job explicitly mentions India city or Remote
    # ── Company Tier (NEW v2) ─────────────────────────────────────────────
    "company_tier_bonus":     1,   # company appears in curated companies.yaml
    # ── Penalty signals ───────────────────────────────────────────────────
    "penalty_no_skill_match":    -3,  # no skill keyword anywhere in job text
    "penalty_generic_title":     -2,  # title has no recognisable tech/role signal
    "penalty_bodyshop_company":  -1,  # company looks like staffing / outsourcing firm
    "penalty_ats_stub_desc":     -2,  # ATS source + description below threshold
    "ats_stub_desc_threshold":   300,
    "penalty_role_mismatch":     -4,  # clearly non-backend role (TechOps, IT Ops, etc.)
    "penalty_seniority_level":   -8,  # Senior/Staff/Principal/Lead/SDE III+ in title
    "penalty_old_job":          -10,  # job posted >10 days ago or previous year
    # ── Synergy bonuses ───────────────────────────────────────────────────
    "synergy_skill_domain":   3,   # primary skill AND high-priority domain both found
    "synergy_skill_project":  2,   # primary skill AND a project signal both found
    # ── Source-aware adjustments ──────────────────────────────────────────
    "source_internshala_stipend_bonus":  2,
    "source_internshala_stipend_min":    10_000,
    "source_freshers_blog_batch_bonus":  1,
    "source_freshers_blog_batches":      [2025, 2026, 2027],
    "source_naukri_stub_penalty":       -1,
    "source_naukri_stub_threshold":      150,
    "source_serper_penalty":            -1,
    # Telegram channels: curated Indian job posts, high signal but short
    # descriptions — needs a boost so they're not buried by ATS jobs
    # with long keyword-rich JDs that score higher on skill density.
    "source_telegram_boost":             3,
    "source_workday_bonus":              2,   # Workday companies are curated ATS employers (Cisco, Adobe, etc.)
}


# ─────────────────────────────────────────────────────────────────────────────
# STRUCTURAL PATTERNS  (not user-preferences; safe to keep in code)
#
# These detect role-level signals and data-quality issues that don't vary
# by candidate preference. Kept in code because they're structural constants,
# not tunable preferences.
# ─────────────────────────────────────────────────────────────────────────────

# Fresher / intern target role signals (universally relevant, not stack-specific)
_FRESHER_TITLE_RE = re.compile(
    r'\bintern\b|\bfresher\b|\btrainee\b|\bjunior\b|\bentry.?level\b|\bgraduate\b|\bsde.?1\b',
    re.IGNORECASE,
)

# Backend / API / microservice — architectural pattern signal (not Go-specific)
_BACKEND_RE = re.compile(
    r'\bbackend\b|\bback.?end\b|\brest\s*api\b|\bmicroservice\b|\bserver.?side\b',
    re.IGNORECASE,
)

# Generic title detection: catches titles with NO recognisable tech/role signal.
# This list is intentionally broad — it covers many disciplines, not just backend.
_TECH_ROLE_TITLE_RE = re.compile(
    r'\bengineer\b|\bdeveloper\b|\bdev\b|\bprogrammer\b|\bsde\b|\bswe\b'
    r'|\bintern\b|\bfresher\b|\btrainee\b|\bassociate\b|\bspecialist\b'
    r'|\bbackend\b|\bfullstack\b|\bfull.?stack\b|\bfrontend\b'
    r'|\bplatform\b|\binfrastructure\b|\bdevops\b|\bsre\b|\bsecurity\b'
    r'|\banalyst\b|\barchitect\b|\bscientist\b|\bresearcher\b'
    r'|\bapi\b|\bmicroservice\b|\bcloud\b|\bdata\b|\bml\b|\bai\b',
    re.IGNORECASE,
)

# Body-shop / staffing firm signals in company name (generic, not industry-specific)
_BODYSHOP_RE = re.compile(
    r'\bit\s+solutions\b|\btech\s+solutions\b|\bsoftware\s+solutions\b'
    r'|\bstaffing\b|\brecruiting\b|\boutsourc\b|\bconsultanc\b'
    r'|\bmanpower\b|\bresourcing\b|\bplacement[s]?\b|\bservices\s+pvt\b',
    re.IGNORECASE,
)

# NEW v2: Role mismatch — clearly non-backend roles that slip through prefilter
# These roles have tech overlap (so prefilter doesn't catch them) but are NOT
# backend SWE/engineering roles. They should rank below real backend jobs.
_ROLE_MISMATCH_RE = re.compile(
    r'\btechops\b|\btech\s*ops\b|\bit\s+operations?\b|\bit\s+support\b'
    r'|\bsystems?\s+admin\b|\bnetwork\s+engineer\b|\bnetwork\s+admin\b'
    r'|\bsupport\s+engineer\b|\btechnical\s+support\b|\bhelp\s*desk\b'
    r'|\bsolutions?\s+architect\b|\bsales\s+engineer\b|\bcustomer\s+engineer\b'
    r'|\btechnical\s+writer\b|\bdocumentation\s+engineer\b'
    r'|\bhardware\b|\belectrical\b|\bmechanical\b'
    r'|\brelease\s+engineer\b|\bbuild\s+engineer\b'
    r'|\bsite\s+reliability\b'
    r'|\betl\b|\bbi\s+developer\b|\bbi\s+engineer\b'
    r'|\bembedded\b|\bfirmware\b',
    re.IGNORECASE,
)

# NEW v3: Seniority-level mismatch — catches Senior/Staff/Principal/Lead titles
# that slip through the prefilter role_blacklist. Separate from _ROLE_MISMATCH_RE
# because the *role* might be fine (Backend Engineer) but the *level* is wrong
# (Staff/Principal/SDE IV). v3 run showed ~25 senior jobs wasting AI tokens,
# all scored 1-2/10.
_SENIORITY_LEVEL_RE = re.compile(
    r'\bsenior\b|\bstaff\b|\bprincipal\b'
    r'|\blead\b'
    r'|\bsde\s*(?:ii?i|iv|[3-9])\b'         # SDE III, SDE IV, SDE 3+
    r'|\b(?:engineer|developer)\s+(?:ii?i|iv|[3-9])\b'  # "Developer III"
    r'|\bengineering\s*manager\b'
    r'|\btech\s*lead\b',
    re.IGNORECASE,
)

# ATS sources with structured, reliable location/title fields
_ATS_SOURCES = {"greenhouse", "greenhouse_eu", "lever", "ashby", "workable", "workday"}

# Sources where description is lazy-fetched AFTER ranking.
# These must NOT be penalized for short/empty descriptions at rank time.
_LAZY_FETCH_SOURCES = {"workday", "naukri"}
# freshers_blogs is prefix-matched separately (source starts with "freshers_blogs")

# Internshala stipend extraction
_STIPEND_NUM_RE = re.compile(r'\d+')

# NEW v2: India/Remote location affinity
_INDIA_REMOTE_RE = re.compile(
    r'\bindia\b|\bbangalore\b|\bbengaluru\b|\bmumbai\b|\bhyderabad\b'
    r'|\bdelhi\b|\bpune\b|\bchennai\b|\bkolkata\b|\bnoida\b'
    r'|\bgurgaon\b|\bgurugram\b'
    r'|\bremote\b|\bwork\s*from\s*home\b|\bwfh\b|\bpan\s*india\b'
    r'|\banywhere\b|\bworldwide\b|\bglobal\b',
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE-DRIVEN PATTERN BUILDER  (no hardcoded preferences)
#
# Builds all candidate-preference regexes from profile.yaml at runtime.
# A cybersecurity candidate, a data engineer, a mobile developer — any
# profile works without changing a single line of code.
# ─────────────────────────────────────────────────────────────────────────────

class ProfilePatterns(NamedTuple):
    """Compiled regexes derived from profile.yaml — rebuilt on each run."""
    primary_skill_re:     re.Pattern   # top-priority skills (deduplicated)
    secondary_skill_re:   re.Pattern   # remaining skills
    skill_match_re:       re.Pattern   # any skill keyword (for penalty check)
    high_domain_re:       re.Pattern   # high-priority industries
    med_domain_re:        re.Pattern   # medium-priority industries
    project_signal_res:   list         # per-project compiled regexes
    synergy_domain_re:    re.Pattern   # same as high_domain_re (alias for clarity)
    all_skill_patterns:   list         # individual skill regexes for density counting
    primary_skill_list:   list         # raw primary skill strings for concordance check
    curated_companies:    set          # company names from companies.yaml for tier bonus


def _escape_keywords(keywords: list[str]) -> str:
    """Join a list of strings into a regex alternation, escaping each term."""
    return "|".join(re.escape(k) for k in keywords if k)


def _deduplicate_skills(skills: list[str]) -> list[str]:
    """
    Remove conceptual duplicates from a skill list.

    "Go" and "Golang" are the same skill — keeping both wastes a primary
    slot. This deduplicator keeps the first occurrence and drops later
    synonyms. Synonym groups are defined below.
    """
    # Synonym groups: all variations map to the canonical (first) form.
    # Add more groups here as needed — each inner list is one "concept".
    synonym_groups = [
        ["Go", "Golang"],
        ["TypeScript", "TS"],
        ["JavaScript", "JS"],
        ["Node.js", "NodeJS", "Node"],
        ["PostgreSQL", "Postgres"],
        ["Kubernetes", "K8s"],
        ["gRPC", "GRPC"],
    ]

    # Build a lowercase → canonical map
    canonical_map: dict[str, str] = {}
    for group in synonym_groups:
        canon = group[0].lower()
        for variant in group:
            canonical_map[variant.lower()] = canon

    seen_canonical: set[str] = set()
    result: list[str] = []

    for skill in skills:
        canon = canonical_map.get(skill.lower(), skill.lower())
        if canon not in seen_canonical:
            seen_canonical.add(canon)
            result.append(skill)

    return result


def _load_curated_companies() -> set[str]:
    """
    Load company names from companies.yaml to build the company tier set.

    Returns a set of lowercase company names/slugs that the user has
    curated. Jobs from these companies get a small tier bonus.
    """
    import yaml
    import os

    companies_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "companies.yaml")
    if not os.path.exists(companies_path):
        return set()

    try:
        with open(companies_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return set()

    names: set[str] = set()
    for platform, entries in data.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                # Slug-style entries: "razorpaysoftwareprivatelimited" → add as-is
                names.add(entry.lower())
            elif isinstance(entry, dict) and "name" in entry:
                # Workday-style entries: {"name": "Adobe", ...}
                names.add(entry["name"].lower())
                if "tenant" in entry:
                    names.add(entry["tenant"].lower())
    return names


def build_profile_patterns(profile: dict) -> ProfilePatterns:
    """
    Build all candidate-preference regex patterns from profile.yaml.

    Called once per run in rank_eligible_jobs. The result is passed
    through to all sub-functions so they never reference hardcoded terms.

    Profile keys used:
      candidate.skills.strong   → primary + secondary skill regexes
      candidate.industries.*    → domain regexes
      candidate.projects        → project signal regexes (from relevance_signal)
    """
    candidate = profile.get("candidate", {})
    skills    = candidate.get("skills", {})
    industries = candidate.get("industries", {})

    # ── Stack skills ──────────────────────────────────────────────────────
    # Keep original skill strings for regex building (Go AND Golang both
    # need to match in job text). Deduplication only controls SLOT COUNTING
    # — how many skills count as "primary" vs "secondary".
    raw_strong_skills = [s for s in skills.get("strong", []) if s]
    learning_skills = [s for s in skills.get("learning", []) if s]

    # Deduplicate to count concept-slots: Go/Golang = 1 slot, not 2
    deduped_strong = _deduplicate_skills(raw_strong_skills)

    # Primary concepts: first 3 unique skill CONCEPTS after deduplication
    primary_concepts  = set(s.lower() for s in deduped_strong[:3]) if deduped_strong else set()
    # Secondary concepts: remaining strong + learning
    secondary_concepts = set(s.lower() for s in deduped_strong[3:] + learning_skills)

    # Map original skill strings into primary/secondary based on their concept
    synonym_groups = [
        ["Go", "Golang"], ["TypeScript", "TS"], ["JavaScript", "JS"],
        ["Node.js", "NodeJS", "Node"], ["PostgreSQL", "Postgres"],
        ["Kubernetes", "K8s"], ["gRPC", "GRPC"],
    ]
    _canon_map: dict[str, str] = {}
    for group in synonym_groups:
        canon = group[0].lower()
        for variant in group:
            _canon_map[variant.lower()] = canon

    # Split original skill strings into primary/secondary lists
    # Primary regex includes ALL variants of primary concepts (Go AND Golang)
    primary_skill_strings: list[str] = []
    secondary_skill_strings: list[str] = []
    for skill in raw_strong_skills:
        canon = _canon_map.get(skill.lower(), skill.lower())
        if canon in primary_concepts:
            primary_skill_strings.append(skill)
        else:
            secondary_skill_strings.append(skill)
    secondary_skill_strings += learning_skills

    # For the primary_skill_list (used in concordance), use original strings
    primary_skills = primary_skill_strings

    # Build individual skill regexes for density counting (NEW v2)
    # Use ALL original skill strings (no dedup) so each variant matches
    all_skills_raw = raw_strong_skills + learning_skills
    all_skill_patterns = []
    seen_density_canons: set[str] = set()
    for skill in all_skills_raw:
        # Deduplicate for DENSITY counting (Go and Golang = 1 density hit)
        canon = _canon_map.get(skill.lower(), skill.lower())
        if canon in seen_density_canons:
            continue
        seen_density_canons.add(canon)
        # Build regex that matches ALL variants of this concept
        variants = [skill]
        for group in synonym_groups:
            if skill.lower() in [v.lower() for v in group]:
                variants = group
                break
        try:
            pattern = _escape_keywords(variants)
            pat = re.compile(r'\b(?:' + pattern + r')\b', re.IGNORECASE)
            all_skill_patterns.append((skill, pat))
        except re.error:
            pass

    # Build primary/secondary/all skill regexes
    if primary_skill_strings:
        primary_pattern = _escape_keywords(primary_skill_strings)
        primary_skill_re = re.compile(r'\b(?:' + primary_pattern + r')\b', re.IGNORECASE)
    else:
        primary_skill_re = re.compile(r'(?!x)x')  # never matches — safe no-op

    if secondary_skill_strings:
        secondary_pattern = _escape_keywords(secondary_skill_strings)
        secondary_skill_re = re.compile(r'\b(?:' + secondary_pattern + r')\b', re.IGNORECASE)
    else:
        secondary_skill_re = re.compile(r'(?!x)x')

    # Stack match: any skill at all — used for the "no skill match" penalty
    all_skills = raw_strong_skills + learning_skills
    if all_skills:
        all_skills_pattern = _escape_keywords(all_skills)
        skill_match_re = re.compile(r'\b(?:' + all_skills_pattern + r')\b', re.IGNORECASE)
    else:
        skill_match_re = re.compile(r'(?!x)x')

    # ── Industry domains ──────────────────────────────────────────────────
    high_priority   = industries.get("high_priority", [])
    med_priority    = industries.get("medium_priority", [])

    if high_priority:
        high_domain_re = re.compile(
            r'\b(?:' + _escape_keywords(high_priority) + r')\b', re.IGNORECASE
        )
    else:
        high_domain_re = re.compile(r'(?!x)x')

    if med_priority:
        med_domain_re = re.compile(
            r'\b(?:' + _escape_keywords(med_priority) + r')\b', re.IGNORECASE
        )
    else:
        med_domain_re = re.compile(r'(?!x)x')

    # ── Project relevance signals ─────────────────────────────────────────
    # Each project's relevance_signal field contains comma-separated keywords.
    # We compile one regex per project from its signal keywords.
    project_signal_res = []
    for proj in candidate.get("projects", []):
        signal_text = proj.get("relevance_signal", "")
        if not signal_text:
            continue
        # Split on comma/semicolon and clean up
        keywords = [k.strip() for k in re.split(r'[,;]', signal_text) if k.strip()]
        if keywords:
            pattern = _escape_keywords(keywords)
            project_signal_res.append(re.compile(
                r'\b(?:' + pattern + r')\b', re.IGNORECASE
            ))

    # ── Curated companies ─────────────────────────────────────────────────
    curated_companies = _load_curated_companies()

    return ProfilePatterns(
        primary_skill_re   = primary_skill_re,
        secondary_skill_re = secondary_skill_re,
        skill_match_re     = skill_match_re,
        high_domain_re     = high_domain_re,
        med_domain_re      = med_domain_re,
        project_signal_res = project_signal_res,
        synergy_domain_re  = high_domain_re,  # alias
        all_skill_patterns = all_skill_patterns,
        primary_skill_list = primary_skills,
        curated_companies  = curated_companies,
    )


# ─────────────────────────────────────────────────────────────────────────────
# WEIGHT RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_weights(weights: dict | None) -> dict:
    """
    Merge caller-provided ranker_weights with module defaults.

    Any key present in `weights` overrides the corresponding default.
    Keys absent from `weights` fall back to `_DEFAULT_WEIGHTS`.
    If `weights` is None or empty, a copy of `_DEFAULT_WEIGHTS` is returned.
    """
    if not weights:
        return _DEFAULT_WEIGHTS.copy()
    merged = _DEFAULT_WEIGHTS.copy()
    merged.update(weights)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# RECENCY HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _recency_bonus(posted_at: str | None, w: dict) -> int:
    """
    Return a recency bonus based on how recently the job was posted.

    Jobs with no date get 0 — not penalised (fixes the original bug where
    undated jobs were sorted to the bottom and dropped by the cap).

    Day buckets are structural constants (7/14/30 days).
    Bonus values come from w["recency_7d"], w["recency_14d"], w["recency_30d"].
    """
    if not posted_at:
        return 0

    s = str(posted_at).strip().lower()

    # Fast path: relative strings — avoids dateutil parse for common cases
    relative_map = [
        (r'\btoday\b|\b[0-9]?\s*hour[s]?\s+ago\b|\bminute[s]?\s+ago\b', w["recency_7d"]),
        (r'\b[1-6]\s*day[s]?\s+ago\b|\byesterday\b',                     w["recency_7d"]),
        (r'\b[7-9]\s*day[s]?\s+ago\b|\b1\s*week\s+ago\b',               w["recency_14d"]),
        (r'\b10\s*day[s]?\s+ago\b',                                      w["recency_14d"]),
        (r'\b1[1-9]\s*day[s]?\s+ago\b|\b[2-9][0-9]\s*day[s]?\s+ago\b',  w.get("penalty_old_job", -10)),
        (r'\b[1-9]\s*month[s]?\s+ago\b',                                 w.get("penalty_old_job", -10)),
        (r'\byear[s]?\s+ago\b',                                          w.get("penalty_old_job", -10)),
        (r'\b2024\b|\b2025\b',                                           w.get("penalty_old_job", -10)),
    ]
    for pattern, bonus in relative_map:
        if re.search(pattern, s):
            return bonus

    # ISO / epoch parse fallback
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(str(posted_at))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= 7:
            return w["recency_7d"]
        if age_days <= 14:
            return w["recency_14d"]
        if age_days <= 30:
            return w["recency_30d"]
    except Exception:
        pass

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# LAZY-FETCH SOURCE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _is_lazy_fetch_source(source: str) -> bool:
    """
    Returns True for sources whose description is NOT available at ranking
    time because it's lazy-fetched later (in scorer.py after ranking).

    These sources must NOT be penalized for short/empty descriptions —
    that's a data availability issue, not a quality signal.
    """
    if source in _LAZY_FETCH_SOURCES:
        return True
    if source.startswith("freshers_blogs"):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# SKILL DENSITY SCORER  (NEW v2)
# ─────────────────────────────────────────────────────────────────────────────

def _skill_density_score(
    full_text: str,
    pp: ProfilePatterns,
    w: dict,
) -> tuple[int, list[str]]:
    """
    Count how many DISTINCT skills from the candidate's profile appear
    in the job text. More matches = stronger signal.

    A job mentioning Go + gRPC + PostgreSQL + Redis + Docker (5 distinct
    skills) is a dramatically stronger match than one mentioning only "Go".

    The first skill match is already counted by primary/secondary bonuses,
    so density bonus starts from the 2nd match onwards.

    Returns:
        (bonus, reasons) — bonus capped at w["skill_density_max"].
    """
    if not pp.all_skill_patterns:
        return 0, []

    matched_skills = []
    for skill_name, skill_re in pp.all_skill_patterns:
        if skill_re.search(full_text):
            matched_skills.append(skill_name)

    # Density bonus kicks in from 2nd match onwards (1st is already counted)
    extra_matches = max(0, len(matched_skills) - 1)
    if extra_matches == 0:
        return 0, []

    per_hit = w["skill_density_per_hit"]
    cap     = w["skill_density_max"]
    bonus   = min(extra_matches * per_hit, cap)

    return bonus, [f"skill-density({bonus:+}|{len(matched_skills)}hits:{','.join(matched_skills[:5])})"]


# ─────────────────────────────────────────────────────────────────────────────
# CONCORDANCE & MULTIPLICATIVE BOOSTERS  (NEW v2)
# ─────────────────────────────────────────────────────────────────────────────

def _concordance_and_boosters(
    title: str,
    desc: str,
    has_primary_title: bool,
    has_fresher_title: bool,
    has_backend_title: bool,
    pp: ProfilePatterns,
    w: dict,
) -> tuple[int, list[str]]:
    """
    Compute concordance and multiplicative booster bonuses.

    Three new signals that break score flatness:

    1. Title-Description Concordance: If a primary skill appears in BOTH
       the title AND the description, that's a much stronger signal than
       appearing in only one. The old system used elif (title OR desc,
       never both). This grants an ADDITIONAL concordance bonus.

    2. Holy Trinity: If fresher_title + primary_skill_title + backend_title
       ALL match, this is the exact profile of a job the AI consistently
       scores 8+. Grant a multiplicative boost.

    3. Title Richness: Count distinct signal TYPES in the title (skill,
       role-level, backend, domain). More types = higher confidence.
    """
    delta   = 0
    reasons = []

    # ── 1. Title-Description Concordance ──────────────────────────────────
    # Check if any primary skill appears in BOTH title AND description
    if has_primary_title and desc:
        for skill in pp.primary_skill_list:
            skill_re = re.compile(r'\b' + re.escape(skill) + r'\b', re.IGNORECASE)
            if skill_re.search(title) and skill_re.search(desc):
                b = w["concordance_bonus"]
                delta += b
                reasons.append(f"concordance({b:+}|{skill})")
                break  # One concordance hit is enough

    # ── 2. Holy Trinity (fresher + primary skill + backend in title) ──────
    if has_primary_title and has_fresher_title and has_backend_title:
        b = w["holy_trinity_bonus"]
        delta += b
        reasons.append(f"holy-trinity({b:+})")

    # ── 3. Title Richness ─────────────────────────────────────────────────
    # Count distinct signal types present in the title
    signal_types = 0
    if has_primary_title or pp.secondary_skill_re.search(title):
        signal_types += 1  # skill signal
    if has_fresher_title:
        signal_types += 1  # level signal
    if has_backend_title:
        signal_types += 1  # architecture signal
    if pp.high_domain_re.search(title) or pp.med_domain_re.search(title):
        signal_types += 1  # domain signal

    if signal_types >= 3:
        b = w["title_richness_3"]
        delta += b
        reasons.append(f"title-richness({b:+}|{signal_types}types)")
    elif signal_types >= 2:
        b = w["title_richness_2"]
        delta += b
        reasons.append(f"title-richness({b:+}|{signal_types}types)")

    return delta, reasons


# ─────────────────────────────────────────────────────────────────────────────
# PENALTY + SYNERGY SCORER
# ─────────────────────────────────────────────────────────────────────────────

def _penalty_score(
    job: dict,
    has_primary_skill: bool,
    has_high_domain: bool,
    has_project: bool,
    pp: ProfilePatterns,
    w: dict,
) -> tuple[int, list[str]]:
    """
    Compute penalty and synergy-bonus adjustments for a job.

    All detection uses profile-derived patterns (pp), not hardcoded terms.

    Penalties:
      no skill match anywhere   → w["penalty_no_skill_match"]    (default −3)
      generic title             → w["penalty_generic_title"]     (default −2)
      bodyshop company          → w["penalty_bodyshop_company"]  (default −1)
      ATS stub description      → w["penalty_ats_stub_desc"]     (default −2)
      role mismatch (NEW v2)    → w["penalty_role_mismatch"]     (default −4)

    Synergy bonuses:
      primary-skill + high-priority domain → w["synergy_skill_domain"] (+3)
      primary-skill + project signal       → w["synergy_skill_project"] (+2)
    """
    delta   = 0
    reasons = []

    title     = job.get("title", "")
    desc      = job.get("description", "")
    company   = job.get("company", "")
    full_text = title + " " + desc
    source    = job.get("source", "")

    is_lazy = _is_lazy_fetch_source(source)

    # ── No skill match anywhere ───────────────────────────────────────────
    # Skip this penalty for lazy-fetch sources — they have stub descriptions
    # at ranking time; the scorer lazy-fetches the full post body later.
    if not is_lazy and not pp.skill_match_re.search(full_text):
        p = w["penalty_no_skill_match"]
        delta += p
        reasons.append(f"no-skill-match({p:+})")

    # ── Generic title ─────────────────────────────────────────────────────
    if not _TECH_ROLE_TITLE_RE.search(title):
        p = w["penalty_generic_title"]
        delta += p
        reasons.append(f"generic-title({p:+})")

    # ── Body-shop company ─────────────────────────────────────────────────
    if _BODYSHOP_RE.search(company):
        p = w["penalty_bodyshop_company"]
        delta += p
        reasons.append(f"bodyshop-company({p:+})")

    # ── ATS stub description ──────────────────────────────────────────────
    # FIXED v2: Exempt lazy-fetch sources — they haven't fetched their
    # description yet. Penalizing them for empty descriptions is a bug.
    if not is_lazy and source in _ATS_SOURCES and len(desc) < w["ats_stub_desc_threshold"]:
        p = w["penalty_ats_stub_desc"]
        delta += p
        reasons.append(f"ats-stub-desc({p:+})")

    # ── Role mismatch (NEW v2) ────────────────────────────────────────────
    # Detect clearly non-backend roles that slip through the prefilter.
    # TechOps at Binance scored 8/10 urgent — this prevents that.
    if _ROLE_MISMATCH_RE.search(title):
        p = w["penalty_role_mismatch"]
        delta += p
        reasons.append(f"role-mismatch({p:+})")

    # ── Seniority-level mismatch (NEW v3) ─────────────────────────────
    # Senior/Staff/Principal/Lead/SDE III+ are experience-level mismatches
    # for a fresher candidate. Stronger penalty than role_mismatch because
    # seniority is a harder barrier than role type.
    if _SENIORITY_LEVEL_RE.search(title):
        p = w["penalty_seniority_level"]
        delta += p
        reasons.append(f"seniority-level({p:+})")

    # ── Synergy: primary skill + high-priority domain ─────────────────────
    if has_primary_skill and has_high_domain:
        b = w["synergy_skill_domain"]
        delta += b
        reasons.append(f"synergy:skill+domain({b:+})")

    # ── Synergy: primary skill + project signal ───────────────────────────
    if has_primary_skill and has_project:
        b = w["synergy_skill_project"]
        delta += b
        reasons.append(f"synergy:skill+project({b:+})")

    return delta, reasons


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE-AWARE ADJUSTMENT
# ─────────────────────────────────────────────────────────────────────────────

def _source_adjustment(job: dict, w: dict) -> tuple[int, list[str]]:
    """
    Compute a source-specific score adjustment reflecting structural signal
    quality differences between job sources.

    Offsets applied AFTER all other scoring:
      internshala + stipend ≥ min   → w["source_internshala_stipend_bonus"] (+2)
      freshers_blogs + batch match  → w["source_freshers_blog_batch_bonus"] (+1)
      naukri + stub description     → w["source_naukri_stub_penalty"]       (−1)
      serper                        → w["source_serper_penalty"]            (−1)
      workday                       → w["source_workday_bonus"]             (+2)
      telegram_channels             → w["source_telegram_boost"]            (+3)
    """
    delta   = 0
    reasons = []

    source = job.get("source", "")
    desc   = job.get("description", "")

    # ── Internshala: stipend bonus ────────────────────────────────────────
    if source == "internshala":
        salary_raw  = job.get("salary", "") or ""
        min_stipend = w["source_internshala_stipend_min"]
        nums = _STIPEND_NUM_RE.findall(salary_raw.replace(",", ""))
        for num_str in nums:
            try:
                if int(num_str) >= min_stipend:
                    b = w["source_internshala_stipend_bonus"]
                    delta += b
                    reasons.append(f"internshala-stipend≥{min_stipend}({b:+})")
                    break
            except ValueError:
                pass

    # ── freshers_blogs: graduation batch tag bonus ────────────────────────
    if source.startswith("freshers_blogs"):
        matching_batches = {str(y) for y in w.get("source_freshers_blog_batches", [])}
        for tag in job.get("batch_tags", []):
            tag_str = str(tag)
            if any(year in tag_str for year in matching_batches):
                b = w["source_freshers_blog_batch_bonus"]
                delta += b
                reasons.append(f"batch-tag-match({b:+}):{tag_str[:20]}")
                break

    # ── Naukri: stub description penalty ─────────────────────────────────
    # NOTE: This is a SOURCE-LEVEL quality penalty, separate from the
    # ATS stub penalty. Naukri stubs are intentionally short snippets
    # from Stage-1 results, not missing data. The penalty reflects
    # lower confidence in ranking accuracy, not a data bug.
    if source == "naukri" and len(desc) < w["source_naukri_stub_threshold"]:
        p = w["source_naukri_stub_penalty"]
        delta += p
        reasons.append(f"naukri-stub-desc({p:+})")

    # ── Serper: dork result quality penalty ───────────────────────────────
    if source == "serper":
        p = w["source_serper_penalty"]
        delta += p
        reasons.append(f"serper-quality({p:+})")

    # ── Workday: curated ATS employer bonus ────────────────────────────────────
    # Workday companies in companies.yaml are curated employers (Cisco,
    # Adobe, Samsung, BrowserStack, etc.) — structurally reliable ATS data.
    # Stub description is compact by design; this bonus compensates for the
    # lower-content stub vs sources that arrive with full descriptions.
    if source == "workday":
        b = w.get("source_workday_bonus", 2)
        delta += b
        reasons.append(f"workday-curated({b:+})")

    # ── Telegram channels: high-signal Indian job posts, short descriptions ──
    # These arrive with thin descriptions (2-4 sentences from Telegram posts)
    # vs ATS jobs with 500-1500 word JDs, causing skill-density scoring to
    # systematically underrank them. This boost corrects that structural bias.
    if source == "telegram_channels":
        b = w.get("source_telegram_boost", 3)
        delta += b
        reasons.append(f"telegram-high-signal({b:+})")

    return delta, reasons


# ─────────────────────────────────────────────────────────────────────────────
# LOCATION AFFINITY  (NEW v2)
# ─────────────────────────────────────────────────────────────────────────────

def _location_affinity(job: dict, w: dict) -> tuple[int, list[str]]:
    """
    Boost jobs that explicitly mention India cities or Remote/WFH.

    This is NOT a rejection mechanism (that's prefilter's job) — it's a
    positive signal that differentiates among surviving jobs. A job with
    "Bangalore, Remote" in its location field is a stronger India-fit
    signal than one with no location info.
    """
    location = job.get("location", "")
    title    = job.get("title", "")
    # Check location field and title (some sources embed location in title)
    text = location + " " + title

    if _INDIA_REMOTE_RE.search(text):
        b = w["location_india_bonus"]
        return b, [f"location-affinity({b:+})"]

    return 0, []


# ─────────────────────────────────────────────────────────────────────────────
# COMPANY TIER  (NEW v2)
# ─────────────────────────────────────────────────────────────────────────────

def _company_tier(job: dict, pp: ProfilePatterns, w: dict) -> tuple[int, list[str]]:
    """
    Small bonus for jobs from companies in the curated companies.yaml.

    These are companies the user specifically chose to track — they're
    inherently more likely to be relevant than random unknown companies.
    """
    if not pp.curated_companies:
        return 0, []

    company = job.get("company", "").lower()
    if not company:
        return 0, []

    # Check if the company name (or any word in it) matches a curated slug
    if company in pp.curated_companies:
        b = w["company_tier_bonus"]
        return b, [f"curated-company({b:+})"]

    # Also check individual words (company name might be "Cloudflare Inc" but slug is "cloudflare")
    for word in company.split():
        cleaned = word.strip(".,()[]")
        if cleaned and cleaned in pp.curated_companies:
            b = w["company_tier_bonus"]
            return b, [f"curated-company({b:+})"]

    return 0, []


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCORER
# ─────────────────────────────────────────────────────────────────────────────

def _heuristic_score(
    job: dict,
    pp: ProfilePatterns,
    w: dict,
) -> tuple[int, list[str]]:
    """
    Compute a fast heuristic relevance score for a job.

    All detection uses profile-derived patterns (pp) — no hardcoded skills,
    domains, or project keywords. Numeric weights come from w.

    Layers:
      1. Positive bonuses (primary skill, secondary skill, backend, fresher,
         high/med domain, project signals, desc quality, date, recency)
      2. Skill density scoring (NEW v2 — count distinct skill matches)
      3. Concordance + multiplicative boosters (NEW v2)
      4. _penalty_score() — penalties + synergy bonuses + role mismatch
      5. _source_adjustment() — per-source quality offsets
      6. Location affinity (NEW v2)
      7. Company tier (NEW v2)

    Returns:
        (score, reasons) — score can be negative; higher is always better.
    """
    title     = job.get("title", "")
    desc      = job.get("description", "")
    full_text = title + " " + desc

    score   = 0
    reasons = []

    # ── Primary skill (CHANGED v2: additive with concordance) ─────────────
    # v1 used elif (title OR desc). v2 still awards the bigger bonus for
    # title match, but concordance is handled separately in Layer 3.
    has_primary_title = bool(pp.primary_skill_re.search(title))
    has_primary_desc  = bool(pp.primary_skill_re.search(desc)) if desc else False
    has_primary_skill = has_primary_title or has_primary_desc

    if has_primary_title:
        b = w["primary_skill_title"]
        score += b
        reasons.append(f"primary-skill-title({b:+})")
    elif has_primary_desc:
        b = w["primary_skill_desc"]
        score += b
        reasons.append(f"primary-skill-desc({b:+})")

    # ── Secondary skill (CHANGED v2: now additive, not elif with primary) ─
    # v1: elif meant secondary was only checked if primary didn't match.
    # v2: check secondary independently — a job matching BOTH primary AND
    # secondary skills (e.g. Go + REST APIs) should score higher.
    has_secondary_title = bool(pp.secondary_skill_re.search(title))
    has_secondary_desc  = bool(pp.secondary_skill_re.search(desc)) if desc else False

    if has_secondary_title:
        b = w["secondary_skill_title"]
        score += b
        reasons.append(f"secondary-skill-title({b:+})")
    elif has_secondary_desc:
        b = w["secondary_skill_desc"]
        score += b
        reasons.append(f"secondary-skill-desc({b:+})")

    # ── Backend / API / microservice focus ────────────────────────────────
    has_backend_title = bool(_BACKEND_RE.search(title))
    if has_backend_title:
        b = w["backend_title"]
        score += b
        reasons.append(f"backend-title({b:+})")
    elif _BACKEND_RE.search(desc):
        b = w["backend_desc"]
        score += b
        reasons.append(f"backend-desc({b:+})")

    # ── Intern / Fresher ─────────────────────────────────────────────────
    has_fresher_title = bool(_FRESHER_TITLE_RE.search(title))
    if has_fresher_title:
        b = w["fresher_title"]
        score += b
        reasons.append(f"fresher-title({b:+})")

    # ── High-priority industry domain ─────────────────────────────────────
    has_high_domain = bool(pp.high_domain_re.search(full_text))
    if has_high_domain:
        b = w["high_priority_domain"]
        score += b
        reasons.append(f"high-domain({b:+})")
    elif pp.med_domain_re.search(full_text):
        b = w["med_priority_domain"]
        score += b
        reasons.append(f"med-domain({b:+})")

    # ── Project relevance signals (capped) ────────────────────────────────
    proj_bonus  = 0
    has_project = False
    per_hit     = w["project_signal_per_hit"]
    cap         = w["project_signal_max"]
    for sig_re in pp.project_signal_res:
        if sig_re.search(full_text):
            proj_bonus  += per_hit
            has_project  = True
    proj_bonus = min(proj_bonus, cap)
    if proj_bonus:
        score += proj_bonus
        reasons.append(f"project-signal({proj_bonus:+})")

    # ── Description quality ───────────────────────────────────────────────
    # Exempt lazy-fetch sources (description not available yet)
    source = job.get("source", "")
    if not _is_lazy_fetch_source(source) and len(desc) >= w["desc_quality_min_chars"]:
        b = w["desc_quality"]
        score += b
        reasons.append(f"desc-quality({b:+})")

    # ── Has a posted_at date ──────────────────────────────────────────────
    if job.get("posted_at"):
        b = w["has_date"]
        score += b
        reasons.append(f"has-date({b:+})")

    # ── Recency bonus / penalty ───────────────────────────────────────────
    rb = _recency_bonus(job.get("posted_at"), w)
    if rb > 0:
        score += rb
        reasons.append(f"recency({rb:+})")
    elif rb < 0:
        score += rb
        reasons.append(f"old-job-penalty({rb:+})")

    # ── Layer 2: Skill Density (NEW v2) ───────────────────────────────────
    density_bonus, density_reasons = _skill_density_score(full_text, pp, w)
    score   += density_bonus
    reasons += density_reasons

    # ── Layer 3: Concordance + Multiplicative Boosters (NEW v2) ───────────
    booster_delta, booster_reasons = _concordance_and_boosters(
        title, desc,
        has_primary_title, has_fresher_title, has_backend_title,
        pp, w,
    )
    score   += booster_delta
    reasons += booster_reasons

    # ── Layer 4: Penalty + synergy adjustments ────────────────────────────
    penalty_delta, penalty_reasons = _penalty_score(
        job, has_primary_skill, has_high_domain, has_project, pp, w
    )
    score   += penalty_delta
    reasons += penalty_reasons

    # ── Layer 5: Source-aware adjustments ─────────────────────────────────
    source_delta, source_reasons = _source_adjustment(job, w)
    score   += source_delta
    reasons += source_reasons

    # ── Layer 6: Location Affinity (NEW v2) ───────────────────────────────
    loc_delta, loc_reasons = _location_affinity(job, w)
    score   += loc_delta
    reasons += loc_reasons

    # ── Layer 7: Company Tier (NEW v2) ────────────────────────────────────
    tier_delta, tier_reasons = _company_tier(job, pp, w)
    score   += tier_delta
    reasons += tier_reasons

    return score, reasons


# ─────────────────────────────────────────────────────────────────────────────
# SCORE DISTRIBUTION LOGGING  (NEW v2)
# ─────────────────────────────────────────────────────────────────────────────

def _log_score_distribution(jobs: list[dict], ai_cap: int | None = None) -> None:
    """
    Log score distribution statistics for observability.

    Shows min, max, median, p25, p75, and the score at the AI cap cutoff.
    This tells you at a glance whether the ranker is differentiating jobs
    or producing a flat cluster.
    """
    if not jobs:
        return

    scores = [j["_heuristic_score"] for j in jobs]

    if len(scores) < 2:
        logger.info(f"Score distribution: {scores}")
        return

    sorted_scores = sorted(scores, reverse=True)
    n = len(sorted_scores)

    stats = {
        "max":    sorted_scores[0],
        "p75":    sorted_scores[max(0, n // 4)],
        "median": statistics.median(scores),
        "p25":    sorted_scores[min(n - 1, 3 * n // 4)],
        "min":    sorted_scores[-1],
    }

    spread = stats["max"] - stats["min"]
    iqr    = stats["p75"] - stats["p25"]

    cap_msg = ""
    if ai_cap and n > ai_cap:
        cutoff_score = sorted_scores[ai_cap - 1]
        below_cutoff = sorted_scores[ai_cap] if ai_cap < n else "N/A"
        cap_msg = f" | AI cap@{ai_cap}: score≥{cutoff_score} (next={below_cutoff})"

    logger.info(
        f"Score distribution ({n} jobs): "
        f"max={stats['max']} p75={stats['p75']} "
        f"median={stats['median']:.0f} p25={stats['p25']} "
        f"min={stats['min']} | spread={spread} IQR={iqr}"
        f"{cap_msg}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def rank_eligible_jobs(
    jobs: list[dict],
    weights: dict | None = None,
    profile: dict | None = None,
) -> list[dict]:
    """
    Rank a list of pre-filtered jobs by heuristic relevance score.

    Args:
        jobs:    Pre-filtered job dicts (output of prefilter.py).
        weights: Optional ranker_weights dict from profile.yaml.
                 Pass `profile.get("ranker_weights")`.
                 Falls back to `_DEFAULT_WEIGHTS` for any missing key.
        profile: Full profile dict. Used to build skill/domain/project
                 detection patterns dynamically. If None, all pattern-
                 based signals are disabled (penalty-only mode).

    Ranking behaviour:
      - Higher score = more likely to be a strong match → scored first.
      - Jobs with no posted_at are NOT penalised.
      - Scores can be negative. sort() handles this correctly.
      - `_heuristic_score` and `_heuristic_reasons` are attached for
        debugging; stripped before DB persistence in scorer.py.

    Returns:
        The same jobs list, sorted descending by heuristic score.
    """
    w  = _resolve_weights(weights)
    pp = build_profile_patterns(profile or {})

    for job in jobs:
        s, reasons = _heuristic_score(job, pp, w)
        job["_heuristic_score"]   = s
        job["_heuristic_reasons"] = reasons

    jobs.sort(key=lambda j: j["_heuristic_score"], reverse=True)

    # Log top-5 for observability
    top5 = jobs[:5]
    logger.info(
        f"Relevance ranking: {len(jobs)} jobs ranked. "
        f"Top scores: {[j['_heuristic_score'] for j in top5]}"
    )
    for j in top5:
        logger.debug(
            f"  #{j['_heuristic_score']:>3} | {j.get('title','?')[:50]:<50} "
            f"@ {j.get('company','?')[:25]} | {', '.join(j['_heuristic_reasons'])}"
        )

    # Log score distribution stats (NEW v2)
    # Attempt to get AI cap from profile for cutoff reporting
    ai_cap = None
    if profile:
        ai_cap = profile.get("hard_reject", {}).get("max_ai_jobs_per_run")
    _log_score_distribution(jobs, ai_cap)

    return jobs
