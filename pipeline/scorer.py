import os
import json
import yaml
import logging
import google.generativeai as genai
from storage.db import save_job

logger = logging.getLogger(__name__)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate = profile["candidate"]
    
    return f"""You are a job relevance scorer for a specific candidate. Your job is to score how relevant a job posting is for this person.

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / 0 years experience

Target roles (in priority order):
{chr(10).join('- ' + r for r in candidate['roles']['primary'])}
Also acceptable: {', '.join(candidate['roles']['secondary'])}

Tech stack:
- Strong: {', '.join(candidate['skills']['strong'])}
- Learning: {', '.join(candidate['skills']['learning'])}

Key project: {candidate['projects'][0]['name']}
Project description: {candidate['projects'][0]['description']}
Project relevance signal: {candidate['projects'][0]['relevance_signal']}

Location: {candidate['location']['base']}
Acceptable locations: {', '.join(candidate['location']['acceptable'])}

High-priority industries (give bonus):
{', '.join(candidate['industries']['high_priority'])}

Medium-priority industries:
{', '.join(candidate['industries']['medium_priority'])}

## JOB POSTING TO SCORE

Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Location: {job.get('location', 'N/A')}
Salary/Stipend: {job.get('salary', 'Not mentioned')}
Source: {job.get('source', 'N/A')}

Job Description:
{job.get('description', 'No description available')[:4000]}

## SCORING INSTRUCTIONS

Score from 1 to 10 where:
- 10 = Perfect match (Golang + fintech/crypto + intern/fresher + remote/India, crypto exchange project directly relevant)
- 8-9 = Very strong match (backend intern, uses Go or compatible stack)  
- 6-7 = Good match (backend adjacent, could be relevant)
- 4-5 = Weak match (tangentially related)
- 1-3 = Not relevant (wrong role, wrong stack, unclear)

IMPORTANT rules:
- If the role requires more than 1 year of experience: score MUST be 0-2 max (pre-filter should have caught this but double-check)
- If the company is in fintech/crypto/payments AND uses Go: add +2 to base score
- If location is outside India AND in-office: score MUST be 1
- If the candidate's crypto exchange project is directly relevant to this company (fintech, crypto, trading, payments): add +2 to base score
- If Golang/Go is mentioned in requirements or stack: add +2 to base score

Return ONLY a valid JSON object with these exact keys:
{{
  "score": <integer 1-10>,
  "reason": "<2-3 sentence explanation of the score>",
  "highlights": ["<key reason 1>", "<key reason 2>", "<key reason 3>"],
  "red_flags": ["<issue 1 if any>"],
  "golang_match": <true/false>,
  "fintech_match": <true/false>,
  "apply_urgency": "<high/medium/low>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Gemini. Returns job dict with score fields added."""
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    try:
        prompt = build_scoring_prompt(job, profile)
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Clean markdown code fences if model adds them
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()
        
        result = json.loads(text)
        
        job["score"]       = result.get("score", 0)
        job["reason"]      = result.get("reason", "")
        job["highlights"]  = ", ".join(result.get("highlights", []))
        job["red_flags"]   = ", ".join(result.get("red_flags", []))
        job["urgency"]     = result.get("apply_urgency", "low")
        
        logger.info(f"Scored: {job['title']} @ {job['company']} → {job['score']}/10")
        return job
        
    except Exception as e:
        logger.error(f"Gemini scoring failed for {job.get('title', '?')}: {e}")
        job["score"]    = -1  # Flag as unscored
        job["reason"]   = f"Scoring error: {e}"
        job["highlights"] = ""
        job["red_flags"]  = ""
        job["urgency"]    = "low"
        return job


def score_all(jobs: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Score all jobs and split into buckets.
    Returns: (urgent_jobs, digest_jobs, low_jobs)
    """
    profile = load_profile()
    urgent  = []  # score 8-10
    digest  = []  # score 6-7
    low     = []  # score < 6
    
    for job in jobs:
        scored_job = score_job(job, profile)
        
        # Save to DB regardless of score
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
