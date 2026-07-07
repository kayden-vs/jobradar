"""
sources/telegram_channels.py — Fetch job posts from Indian Telegram channels via Telethon.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY TELETHON (MTProto) INSTEAD OF HTML SCRAPING:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Scraping t.me/s/<channel> pages is fragile — Telegram changes their
HTML structure frequently and uses CSP/Cloudflare that breaks scrapers.
Telethon uses the official MTProto API (the same protocol Telegram apps
use), so it is immune to frontend changes and gives clean structured
message objects (message.text, message.date, etc.) without any parsing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP REQUIREMENTS (one-time, before first run):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Get api_id and api_hash from https://my.telegram.org (free signup):
       TELEGRAM_API_ID=<integer>
       TELEGRAM_API_HASH=<hex string>

2. Run tools/telethon_login.py ONCE locally to generate a session string:
       python tools/telethon_login.py
   It prompts for your phone number + OTP and prints/saves:
       TELEGRAM_SESSION_STRING=<long base64 string>

3. All three must be in .env. After that, all runs are fully headless —
   no interactive prompt. StringSession stores auth entirely in memory
   (no .session file to manage or persist across EC2 reboots).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RATE LIMITS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- 1.5s delay between channel fetches (asyncio.sleep) to avoid FloodWaitError
- FloodWaitError: if caught, log the required wait time and skip that
  channel for this run rather than blocking the whole pipeline
- Gemini AI calls use the shared gemini_throttle() (4.5s between calls)
  — same token shared with hackernews.py and scorer.py, preventing
  rate-limit collisions across the whole pipeline
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from google import genai
from google.genai import types
from pipeline.gemini_throttle import gemini_throttle

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# CHANNEL LIST
# Only public channels whose usernames resolve correctly.
# Telethon raises ValueError/UsernameInvalidError on bad usernames
# — these are caught per-channel so one bad channel never fails the rest.
# ─────────────────────────────────────────────────────────────────
CHANNELS: list[str] = [
    "dot_aware",
    "internfreak",
    "getjobss",
    "fresheroffcampus",
    "jobsandinternshipsupdates",
    "CSE_IT_BCA_MCA_Computer_Jobs",
    "jobsinternshipswale",
    "jobsandinternshipsindia",
    "gocareers",
]

# Messages to fetch per channel — 7 is enough to catch recent posts
# without over-fetching from slow/inactive channels.
MESSAGES_PER_CHANNEL: int = 7

# Seconds to sleep between channel fetches — keeps us well under
# Telegram's rate limit. FloodWaitError retry is not done; we skip instead.
INTER_CHANNEL_DELAY: float = 1.5

# Gemini model — same as scorer.py and hackernews.py
GEMINI_MODEL: str = "gemini-3.1-flash-lite"


# ─────────────────────────────────────────────────────────────────
# HEURISTIC PRE-FILTER
# Lightweight keyword checks run BEFORE Gemini to avoid wasting
# AI calls on obvious non-job posts (course ads, WhatsApp promos,
# government exam announcements, etc.).
#
# Design: two-gate approach
#   Gate 1: must match at least one job-intent keyword
#   Gate 2: must match at least one tech-role keyword
# Either gate alone would have false positives.
# ─────────────────────────────────────────────────────────────────

_JOB_INTENT_KEYWORDS: frozenset[str] = frozenset([
    "hiring", "intern", "internship", "apply", "vacancy", "opening",
    "recruitment", "job", "fresher", "graduate", "walk-in", "walkin",
    "opportunity", "position", "role", "joining",
])

_TECH_ROLE_KEYWORDS: frozenset[str] = frozenset([
    "developer", "engineer", "software", "backend", "frontend", "fullstack",
    "full stack", "full-stack", "sde", "swe", "devops", "python", "java",
    "golang", "go ", "node", "nodejs", "react", "typescript", "cloud",
    "data", "ml", "ai ", "tech", "it ", "computer", "coding", "programming",
    "api", "database", "web", "mobile", "android", "ios",
])

# These patterns definitively mark a post as noise — skip regardless of
# job-intent or tech keywords being present.
_NOISE_PATTERNS: list[str] = [
    r"pay after placement",
    r"guaranteed (?:job|placement|salary)",
    r"whatsapp (?:group|channel|link)",
    r"join (?:our|this|my) (?:channel|group|community)",
    r"\bibps\b",           # banking exam
    r"\bupsc\b",           # civil services exam
    r"\bssc\b",            # staff selection commission
    r"\bneet\b",           # medical entrance
    r"\bgate exam\b",      # engineering entrance
    r"\btifr\b",           # research institute recruitment
    r"\bisro\b",           # space agency exam
    r"\bdrdo\b",           # defence research exam
    r"free (?:course|training|bootcamp)",
    r"course (?:fee|enroll|enrollment|registration)",
    r"batch (?:start|starting|begins|enrollment)",
]
_NOISE_RE: re.Pattern = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)

# Emoji ranges — used to strip emojis before keyword matching so that
# emoji-heavy posts (e.g. "🚀💼 Hiring Backend Dev!") still match keywords.
# Note: dedup hashing in storage/db.py _normalize() already strips emojis
# via [^a-z0-9 ] regex — this strip is only for heuristic matching here.
_EMOJI_RE: re.Pattern = re.compile(
    "[\U00010000-\U0010ffff"      # supplementary multilingual plane (most emojis)
    "\U0001f300-\U0001f9ff"       # misc symbols
    "\u2600-\u26ff"               # misc symbols block
    "\u2700-\u27bf"               # dingbats
    "]",
    flags=re.UNICODE,
)


def _passes_heuristic(text: str) -> bool:
    """
    Lightweight pre-filter to skip obvious non-job posts before sending
    to Gemini. Returns True if the post is worth AI extraction.

    Logic:
      - Skip if text is too short (likely a media caption or just emojis)
      - Skip if any noise pattern matches (course ads, exam announcements, etc.)
      - Must have at least one job-intent keyword AND one tech-role keyword
    """
    if not text or len(text.strip()) < 50:
        return False

    # Strip emojis for cleaner keyword matching
    clean = _EMOJI_RE.sub("", text).lower()

    # Gate 0: hard noise patterns override everything
    if _NOISE_RE.search(clean):
        return False

    # Gate 1: must show job-intent
    has_intent = any(kw in clean for kw in _JOB_INTENT_KEYWORDS)
    if not has_intent:
        return False

    # Gate 2: must mention a tech role
    has_tech = any(kw in clean for kw in _TECH_ROLE_KEYWORDS)
    if not has_tech:
        return False

    return True


# ─────────────────────────────────────────────────────────────────
# TELETHON CLIENT
# ─────────────────────────────────────────────────────────────────

def _get_telegram_credentials() -> tuple[int, str, str] | None:
    """
    Reads Telegram credentials from env. Returns (api_id, api_hash,
    session_string) or None if any are missing (with a warning logged).
    """
    api_id_str      = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash        = os.getenv("TELEGRAM_API_HASH", "").strip()
    session_string  = os.getenv("TELEGRAM_SESSION_STRING", "").strip()

    if not api_id_str:
        logger.warning(
            "TELEGRAM_API_ID not set — skipping Telegram channels source. "
            "Get it from https://my.telegram.org and run tools/telethon_login.py"
        )
        return None
    if not api_hash:
        logger.warning(
            "TELEGRAM_API_HASH not set — skipping Telegram channels source. "
            "Get it from https://my.telegram.org"
        )
        return None
    if not session_string:
        logger.warning(
            "TELEGRAM_SESSION_STRING not set — skipping Telegram channels source. "
            "Run tools/telethon_login.py once locally to generate it."
        )
        return None

    try:
        api_id = int(api_id_str)
    except ValueError:
        logger.error(f"TELEGRAM_API_ID must be an integer, got: {api_id_str!r}")
        return None

    return api_id, api_hash, session_string


# ─────────────────────────────────────────────────────────────────
# ASYNC MESSAGE FETCHING
# ─────────────────────────────────────────────────────────────────

async def _fetch_all_channels(
    api_id: int,
    api_hash: str,
    session_string: str,
) -> dict[str, list[tuple[str, datetime]]]:
    """
    Connects to Telegram via Telethon MTProto and fetches the latest
    MESSAGES_PER_CHANNEL messages from each channel in CHANNELS.

    Returns:
        dict mapping channel_name -> list of (message_text, message_date)
        Only messages with non-empty text are included.

    Error handling (per-channel):
        - FloodWaitError: log the required wait, skip channel this run
        - ValueError / UsernameInvalidError: log invalid channel, skip
        - Any other error: log and continue to next channel
    """
    # Late import — prevents import errors if telethon isn't installed when
    # this module is imported by the pipeline but the source is disabled.
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import FloodWaitError, UsernameInvalidError
    except ImportError:
        logger.error("telethon not installed — run: pip install telethon")
        return {}

    results: dict[str, list[tuple[str, datetime]]] = {}

    async with TelegramClient(StringSession(session_string), api_id, api_hash) as client:
        for i, channel in enumerate(CHANNELS):
            # Rate-limit delay between channel requests (skip before first)
            if i > 0:
                await asyncio.sleep(INTER_CHANNEL_DELAY)

            try:
                messages = await client.get_messages(channel, limit=MESSAGES_PER_CHANNEL)
                channel_posts: list[tuple[str, datetime]] = []

                for msg in messages:
                    # Only process messages with actual text content
                    text = getattr(msg, "text", None) or ""
                    if not text.strip():
                        continue

                    # message.date is a timezone-aware datetime (UTC) from Telethon
                    msg_date = getattr(msg, "date", None) or datetime.now(timezone.utc)

                    channel_posts.append((text, msg_date))

                results[channel] = channel_posts
                logger.info(
                    f"Telegram/{channel}: fetched {len(messages)} messages, "
                    f"{len(channel_posts)} with text"
                )

            except FloodWaitError as e:
                # Telegram asks us to wait e.seconds — skip this channel
                # rather than blocking the entire pipeline.
                logger.warning(
                    f"Telegram/{channel}: FloodWaitError — Telegram requires waiting "
                    f"{e.seconds}s. Skipping this channel for this run."
                )
                results[channel] = []

            except (ValueError, UsernameInvalidError) as e:
                # Channel username doesn't exist or user can't access it
                logger.warning(f"Telegram/{channel}: invalid or inaccessible channel — {e}")
                results[channel] = []

            except Exception as e:
                logger.error(f"Telegram/{channel}: unexpected error — {e}")
                results[channel] = []

    return results


# ─────────────────────────────────────────────────────────────────
# GEMINI AI EXTRACTION
# ─────────────────────────────────────────────────────────────────

def _gemini_client() -> genai.Client:
    """Create a Gemini client using GEMINI_API_KEY from env."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    return genai.Client(api_key=api_key)


def _parse_posts_with_gemini(
    posts: list[tuple[str, datetime, str]],  # (text, date, channel)
) -> list[dict]:
    """
    Send batches of Telegram posts to Gemini to extract structured job dicts.

    Input posts are (text, date, channel_name) tuples that have already
    passed the heuristic pre-filter.

    Uses:
    - gemini_throttle() from pipeline.gemini_throttle (shared 4.5s interval)
    - gemini-3.1-flash-lite with JSON mode
    - Batch size 5 (same as hackernews.py) to keep responses clean

    Returns list of job dicts with keys: title, company, location,
    description, url, salary, source, posted_at.
    """
    if not posts:
        return []

    client = _gemini_client()
    all_jobs: list[dict] = []
    batch_size = 5

    for i in range(0, len(posts), batch_size):
        batch = posts[i : i + batch_size]

        # Format posts for the prompt: numbered, with channel attribution
        formatted_posts = []
        for j, (text, date, channel) in enumerate(batch, 1):
            formatted_posts.append(
                f"--- Post {j} (from @{channel}, {date.strftime('%Y-%m-%d')}) ---\n{text[:2000]}"
            )
        combined = "\n\n".join(formatted_posts)

        # Respect shared Gemini rate limit (4.5s inter-call interval)
        # This prevents collisions with scorer.py and hackernews.py calls.
        gemini_throttle()

        prompt = f"""Extract ALL job opportunities from these Telegram channel posts.
Each post is separated by ---. These are unstructured text posts from Indian job channels.

Return ONLY a valid JSON array. If a post contains NO job listing, skip it.
If no jobs are found at all, return an empty array [].

For each job posting extract:
- title: job title (e.g. "Backend Engineering Intern", "SDE Intern")
- company: company name (extract from post; use "" if not mentioned)
- location: city, state, "Remote", or "India" if unspecified
- description: the full relevant text from the post describing the role
- url: application URL or link from the post (often labeled "Apply" or "Apply here"); use "" if none
- salary: stipend/salary if mentioned (e.g. "₹20,000/month", "5 LPA"); use "" if not mentioned

Rules:
- One job per JSON object. If a post lists multiple jobs, create one object per job.
- If the post is just a WhatsApp link, course ad, or channel promotion, return nothing for it.
- For Indian channels: extract INR stipend amounts carefully — they often use ₹ or "Rs" or "LPA"
- "Apply link:" or "🔗" often precedes the application URL — extract it as the url field

Posts:
{combined}"""

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "You extract structured job data from unstructured Telegram posts. "
                        "Always respond with a valid JSON array only. No markdown, no explanation."
                    ),
                    temperature=0.1,
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                ),
            )

            text_response = response.text.strip()

            # Safety: strip markdown fences if present despite JSON mode
            if text_response.startswith("```"):
                text_response = text_response.split("```")[1]
                if text_response.startswith("json"):
                    text_response = text_response[4:].strip()

            jobs_raw = json.loads(text_response)
            if not isinstance(jobs_raw, list):
                jobs_raw = []

            for job in jobs_raw:
                if not isinstance(job, dict):
                    continue

                title = job.get("title", "").strip()
                if not title or len(title) < 3:
                    continue  # Skip malformed extractions

                # Find the original post's date (use first matching batch item's date)
                # — approximate: use batch start date for the whole batch
                post_date = batch[0][1] if batch else datetime.now(timezone.utc)

                all_jobs.append({
                    "title":       title,
                    "company":     job.get("company", "").strip(),
                    "location":    job.get("location", "India").strip() or "India",
                    "description": job.get("description", "").strip(),
                    "url":         job.get("url", "").strip(),
                    "salary":      job.get("salary", "").strip(),
                    "source":      "telegram_channels",
                    "posted_at":   post_date.isoformat(),
                })

        except json.JSONDecodeError as e:
            logger.warning(f"Telegram AI batch {i}: JSON parse error — {e}")
        except Exception as e:
            logger.warning(f"Telegram AI batch {i}: extraction failed — {e}")

    return all_jobs


# ─────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def fetch_telegram_channels() -> list[dict]:
    """
    Main entry point: fetch and parse job posts from Telegram channels.

    Flow:
      1. Validate env vars (TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING)
      2. Fetch latest 7 messages per channel via Telethon MTProto API
      3. Apply heuristic pre-filter (skip obvious noise)
      4. Send surviving posts to Gemini in batches for structured extraction
      5. Return list[dict] in standard job dict format

    Returns [] if env vars are missing or all channels fail.
    One channel failing never stops others (per-channel error handling).
    """
    creds = _get_telegram_credentials()
    if creds is None:
        return []

    api_id, api_hash, session_string = creds

    # ── Step 1: Fetch raw messages from all channels ──────────────────────
    # asyncio.run() bridges from sync pipeline to async Telethon calls.
    # JobRadar is single-threaded so there's no existing event loop to worry about.
    logger.info(f"Telegram channels: fetching from {len(CHANNELS)} channels")
    try:
        channel_messages = asyncio.run(
            _fetch_all_channels(api_id, api_hash, session_string)
        )
    except Exception as e:
        logger.error(f"Telegram: fatal error during message fetch — {e}")
        return []

    # ── Step 2: Log raw counts and apply heuristic pre-filter ────────────
    total_raw = sum(len(msgs) for msgs in channel_messages.values())
    logger.info(f"Telegram channels: {total_raw} raw messages fetched across all channels")

    # Collect surviving posts with their metadata for AI extraction
    # Format: (text, date, channel_name)
    filtered_posts: list[tuple[str, datetime, str]] = []

    for channel, posts in channel_messages.items():
        channel_raw = len(posts)
        channel_passed = 0
        for text, date in posts:
            if _passes_heuristic(text):
                filtered_posts.append((text, date, channel))
                channel_passed += 1
        logger.info(
            f"Telegram/{channel}: {channel_raw} raw → {channel_passed} passed heuristic filter"
        )

    logger.info(
        f"Telegram channels: {len(filtered_posts)}/{total_raw} posts passed heuristic filter"
    )

    if not filtered_posts:
        logger.info("Telegram channels: no posts survived heuristic filter — no AI calls made")
        return []

    # ── Step 3: AI extraction → structured job dicts ─────────────────────
    jobs = _parse_posts_with_gemini(filtered_posts)
    logger.info(f"Telegram channels: {len(jobs)} jobs extracted from {len(filtered_posts)} posts")

    return jobs
