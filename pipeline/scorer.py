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
# Google Gemini: gemini-3.1-flash-lite (free tier via AI Studio)
#
# Model history (why we're here):
#   - Groq llama-4-scout-17b  → discontinued by Groq
#   - gemini-2.5-flash        → 5 RPM + thinking tokens contaminate JSON
#   - gemini-2.0-flash        → deprecated March 2026
#   - gemini-3.1-flash-lite   → current best for high-volume free tier
#
# Why gemini-3.1-flash-lite:
#   - Generally Available (stable), released May 7 2026
#   - ~15 RPM free tier — same as old 2.0-flash, 3x more than 2.5-flash
#   - ~1,500 RPD free tier — 130 jobs/run × 2 runs = 260 RPD (well within)
#   - ~250K TPM — sufficient for our workload (13 req/min × ~2,400 tok = 31K)
#   - JSON mode (response_mime_type=application/json) works cleanly —
#     thinking output is properly suppressed in structured output mode
#   - 1M token context window, 64K output limit
#   - Optimised for high-throughput, cost-sensitive use cases
#
# Confirmed free-tier limits (verify in Google AI Studio):
#   RPM  ≈ 15 requests / minute
#   RPD  ≈ 1,500 requests / day
#   TPM  ≈ 250,000 tokens / minute
# ─────────────────────────────────────────────────────────────────
MODEL        = "gemini-3.1-flash-lite"
REQ_INTERVAL = 4.5        # seconds between scoring calls
                           # 60s / 4.5 = 13.3 req/min → safely under 15 RPM.
                           # TPM: 13.3 req/min × ~2,400 tok = 31K TPM —
                           # well within the ~250K TPM free-tier limit.
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
#   A 150-job run takes ~11 min — acceptable per user preference.
#
# Per-job cost estimate (Gemini, 7K char desc + thinking_budget=256):
#   System prompt + few-shot : ~2,000 tokens (richer rubric + more examples)
#   User prompt (profile+JD) : ~1,900 tokens average (7K chars ÷ 4)
#   Thinking tokens (hidden) : ~256 tokens (internal reasoning, not in output)
#   Response (full reason)   :  ~600 tokens (richer structured output)
#   ─────────────────────────────────────────────────────────────────
#   Total per job            : ~4,756 tokens (estimated)
#   150 jobs × 4,756 tok     : ~713,400 tokens/run
#   × 2 runs/day             : ~1,427K TPD → 95% of 1,500K TPD budget
#   ─────────────────────────────────────────────────────────────────
#   Conservative: thinking_budget=256 (cap, not guarantee — typical ~150-250)
#   If TPD limits hit: reduce DESC_CHAR_LIMIT to 6000 or thinking_budget to 128
# ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_TOKENS  = 2000     # ~2,000 tokens (richer rubric + 8 few-shot examples)
RESPONSE_TOKENS       = 856      # ~600 response + ~256 thinking (both count toward TPM/TPD)
CHARS_PER_TOKEN       = 4        # standard approximation (1 token ≈ 4 chars)
DESC_CHAR_LIMIT       = 7000     # Expanded from 6000 — covers longer Naukri/Internshala JDs
                                  # where stipend, batch year, and stack appear late in text.
                                  # Cost: ~250 extra tok/job — well within TPM/TPD budget.


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
Dimension analysis: [A] backend engineering ✓ [B] intern/fresher ✓ [C] Go/TS +2 [D] India/Remote ✓
  [E] fintech +2 [F] startup known ✓ [G] stipend ₹20K+ meets minimum ✓ [H] active ✓
→ Correct score: 9
→ Reasoning: Backend intern role in a relevant domain (fintech), remote/India acceptable,
  fresher-friendly, primary stack. Stipend meets minimum. Target archetype — score at 9.
→ apply_urgency: "high"

### Example B — Score 7 (good non-Go backend role)
Job: "Backend Intern (Node.js/TypeScript)" at an Indian SaaS startup, Bangalore/Remote
Description excerpt: "0–6 months experience required. Build REST APIs using Express/Node.js.
  TypeScript preferred. Stipend ₹20,000–30,000/month."
Dimension analysis: [A] backend engineering ✓ [B] intern 0-6mo ✓ [C] TypeScript +1 [D] India ✓
  [E] SaaS +1 [F] startup ✓ [G] stipend ₹20K+ ✓ [H] active ✓
→ Correct score: 7
→ Reasoning: TypeScript backend is in candidate's strong stack, India location, fresher-friendly.
  Not Go or fintech so not a 9. Must not be scored below 7 — this is a genuinely good match.
→ apply_urgency: "medium"

### Example C — Score 6 (solid adjacent, should surface in digest)
Job: "Graduate Software Engineer" at a remote-first company (Python/Kafka)
Description excerpt: "Any location accepted. 2026/2027 batch welcome. Backend focused,
  Python, REST APIs, message queues. 0 experience required."
Dimension analysis: [A] SWE role ✓ [B] graduate/fresher ✓ [C] Python (backend) 0 [D] remote ✓
  [E] neutral 0 [F] remote-first ✓ [G] not mentioned, benefit of doubt [H] active ✓
→ Correct score: 6
→ Reasoning: Remote-first and fresh-batch are strong positives. Python is not primary stack
  but role is backend-adjacent, entry-level. Worth a digest — do not score lower than 6.
→ apply_urgency: "medium"

### Example D — Score 4 (unpaid — hard compensation cap)
Job: "Full Stack Developer Internship" at an Indian startup, Bangalore
Description excerpt: "No specific experience required. Build REST APIs and UI components.
  Unpaid internship / no stipend. Great learning opportunity."
Dimension analysis: [A] full-stack ✓ [B] intern ✓ [C] React+Node mixed [D] India ✓
  [E] neutral [F] unknown startup [G] UNPAID — hard cap: max score 4 ✗
→ Correct score: 4
→ Reasoning: Unpaid internship — candidate's stated minimum is ₹10,000/month. Compensation
  disqualifier hard-caps this at 4 regardless of other signals.
→ apply_urgency: "low"

### Example E — Score 2 (aggregate listing page, not a real job)
Job: "50+ TypeScript Jobs in India - Cutshort" at Cutshort
Description excerpt: "Browse hundreds of TypeScript jobs on Cutshort. Filter by..."
Dimension analysis: [A] NOT a job posting — it is a search/listing page ✗
→ Correct score: 2
→ Reasoning: This is a job board aggregator page, not an individual job posting.
  No company, no description, no application details. Score 2 regardless of title keywords.
→ apply_urgency: "low"

### Example F — Score 3 (talent pipeline form — not a real open role)
Job: "Talent Pipeline - Product Engineering" at a SaaS company
Description excerpt: "Not an active job posting. Submit your details to be considered
  for future Product Engineering openings as they arise."
Dimension analysis: [A] engineering team ✓ [B] unclear [C] unclear [D] unclear
  [E] SaaS [F] known company [G] unclear [H] NOT AN ACTIVE ROLE — talent pipeline ✗
→ Correct score: 3
→ Reasoning: Talent pipelines are not open positions — no active hiring, no timeline,
  no guaranteed interview. Score 3 max; candidate's time is better spent on active postings.
→ apply_urgency: "low"

### Example G — Score 1 (hard reject: wrong role type)
Job: "Sales Engineer" at a US-only SaaS company
Description excerpt: "3+ years experience in B2B SaaS sales required. Drive revenue
  through technical demos and client presentations. Must be based in USA."
Dimension analysis: [A] SALES, not backend engineering ✗ [B] 3+ yrs ✗ [C] N/A
  [D] USA on-site only ✗ [E] SaaS [F] known [G] unclear [H] active
→ Correct score: 1
→ Reasoning: Sales Engineer is a revenue/customer-facing role, not software engineering.
  Additionally requires 3+ years and US-only location. Triple disqualification.
→ apply_urgency: "low"

### Example H — Score 5 (borderline — backend adjacent, weak signals)
Job: "Full Stack Developer Intern (React + Node.js)" at Indian startup, Bangalore
Description excerpt: "No specific experience required. Build UI components and
  REST APIs. Bangalore office only. Stipend ₹15,000/month."
Dimension analysis: [A] full-stack ✓ [B] intern ✓ [C] Node.js +1 but React-heavy [D] India ✓
  [E] neutral [F] small startup [G] stipend ₹15K ≥ min ✓ [H] active ✓
→ Correct score: 5
→ Reasoning: Full-stack role with frontend-heavy framing. Backend component (REST APIs,
  Node.js) exists but React is the primary focus. Save to DB but no notification.
→ apply_urgency: "low"
"""

# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT (sent once per request, anchors the model behaviour)
# ─────────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a precise job relevance scorer for a specific candidate. "
    "Before assigning a final score, you MUST evaluate EVERY dimension "
    "listed in the scoring rubric. This prevents pattern-matching to a "
    "single strong signal and ignoring disqualifiers.\n\n"
    "Candidate context: India-based fresher (graduating May 2027) targeting "
    "backend engineering roles. It is July 2026 — India Go/backend fresher "
    "roles are rare. Score relative to what is realistically available:\n"
    "  • TypeScript, Node.js, or Python backend intern: score 6–8 (not 3–4)\n"
    "  • A role being 'not Go' is NOT a reason to score below 6 if it is a "
    "    strong backend fresher match in other dimensions\n"
    "  • Sales/Solutions/Customer/GTM 'Engineer' titles are NOT software "
    "    engineering roles — score 1–2 regardless of company prestige\n\n"
    "HARD RULES (override all bonuses):\n"
    "  1. EXPIRY: Any closed/filled/deadline-passed signal → score=1, expired=true\n"
    "  2. UNPAID: Explicitly unpaid/no stipend → hard cap: score ≤ 4\n"
    "  3. AGGREGATE PAGE: Job board listing page, not an individual posting → score ≤ 2\n"
    "  4. TALENT PIPELINE: 'Submit for future consideration' form → score ≤ 3\n"
    "  5. WRONG ROLE: Sales, marketing, HR, operations, customer success roles → score ≤ 2\n"
    "  6. SENIOR/LEAD: Requires 2+ years or has Senior/Lead/Staff/Principal in title → score ≤ 3\n"
    "  7. LOCATION: On-site outside India with no remote option → score ≤ 2\n\n"
    "MANDATORY: 'reason' field must be non-empty for ALL scores (1–10). "
    "For score ≥ 6: also fill 'highlights' and 'red_flags'.\n\n"
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

    # Truncate description at DESC_CHAR_LIMIT (7000 chars).
    # Gemini's large context window allows richer JD input vs the old 3000-char
    # Groq limit. 7000 chars (expanded from 6000) covers longer Naukri/Internshala
    # JDs where stipend, batch year, and tech stack appear late in the text.
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

## SCORING TASK

Evaluate the job below using this structured approach:

### STEP 1 — DIMENSION ANALYSIS (evaluate each before scoring)

Work through every dimension and determine its signal:

[A] ROLE TYPE — Is this actually a backend/SWE/engineering role?
    PASS signals: backend, SDE, software engineer, full-stack, platform, infrastructure
    FAIL signals: sales engineer, solutions engineer, customer engineer, GTM engineer,
      forward deployed engineer, customer success, business development, HR, analyst,
      designer, operations, QA tester, data scientist, ML engineer, security analyst
    → If FAIL: hard cap score ≤ 2

[B] SENIORITY — Is this genuinely fresher/entry-level/intern?
    PASS: intern, fresher, junior, associate, 0–1 year, entry level, graduate trainee,
      2026/2027 batch, accelerator program (e.g. Binance Accelerator)
    FAIL: 2+ years required, senior, lead, staff, principal, architect, head of, director
    → If FAIL: hard cap score ≤ 3

[C] STACK FIT — How well does the stack match candidate's skills?
    +3: Go/Golang explicitly required or preferred
    +2: TypeScript backend, Node.js backend (as primary, not just mentioned)
    +1: Python backend, Rust, gRPC, microservices-heavy
     0: Java, C#, Scala, other backend languages
    -1: Primarily frontend (React, Vue, Angular) with backend as secondary
    -2: No backend at all, or irrelevant stack

[D] LOCATION — Is the location acceptable?
    PASS: India (any city), Remote, WFH, Worldwide, Anywhere
    FAIL: USA only / UK only / Europe only / on-site outside India with no remote option
    → If FAIL: hard cap score ≤ 2

[E] DOMAIN — Company/industry relevance?
    +2: Fintech, crypto, payments, blockchain, trading, banking tech
    +1: SaaS, developer tools, infrastructure, API-first, e-commerce backend, cybersecurity
     0: Generic tech, e-commerce, media, gaming
    -1: Consulting/staffing/outsourcing firms (body shops)

[F] COMPENSATION — Does it meet the candidate's stated minimum?
    PASS: stipend mentioned and ≥ ₹10,000/month, OR full-time with CTC ≥ ₹4 LPA,
          OR compensation not mentioned (benefit of doubt)
    FAIL: explicitly UNPAID or "no stipend" or "volunteer basis"
    → If FAIL: hard cap score ≤ 4 (non-negotiable — candidate has a stated minimum)

[G] POSTING TYPE — Is this an actual job opening?
    PASS: Individual job posting with application link, role description, responsibilities
    FAIL: Aggregate listing page ("50+ jobs on Cutshort"), talent pipeline
          ("submit for future consideration"), closed/expired posting, category page
    → If FAIL: score ≤ 3 (aggregate page ≤ 2, talent pipeline ≤ 3)

[H] EXPIRY — Is the posting still active?
    Check for: application closed, hiring closed, position filled, no longer accepting,
    deadline has passed, last date was [past date]
    → If FAIL: score=1, expired=true, apply_urgency="expired"

[I] PROJECT RELEVANCE — Do candidate's projects match this role?
    +2: Zaraba (crypto exchange) relevant to fintech/crypto/trading/high-perf systems
    +2: Sentinel-Proxy relevant to security/infrastructure/proxy/monitoring
    +1: JobRadar relevant to data pipeline/automation/API integration roles
    +1: CipherBin relevant to web/Go/PostgreSQL roles
     0: No project relevance

### STEP 2 — COMBINE DIMENSIONS → FINAL SCORE

Use the dimension scores above to determine the final score:
- 9–10: Strong PASS on [A][B][C][D], bonus domain/stack, great company
- 7–8 : PASS on [A][B], good stack fit, India/remote confirmed
- 5–6 : PASS on [A], weak or mixed stack, adjacent role
- 3–4 : Some relevance but key dimension(s) fail (wrong stack, unpaid, weak role type)
- 1–2 : Hard fail on [A] or [D] or [G] or [H], or aggregate/pipeline/wrong-role

**Market calibration (July 2026)**: Go/backend fresher roles in India are rare.
A TypeScript/Node.js backend intern with India/Remote + 0-exp signals should score 7–8.

Mandatory score thresholds from STEP 1 apply BEFORE final score assignment.

### STEP 3 — OUTPUT (JSON only, no markdown)

Return ONLY a valid JSON object:
{{
  "score": <integer 1-10>,
  "expired": <true if [H] triggered, false otherwise>,
  "reason": "<1-3 sentences: key dimension results + primary factor for the score — MANDATORY for ALL scores>",
  "highlights": ["<positive signal 1>", "<positive signal 2>", "<positive signal 3> — required if score>=6, else []"],
  "red_flags": ["<concern 1>", "<concern 2> — required if score>=6, else []"],
  "golang_match": <true if Go/Golang mentioned in JD, false otherwise>,
  "fintech_match": <true if fintech/crypto/payments company, false otherwise>,
  "apply_urgency": "<high (score 8-10) / medium (score 6-7) / low (score 1-5) / expired>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Google Gemini (gemini-3.1-flash-lite)."""

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
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.1,        # Low temperature for consistent scoring
                max_output_tokens=1536, # Increased from 1024 — richer rubric produces
                                        # longer reasons; 1536 gives headroom for
                                        # dimension analysis + highlights + red_flags.
                thinking_config=types.ThinkingConfig(
                    thinking_budget=256,  # Conservative thinking budget (256 tokens).
                    # The model reasons internally before producing JSON:
                    #   "Role type? Backend ✓. Seniority? Intern ✓. Stack? Go +3..."
                    # This prevents pattern-matching to one signal and missing
                    # disqualifiers. Thinking tokens are hidden from response.text.
                    # Budget=256 is a cap — typical usage is ~150-200 tokens.
                    # If TPD limits are hit, reduce to 128 or set to 0 to disable.
                    # JSON mode is SAFE: flash-lite isolates thinking from output
                    # (unlike gemini-2.5-flash which contaminated JSON with CoT).
                ),
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
