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


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate  = profile["candidate"]
    hard_reject = profile.get("hard_reject", {})
    today = datetime.now().strftime("%B %d, %Y")

    # Education context — built from profile so every user gets the right details
    edu        = candidate.get("education", {})
    degree     = edu.get("degree", "Student")
    graduation = edu.get("graduation", "upcoming")
    exp_years  = candidate.get("experience", {}).get("years", 0)
    max_exp    = candidate.get("experience", {}).get("max_required", 1)

    # Compensation floor — from profile, not hardcoded
    min_stipend = candidate.get("salary", {}).get("min_stipend_inr", 0)

    # Age threshold — must match what prefilter already enforces
    max_age_days = hard_reject.get("max_job_age_days", 45)

    # Score rubric lines — built from the user's actual target roles and top skills
    primary_roles = candidate["roles"]["primary"]
    top_skills    = candidate["skills"]["strong"][:3]   # top 3 for concise rubric
    rubric_perfect  = f"backend intern/fresher + India/Remote + {primary_roles[0]}"
    rubric_strong   = f"{primary_roles[0]}, {', '.join(top_skills[:2])}, relevant company"

    # Build project context string from all projects in profile
    projects_text = "\n".join(
        f"- {p['name']}: {p['description']} | Signals: {p['relevance_signal']}"
        for p in candidate.get('projects', [])
    )

    return f"""You are a job relevance scorer for a specific candidate. Score how relevant a job posting is for this person.

Today's Date: {today}

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / {exp_years} years experience ({degree}, graduating {graduation})

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

Score 1-10:
- 10 = Perfect match ({rubric_perfect})
- 8-9 = Very strong ({rubric_strong})
- 6-7 = Good (backend adjacent, potentially relevant, worth applying)
- 4-5 = Weak (tangentially related)
- 1-3 = Not relevant

Mandatory rules — apply in this exact order, override scoring bonuses:
1. EXPIRY CHECK (highest priority): If the description contains ANY of these signals—
   application closed / hiring closed / recruitment closed / position filled /
   no longer accepting / deadline has passed / last date was [past date]—
   set score=1, apply_urgency="expired", expired=true. Do NOT apply any bonuses.
2. Requires >{max_exp} year experience: score 1-2 (pre-filter miss, still log it)
3. Location is outside India AND in-office only: score=1
4. Post is older than {max_age_days} days (check posted dates in description): score 1-3
5. Go/Golang mentioned: +2 to base score
6. TypeScript or Node.js backend role: +1 to base score
7. General backend focus (REST APIs, microservices, databases): +1 to base score
8. Fintech/crypto/payments company: +2 to base score
9. Any of candidate's projects are directly relevant: +2 to base score
10. Internshala source with matching stipend (>={min_stipend} INR/month): slight bonus

TOKEN SAVING RULE — IMPORTANT:
If score < 6: set reason="", highlights=[], red_flags=[] — do NOT write any text for these fields.
If score >= 6: fill in reason, highlights, and red_flags normally.

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
    """Score a single job with Groq llama-3.3-70b-versatile."""

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
        job["score"]    = 1
        job["expired"]  = True
        job["reason"]   = ""
        job["highlights"] = ""
        job["red_flags"]  = ""
        job["urgency"]    = "expired"
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
                    "content": "You are a precise job relevance scorer. Always respond with valid JSON only, no markdown.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,   # Low temperature for consistent scoring
            max_tokens=512,
        )

        text = response.choices[0].message.content.strip()

        # Strip markdown code fences if model adds them despite instructions
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()

        result = json.loads(text)

        job["score"]      = int(result.get("score", 0))
        job["expired"]    = bool(result.get("expired", False))
        job["reason"]     = result.get("reason", "")
        job["highlights"] = ", ".join(result.get("highlights", []))
        job["red_flags"]  = ", ".join(result.get("red_flags", []))
        job["urgency"]    = result.get("apply_urgency", "low")

        logger.info(f"Scored: {job['title']} @ {job['company']} -> {job['score']}/10 [{job['urgency']}]")
        return job

    except Exception as e:
        logger.error(f"Groq scoring failed for {job.get('title', '?')}: {e}")
        job["score"]      = -1
        job["reason"]     = f"Scoring error: {e}"
        job["highlights"] = ""
        job["red_flags"]  = ""
        job["urgency"]    = "low"
        return job


def score_all(
    jobs: list[dict],
    profile: dict,
    db_path: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Score all eligible jobs and split into buckets.
    Throttled to 1 req per 3 sec to stay under Groq's 30 req/min limit.

    Args:
        jobs:    Pre-filtered jobs to score.
        profile: Loaded user profile dict (from main.py — not re-loaded here).
        db_path: Path to the user's SQLite file for persisting scored jobs.

    Returns: (urgent_jobs [8-10], digest_jobs [6-7], low_jobs [<6])
    Expired jobs (urgency='expired' or expired=True) are excluded from all
    buckets and never notified or persisted.
    """
    urgent  = []
    digest  = []
    low     = []
    expired_count = 0

    logger.info(f"Scoring {len(jobs)} eligible jobs with Groq ({MODEL})")

    for job in jobs:
        scored_job = score_job(job, profile)

        # Hard drop: expired jobs are never sent anywhere
        if scored_job.get("expired") or scored_job.get("urgency") == "expired":
            expired_count += 1
            logger.debug(f"Dropping expired job: {scored_job.get('title','?')}")
            continue

        # Only persist jobs worth reviewing
        if scored_job["score"] >= 5:
            save_job(
                scored_job,
                db_path    = db_path,
                score      = scored_job["score"],
                reason     = scored_job.get("reason", ""),
                highlights = scored_job.get("highlights", ""),
                red_flags  = scored_job.get("red_flags", ""),
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
