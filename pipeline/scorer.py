import os
import re
import json
import time
import yaml
import logging
from datetime import datetime, timezone
from dateutil import parser as dateutil_parser
from groq import Groq
from storage.db import save_job
from sources.freshers_blogs import fetch_full_description
from sources.naukri import lazy_fetch_naukri_detail
from pipeline.prefilter import _CLOSED_PHRASES, _DEADLINE_CONTEXT_RE
from pipeline.ranker import rank_eligible_jobs

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Groq model: llama-4-scout (best balance of quality + budget)
# TPD: 500K (was 100K with llama-3.3-70b — we hit the limit in 1 run)
# TPM: 30K (highest of all Groq free-tier models)
# RPD: 1K  (sufficient for 1 scheduled run/day)
# Quality: Llama 4 MoE architecture — better than 8B, close to 70B
# ─────────────────────────────────────────────────────────────────
MODEL        = "meta-llama/llama-4-scout-17b-16e-instruct"
REQ_INTERVAL = 5.0        # seconds between scoring calls
                           # Calibrated from observed usage: ~2,240 tokens/call actual.
                           # 60s / 5.0 = 12 req/min × 2,400 = 28,800 TPM → safely under 30K.
                           # (Previous 3.6s → 37K TPM → exceeded limit 4× per run)
_last_call_ts = 0.0        # module-level timestamp tracker

# ─────────────────────────────────────────────────────────────────
# TWO-LAYER RATE SYSTEM
#
# Groq free-tier limits for llama-4-scout:
#   TPM  = 30,000 tokens / minute   ← per-minute rate limit
#   TPD  = 500,000 tokens / day     ← daily hard ceiling
#   RPD  = 1,000 requests / day
#
# Pipeline: 2 runs/day  ⇒  per-run daily budget = 500K ÷ 2 = 250K.
# With 20% safety margin: 200K usable tokens per run.
#
# Layer 1 — Per-minute (TPM): handled by REQ_INTERVAL throttle.
#   Observed actual cost: ~2,240 tokens/call (system prompt is heavier
#   than estimated — few-shot examples + full scoring rules + profile).
#   12 req/min (5.0s gap) × 2,400 = 28,800 TPM → safely under 30K.
#
# Layer 2 — Per-run daily budget: TOKEN_BUDGET_PER_RUN.
#   This is NOT a per-minute cap. A run of ~83 jobs at 5s each takes
#   ~7 min, spanning multiple TPM windows. Total budget for that whole
#   run is 200K tokens (80% of 250K half-day allocation).
#   This guards against accidentally scoring thousands of jobs if the
#   pre-filter is overly permissive.
#
# Per-job cost (observed from actual runs):
#   System prompt + few-shot examples : ~800 tokens  (heavier than estimated)
#   User prompt (profile + rules + JD): ~1,100 tokens average
#   Response (actual, not max_tokens) :  ~340 tokens
#   ──────────────────────────────────────────────────
#   Total per job                      : ~2,240 tokens (observed)
#   Max jobs per run at 200K budget    : ~89 jobs
#   Run duration for 89 jobs           : ~7.4 minutes
# ─────────────────────────────────────────────────────────────────
TOKEN_BUDGET_PER_RUN  = 200_000  # 80% of (500K TPD ÷ 2 runs/day) — per-run ceiling
SYSTEM_PROMPT_TOKENS  = 800      # observed: system msg + few-shot + rules overhead
RESPONSE_TOKENS       = 400      # observed average (actual responses ~300-400 tokens)
CHARS_PER_TOKEN       = 4        # standard approximation (1 token ≈ 4 chars)



def _groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set in .env")
    return Groq(api_key=api_key)


def _throttle():
    """Sleep enough to keep under 30 req/min (1 req per 3 sec)."""
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < REQ_INTERVAL:
        time.sleep(REQ_INTERVAL - elapsed)
    _last_call_ts = time.time()


def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────
# FEW-SHOT CALIBRATION EXAMPLES
#
# Purpose: anchor the 1–10 scale so Llama-4-Scout doesn't drift.
# Without anchors, the model tends to compress scores toward the
# middle (4–7) or inflate them unpredictably run-to-run.
#
# Two calibration examples are injected into the SYSTEM prompt
# (not the user message) so they are cached by Groq and don't
# count against per-request token budget for each job scored.
#
# Chosen to bracket the useful range:
#   9 = nearly perfect match for Rohit
#   3 = looks like a tech job but is actually a bad fit
# ─────────────────────────────────────────────────────────────────
_FEW_SHOT_EXAMPLES = """
## CALIBRATION EXAMPLES (use these to anchor your scoring scale)

### Example A — Score 9 (near-perfect match)
Job: "Backend Intern – Go/Golang" at Koinbase (crypto exchange), Bangalore/Remote
Description excerpt: "We're building a high-throughput order matching engine in Go.
  You'll work on our gRPC microservices, PostgreSQL schemas, and Redis caching layer.
  0–1 years experience. Stipend: ₹25,000/month. Apply before July 2026."
→ Correct score: 9
→ Reasoning: Golang + gRPC + PostgreSQL + Redis = exact stack match. Crypto/fintech
  domain matches Zaraba project signal. Remote/Bangalore is acceptable. Fresher role.
  Stipend above minimum. Only reason it isn't 10: no mention of equity/ESOPs and
  company is less well-known.
→ apply_urgency: "high"

### Example B — Score 3 (tech role, poor fit)
Job: "Junior DevOps Engineer" at TechCorp, Pune (on-site)
Description excerpt: "2+ years with AWS, Terraform, Jenkins CI/CD pipelines required.
  Must have experience managing production Kubernetes clusters."
→ Correct score: 3
→ Reasoning: DevOps is on the role blacklist. Requires 2+ years experience (hard
  reject signal). On-site Pune is borderline acceptable but the experience requirement
  alone makes this unfit. Terraform/Jenkins are not in the candidate's stack.
→ apply_urgency: "low"
"""


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate = profile["candidate"]
    today = datetime.now().strftime("%B %d, %Y")

    # Build project context string from all projects in profile
    projects_text = "\n".join(
        f"- {p['name']}: {p['description']} | Signals: {p['relevance_signal']}"
        for p in candidate.get('projects', [])
    )



    return f"""You are a job relevance scorer for a specific candidate. Score how relevant a job posting is for this person.

Today's Date: {today}

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / 0 years experience (B.Tech student, graduating May 2027)

Target roles (priority order):
{chr(10).join('- ' + r for r in candidate['roles']['primary'])}
Also acceptable: {', '.join(candidate['roles']['secondary'])}

Tech stack:
- Strong: {', '.join(candidate['skills']['strong'])}
- Learning: {', '.join(candidate['skills']['learning'])}

Projects (all three are strong portfolio signals):
{projects_text}

Location: {candidate['location']['base']}
Acceptable locations: {', '.join(candidate['location']['acceptable'])}

High-priority industries (bonus): {', '.join(candidate['industries']['high_priority'])}
Medium-priority industries: {', '.join(candidate['industries']['medium_priority'])}

## JOB POSTING

Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Location: {job.get('location', 'N/A')}
Salary/Stipend: {job.get('salary', 'Not mentioned')}
Source: {job.get('source', 'N/A')}

Job Description:
{job.get('description', 'No description available')[:3000]}

## SCORING RULES

Score 1-10 (use the calibration examples in the system prompt as anchors):
- 10 = Perfect match (backend intern/fresher + India/Remote + strong stack match)
- 8-9 = Very strong (backend intern, Go or TypeScript/Node.js, relevant company)
- 6-7 = Good (backend adjacent, potentially relevant, worth applying)
- 4-5 = Weak (tangentially related)
- 1-3 = Not relevant

Mandatory rules — apply in this exact order, override scoring bonuses:
1. EXPIRY CHECK (highest priority): If the description contains ANY of these signals—
   application closed / hiring closed / recruitment closed / position filled /
   no longer accepting / deadline has passed / last date was [past date]—
   set score=1, apply_urgency="expired", expired=true. Do NOT apply any bonuses.
2. Requires >1 year experience: score 1-2 (pre-filter miss, still log it)
3. Location is outside India AND in-office only: score=1
4. Post is older than 2 months (check posted dates in description): score 1-3
5. Go/Golang mentioned: +2 to base score
6. TypeScript or Node.js backend role: +1 to base score
7. General backend focus (REST APIs, microservices, databases): +1 to base score
8. Fintech/crypto/payments company: +2 to base score
9. Any of candidate's projects are directly relevant: +2 to base score
10. Internshala source with matching stipend (>=10000 INR/month): slight bonus

TOKEN SAVING RULES — IMPORTANT:
- If score < 6: set reason="", highlights=[], red_flags=[] — write nothing for these fields.
- If score >= 6: fill in reason, highlights, and red_flags normally.

Return ONLY a valid JSON object, no markdown fences:
{{
  "score": <integer 1-10>,
  "expired": <true if application is closed/deadline passed, false otherwise>,
  "reason": "<2-3 sentences IF score>=6, else empty string>",
  "highlights": ["<reason 1>", "<reason 2>", "<reason 3> — IF score>=6, else []"],
  "red_flags": ["<issue if any> — IF score>=6, else []"],
  "golang_match": <true/false>,
  "fintech_match": <true/false>,
  "apply_urgency": "<high/medium/low/expired>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Groq llama-4-scout."""

    # ── Lazy description fetch (freshers_blogs) ──────────────────────────────────
    # freshers_blogs sources return empty/partial descriptions intentionally
    # (avoids fetching hundreds of post pages upfront). After pre-filter confirms
    # this job is worth scoring, fetch the full body now.
    # Threshold: <100 chars
    desc = job.get("description", "")
    if len(desc) < 100 and job.get("url") and "freshers_blogs" in job.get("source", ""):
        logger.debug(f"Lazy-fetching JD for {job.get('title', '?')}")
        fetched = fetch_full_description(job["url"])
        if fetched:
            job["description"] = fetched
            desc = job["description"]

    # ── Lazy description fetch (naukri) ───────────────────────────────────────
    # Naukri Stage-1 returns truncated snippets only. The full JD is fetched
    # HERE (after prefilter) to avoid calling the detail API for the ~90% of
    # jobs that prefilter drops. `_naukri_job_id` is the trigger key.
    if job.get("_naukri_job_id") and len(desc) < 150:
        logger.debug(f"Lazy-fetching Naukri JD for {job.get('title', '?')}")
        fetched = lazy_fetch_naukri_detail(job)
        if fetched:
            job["description"] = fetched
            desc = job["description"]
    job.pop("_naukri_job_id", None)   # strip internal key before any persistence

    # ── Lazy description fetch (workday) ───────────────────────────────────────
    # Workday list endpoint returns only title/location/postedOn — no JD.
    # The full HTML description is fetched HERE (after prefilter) from the
    # detail endpoint to avoid calling it for the ~80% of jobs prefilter drops.
    if job.get("_workday_detail_path") and len(desc) < 150:
        logger.debug(f"Lazy-fetching Workday JD for {job.get('title', '?')}")
        from sources.workday import lazy_fetch_workday_detail
        fetched = lazy_fetch_workday_detail(job)
        if fetched:
            job["description"] = fetched
            desc = job["description"]
    job.pop("_workday_detail_path", None)   # strip internal keys before persistence
    job.pop("_workday_tenant", None)
    job.pop("_workday_wd_server", None)
    job.pop("_workday_site", None)

    # ── Pre-Groq expiry scan on fetched description ───────────────────────────
    # After fetching the full body, scan for closure/deadline signals.
    # Catches stale blog posts where the page says "Application Closed" or
    # "Last Date: Jan 2025" — not visible in RSS summary. Zero token cost.
    expiry_signal: re.Match | None = _CLOSED_PHRASES.search(desc)
    if not expiry_signal:
        now = datetime.now(timezone.utc)
        for m in _DEADLINE_CONTEXT_RE.finditer(desc):
            try:
                dl = dateutil_parser.parse(m.group(1).strip(), dayfirst=False)
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if dl < now:
                    expiry_signal = m
                    break
            except Exception:
                pass

    if expiry_signal:
        logger.info(
            f"Pre-Groq expiry detected for '{job.get('title','?')}': "
            f"'{expiry_signal.group(0).strip()[:60]}' — skipping scorer"
        )
        job["score"]       = 1
        job["expired"]     = True
        job["reason"]      = ""
        job["highlights"]  = ""
        job["red_flags"]   = ""
        job["urgency"]     = "expired"
        return job

    _throttle()  # Respect rate limit before every call

    client = _groq_client()

    try:
        prompt = build_scoring_prompt(job, profile)

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    # Few-shot examples live in the system prompt so they anchor
                    # the score scale without burning per-job user-message tokens.
                    "content": (
                        "You are a precise job relevance scorer. "
                        "Always respond with valid JSON only, no markdown.\n"
                        + _FEW_SHOT_EXAMPLES
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,   # Low temperature for consistent scoring
            max_tokens=512,    # Standard token limit since apply_angle is removed
        )

        text = response.choices[0].message.content.strip()

        # Strip markdown code fences if model adds them despite instructions
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()

        result = json.loads(text)

        job["score"]       = int(result.get("score", 0))
        job["expired"]     = bool(result.get("expired", False))
        job["reason"]      = result.get("reason", "")
        job["highlights"]  = ", ".join(result.get("highlights", []))
        job["red_flags"]   = ", ".join(result.get("red_flags", []))
        job["urgency"]     = result.get("apply_urgency", "low")

        logger.info(
            f"Scored: {job['title']} @ {job['company']} -> {job['score']}/10 [{job['urgency']}]"
        )
        return job

    except Exception as e:
        logger.error(f"Groq scoring failed for {job.get('title', '?')}: {e}")
        job["score"]       = -1
        job["reason"]      = f"Scoring error: {e}"
        job["highlights"]  = ""
        job["red_flags"]   = ""
        job["urgency"]     = "low"
        return job


def _estimate_prompt_tokens(job: dict) -> int:
    """
    Estimate the token cost for scoring one job.

    Formula:
      system prompt (fixed)  : SYSTEM_PROMPT_TOKENS
      user prompt (variable) : base overhead + description chars / CHARS_PER_TOKEN
      response               : RESPONSE_TOKENS (max_tokens setting)

    This is intentionally approximate — character/4 is the standard heuristic.
    We budget conservatively so we never hit the actual limit.
    """
    desc_chars = len(job.get("description", "")[:3000])  # scorer truncates at 3000
    # ~400 chars of non-description prompt overhead (title, company, profile fields)
    user_prompt_tokens = (desc_chars + 400) // CHARS_PER_TOKEN
    return SYSTEM_PROMPT_TOKENS + user_prompt_tokens + RESPONSE_TOKENS


def score_all(
    jobs: list[dict],
    profile: dict | None = None,
    db_path: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Rank, then score eligible jobs within a token budget.

    Pipeline:
      1. Heuristic relevance ranking  — best-match jobs first (zero AI cost).
      2. Token budget guard           — stop before hitting Groq's 30K TPM.
      3. Hard fallback cap            — profile max_ai_jobs_per_run (safety net).

    Why ranking before budget:
      Jobs without a posted_at date used to be silently dropped because the
      old approach sorted by date. Now recency is one signal among many —
      a strong Go/fintech job with no date beats a weak dated job.

    Args:
        jobs:     Pre-filtered job dicts to score.
        profile:  Loaded profile dict.  If None, loaded from default path.
        db_path:  Path to the SQLite database.  If None, uses db.py default.

    Returns: (urgent_jobs [8-10], digest_jobs [6-7], low_jobs [<6])
    Expired jobs (urgency='expired' or expired=True) are excluded from all
    buckets and never notified or persisted.
    """
    if profile is None:
        profile = load_profile()

    # ── Step 1: Heuristic relevance ranking ───────────────────────────────
    # Sorts jobs so the most promising ones are scored first.
    # This ensures the token budget is spent on best-fit jobs.
    # weights: numeric values from profile.yaml ranker_weights block.
    # profile: full dict so ranker can build skill/domain/project patterns
    #          dynamically from candidate.skills / industries / projects.
    jobs = rank_eligible_jobs(
        jobs,
        weights=profile.get("ranker_weights"),
        profile=profile,
    )

    # ── Step 2a: Post-ranking ATS per-company cap ─────────────────────────
    # The prefilter has a safety ceiling (100/company) to prevent runaway
    # single-company domination. Here, AFTER ranking, we apply the real
    # per-company cap (ats_per_company_cap, default 25) so we keep the
    # TOP-ranked N jobs per company instead of the first-fetched N.
    hard_reject_cfg = profile.get("hard_reject", {})
    ats_per_company_cap = hard_reject_cfg.get("ats_per_company_cap", 25)
    _ATS_SOURCES_SET = {"greenhouse", "greenhouse_eu", "lever", "ashby", "workable", "workday"}
    ranked_company_counts: dict[str, int] = {}
    capped_post_rank: list[str] = []
    filtered_jobs = []
    for job in jobs:
        src = job.get("source", "")
        if src in _ATS_SOURCES_SET:
            co = job.get("company", "")
            n  = ranked_company_counts.get(co, 0)
            if n >= ats_per_company_cap:
                capped_post_rank.append(co)
                continue
            ranked_company_counts[co] = n + 1
        filtered_jobs.append(job)
    jobs = filtered_jobs
    if capped_post_rank:
        unique_capped = sorted(set(capped_post_rank))
        logger.info(
            f"Post-ranking ATS cap ({ats_per_company_cap}/company): dropped lower-ranked "
            f"surplus from — {', '.join(unique_capped)}"
        )

    # ── Step 2b: Hard fallback cap (absolute worst-case guard) ────────────
    # Primary guard is the token budget below. This is a last-resort ceiling
    # in case token estimation is wildly off (e.g. all jobs have huge JDs).
    max_ai_jobs = profile.get("hard_reject", {}).get("max_ai_jobs_per_run", 200)
    if len(jobs) > max_ai_jobs:
        dropped_cap = len(jobs) - max_ai_jobs
        jobs = jobs[:max_ai_jobs]
        logger.warning(
            f"Hard fallback cap: trimmed {dropped_cap} lowest-ranked jobs "
            f"(max_ai_jobs_per_run={max_ai_jobs}). Increase cap in profile.yaml if needed."
        )

    urgent        : list[dict] = []
    digest        : list[dict] = []
    low           : list[dict] = []
    expired_count : int        = 0
    tokens_used   : int        = 0
    budget_skipped: int        = 0

    logger.info(
        f"Scoring up to {len(jobs)} ranked jobs with Groq ({MODEL}) "
        f"| token budget: {TOKEN_BUDGET_PER_RUN:,}"
    )

    for job in jobs:
        # ── Step 3: Token budget check ─────────────────────────────────────
        job_tokens = _estimate_prompt_tokens(job)
        if tokens_used + job_tokens > TOKEN_BUDGET_PER_RUN:
            budget_skipped += 1
            logger.debug(
                f"Budget skip: '{job.get('title','?')}' would cost ~{job_tokens} tokens "
                f"(used {tokens_used:,}/{TOKEN_BUDGET_PER_RUN:,}) — heuristic score: "
                f"{job.get('_heuristic_score', '?')}"
            )
            continue

        tokens_used += job_tokens
        scored_job = score_job(job, profile)

        # Strip internal heuristic keys before DB persistence
        scored_job.pop("_heuristic_score", None)
        scored_job.pop("_heuristic_reasons", None)

        # Hard drop: expired jobs are never sent anywhere
        if scored_job.get("expired") or scored_job.get("urgency") == "expired":
            expired_count += 1
            logger.debug(f"Dropping expired job: {scored_job.get('title','?')}")
            continue

        # Persist jobs worth reviewing (score >= 5)
        if scored_job["score"] >= 5:
            save_job(
                scored_job,
                score       = scored_job["score"],
                reason      = scored_job.get("reason", ""),
                highlights  = scored_job.get("highlights", ""),
                red_flags   = scored_job.get("red_flags", ""),
                db_path     = db_path,
            )

        if scored_job["score"] >= 8:
            urgent.append(scored_job)
        elif scored_job["score"] >= 6:
            digest.append(scored_job)
        else:
            low.append(scored_job)

    if budget_skipped:
        logger.info(
            f"Token budget: {tokens_used:,}/{TOKEN_BUDGET_PER_RUN:,} tokens used. "
            f"Skipped {budget_skipped} lower-ranked jobs to stay under limit."
        )
    else:
        logger.info(f"Token budget: {tokens_used:,}/{TOKEN_BUDGET_PER_RUN:,} tokens used (all jobs scored).")

    logger.info(
        f"Scoring complete: {len(urgent)} urgent, {len(digest)} digest, "
        f"{len(low)} low, {expired_count} expired (dropped)"
    )
    return urgent, digest, low
