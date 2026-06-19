"""
tracker_bot.py — Standalone Telegram polling bot for the application tracker.

Handles:
  /applied <url>        — log a job application
  /responded <url>      — mark an application as responded (got a reply)
  /applications         — list all tracked applications with status
  /status               — alias for /applications

Run as a background process alongside (or after) the main pipeline:
  python -m notify.tracker_bot

The bot uses long-polling (python-telegram-bot ApplicationBuilder) and runs
until it receives a SIGTERM/SIGINT, so kill it with `kill <pid>` from run.sh
once the main pipeline finishes.
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is in path when run as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logger = logging.getLogger("tracker_bot")
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── DB path resolution ─────────────────────────────────────────────────────────
# Uses TRACKER_DB_PATH env var if set, otherwise auto-discovers the pipeline DB:
# finds the most recently modified .db file in the data/ directory.
# Falls back to data/profile.db (the default when profile.yaml is used).
def _resolve_db_path() -> str:
    explicit = os.getenv("TRACKER_DB_PATH", "")
    if explicit and os.path.exists(explicit):
        return explicit
    data_dir = Path("data")
    if data_dir.exists():
        db_files = list(data_dir.glob("*.db"))
        if db_files:
            # pick the most recently modified one (= the active pipeline DB)
            return str(max(db_files, key=lambda p: p.stat().st_mtime))
    return "data/profile.db"  # default — created by init_db when pipeline first runs

_DB_PATH = _resolve_db_path()

IST = timezone(timedelta(hours=5, minutes=30))


def _fmt_date(iso: str) -> str:
    """Format an ISO datetime string to a readable IST date."""
    try:
        dt = datetime.fromisoformat(iso).astimezone(IST)
        return dt.strftime("%-d %b %Y")
    except Exception:
        return iso or "unknown"


def _status_emoji(status: str) -> str:
    return {
        "applied":       "📤",
        "followup_sent": "📨",
        "responded":     "✅",
        "dead":          "💀",
    }.get(status, "❓")


def _extract_url(text: str) -> str | None:
    """Extract the first URL from a message text."""
    match = re.search(r'https?://\S+', text)
    return match.group(0).rstrip(".,)>") if match else None


def _is_authorised(update: Update) -> bool:
    """Only accept commands from the configured chat (group or user)."""
    chat_id = str(update.effective_chat.id)
    return not TELEGRAM_CHAT_ID or chat_id == TELEGRAM_CHAT_ID


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_applied(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /applied <url>
    Logs a job application. Optionally parses company/title from the text
    if provided after the URL.
    """
    if not _is_authorised(update):
        return

    text = update.message.text or ""
    url  = _extract_url(text)

    if not url:
        await update.message.reply_text(
            "⚠️ Usage: /applied <job_url>\n\nExample:\n/applied https://jobs.lever.co/company/12345"
        )
        return

    from storage.db import log_application, init_db
    init_db(_DB_PATH)

    is_new = log_application(url=url, db_path=_DB_PATH)
    if is_new:
        await update.message.reply_text(
            f"✅ Application logged!\n\n"
            f"📎 {url}\n\n"
            f"I'll remind you with a follow-up draft in 7 days if there's no response, "
            f"and mark it dead after 14 days. Use /responded <url> if they get back to you."
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Already tracking this application:\n{url}"
        )


async def cmd_responded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /responded <url>
    Mark an application as responded — stops follow-up reminders.
    """
    if not _is_authorised(update):
        return

    text = update.message.text or ""
    url  = _extract_url(text)

    if not url:
        await update.message.reply_text(
            "⚠️ Usage: /responded <job_url>\n\nExample:\n/responded https://jobs.lever.co/company/12345"
        )
        return

    from storage.db import mark_application_responded, init_db
    init_db(_DB_PATH)

    found = mark_application_responded(url=url, db_path=_DB_PATH)
    if found:
        await update.message.reply_text(
            f"🎉 Marked as responded — good luck with the next round!\n\n📎 {url}"
        )
    else:
        await update.message.reply_text(
            f"⚠️ Couldn't find this URL in your tracked applications:\n{url}\n\n"
            f"Make sure it matches what you used with /applied."
        )


async def cmd_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /applications  (also /status)
    Show all tracked applications with their current status.
    """
    if not _is_authorised(update):
        return

    from storage.db import get_all_applications, init_db
    init_db(_DB_PATH)

    apps = get_all_applications(db_path=_DB_PATH)

    if not apps:
        await update.message.reply_text(
            "📭 No applications tracked yet.\n\nUse /applied <url> after you apply to a job."
        )
        return

    # Group by status
    active    = [a for a in apps if a["status"] == "applied"]
    followups = [a for a in apps if a["status"] == "followup_sent"]
    responded = [a for a in apps if a["status"] == "responded"]
    dead      = [a for a in apps if a["status"] == "dead"]

    lines = [
        f"📋 Application Tracker — {len(apps)} total\n",
        f"📤 Applied: {len(active)}  |  📨 Followed up: {len(followups)}  |  "
        f"✅ Responded: {len(responded)}  |  💀 Dead: {len(dead)}\n",
    ]

    def _entry(app: dict) -> str:
        emoji   = _status_emoji(app["status"])
        date    = _fmt_date(app["applied_at"])
        company = app.get("company") or "?"
        url     = app["url"]
        # Shorten very long URLs for readability
        short_url = url if len(url) <= 60 else url[:57] + "…"
        return f"{emoji} {company} — {date}\n   {short_url}"

    # Show active + followup first, then responded, then dead (collapsed)
    for section, label, items in [
        ("active",    "⏳ Waiting for response:", active),
        ("followup",  "📨 Follow-up sent:",       followups),
        ("responded", "✅ Got a reply:",           responded),
        ("dead",      "💀 No response (>14d):",   dead),
    ]:
        if items:
            lines.append(f"\n{label}")
            for app in items[:10]:   # cap at 10 per section to avoid Telegram length limit
                lines.append(_entry(app))
            if len(items) > 10:
                lines.append(f"  … and {len(items) - 10} more")

    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a quick command reference."""
    if not _is_authorised(update):
        return
    await update.message.reply_text(
        "🤖 JobRadar Application Tracker\n\n"
        "/applied <url>    — log a job you applied to\n"
        "/responded <url>  — mark as replied (stops reminders)\n"
        "/applications     — show all tracked applications\n"
        "/status           — same as /applications\n"
        "/help             — this message\n\n"
        "Follow-ups are automatically drafted at 7 days.\n"
        "Applications with no response are marked dead at 14 days."
    )


# ── Main entrypoint ────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set — cannot start tracker bot")
        sys.exit(1)

    logger.info("Starting tracker bot (long-polling)…")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("applied",      cmd_applied))
    app.add_handler(CommandHandler("responded",    cmd_responded))
    app.add_handler(CommandHandler("applications", cmd_applications))
    app.add_handler(CommandHandler("status",       cmd_applications))
    app.add_handler(CommandHandler("help",         cmd_help))

    logger.info("Tracker bot is polling for commands…")
    app.run_polling(
        drop_pending_updates = True,   # ignore any commands sent while bot was offline
        allowed_updates      = ["message"],
    )


if __name__ == "__main__":
    main()
