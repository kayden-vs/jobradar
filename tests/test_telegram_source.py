"""
tools/test_telegram_source.py — Standalone test for the Telegram channels source.

Tests fetch_telegram_channels() in isolation WITHOUT touching main.py.
Run this after completing the one-time login (tools/telethon_login.py).

Usage:
    cd /path/to/jobradar
    python tools/test_telegram_source.py

What this tests:
  1. Per-channel raw message count (validates Telethon auth works)
  2. Per-channel post-heuristic count (validates noise filtering)
  3. Total parsed job count after Gemini extraction
  4. 2-3 sample job dicts printed in full for visual verification

DO NOT wire into main.py until this output is reviewed and approved.
"""

import os
import sys
import json
import asyncio
import logging

# Add project root to path so imports work when run from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Configure logging for readable test output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("test_telegram")


def _check_env() -> bool:
    """Verify all required env vars are set before running the test."""
    missing = []
    for var in ["TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION_STRING", "GEMINI_API_KEY"]:
        if not os.getenv(var, "").strip():
            missing.append(var)

    if missing:
        print("\n❌ Missing required env vars:")
        for var in missing:
            print(f"   {var}")
        if "TELEGRAM_SESSION_STRING" in missing:
            print("\n   → Run tools/telethon_login.py first to generate a session string.")
        if "TELEGRAM_API_ID" in missing or "TELEGRAM_API_HASH" in missing:
            print("\n   → Get API credentials from https://my.telegram.org")
        return False
    return True


def _print_separator(title: str = "") -> None:
    if title:
        pad = (60 - len(title) - 2) // 2
        print("─" * pad + f" {title} " + "─" * pad)
    else:
        print("─" * 62)


def main() -> None:
    print("\n" + "=" * 62)
    print("  JobRadar — Telegram Channels Source Test")
    print("=" * 62 + "\n")

    # ── Pre-flight checks ─────────────────────────────────────────────────
    if not _check_env():
        sys.exit(1)

    print("✅ All env vars present. Starting test...\n")

    # ── Import the source module ──────────────────────────────────────────
    try:
        from sources.telegram_channels import (
            CHANNELS,
            MESSAGES_PER_CHANNEL,
            _get_telegram_credentials,
            _fetch_all_channels,
            _passes_heuristic,
            _parse_posts_with_gemini,
        )
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        print("   Make sure telethon is installed: pip install telethon")
        sys.exit(1)

    creds = _get_telegram_credentials()
    if creds is None:
        print("❌ Failed to read Telegram credentials from env.")
        sys.exit(1)
    api_id, api_hash, session_string = creds

    # ── STEP 1: Fetch raw messages ────────────────────────────────────────
    _print_separator("STEP 1: Fetching Raw Messages")
    print(f"Channels: {CHANNELS}")
    print(f"Messages per channel: {MESSAGES_PER_CHANNEL}\n")

    try:
        channel_messages = asyncio.run(
            _fetch_all_channels(api_id, api_hash, session_string)
        )
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
        print("   Possible causes:")
        print("   - Session string is invalid (re-run tools/telethon_login.py)")
        print("   - No internet / Telegram blocked")
        sys.exit(1)

    print("\n📊 Raw message counts per channel:")
    total_raw = 0
    for channel in CHANNELS:
        msgs = channel_messages.get(channel, [])
        total_raw += len(msgs)
        status = "✅" if msgs else "⚠️ "
        print(f"   {status} @{channel}: {len(msgs)} messages")

    print(f"\n   Total raw messages: {total_raw}")

    # ── STEP 2: Apply heuristic pre-filter ───────────────────────────────
    _print_separator("STEP 2: Heuristic Pre-Filter")

    filtered_posts = []
    print("\n📊 Per-channel heuristic results:")
    for channel in CHANNELS:
        posts = channel_messages.get(channel, [])
        passed = []
        for text, date in posts:
            if _passes_heuristic(text):
                passed.append((text, date, channel))
        filtered_posts.extend(passed)
        print(f"   @{channel}: {len(posts)} raw → {len(passed)} passed")

    print(f"\n   Total after filter: {len(filtered_posts)}/{total_raw} posts")

    # Show some examples of what was filtered out
    if total_raw > len(filtered_posts):
        print("\n📋 Sample FILTERED-OUT posts (first 2 that failed):")
        shown = 0
        for channel in CHANNELS:
            for text, date in channel_messages.get(channel, []):
                if not _passes_heuristic(text) and shown < 2:
                    preview = text.replace("\n", " ")[:120]
                    print(f"   [{channel}] {preview}...")
                    shown += 1
            if shown >= 2:
                break

    # ── STEP 3: Gemini AI extraction ─────────────────────────────────────
    _print_separator("STEP 3: Gemini AI Extraction")

    if not filtered_posts:
        print("\n⚠️  No posts survived heuristic filter — no Gemini calls made.")
        print("   This means either channels are inactive or all posts are noise.")
        print("   Try adding more channels or loosening the heuristic filter.\n")
        return

    print(f"\nSending {len(filtered_posts)} posts to Gemini in batches of 5...")
    print("(This will take ~4.5s per batch due to rate limiting)\n")

    try:
        jobs = _parse_posts_with_gemini(filtered_posts)
    except Exception as e:
        print(f"❌ Gemini extraction failed: {e}")
        sys.exit(1)

    # ── RESULTS ──────────────────────────────────────────────────────────
    _print_separator("RESULTS")
    print(f"\n✅ FINAL PARSED JOB COUNT: {len(jobs)}")
    print(f"   Raw messages: {total_raw}")
    print(f"   After filter: {len(filtered_posts)}")
    print(f"   Extracted jobs: {len(jobs)}")

    if not jobs:
        print("\n⚠️  No jobs extracted. Possible causes:")
        print("   - Gemini couldn't find job structure in the posts")
        print("   - All posts were event/ad/course content")
        print("   - Try checking the raw post content below")
        print("\n📋 Sample raw posts (for debugging):")
        for text, date, channel in filtered_posts[:3]:
            print(f"\n   [{channel}] {date.strftime('%Y-%m-%d')}:")
            print("   " + text.replace("\n", "\n   ")[:300] + "...")
        return

    # ── Print 2-3 sample job dicts ────────────────────────────────────────
    _print_separator("SAMPLE JOB DICTS (first 3)")
    sample_count = min(3, len(jobs))
    print(f"\nShowing {sample_count} of {len(jobs)} extracted jobs:\n")

    for idx, job in enumerate(jobs[:sample_count], 1):
        print(f"{'─' * 40}")
        print(f"Job #{idx}:")
        # Pretty-print each field
        for key in ["title", "company", "location", "salary", "url", "source", "posted_at"]:
            value = job.get(key, "")
            if value:
                print(f"  {key:12s}: {value}")
        desc = job.get("description", "")
        if desc:
            desc_preview = desc.replace("\n", " ")[:200]
            print(f"  {'description':12s}: {desc_preview}{'...' if len(desc) > 200 else ''}")

    print(f"\n{'─' * 40}")
    print("\n✅ Test complete. Review the sample dicts above.")
    print("   If extraction looks accurate, you can wire this into main.py.")
    print("   Run: add 'telegram_channels' to profile.yaml sources and")
    print("        import fetch_telegram_channels in main.py\n")


if __name__ == "__main__":
    main()
