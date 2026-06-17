"""
pipeline/ranker.py
──────────────────
Pure-Python heuristic relevance ranker.

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

Three-layer scoring model
-------------------------

  Layer 1 — Positive signal bonuses
    Additive score for stack, role, domain, recency, and project signals.
    All detection patterns are built DYNAMICALLY from profile.yaml, so
    the ranker works correctly for any candidate — not just backend/Go.

  Layer 2 — Penalty-augmented scoring  (Approach 1)
    Negative signals push clearly-weak jobs further down the queue,
    breaking the large flat cluster that forms when many jobs share the
    same positive signals. Synergy bonuses reward high-precision combos
    (primary-skill + high-priority industry) that the AI scores ≥9.

  Layer 3 — Source-aware adaptive weighting  (Approach 2)
    Not all sources produce equally reliable signals. Per-source offsets
    are applied last to reflect structural data-quality differences.

Fully configurable — zero hardcoded preferences
-------------------------------------------------
  Approach 3: all numeric values live in `ranker_weights` in profile.yaml.
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
from datetime import datetime, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT WEIGHTS  (Approach 3 — numeric config; mirrors profile.yaml)
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
    # ── Penalty signals (Approach 1) ──────────────────────────────────────
    "penalty_no_skill_match":    -3,  # no skill keyword anywhere in job text
    "penalty_generic_title":     -2,  # title has no recognisable tech/role signal
    "penalty_bodyshop_company":  -1,  # company looks like staffing / outsourcing firm
    "penalty_ats_stub_desc":     -2,  # ATS source + description below threshold
    "ats_stub_desc_threshold":   300,
    # ── Synergy bonuses (Approach 1) ──────────────────────────────────────
    "synergy_skill_domain":   3,   # primary skill AND high-priority domain both found
    "synergy_skill_project":  2,   # primary skill AND a project signal both found
    # ── Source-aware adjustments (Approach 2) ─────────────────────────────
    "source_internshala_stipend_bonus":  2,
    "source_internshala_stipend_min":    10_000,
    "source_freshers_blog_batch_bonus":  1,
    "source_freshers_blog_batches":      [2025, 2026, 2027],
    "source_naukri_stub_penalty":       -1,
    "source_naukri_stub_threshold":      150,
    "source_serper_penalty":            -1,
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

# ATS sources with structured, reliable location/title fields
_ATS_SOURCES = {"greenhouse", "greenhouse_eu", "lever", "ashby", "workable"}

# Internshala stipend extraction
_STIPEND_NUM_RE = re.compile(r'\d+')


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE-DRIVEN PATTERN BUILDER  (Approach 3 — no hardcoded preferences)
#
# Builds all candidate-preference regexes from profile.yaml at runtime.
# A cybersecurity candidate, a data engineer, a mobile developer — any
# profile works without changing a single line of code.
# ─────────────────────────────────────────────────────────────────────────────

class ProfilePatterns(NamedTuple):
    """Compiled regexes derived from profile.yaml — rebuilt on each run."""
    primary_skill_re:     re.Pattern   # top-priority skills (strong[0:2])
    secondary_skill_re:   re.Pattern   # remaining skills
    skill_match_re:       re.Pattern   # any skill keyword (for penalty check)
    high_domain_re:       re.Pattern   # high-priority industries
    med_domain_re:        re.Pattern   # medium-priority industries
    project_signal_res:   list         # per-project compiled regexes
    synergy_domain_re:    re.Pattern   # same as high_domain_re (alias for clarity)


def _escape_keywords(keywords: list[str]) -> str:
    """Join a list of strings into a regex alternation, escaping each term."""
    return "|".join(re.escape(k) for k in keywords if k)


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
    # Split strong skills: first two = "primary" (highest bonus); rest = "secondary"
    strong_skills = [s for s in skills.get("strong", []) if s]
    learning_skills = [s for s in skills.get("learning", []) if s]

    # Primary skills get the highest ranker bonus — take the first 3 strong skills
    # (usually the candidate's most distinguishing stack, e.g. ["Go", "Golang", "TypeScript"])
    primary_skills   = strong_skills[:3] if strong_skills else []
    secondary_skills = strong_skills[3:] + learning_skills

    # Fallback: if profile has no skills defined, match everything (no penalty)
    if primary_skills:
        primary_pattern = _escape_keywords(primary_skills)
        primary_skill_re = re.compile(r'\b(?:' + primary_pattern + r')\b', re.IGNORECASE)
    else:
        primary_skill_re = re.compile(r'(?!x)x')  # never matches — safe no-op

    if secondary_skills:
        secondary_pattern = _escape_keywords(secondary_skills)
        secondary_skill_re = re.compile(r'\b(?:' + secondary_pattern + r')\b', re.IGNORECASE)
    else:
        secondary_skill_re = re.compile(r'(?!x)x')

    # Stack match: any skill at all — used for the "no skill match" penalty
    all_skills = strong_skills + learning_skills
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

    return ProfilePatterns(
        primary_skill_re   = primary_skill_re,
        secondary_skill_re = secondary_skill_re,
        skill_match_re     = skill_match_re,
        high_domain_re     = high_domain_re,
        med_domain_re      = med_domain_re,
        project_signal_res = project_signal_res,
        synergy_domain_re  = high_domain_re,  # alias
    )


# ─────────────────────────────────────────────────────────────────────────────
# WEIGHT RESOLVER  (Approach 3)
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
        (r'\b1[0-4]\s*day[s]?\s+ago\b',                                  w["recency_14d"]),
        (r'\b1[5-9]\s*day[s]?\s+ago\b|\b2[0-9]\s*day[s]?\s+ago\b',      w["recency_30d"]),
        (r'\b[23]\s*week[s]?\s+ago\b',                                   w["recency_30d"]),
        (r'\b1\s*month\s+ago\b',                                          w["recency_30d"]),
        (r'\b[2-9]\s*month[s]?\s+ago\b|\byear[s]?\s+ago\b',              0),
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
# PENALTY + SYNERGY SCORER  (Approach 1)
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

    # ── No skill match anywhere ───────────────────────────────────────────
    # freshers_blogs sources have stub descriptions at ranking time — the scorer
    # lazy-fetches the full post body later. Skip the penalty for them so we
    # don't push a good blog post out of the scoring window due to a missing
    # skill keyword that's actually in the full JD.
    is_freshers_blog = source.startswith("freshers_blogs")
    if not is_freshers_blog and not pp.skill_match_re.search(full_text):
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
    if source in _ATS_SOURCES and len(desc) < w["ats_stub_desc_threshold"]:
        p = w["penalty_ats_stub_desc"]
        delta += p
        reasons.append(f"ats-stub-desc({p:+})")

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
# SOURCE-AWARE ADJUSTMENT  (Approach 2)
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
    if source == "naukri" and len(desc) < w["source_naukri_stub_threshold"]:
        p = w["source_naukri_stub_penalty"]
        delta += p
        reasons.append(f"naukri-stub-desc({p:+})")

    # ── Serper: dork result quality penalty ───────────────────────────────
    if source == "serper":
        p = w["source_serper_penalty"]
        delta += p
        reasons.append(f"serper-quality({p:+})")

    return delta, reasons


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
      2. _penalty_score() — Approach 1: penalties + synergy bonuses
      3. _source_adjustment() — Approach 2: per-source quality offsets

    Returns:
        (score, reasons) — score can be negative; higher is always better.
    """
    title     = job.get("title", "")
    desc      = job.get("description", "")
    full_text = title + " " + desc

    score   = 0
    reasons = []

    # ── Primary skill ─────────────────────────────────────────────────────
    has_primary_skill = False
    if pp.primary_skill_re.search(title):
        b = w["primary_skill_title"]
        score            += b
        has_primary_skill = True
        reasons.append(f"primary-skill-title({b:+})")
    elif pp.primary_skill_re.search(desc):
        b = w["primary_skill_desc"]
        score            += b
        has_primary_skill = True
        reasons.append(f"primary-skill-desc({b:+})")

    # ── Secondary skill ───────────────────────────────────────────────────
    if pp.secondary_skill_re.search(title):
        b = w["secondary_skill_title"]
        score += b
        reasons.append(f"secondary-skill-title({b:+})")
    elif pp.secondary_skill_re.search(desc):
        b = w["secondary_skill_desc"]
        score += b
        reasons.append(f"secondary-skill-desc({b:+})")

    # ── Backend / API / microservice focus ────────────────────────────────
    if _BACKEND_RE.search(title):
        b = w["backend_title"]
        score += b
        reasons.append(f"backend-title({b:+})")
    elif _BACKEND_RE.search(desc):
        b = w["backend_desc"]
        score += b
        reasons.append(f"backend-desc({b:+})")

    # ── Intern / Fresher ─────────────────────────────────────────────────
    if _FRESHER_TITLE_RE.search(title):
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
    if len(desc) >= w["desc_quality_min_chars"]:
        b = w["desc_quality"]
        score += b
        reasons.append(f"desc-quality({b:+})")

    # ── Has a posted_at date ──────────────────────────────────────────────
    if job.get("posted_at"):
        b = w["has_date"]
        score += b
        reasons.append(f"has-date({b:+})")

    # ── Recency bonus ─────────────────────────────────────────────────────
    rb = _recency_bonus(job.get("posted_at"), w)
    if rb > 0:
        score += rb
        reasons.append(f"recency({rb:+})")

    # ── Penalty + synergy adjustments (Approach 1) ────────────────────────
    penalty_delta, penalty_reasons = _penalty_score(
        job, has_primary_skill, has_high_domain, has_project, pp, w
    )
    score   += penalty_delta
    reasons += penalty_reasons

    # ── Source-aware adjustments (Approach 2) ─────────────────────────────
    source_delta, source_reasons = _source_adjustment(job, w)
    score   += source_delta
    reasons += source_reasons

    return score, reasons


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

    return jobs
