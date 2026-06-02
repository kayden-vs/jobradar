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
   many — a job with no date can still rank highly if it matches Go/fintech.

2. Ensure the token budget (used to stop scoring early) is spent on the
   most promising jobs, not just the newest ones.

The score is additive and intentionally mirrors the AI scorer's bonus
rules so the ranking is consistent with what the AI would score highly.
"""

import re
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

# Go/Golang — highest priority stack signal (+5)
_GO_TITLE_RE = re.compile(
    r'\bgolang\b|\bgo\s+(?:developer|engineer|intern|backend|developer)\b'
    r'|\bgo[,\s)]|(?:backend|intern|engineer|sde)\s+[–-]?\s*go\b',
    re.IGNORECASE,
)
_GO_DESC_RE = re.compile(r'\bgolang\b|\bgo\s+(?:routine|channel|module|fiber|gin|echo)\b', re.IGNORECASE)

# TypeScript / Node.js (+3)
_TS_NODE_RE = re.compile(r'\btypescript\b|\bnode\.?js\b|\bts\s+backend\b', re.IGNORECASE)

# Backend focus (+2)
_BACKEND_RE = re.compile(
    r'\bbackend\b|\bback.?end\b|\brest api\b|\bmicroservice\b|\bserver.?side\b',
    re.IGNORECASE,
)

# Intern / Fresher / Junior target (+2)
_FRESHER_TITLE_RE = re.compile(
    r'\bintern\b|\bfresher\b|\btrainee\b|\bjunior\b|\bentry.?level\b|\bgraduate\b|\bsde.?1\b',
    re.IGNORECASE,
)

# Fintech / Crypto (+3)
_FINTECH_RE = re.compile(
    r'\bfintech\b|\bcrypto\b|\bblockchain\b|\bpayment\b|\btrading\b'
    r'|\bwallet\b|\bdefi\b|\bexchange\b|\bbanking\b|\bfinancial\b',
    re.IGNORECASE,
)

# Candidate's project relevance signals (+2 each, max +4)
_PROJECT_SIGNALS = [
    # Zaraba — crypto exchange
    re.compile(r'\border.?book\b|\bmatching.?engine\b|\bcrypto.?exchange\b|\bgrpc\b|\bprotobuf\b', re.IGNORECASE),
    # CipherBin / ClearArch
    re.compile(r'\btempl\b|\bclean.?arch\b|\bserver.?side.?render\b|\bsession.?auth\b', re.IGNORECASE),
    # Sentinel-Proxy
    re.compile(r'\breverse.?proxy\b|\btraffic.?monitor\b|\bdata.?exfiltration\b', re.IGNORECASE),
]


# ─────────────────────────────────────────────────────────────────────────────
# RECENCY HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _recency_bonus(posted_at: str | None) -> int:
    """
    Return a recency bonus based on how recently the job was posted.

    Jobs with no date get 0 — not penalised (fixes the original bug where
    undated jobs were sorted to the bottom and dropped by the cap).

    Points:
      posted within  7 days → +4
      posted within 14 days → +2
      posted within 30 days → +1
      older / unknown       →  0
    """
    if not posted_at:
        return 0

    s = str(posted_at).strip().lower()

    # Fast path: relative strings without full date parsing
    relative_map = [
        (r'\btoday\b|\b[0-9]?\s*hour[s]?\s+ago\b|\bminute[s]?\s+ago\b', 4),
        (r'\b[1-6]\s*day[s]?\s+ago\b|\byesterday\b',                     4),
        (r'\b[7-9]\s*day[s]?\s+ago\b|\b1\s*week\s+ago\b',               2),
        (r'\b1[0-4]\s*day[s]?\s+ago\b',                                  2),
        (r'\b1[5-9]\s*day[s]?\s+ago\b|\b2[0-9]\s*day[s]?\s+ago\b',      1),
        (r'\b[23]\s*week[s]?\s+ago\b',                                   1),
        (r'\b1\s*month\s+ago\b',                                          1),
        (r'\b[2-9]\s*month[s]?\s+ago\b|\byear[s]?\s+ago\b',              0),
    ]
    for pattern, bonus in relative_map:
        if re.search(pattern, s):
            return bonus

    # ISO / epoch parse
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(str(posted_at))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).days
        if age_days <= 7:
            return 4
        if age_days <= 14:
            return 2
        if age_days <= 30:
            return 1
    except Exception:
        pass

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCORER
# ─────────────────────────────────────────────────────────────────────────────

def _heuristic_score(job: dict) -> tuple[int, list[str]]:
    """
    Compute a fast heuristic relevance score for a job.

    Returns:
        (score, reasons) where reasons is a list of triggered signals.
    """
    title = job.get("title", "")
    desc  = job.get("description", "")
    full_text = title + " " + desc

    score   = 0
    reasons = []

    # ── Go / Golang ───────────────────────────────────────────────────────
    if _GO_TITLE_RE.search(title):
        score += 5
        reasons.append("golang-title(+5)")
    elif _GO_DESC_RE.search(desc):
        score += 3
        reasons.append("golang-desc(+3)")

    # ── TypeScript / Node.js ──────────────────────────────────────────────
    if _TS_NODE_RE.search(title):
        score += 3
        reasons.append("ts/node-title(+3)")
    elif _TS_NODE_RE.search(desc):
        score += 1
        reasons.append("ts/node-desc(+1)")

    # ── Backend focus ─────────────────────────────────────────────────────
    if _BACKEND_RE.search(title):
        score += 2
        reasons.append("backend-title(+2)")
    elif _BACKEND_RE.search(desc):
        score += 1
        reasons.append("backend-desc(+1)")

    # ── Intern / Fresher ─────────────────────────────────────────────────
    if _FRESHER_TITLE_RE.search(title):
        score += 2
        reasons.append("fresher-title(+2)")

    # ── Fintech / Crypto ─────────────────────────────────────────────────
    if _FINTECH_RE.search(full_text):
        score += 3
        reasons.append("fintech(+3)")

    # ── Project relevance (max +4 total) ──────────────────────────────────
    proj_bonus = 0
    for sig_re in _PROJECT_SIGNALS:
        if sig_re.search(full_text):
            proj_bonus += 2
    proj_bonus = min(proj_bonus, 4)
    if proj_bonus:
        score += proj_bonus
        reasons.append(f"project-signal(+{proj_bonus})")

    # ── Description quality ───────────────────────────────────────────────
    if len(desc) >= 200:
        score += 1
        reasons.append("desc-quality(+1)")

    # ── Has a parseable date (minor signal of data quality) ───────────────
    if job.get("posted_at"):
        score += 1
        reasons.append("has-date(+1)")

    # ── Recency bonus ─────────────────────────────────────────────────────
    rb = _recency_bonus(job.get("posted_at"))
    if rb > 0:
        score += rb
        reasons.append(f"recency(+{rb})")

    return score, reasons


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def rank_eligible_jobs(jobs: list[dict]) -> list[dict]:
    """
    Rank a list of pre-filtered jobs by heuristic relevance score.

    - Higher score = more likely to be a strong match → scored first.
    - Jobs with no posted_at are NOT penalised — they receive 0 recency
      bonus but can still rank highly via stack/role signals.
    - The `_heuristic_score` key is attached to each job dict for
      debugging but is stripped before DB persistence.

    Returns:
        The same jobs list, sorted descending by heuristic score.
    """
    for job in jobs:
        s, reasons = _heuristic_score(job)
        job["_heuristic_score"] = s
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
            f"  #{j['_heuristic_score']:>2} | {j.get('title','?')[:50]:<50} "
            f"@ {j.get('company','?')[:25]} | {', '.join(j['_heuristic_reasons'])}"
        )

    return jobs
