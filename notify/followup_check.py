"""
followup_check.py — Check application tracker for overdue follow-ups and dead applications.

Called by run.sh at the end of every pipeline run.

Actions:
  - For each application 7+ days old with no followup sent yet:
      → Sends a follow-up email DRAFT to Telegram (one tap to copy & send)
  - For each application 14+ days old with no response:
      → Marks it dead and sends a quick dead-notification to Telegram

Run directly:
  python -m notify.followup_check
  python -m notify.followup_check --db-path data/profile.db
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger("followup_check")
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

IST = timezone(timedelta(hours=5, minutes=30))


def _days_since(iso_date: str) -> int:
    """Return the number of full days since an ISO datetime string."""
    try:
        applied = datetime.fromisoformat(iso_date)
        if applied.tzinfo is None:
            applied = applied.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - applied
        return delta.days
    except Exception:
        return 0


def _build_followup_draft(app: dict) -> str:
    """Build a ready-to-copy follow-up email draft for a given application."""
    company = app.get("company") or "the company"
    title   = app.get("title")   or "the position"
    days    = _days_since(app.get("applied_at", ""))
    url     = app.get("url", "")

    # Short readable date
    try:
        applied_dt = datetime.fromisoformat(app["applied_at"]).astimezone(IST)
        applied_str = applied_dt.strftime("%-d %b %Y")
    except Exception:
        applied_str = "a week ago"

    draft = (
        f"Subject: Following up on my application — {title}\n\n"
        f"Hi [Hiring Manager / Team],\n\n"
        f"I applied for the {title} role at {company} on {applied_str} and wanted to follow up "
        f"to express my continued interest in the opportunity.\n\n"
        f"I'm excited about what {company} is building and believe my background in backend "
        f"engineering (Go, TypeScript, REST APIs, PostgreSQL) aligns well with the role. "
        f"I'd love to hear if there are any updates on the hiring process.\n\n"
        f"Please let me know if there's anything else you need from my end.\n\n"
        f"Best regards,\n"
        f"Rohit Kumar Roy\n"
        f"[Your Email] | [LinkedIn] | [GitHub]"
    )
    return draft


async def _run_checks_async(db_path: str, chat_id: str):
    from storage.db import (
        init_db,
        get_applications_pending_followup,
        get_applications_pending_dead,
        mark_followup_sent,
        mark_application_dead,
    )

    init_db(db_path)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # ── 14-day dead check first (no point sending followup if marking dead) ────
    dead_apps = get_applications_pending_dead(db_path=db_path)
    for app in dead_apps:
        mark_application_dead(app["id"], db_path=db_path)
        company = app.get("company") or "unknown company"
        days    = _days_since(app.get("applied_at", ""))
        try:
            await bot.send_message(
                chat_id = chat_id,
                text    = (
                    f"💀 Marked dead — no response in {days} days\n"
                    f"🏢 {company}\n"
                    f"🔗 {app['url']}"
                ),
            )
            logger.info(f"Marked dead: {app['url']}")
        except Exception as e:
            logger.error(f"Failed to send dead notification: {e}")
        await asyncio.sleep(0.5)

    # ── 7-day follow-up drafts ────────────────────────────────────────────────
    followup_apps = get_applications_pending_followup(db_path=db_path)
    for app in followup_apps:
        draft   = _build_followup_draft(app)
        company = app.get("company") or "unknown company"
        days    = _days_since(app.get("applied_at", ""))

        intro = (
            f"📨 Follow-up due — {days} days, no response\n"
            f"🏢 {company}\n"
            f"🔗 {app['url']}\n\n"
            f"Here's your follow-up draft — copy, personalise, and send:\n"
            f"─────────────────────────\n"
        )
        full_msg = intro + draft

        try:
            await bot.send_message(
                chat_id = chat_id,
                text    = full_msg,
            )
            mark_followup_sent(app["id"], db_path=db_path)
            logger.info(f"Sent follow-up draft for: {app['url']}")
        except Exception as e:
            logger.error(f"Failed to send followup draft: {e}")
        await asyncio.sleep(0.5)

    total = len(dead_apps) + len(followup_apps)
    if total == 0:
        logger.info("Follow-up check: no overdue applications.")
    else:
        logger.info(
            f"Follow-up check complete: {len(dead_apps)} marked dead, "
            f"{len(followup_apps)} follow-up drafts sent."
        )


def run_followup_check(db_path: str | None = None, chat_id: str = ""):
    """Entry point for synchronous callers (e.g. run.sh via python -m)."""
    if db_path and os.path.exists(db_path):
        effective_db = db_path
    else:
        # Auto-discover: pick most recently modified .db in data/
        from pathlib import Path as _Path
        data_dir = _Path("data")
        if data_dir.exists():
            db_files = list(data_dir.glob("*.db"))
            if db_files:
                effective_db = str(max(db_files, key=lambda p: p.stat().st_mtime))
            else:
                effective_db = "data/profile.db"
        else:
            effective_db = os.getenv("TRACKER_DB_PATH", "data/profile.db")

    effective_chat = chat_id or TELEGRAM_CHAT_ID

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — cannot send follow-up notifications")
        return
    if not effective_chat:
        logger.error("TELEGRAM_CHAT_ID not set — cannot send follow-up notifications")
        return

    logger.info(f"Running follow-up check against DB: {effective_db}")
    asyncio.run(_run_checks_async(effective_db, effective_chat))



if __name__ == "__main__":
    # Allow --db-path override from CLI
    db_override = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--db-path" and i < len(sys.argv):
            db_override = sys.argv[i + 1]
        elif arg.startswith("--db-path="):
            db_override = arg.split("=", 1)[1]

    run_followup_check(db_path=db_override)
