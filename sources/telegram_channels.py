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
# MINIMAL SANITY FILTER
# Only skips posts that literally have no content worth sending to
# Gemini. The old two-gate keyword heuristic was incorrect: it
# was blocking real job posts (e.g. "Broccoli AI is hiring for
# Software Engineer") because Indian Telegram channels use phrasing
# like "applications open", "drive", "batch 2025" which didn't
# match the hard-coded keywords. The pre-filter killed 80% of valid
# posts from @dot_aware, @fresheroffcampus, etc.
#
# The actual noise filter is already handled downstream:
#   • pipeline/prefilter.py (experience/role/location checks)
#   • pipeline/scorer.py (AI gives 1-2/10 to noise, it's dropped)
# So there's nothing to protect here — just skip genuinely empty
# posts (media-only, stickers, very short captions).
# ─────────────────────────────────────────────────────────────────

def _passes_sanity(text: str) -> bool:
    """
    Minimal sanity check — only skips posts with no actual text content.
    A 40-character minimum removes pure stickers / emoji-only media captions.
    """
    return bool(text) and len(text.strip()) >= 40


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

    # Build channel name set once — used in post-extraction validation
    # to reject Gemini hallucinating the channel name as the company name.
    _known_channels_lower = {c.lower() for c in CHANNELS}

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

        prompt = f"""You are extracting job postings from Indian Telegram job channels.

Posts are separated by ---. Each post was scraped from a Telegram channel.
The channel name shown in the header (e.g. @gocareers, @internfreak) is NOT the company —
it is just the channel that shared the post. Find the ACTUAL company hiring.

Return ONLY a valid JSON array. Skip posts with no job listing.
If no jobs found at all, return [].

For each job, extract:
- title: the exact job title from the post (e.g. "Backend Engineering Intern", "SDE-1", "Software Engineer")
- company: the name of the COMPANY hiring (NOT the channel name). Use "" if not mentioned.
- location: city or "Remote" or "India" if unclear
- description: copy the FULL original post text verbatim — do not summarize. This is critical for downstream scoring.
- url: the application link (look for "Apply:", "Apply here:", "Link:", "🔗", bit.ly links, LinkedIn URLs, Ashby/Lever/Greenhouse URLs). Use "" if none.
- salary: stipend or CTC if mentioned (e.g. "₹25,000/month", "6 LPA"). Use "" if not mentioned.

Rules:
- One JSON object per job. If a post lists 3 jobs, create 3 objects.
- If a post is a forwarded message with a job link, still extract it.
- Do NOT use the @channel name as the company name.
- If no company name is visible, set company to "".
- Keep description as the full post text, not a summary.

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
                    # 4096 tokens: batch of 5 posts × ~600 tokens each (full verbatim
                    # text as description) + JSON structure overhead.
                    # 2048 was too small — truncated responses caused silent JSON parse
                    # errors when posts were long. gemini-3.1-flash-lite supports 8192
                    # output tokens so 4096 is a safe ceiling well within limits.
                    max_output_tokens=4096,
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

            for idx_j, job in enumerate(jobs_raw):
                if not isinstance(job, dict):
                    continue

                title = job.get("title", "").strip()
                if not title or len(title) < 3:
                    continue  # Skip malformed extractions

                # Map each extracted job back to the correct post date.
                # Gemini can extract multiple jobs from one batch; we use
                # the date of the batch's first post as a safe approximation.
                # (Exact mapping would require Gemini to output a post_index
                # field — not worth the prompt complexity.)
                post_date = batch[0][1] if batch else datetime.now(timezone.utc)

                # Reject if Gemini hallucinated the channel name as company
                company_raw = job.get("company", "").strip()
                if company_raw.lower().replace(" ", "") in _known_channels_lower:
                    company_raw = ""
                # Also reject single-letter companies (Gemini confusion artifact)
                if len(company_raw) <= 1:
                    company_raw = ""

                all_jobs.append({
                    "title":       title,
                    "company":     company_raw,
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
      2. Fetch latest MESSAGES_PER_CHANNEL messages per channel via Telethon MTProto API
      3. Apply minimal sanity filter (skip empty / too-short posts)
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

    # ── Step 2: Sanity filter (skip empty/too-short posts) ────────────────
    total_raw = sum(len(msgs) for msgs in channel_messages.values())
    logger.info(f"Telegram channels: {total_raw} raw messages fetched across all channels")

    # Collect surviving posts with their metadata for AI extraction
    # Format: (text, date, channel_name)
    filtered_posts: list[tuple[str, datetime, str]] = []

    for channel, posts in channel_messages.items():
        channel_raw = len(posts)
        channel_passed = 0
        for text, date in posts:
            if _passes_sanity(text):
                filtered_posts.append((text, date, channel))
                channel_passed += 1
        logger.info(
            f"Telegram/{channel}: {channel_raw} raw → {channel_passed} passed sanity filter"
        )

    logger.info(
        f"Telegram channels: {len(filtered_posts)}/{total_raw} posts passed sanity filter"
    )

    if not filtered_posts:
        logger.info("Telegram channels: no posts survived sanity filter")
        return []

    # ── Step 3: AI extraction → structured job dicts ─────────────────────
    jobs = _parse_posts_with_gemini(filtered_posts)
    logger.info(f"Telegram channels: {len(jobs)} jobs extracted from {len(filtered_posts)} posts")

    return jobs
