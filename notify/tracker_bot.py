"""
tracker_bot.py — Standalone Telegram polling bot for the application tracker.

Handles:
  /applied <url>        — log a job application
  /responded <url>      — mark an application as responded (got a reply)
  /applications         — list all tracked applications with status
  /status               — alias for /applications
  /help                 — command reference

Run as a background process alongside (or after) the main pipeline:
  python -m notify.tracker_bot

The bot uses long-polling and runs until SIGTERM/SIGINT.
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

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger("tracker_bot")
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Strip whitespace — shell `source .env` can leave trailing \r on Windows-edited files
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "").strip()

# ── DB path resolution ─────────────────────────────────────────────────────────
def _resolve_db_path() -> str:
    explicit = os.getenv("TRACKER_DB_PATH", "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    data_dir = Path("data")
    if data_dir.exists():
        db_files = list(data_dir.glob("*.db"))
        if db_files:
            return str(max(db_files, key=lambda p: p.stat().st_mtime))
    return "data/profile.db"

_DB_PATH = _resolve_db_path()

IST = timezone(timedelta(hours=5, minutes=30))

# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_date(iso: str) -> str:
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
    """Extract the first URL from message text, stripping trailing punctuation."""
    match = re.search(r'https?://\S+', text)
    return match.group(0).rstrip(".,)>\"'") if match else None


def _is_authorised(update: Update) -> bool:
    """
    Accept commands from the configured TELEGRAM_CHAT_ID group, or from
    anyone if TELEGRAM_CHAT_ID is not set.

    The check is intentionally loose: it compares the absolute numeric value
    of the chat ID to handle edge cases where the env var might or might not
    have the leading '-'. Both '-1003906827922' and '1003906827922' are
    treated as the same group.
    """
    if not TELEGRAM_CHAT_ID:
        return True  # open mode — single-user bot, no guard needed

    incoming = str(update.effective_chat.id).lstrip("-")
    expected = TELEGRAM_CHAT_ID.lstrip("-")

    match = incoming == expected
    if not match:
        logger.info(
            f"Rejected command from chat {update.effective_chat.id} "
            f"(expected {TELEGRAM_CHAT_ID})"
        )
    return match


# ── Command handlers ───────────────────────────────────────────────────────────

async def cmd_applied(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/applied <url> — log a job application."""
    if not _is_authorised(update):
        return

    text = update.effective_message.text or ""
    url  = _extract_url(text)
    logger.info(f"/applied from chat={update.effective_chat.id}: {url or '(no url found)'}")

    if not url:
        await update.effective_message.reply_text(
            "⚠️ Usage: /applied <job_url>\n\n"
            "Example:\n/applied https://jobs.lever.co/company/12345"
        )
        return

    from storage.db import log_application, init_db
    init_db(_DB_PATH)

    is_new = log_application(url=url, db_path=_DB_PATH)
    if is_new:
        await update.effective_message.reply_text(
            f"✅ Application logged!\n\n"
            f"📎 {url}\n\n"
            f"I'll send a follow-up draft in 7 days if no response, "
            f"and mark it dead after 14 days.\n"
            f"Use /responded <url> when they reply."
        )
    else:
        await update.effective_message.reply_text(f"ℹ️ Already tracking:\n{url}")


async def cmd_responded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/responded <url> — mark an application as responded."""
    if not _is_authorised(update):
        return

    text = update.effective_message.text or ""
    url  = _extract_url(text)
    logger.info(f"/responded from chat={update.effective_chat.id}: {url or '(no url found)'}")

    if not url:
        await update.effective_message.reply_text(
            "⚠️ Usage: /responded <job_url>\n\n"
            "Example:\n/responded https://jobs.lever.co/company/12345"
        )
        return

    from storage.db import mark_application_responded, init_db
    init_db(_DB_PATH)

    found = mark_application_responded(url=url, db_path=_DB_PATH)
    if found:
        await update.effective_message.reply_text(
            f"🎉 Marked as responded — good luck with the next round!\n\n📎 {url}"
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ URL not found in tracked applications:\n{url}\n\n"
            f"Make sure it exactly matches what you used with /applied."
        )


async def cmd_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/applications — show all tracked applications."""
    if not _is_authorised(update):
        return
    logger.info(f"/applications from chat={update.effective_chat.id}")

    from storage.db import get_all_applications, init_db
    init_db(_DB_PATH)
    apps = get_all_applications(db_path=_DB_PATH)

    if not apps:
        await update.effective_message.reply_text(
            "📭 No applications tracked yet.\n\nUse /applied <url> after you apply to a job."
        )
        return

    active    = [a for a in apps if a["status"] == "applied"]
    followups = [a for a in apps if a["status"] == "followup_sent"]
    responded = [a for a in apps if a["status"] == "responded"]
    dead      = [a for a in apps if a["status"] == "dead"]

    lines = [
        f"📋 Application Tracker — {len(apps)} total\n",
        f"📤 {len(active)} waiting  📨 {len(followups)} followed up  "
        f"✅ {len(responded)} responded  💀 {len(dead)} dead\n",
    ]

    def _entry(app: dict) -> str:
        emoji     = _status_emoji(app["status"])
        date      = _fmt_date(app["applied_at"])
        company   = app.get("company") or "?"
        url       = app["url"]
        return f"{emoji} {company} — {date}\n   {url}"

    for label, items in [
        ("⏳ Waiting for response:", active),
        ("📨 Follow-up sent:",       followups),
        ("✅ Got a reply:",           responded),
        ("💀 No response (>14d):",   dead),
    ]:
        if items:
            lines.append(f"\n{label}")
            for app in items[:10]:
                lines.append(_entry(app))
            if len(items) > 10:
                lines.append(f"  … and {len(items) - 10} more")

    await update.effective_message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — command reference."""
    if not _is_authorised(update):
        return
    logger.info(f"/help from chat={update.effective_chat.id}")
    await update.effective_message.reply_text(
        "🤖 JobRadar Application Tracker\n\n"
        "/applied <url>    — log a job you applied to\n"
        "/responded <url>  — mark as replied (stops reminders)\n"
        "/applications     — show all tracked applications\n"
        "/status           — same as /applications\n"
        "/help             — this message\n\n"
        "⏱ Follow-ups drafted at 7 days, dead at 14 days.\n"
        "💡 DM this bot directly — always works, no @mention needed."
    )


# ── Startup: register commands + send online ping ──────────────────────────────

async def _on_startup(app: Application) -> None:
    """
    Called once after the bot connects. Registers the command list with
    Telegram (enables the '/' autocomplete menu in clients) and sends a
    ready ping to the configured group so the user knows the bot is live.
    """
    # Register commands — this is what makes the '/' menu and '@botname'
    # command suggestions appear in Telegram clients.
    commands = [
        BotCommand("applied",      "Log a job you applied to"),
        BotCommand("responded",    "Mark application as replied"),
        BotCommand("applications", "Show all tracked applications"),
        BotCommand("status",       "Show all tracked applications"),
        BotCommand("help",         "Command reference"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Bot commands registered with Telegram (autocomplete enabled)")
    except Exception as e:
        logger.warning(f"Failed to register bot commands: {e}")

    # Send a ready ping to the group
    if TELEGRAM_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id = TELEGRAM_CHAT_ID,
                text    = "🤖 Tracker bot online — /help to see commands.",
            )
            logger.info(f"Ready ping sent to chat {TELEGRAM_CHAT_ID}")
        except Exception as e:
            logger.warning(f"Ready ping failed: {e}")


# ── Main entrypoint ────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set — cannot start tracker bot")
        sys.exit(1)

    logger.info(f"Starting tracker bot… DB={_DB_PATH} CHAT_ID={TELEGRAM_CHAT_ID!r}")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_on_startup)
        .build()
    )

    # Accept commands from both supergroups (message) and channels (channel_post).
    # Without this, commands sent in a Telegram channel are silently ignored.
    _cmd = filters.UpdateType.MESSAGES | filters.UpdateType.CHANNEL_POSTS

    app.add_handler(CommandHandler("applied",      cmd_applied,      filters=_cmd))
    app.add_handler(CommandHandler("responded",    cmd_responded,    filters=_cmd))
    app.add_handler(CommandHandler("applications", cmd_applications, filters=_cmd))
    app.add_handler(CommandHandler("status",       cmd_applications, filters=_cmd))
    app.add_handler(CommandHandler("help",         cmd_help,         filters=_cmd))

    # Catch-all: log every update the bot receives so we can diagnose issues.
    async def _log_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat_id   = update.effective_chat.id if update.effective_chat else "?"
        chat_type = update.effective_chat.type if update.effective_chat else "?"
        text      = ""
        if update.message:
            text = update.message.text or ""
        elif update.channel_post:
            text = update.channel_post.text or ""
        logger.debug(f"Update received: chat={chat_id} type={chat_type} text={text!r}")

    app.add_handler(MessageHandler(filters.ALL, _log_update))

    logger.info("Polling for commands…")
    app.run_polling(
        drop_pending_updates = True,
        # Explicitly request both message and channel_post update types.
        allowed_updates      = ["message", "channel_post"],
    )


if __name__ == "__main__":
    main()
