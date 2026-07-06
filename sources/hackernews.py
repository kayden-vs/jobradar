import requests
import logging
import json
import os
import re
import time
from datetime import datetime
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Using Gemini 3.1 Flash-Lite — stable GA model (May 2026), 15 RPM free tier
GEMINI_MODEL = "gemini-3.1-flash-lite"
REQ_INTERVAL = 4.5  # seconds between AI calls — stays under Gemini's ~15 RPM
_last_call   = 0.0


def _gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    return genai.Client(api_key=api_key)


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < REQ_INTERVAL:
        time.sleep(REQ_INTERVAL - elapsed)
    _last_call = time.time()

# HN "Who is Hiring" thread IDs — update monthly
# Find it at: news.ycombinator.com/submitted?id=whoishiring
# NOTE: Keys must match current year. The tool auto-discovers if entry is missing.
HN_THREAD_IDS = {
    "2026-05": 47975571,  # May 2026  ← confirmed correct
    "2026-04": 47651334,  # April 2026
    "2026-03": 47329666,  # March 2026
    "2025-05": 43888624,  # May 2025
    "2025-04": 43603014,  # April 2025
}

HN_API = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"


def _autodiscover_thread_id(year: int, month: int) -> int | None:
    """
    Fallback: query Algolia HN search API to find the current month's
    'Who is Hiring' thread automatically. This means you never need to
    manually update the dict above — but keeping the dict is faster.
    """
    import calendar
    month_name = calendar.month_name[month]  # e.g. "May"
    query = f'Ask HN: Who is hiring? ({month_name} {year})'
    try:
        r = requests.get(HN_ALGOLIA, params={
            "query": query,
            "tags": "story,ask_hn",
            "hitsPerPage": 5,
        }, timeout=10)
        hits = r.json().get("hits", [])
        for hit in hits:
            title = hit.get("title", "")
            if "who is hiring" in title.lower() and str(year) in title:
                thread_id = int(hit["objectID"])
                logger.info(f"Auto-discovered HN thread {thread_id} for {month_name} {year}")
                return thread_id
    except Exception as e:
        logger.warning(f"HN Algolia auto-discover failed: {e}")
    return None


def get_current_thread_id() -> int | None:
    """Returns the most recent HN hiring thread ID, with auto-discovery fallback."""
    now = datetime.now()
    key = f"{now.year}-{now.month:02d}"
    thread_id = HN_THREAD_IDS.get(key)
    if thread_id:
        return thread_id
    # Auto-discover if not in the manual dict
    logger.warning(f"HN thread ID for {key} not in manual dict — trying auto-discovery")
    return _autodiscover_thread_id(now.year, now.month)


def fetch_hn_comments(thread_id: int, max_comments: int = 60) -> list[str]:
    """
    Fetch top-level comments from an HN thread.
    Capped at max_comments to control AI token usage:
    60 comments × ~800 tokens/batch → ~4,800 tokens for HN parsing.
    Gemini's generous TPD (~1.5M/day) means this has negligible budget impact.
    """
    r = requests.get(f"{HN_API}/item/{thread_id}.json", timeout=10)
    thread = r.json()

    kid_ids = thread.get("kids", [])[:max_comments]   # Hard cap here
    comments = []

    for kid_id in kid_ids:
        try:
            r2 = requests.get(f"{HN_API}/item/{kid_id}.json", timeout=5)
            item = r2.json()
            if item and item.get("text") and not item.get("deleted"):
                # Strip HTML tags — saves ~30% tokens and improves parsing
                clean_text = re.sub(r'<[^>]+>', ' ', item["text"])
                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                if len(clean_text) > 50:   # Skip trivial one-liners
                    comments.append(clean_text)
        except Exception:
            continue

    return comments


def _is_valid_job(job: dict) -> bool:
    """
    Post-AI filter: rejects discussion threads / candidate posts that
    the AI incorrectly extracted as jobs. These slip through because the
    HN thread contains them mixed with actual job postings.
    """
    title = job.get("title", "").strip()
    if len(title) < 4:
        return False

    # These patterns are discussion threads, not job postings
    non_job_prefixes = (
        "how do", "why do", "advice on", "job market",
        "anyone else", "is it just", "what is", "remote job -",
        "backend / systems",  # candidate post pattern
    )
    title_lower = title.lower()
    if any(title_lower.startswith(p) for p in non_job_prefixes):
        return False

    # Must have at least a company or url to be a real posting
    has_company = bool(job.get("company", "").strip())
    has_url     = bool(job.get("url", "").strip())
    if not has_company and not has_url:
        return False

    return True


def parse_comments_with_ai(comments: list[str]) -> list[dict]:
    """
    Send batches of HN comments to Gemini (gemini-2.5-flash) to extract
    structured job data. Batches of 5 to keep responses clean.
    Throttled at 4.5s per request to stay under ~15 RPM.
    """
    client = _gemini_client()
    all_jobs = []

    # Batch size of 5: prevents JSON truncation and keeps response tokens manageable.
    # Gemini's native JSON mode ensures clean array output without markdown fences.
    batch_size = 5
    for i in range(0, len(comments), batch_size):
        batch    = comments[i:i + batch_size]
        combined = "\n\n---\n\n".join(batch)

        _throttle()

        prompt = f"""Extract ALL job opportunities from these HackerNews 'Who is Hiring' comments.
Each comment is separated by ---.
Return ONLY a JSON array. If no jobs are found in any comment, return an empty array [].

For each job extract:
- title: job title
- company: company name
- location: location or "Remote"
- description: full job text
- url: application URL or email if mentioned, else ""
- salary: if mentioned, else ""
- requires_experience: years required as number (0 if intern/fresher)
- tech_stack: comma-separated technologies

Comments:
{combined[:4000]}"""

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="You extract job postings from text. Always respond with a valid JSON array only.",
                    temperature=0.1,
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                ),
            )

            text = response.text.strip()

            # Safety: strip markdown fences if present despite JSON mode
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:].strip()

            jobs = json.loads(text)
            if not isinstance(jobs, list):
                jobs = []

            for job in jobs:
                if _is_valid_job(job):
                    job["source"]    = "hackernews"
                    job["posted_at"] = datetime.now().isoformat()
                    all_jobs.append(job)

        except Exception as e:
            logger.warning(f"HN AI parsing batch {i} failed: {e}")

    return all_jobs


def fetch_hn_hiring() -> list[dict]:
    """Main function: fetches and parses the current HN Who's Hiring thread."""
    thread_id = get_current_thread_id()
    if not thread_id:
        logger.info("No HN thread ID for current month — skipping")
        return []
    
    logger.info(f"Fetching HN thread {thread_id}")
    comments = fetch_hn_comments(thread_id)
    logger.info(f"Got {len(comments)} comments — parsing with AI")
    jobs = parse_comments_with_ai(comments)
    logger.info(f"HackerNews: {len(jobs)} jobs extracted")
    return jobs
