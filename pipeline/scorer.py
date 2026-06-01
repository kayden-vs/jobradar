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
from pipeline.prefilter import _CLOSED_PHRASES, _DEADLINE_CONTEXT_RE

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Groq model: llama-4-scout (best balance of quality + budget)
# TPD: 500K (was 100K with llama-3.3-70b — we hit the limit in 1 run)
# TPM: 30K (highest of all Groq free-tier models)
# RPD: 1K  (sufficient for 1 scheduled run/day)
# Quality: Llama 4 MoE architecture — better than 8B, close to 70B
# ─────────────────────────────────────────────────────────────────
MODEL        = "meta-llama/llama-4-scout-17b-16e-instruct"
REQ_INTERVAL = 3.0        # seconds between scoring calls
_last_call_ts = 0.0        # module-level timestamp tracker


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
→ apply_angle: "Lead with the Zaraba crypto exchange — matching engine and gRPC
  architecture are directly relevant. Mention fixed-point arithmetic and Redis."

### Example B — Score 3 (tech role, poor fit)
Job: "Junior DevOps Engineer" at TechCorp, Pune (on-site)
Description excerpt: "2+ years with AWS, Terraform, Jenkins CI/CD pipelines required.
  Must have experience managing production Kubernetes clusters."
→ Correct score: 3
→ Reasoning: DevOps is on the role blacklist. Requires 2+ years experience (hard
  reject signal). On-site Pune is borderline acceptable but the experience requirement
  alone makes this unfit. Terraform/Jenkins are not in the candidate's stack.
→ apply_urgency: "low"
→ apply_angle: "" (empty — not worth applying)
"""


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate = profile["candidate"]
    today = datetime.now().strftime("%B %d, %Y")

    # Build project context string from all projects in profile
    projects_text = "\n".join(
        f"- {p['name']}: {p['description']} | Signals: {p['relevance_signal']}"
        for p in candidate.get('projects', [])
    )

    high_score_fields = """  "apply_angle": "<one sentence: what should the candidate emphasize in their cover note / cold email? IF score>=8 ONLY, else empty string>","""

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
- If score < 6: set reason="", highlights=[], red_flags=[], apply_angle="" — write nothing for these fields.
- If score >= 6: fill in reason, highlights, and red_flags normally.
- If score >= 8: ALSO fill in apply_angle with one actionable sentence about what to emphasise in the cover note or cold email to this company. Reference specific projects/skills where possible.
- If score < 8: apply_angle must be an empty string "".

Return ONLY a valid JSON object, no markdown fences:
{{
  "score": <integer 1-10>,
  "expired": <true if application is closed/deadline passed, false otherwise>,
  "reason": "<2-3 sentences IF score>=6, else empty string>",
  "highlights": ["<reason 1>", "<reason 2>", "<reason 3> — IF score>=6, else []"],
  "red_flags": ["<issue if any> — IF score>=6, else []"],
{high_score_fields}
  "golang_match": <true/false>,
  "fintech_match": <true/false>,
  "apply_urgency": "<high/medium/low/expired>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Groq llama-4-scout."""

    # ── Lazy description fetch ────────────────────────────────────────────────
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
        job["apply_angle"] = ""
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
            max_tokens=600,    # +88 vs old 512 to accommodate apply_angle field
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
        job["apply_angle"] = result.get("apply_angle", "")  # only populated for score>=8
        job["urgency"]     = result.get("apply_urgency", "low")

        logger.info(
            f"Scored: {job['title']} @ {job['company']} -> {job['score']}/10 [{job['urgency']}]"
            + (f" | angle: {job['apply_angle'][:60]}" if job.get("apply_angle") else "")
        )
        return job

    except Exception as e:
        logger.error(f"Groq scoring failed for {job.get('title', '?')}: {e}")
        job["score"]       = -1
        job["reason"]      = f"Scoring error: {e}"
        job["highlights"]  = ""
        job["red_flags"]   = ""
        job["apply_angle"] = ""
        job["urgency"]     = "low"
        return job


def score_all(
    jobs: list[dict],
    profile: dict | None = None,
    db_path: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Score all eligible jobs and split into buckets.
    Throttled to 1 req per 3 sec to stay under Groq's 30 req/min limit.

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

    urgent        : list[dict] = []
    digest        : list[dict] = []
    low           : list[dict] = []
    expired_count : int        = 0

    logger.info(f"Scoring {len(jobs)} eligible jobs with Groq ({MODEL})")

    for job in jobs:
        scored_job = score_job(job, profile)

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
                apply_angle = scored_job.get("apply_angle", ""),
                db_path     = db_path,
            )

        if scored_job["score"] >= 8:
            urgent.append(scored_job)
        elif scored_job["score"] >= 6:
            digest.append(scored_job)
        else:
            low.append(scored_job)

    logger.info(
        f"Scoring complete: {len(urgent)} urgent, {len(digest)} digest, "
        f"{len(low)} low, {expired_count} expired (dropped)"
    )
    return urgent, digest, low
