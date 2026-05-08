import os
import json
import time
import yaml
import logging
from groq import Groq
from storage.db import save_job

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

    return f"""You are a job relevance scorer for a specific candidate. Score how relevant a job posting is for this person.

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / 0 years experience

Target roles (priority order):
{chr(10).join('- ' + r for r in candidate['roles']['primary'])}
Also acceptable: {', '.join(candidate['roles']['secondary'])}

Tech stack:
- Strong: {', '.join(candidate['skills']['strong'])}
- Learning: {', '.join(candidate['skills']['learning'])}

Key project: {candidate['projects'][0]['name']}
Description: {candidate['projects'][0]['description']}
Relevance signal: {candidate['projects'][0]['relevance_signal']}

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
- 10 = Perfect (Go + fintech/crypto + intern/fresher + India/remote)
- 8-9 = Very strong (backend intern, Go or close stack)
- 6-7 = Good (backend adjacent, potentially relevant)
- 4-5 = Weak (tangentially related)
- 1-3 = Not relevant

Mandatory rules:
- Requires >1 year exp: score 0-2 (pre-filter miss)
- Go/Golang mentioned: +2 to base score
- Fintech/crypto/payments company using Go: +2 to base score
- Crypto exchange project relevant: +2 to base score
- Location outside India AND in-office only: score = 1

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
