import os
import re
import json
import time
import yaml
import logging
from datetime import datetime, timezone
from dateutil import parser as dateutil_parser
from google import genai
from google.genai import types
from storage.db import save_job
from sources.freshers_blogs import fetch_full_description
from sources.naukri import lazy_fetch_naukri_detail
from pipeline.prefilter import _CLOSED_PHRASES, _DEADLINE_CONTEXT_RE
from pipeline.ranker import rank_eligible_jobs

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Google Gemini: gemini-2.5-flash (free tier via AI Studio)
#
# Why gemini-2.5-flash over 2.0-flash:
#   - Superior instruction-following and structured JSON output
#   - Better calibration on nuanced 1-10 scoring tasks
#   - Same free-tier limits as 2.0 Flash (no cost increase)
#   - Native JSON mode via response_mime_type="application/json"
#     eliminates the markdown-fence stripping hack needed for Groq
#
# Free-tier limits (approximate — project-level, check AI Studio):
#   TPM  ≈ 1,000,000 tokens / minute  (vs Groq's 30,000 — 33× more)
#   TPD  ≈ 1,500,000 tokens / day     (vs Groq's 500,000 — 3× more)
#   RPM  ≈ 10–15 requests / minute    (slightly lower than Groq's 30)
#   RPD  ≈ 1,500 requests / day       (vs Groq's 1,000 — 1.5× more)
# ─────────────────────────────────────────────────────────────────
MODEL        = "gemini-2.5-flash"
REQ_INTERVAL = 4.5        # seconds between scoring calls
                           # 60s / 4.5 = 13.3 req/min → safely under ~15 RPM.
                           # TPM is not a constraint: 13.3 req/min × ~2,400 tok =
                           # 31,920 TPM — negligible against Gemini's ~1M TPM limit.
_last_call_ts = 0.0        # module-level timestamp tracker

# ─────────────────────────────────────────────────────────────────
# RATE SYSTEM (Gemini free tier)
#
# Gemini's generous TPD/TPM means the OLD two-layer token budget
# system is no longer needed. The ONLY binding constraint is RPM.
#
# Old approach (Groq): token budget guard stopped scoring at ~103
#   jobs because 103 × 1,930 tok = 199K ≈ 200K per-run ceiling.
#   The last 27 ranked jobs were skipped every single run.
#
# New approach (Gemini): remove TOKEN_BUDGET_PER_RUN as a hard cap.
#   All jobs up to max_ai_jobs_per_run (130) are scored.
#   Rate control is purely via the 4.5s inter-request interval.
#   A 130-job run takes ~10 minutes — acceptable per user preference.
#
# Per-job cost estimate (Gemini, 6K char desc):
#   System prompt + few-shot : ~1,400 tokens
#   User prompt (profile+JD) : ~1,600 tokens average (6K chars ÷ 4)
#   Response (full reason)   :  ~500 tokens (no token-saving rules)
#   ─────────────────────────────────────────────────────────────
#   Total per job            : ~3,500 tokens (estimated)
#   130 jobs × 3,500 tok     : ~455,000 tokens/run — well within TPD
# ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_TOKENS  = 1400     # ~1,400 tokens for system prompt + few-shot
RESPONSE_TOKENS       = 500      # ~500 tokens per response (full reasons now)
CHARS_PER_TOKEN       = 4        # standard approximation (1 token ≈ 4 chars)
DESC_CHAR_LIMIT       = 6000     # Gemini's large context allows richer JD input
                                  # (was 3000 with Groq — doubled for better accuracy)


def _gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    return genai.Client(api_key=api_key)


def _throttle():
    """Sleep enough to stay under ~15 req/min (1 req per 4.5 sec)."""
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
# Purpose: anchor the 1–10 scale so Gemini doesn't drift.
# Five calibration examples injected into the SYSTEM prompt.
#
# Critical design choices:
#   - Score-9 example is NOT Go-exclusive: TypeScript/Node.js backend
#     internships are the majority of available fresher roles in India.
#     Over-anchoring on Go causes all TS/Node roles to land at 5–6
#     instead of 7–8, producing 0 urgents per run.
#   - Full 5-level bracket: 9 / 7 / 6 / 5 / 3
#     Without mid-range anchors (5, 6, 7), the model collapses
#     ambiguous cases to 3–4 (as seen in v4 run: 0 urgents).
#   - Market context is explicit: India Go fresher roles are rare
#     in July 2026 — score relative to what's realistically available.
# ─────────────────────────────────────────────────────────────────
_FEW_SHOT_EXAMPLES = """\
## CALIBRATION EXAMPLES (use these to anchor your scoring scale)

### Example A — Score 9 (near-perfect match)
Job: "Backend Engineering Intern" at a fintech startup (India/Remote)
Description excerpt: "Build REST APIs and microservices. Stack: Go or TypeScript.
  0–1 years experience. Stipend ₹20,000–30,000/month. 2026/2027 batch welcome."
→ Correct score: 9
→ Reasoning: Backend intern role in a relevant domain (fintech), remote/India
  acceptable, fresher-friendly, uses candidate's primary stack. Stipend meets
  minimum. This is the target archetype — score confidently at 9.
→ apply_urgency: "high"

### Example B — Score 7 (good non-Go backend role)
Job: "Backend Intern (Node.js/TypeScript)" at an Indian SaaS startup, Bangalore/Remote
Description excerpt: "0–6 months experience required. Build REST APIs using
  Express/Node.js. TypeScript preferred. Stipend ₹20,000–30,000/month."
→ Correct score: 7
→ Reasoning: TypeScript/Node.js backend is in the candidate's strong stack.
  India location and fresher-friendly. Not Go or fintech, so not a 9, but
  this is a genuinely good match — must not be scored below 7.
→ apply_urgency: "medium"

### Example C — Score 6 (solid adjacent, should surface in digest)
Job: "Graduate Software Engineer" at a remote-first company (Python/Kafka)
Description excerpt: "Any location accepted. 2026/2027 batch welcome. Backend
  focused, Python, REST APIs, message queues. 0 experience required."
→ Correct score: 6
→ Reasoning: Remote-first and fresh-batch signals are strong positives.
  Python is not the candidate's primary stack but the role is backend-adjacent
  and entry-level. Worth a digest notification — do not score lower than 6.
→ apply_urgency: "medium"

### Example D — Score 5 (borderline — save but don't notify)
Job: "Full Stack Developer Intern (React + Node.js)" at Indian startup, Bangalore
Description excerpt: "No specific experience required. Build UI components and
  REST APIs. Bangalore office only."
→ Correct score: 5
→ Reasoning: Full-stack role with frontend-heavy framing. Backend component
  exists but React is the primary focus. On-site Bangalore is acceptable.
  Save to DB but no urgent or digest notification needed.
→ apply_urgency: "low"

### Example E — Score 3 (tech role, poor fit)
Job: "Junior DevOps Engineer" at TechCorp, Pune (on-site)
Description excerpt: "2+ years with AWS, Terraform, Jenkins CI/CD required.
  Must manage production Kubernetes clusters."
→ Correct score: 3
→ Reasoning: DevOps is on the role blacklist. Requires 2+ years (hard reject
  signal). Terraform/Jenkins not in candidate's stack.
→ apply_urgency: "low"
"""

# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT (sent once per request, anchors the model behaviour)
# ─────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a precise job relevance scorer for an India-based "
    "fresher (graduating May 2027) targeting backend engineering "
    "roles. It is July 2026 — India Go/backend fresher roles are "
    "extremely rare. Score relative to market reality: a "
    "TypeScript, Node.js, or Python backend intern role with "
    "strong India/remote + 0-exp signals should score 6–8, "
    "not 3–4. A role being 'not Go' is NOT a reason to score below 6 "
    "if it is otherwise a strong backend fresher match.\n\n"
    "IMPORTANT: You MUST always provide a non-empty 'reason' field, "
    "even for low scores (score < 6). The reason must be 1–2 sentences "
    "explaining the primary rejection reason (wrong role type, too senior, "
    "wrong location, wrong stack, etc.). This is mandatory — empty reasons "
    "are not acceptable.\n\n"
    "Always respond with valid JSON only — no markdown fences, no extra text.\n\n"
    + _FEW_SHOT_EXAMPLES
)


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate = profile["candidate"]
    today = datetime.now().strftime("%B %d, %Y")

    # Build project context string from all projects in profile
    projects_text = "\n".join(
        f"- {p['name']}: {p['description']} | Signals: {p['relevance_signal']}"
        for p in candidate.get('projects', [])
    )

    # Truncate description at DESC_CHAR_LIMIT (6000 chars).
    # Gemini's large context window means we can provide richer JD content
    # vs the old 3000-char Groq limit. This helps for long Internshala/Naukri
    # JDs where batch year, stipend, and tech stack appear late in the text.
    desc = job.get('description', 'No description available')[:DESC_CHAR_LIMIT]

    return f"""You are a job relevance scorer for a specific candidate. Score how relevant a job posting is for this person.

Today's Date: {today}
Market Context: India Go/backend fresher market is thin in July 2026. Score relative to what's realistically available — don't penalize for "not Go" if it's a genuine backend fresher role.

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / 0 years experience (B.Tech student, graduating May 2027)

Target roles (priority order):
{chr(10).join('- ' + r for r in candidate['roles']['primary'])}
Also acceptable: {', '.join(candidate['roles']['secondary'])}

Tech stack:
- Strong: {', '.join(candidate['skills']['strong'])}
- Learning: {', '.join(candidate['skills']['learning'])}

Projects (all four are strong portfolio signals):
{projects_text}

Location: {candidate['location']['base']}
Acceptable locations: {', '.join(candidate['location']['acceptable'])}

High-priority industries (score bonus): {', '.join(candidate['industries']['high_priority'])}
Medium-priority industries: {', '.join(candidate['industries']['medium_priority'])}

Compensation minimums:
- Internship stipend: ₹{candidate['salary']['min_stipend_inr']:,}/month
- Full-time fresher: ₹{candidate['salary']['min_ctc_lpa']} LPA

## JOB POSTING

Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Location: {job.get('location', 'N/A')}
Salary/Stipend: {job.get('salary', 'Not mentioned')}
Source: {job.get('source', 'N/A')}

Job Description:
{desc}

## SCORING RULES

Score 1-10 (use the calibration examples in the system prompt as anchors):
- 10 = Perfect match (backend intern/fresher + India/Remote + exact stack match + great company)
- 8-9 = Very strong (backend intern/fresher, Go or TypeScript/Node.js, relevant company)
- 6-7 = Good (backend adjacent, potentially relevant, worth applying)
- 4-5 = Weak (tangentially related, significant mismatches)
- 1-3 = Not relevant (wrong role type, too senior, wrong location)

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

CRITICAL REASON RULE:
- For ALL scores (including score < 6): always provide a non-empty 'reason' (1-2 sentences).
- For score >= 6: also fill in 'highlights' and 'red_flags'.
- For score < 6: 'highlights' and 'red_flags' may be empty lists [], but 'reason' MUST be filled.

Return ONLY a valid JSON object, no markdown fences:
{{
  "score": <integer 1-10>,
  "expired": <true if application is closed/deadline passed, false otherwise>,
  "reason": "<1-2 sentences explaining the score — MANDATORY for ALL scores>",
  "highlights": ["<reason 1>", "<reason 2>", "<reason 3> — IF score>=6, else []"],
  "red_flags": ["<issue if any> — IF score>=6, else []"],
  "golang_match": <true/false>,
  "fintech_match": <true/false>,
  "apply_urgency": "<high/medium/low/expired>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Google Gemini 2.5 Flash."""

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

    # ── Pre-Gemini expiry scan on fetched description ───────────────────────
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
            f"Pre-Gemini expiry detected for '{job.get('title','?')}': "
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

    client = _gemini_client()

    try:
        prompt = build_scoring_prompt(job, profile)

        # Gemini native JSON mode: response_mime_type="application/json"
        # guarantees a parseable JSON response — no markdown fences to strip.
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.1,        # Low temperature for consistent scoring
                max_output_tokens=768,  # Enough for full JSON with reasons
                response_mime_type="application/json",
            ),
        )

        text = response.text.strip()

        # Safety: strip markdown fences if model produces them despite JSON mode
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
        logger.error(f"Gemini scoring failed for {job.get('title', '?')}: {e}")
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
      response               : RESPONSE_TOKENS

    Note: With Gemini's ~1M TPM / ~1.5M TPD free tier, token estimation is
    used only for logging purposes — it no longer gates which jobs get scored.
    All jobs up to max_ai_jobs_per_run are scored unconditionally.
    """
    desc_chars = len(job.get("description", "")[:DESC_CHAR_LIMIT])
    # ~600 chars of non-description prompt overhead (title, company, profile fields)
    user_prompt_tokens = (desc_chars + 600) // CHARS_PER_TOKEN
    return SYSTEM_PROMPT_TOKENS + user_prompt_tokens + RESPONSE_TOKENS


def score_all(
    jobs: list[dict],
    profile: dict | None = None,
    db_path: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Rank, then score eligible jobs.

    Pipeline:
      1. Heuristic relevance ranking  — best-match jobs first (zero AI cost).
      2. Hard fallback cap            — profile max_ai_jobs_per_run (safety net).
      3. AI scoring                   — all capped jobs scored (no token budget gate).

    Why no token budget gate:
      Gemini free tier provides ~1M TPM and ~1.5M TPD. At ~3,500 tokens/job
      and 130 jobs/run, total cost is ~455K tokens — well within limits even
      for 3 runs/day. The old 200K per-run token ceiling caused 27 jobs to be
      skipped every run. With Gemini, all ranked jobs are scored.

    Why ranking before scoring:
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
    # This ensures the hard cap keeps the best-fit jobs.
    # weights: numeric values from profile.yaml ranker_weights block.
    # profile: full dict so ranker can build skill/domain/project patterns
    #          dynamically from candidate.skills / industries / projects.
    jobs = rank_eligible_jobs(
        jobs,
        weights=profile.get("ranker_weights"),
        profile=profile,
    )

    # ── Step 2a: Post-ranking ATS per-company cap + title dedup ─────────
    # The prefilter has a safety ceiling (100/company) to prevent runaway
    # single-company domination. Here, AFTER ranking, we apply the real
    # per-company cap (ats_per_company_cap, default 25) so we keep the
    # TOP-ranked N jobs per company instead of the first-fetched N.
    #
    # Title dedup: within ATS sources, skip duplicate (company, title) pairs.
    # v3 run wasted 11 AI calls on "Staff Production Engineer @ Canva" ×4
    # and "Technical Services Engineer @ Mongodb" ×3. These are identical
    # listings with different URLs — URL-based dedup doesn't catch them.
    hard_reject_cfg = profile.get("hard_reject", {})
    ats_per_company_cap = hard_reject_cfg.get("ats_per_company_cap", 25)
    _ATS_SOURCES_SET = {"greenhouse", "greenhouse_eu", "lever", "ashby", "workable", "workday"}
    ranked_company_counts: dict[str, int] = {}
    seen_ats_titles: set[tuple[str, str]] = set()  # (company_lower, title_normalized)
    capped_post_rank: list[str] = []
    title_deduped: int = 0
    filtered_jobs = []
    for job in jobs:
        src = job.get("source", "")
        if src in _ATS_SOURCES_SET:
            co = job.get("company", "")
            co_lower = co.lower().strip()
            title_norm = job.get("title", "").lower().strip()

            # Title-level dedup: skip identical (company, title) pairs
            title_key = (co_lower, title_norm)
            if title_key in seen_ats_titles:
                title_deduped += 1
                continue
            seen_ats_titles.add(title_key)

            # Per-company cap
            n = ranked_company_counts.get(co, 0)
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
    if title_deduped:
        logger.info(
            f"Post-ranking title dedup: dropped {title_deduped} duplicate "
            f"(company, title) pairs from ATS sources"
        )

    # ── Step 2b: Hard fallback cap (absolute ceiling) ────────────────────
    # With Gemini, this is the PRIMARY (and only) cap — no token budget gate.
    # Ensures the pipeline doesn't balloon if prefilter is overly permissive.
    max_ai_jobs = profile.get("hard_reject", {}).get("max_ai_jobs_per_run", 200)
    if len(jobs) > max_ai_jobs:
        dropped_cap = len(jobs) - max_ai_jobs
        jobs = jobs[:max_ai_jobs]
        logger.warning(
            f"Hard cap: trimmed {dropped_cap} lowest-ranked jobs "
            f"(max_ai_jobs_per_run={max_ai_jobs}). Increase cap in profile.yaml if needed."
        )

    urgent        : list[dict] = []
    digest        : list[dict] = []
    low           : list[dict] = []
    expired_count : int        = 0
    tokens_used   : int        = 0   # tracked for logging only — not a hard gate

    logger.info(
        f"Scoring up to {len(jobs)} ranked jobs with Gemini ({MODEL}) "
        f"| no token budget ceiling (Gemini free tier: ~1M TPM)"
    )

    for job in jobs:
        # Track token usage for observability (not used as a gate anymore)
        tokens_used += _estimate_prompt_tokens(job)

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

    logger.info(
        f"Token usage (estimated): ~{tokens_used:,} tokens for {len(urgent)+len(digest)+len(low)} jobs "
        f"(Gemini free-tier TPD: ~1,500,000)"
    )
    logger.info(
        f"Scoring complete: {len(urgent)} urgent, {len(digest)} digest, "
        f"{len(low)} low, {expired_count} expired (dropped)"
    )
    return urgent, digest, low
