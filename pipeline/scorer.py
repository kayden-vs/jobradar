import os
import json
import time
import yaml
import logging
from datetime import datetime
from groq import Groq
from storage.db import save_job
from sources.freshers_blogs import fetch_full_description

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

Score 1-10:
- 10 = Perfect match (backend intern/fresher + India/Remote + strong stack match)
- 8-9 = Very strong (backend intern, Go or TypeScript/Node.js, relevant company)
- 6-7 = Good (backend adjacent, potentially relevant, worth applying)
- 4-5 = Weak (tangentially related)
- 1-3 = Not relevant

Mandatory rules:
- Requires >1 year exp: score 0-2 (pre-filter miss)
- If the post is older than 2 months OR has a passed application deadline: score 1-3
- Go/Golang mentioned: +2 to base score
- TypeScript or Node.js backend role: +1 to base score
- General backend focus (REST APIs, microservices, databases): +1 to base score
- Fintech/crypto/payments company: +2 to base score
- Any of candidate's projects are directly relevant: +2 to base score
- Location outside India AND in-office only: score = 1
- Internshala source with matching stipend (>=10000 INR/month): slight bonus

TOKEN SAVING RULE — IMPORTANT:
If score < 6: set reason="", highlights=[], red_flags=[] — do NOT write any text for these fields.
If score >= 6: fill in reason, highlights, and red_flags normally.

Return ONLY a valid JSON object, no markdown fences:
{{
  "score": <integer 1-10>,
  "reason": "<2-3 sentences IF score>=6, else empty string>",
  "highlights": ["<reason 1>", "<reason 2>", "<reason 3> — IF score>=6, else []"],
  "red_flags": ["<issue if any> — IF score>=6, else []"],
  "golang_match": <true/false>,
  "fintech_match": <true/false>,
  "apply_urgency": "<high/medium/low>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Groq llama-3.3-70b-versatile."""

    # ── Lazy description fetch ────────────────────────────────────────────────
    # freshers_blogs sources return empty/partial descriptions intentionally
    # (avoids fetching hundreds of post pages upfront). After pre-filter confirms
    # this job is worth scoring, fetch the full body now.
    # Threshold: <100 chars — short excerpts don't give the LLM enough signal.
    desc = job.get("description", "")
    if len(desc) < 100 and job.get("url") and "freshers_blogs" in job.get("source", ""):
        logger.debug(f"Lazy-fetching JD for {job.get('title', '?')}")
        fetched = fetch_full_description(job["url"])
        if fetched:
            job["description"] = fetched

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


def score_all(jobs: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Score all eligible jobs and split into buckets.
    Throttled to 1 req per 3 sec to stay under Groq's 30 req/min limit.
    Daily budget: 1,000 req/day. With ~100 eligible jobs/run that's 10 runs/day
    before hitting the daily limit — plenty for once-daily execution.

    Returns: (urgent_jobs [8-10], digest_jobs [6-7], low_jobs [<6])
    """
    profile = load_profile()
    urgent  = []
    digest  = []
    low     = []

    logger.info(f"Scoring {len(jobs)} eligible jobs with Groq ({MODEL})")

    for job in jobs:
        scored_job = score_job(job, profile)

        # Only persist jobs worth reviewing — skip noise (score < 5 or API errors)
        if scored_job["score"] >= 5:
            save_job(
                scored_job,
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

    logger.info(f"Scoring complete: {len(urgent)} urgent, {len(digest)} digest, {len(low)} low")
    return urgent, digest, low
