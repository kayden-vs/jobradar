import requests
import logging
import google.generativeai as genai
import json
import os
from datetime import datetime

logger = logging.getLogger(__name__)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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


def fetch_hn_comments(thread_id: int) -> list[str]:
    """Fetch all top-level comments from an HN thread."""
    # Get thread item
    r = requests.get(f"{HN_API}/item/{thread_id}.json", timeout=10)
    thread = r.json()
    
    kid_ids = thread.get("kids", [])[:150]  # Top 150 comments
    comments = []
    
    for kid_id in kid_ids:
        try:
            r2 = requests.get(f"{HN_API}/item/{kid_id}.json", timeout=5)
            item = r2.json()
            if item and item.get("text") and not item.get("deleted"):
                comments.append(item["text"])
        except Exception:
            continue
    
    return comments


def parse_comments_with_ai(comments: list[str]) -> list[dict]:
    """
    Send batches of HN comments to Gemini to extract structured job data.
    """
    model = genai.GenerativeModel("gemini-2.0-flash")
    all_jobs = []
    
    # Process in batches of 20 comments to fit context window
    batch_size = 20
    for i in range(0, len(comments), batch_size):
        batch = comments[i:i+batch_size]
        combined = "\n\n---\n\n".join(batch)
        
        prompt = f"""You are extracting job postings from HackerNews "Who Is Hiring" thread comments.
Each comment may contain zero or more job opportunities.

Extract ALL job opportunities from these comments. For each job, return a JSON array of objects with these exact fields:
- title: job title
- company: company name (look for "| CompanyName |" pattern in HN posts)
- location: location or "Remote" if remote-friendly
- description: full job description text
- url: application URL or email if mentioned
- salary: salary/stipend if mentioned, else ""
- requires_experience: estimated years of experience required as a number (0 if internship/fresher)
- tech_stack: comma-separated list of mentioned technologies

Return ONLY a JSON array. No markdown, no explanation.

Comments:
{combined}"""
        
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            # Clean up if model wrapped in markdown
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            jobs = json.loads(text)
            for job in jobs:
                job["source"] = "hackernews"
                job["posted_at"] = datetime.now().isoformat()
            all_jobs.extend(jobs)
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
