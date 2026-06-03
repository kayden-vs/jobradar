import os
import re
import asyncio
import logging
from telegram import Bot
from telegram.constants import ParseMode


_MD2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')

def _esc(text: str) -> str:
    """Escape a plain string for Telegram MarkdownV2."""
    return _MD2_SPECIAL.sub(r'\\\1', str(text))

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def format_job_message(job: dict) -> str:
    """Format a job into a rich Telegram message."""

    score   = job.get("score", 0)
    urgency = job.get("urgency", "low")

    # Score emoji
    if score >= 9:
        score_emoji = "🔥🔥"
    elif score >= 8:
        score_emoji = "🔥"
    elif score >= 7:
        score_emoji = "⚡"
    else:
        score_emoji = "💡"

    # Urgency label
    urgency_label = {"high": "Apply Today", "medium": "Apply Soon", "low": "Review"}.get(urgency, "Review")

    highlights = job.get("highlights", "")
    highlight_lines = ""
    if highlights:
        for h in highlights.split(", ")[:3]:
            highlight_lines += f"  ✅ {_esc(h)}\n"

    red_flags = job.get("red_flags", "")
    red_flag_lines = ""
    if red_flags and red_flags != "None":
        for rf in red_flags.split(", ")[:2]:
            red_flag_lines += f"  ⚠️ {_esc(rf)}\n"

    salary_line = f"\n💰 {_esc(job.get('salary', ''))}" if job.get("salary") else ""

    msg = (
        f"{score_emoji} *{_esc(job.get('title', 'N/A'))}*\n"
        f"🏢 {_esc(job.get('company', 'N/A'))}\n"
        f"📍 {_esc(job.get('location', 'N/A'))}{salary_line}\n"
        f"📊 Score: *{_esc(score)}/10* \u2014 {_esc(urgency_label)}\n"
        f"\n*Why it matches:*\n"
        f"{highlight_lines if highlight_lines else _esc('  — See job description')}\n"
    )

    if red_flag_lines:
        msg += f"*Watch out:*\n{red_flag_lines}\n"



    url    = job.get('url', '')
    source = job.get('source', 'unknown')
    msg += f"🔗 [Apply Here]({url})\n"
    msg += f"_{_esc(source)}_"

    return msg


async def send_job_alert(job: dict, chat_id: str = ""):
    """Send a single job alert to Telegram."""
    effective_chat_id = chat_id or TELEGRAM_CHAT_ID
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    message = format_job_message(job)

    try:
        await bot.send_message(
            chat_id    = effective_chat_id,
            text       = message,
            parse_mode = ParseMode.MARKDOWN_V2,
            disable_web_page_preview = True,
        )
        logger.info(f"Telegram: sent alert for {job['title']} @ {job['company']}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        # Fallback: send as plain text (no parse_mode) to guarantee delivery
        try:
            plain = (
                f"{job.get('title','?')} @ {job.get('company','?')} | "
                f"Score: {job.get('score','?')}/10\n{job.get('url','')}"
            )
            await bot.send_message(
                chat_id = effective_chat_id,
                text    = plain,
            )
        except Exception as e2:
            logger.error(f"Telegram plain-text fallback also failed: {e2}")


async def _send_run_summary_async(
    total_raw: int,
    passed_filter: int,
    scored: int,
    urgent: int,
    chat_id: str = "",
):
    """Async implementation of the run-summary Telegram message."""
    from datetime import datetime, timezone, timedelta

    effective_chat_id = chat_id or TELEGRAM_CHAT_ID
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)

    date_str = now.strftime("%-d %b")   # e.g. "29 May"
    time_str = now.strftime("%H:%M")    # e.g. "19:52"

    digest = scored - urgent  # non-urgent scored jobs to review
    urgent_line = (
        "✅ No urgent alerts today"
        if urgent == 0
        else f"🚨 Scroll up — {urgent} high\\-priority job\\(s\\) above\\!"
    )

    msg = (
        f"🔥 {_esc(urgent)} urgent · {_esc(digest)} to review — JobRadar {_esc(date_str)}\n"
        f"📥 {_esc(total_raw)} fetched → {_esc(passed_filter)} filtered → {_esc(scored)} scored\n"
        f"{urgent_line}\n"
        f"🕐 {_esc(time_str)} IST"
    )

    try:
        await bot.send_message(
            chat_id    = effective_chat_id,
            text       = msg,
            parse_mode = ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"Telegram summary send failed: {e}")


async def send_run_summary(total_raw: int, passed_filter: int, scored: int, urgent: int):
    """Legacy async wrapper — kept for backwards compatibility."""
    await _send_run_summary_async(total_raw, passed_filter, scored, urgent)


def notify_urgent_jobs(urgent_jobs: list[dict], chat_id: str = ""):
    """Send instant alerts for all urgent (score 8+) jobs."""
    async def _send_all():
        for job in urgent_jobs:
            await send_job_alert(job, chat_id)
            await asyncio.sleep(1)  # 1s gap between messages

    asyncio.run(_send_all())


def send_session_divider(
    total_raw: int,
    passed:    int,
    scored:    int,
    urgent:    int,
    chat_id:   str = "",
):
    """
    Synchronous wrapper around the async run-summary message.
    Called at the end of every pipeline run (even when scored=0).
    Named 'session_divider' because it visually separates runs in Telegram.
    """
    asyncio.run(_send_run_summary_async(
        total_raw     = total_raw,
        passed_filter = passed,
        scored        = scored,
        urgent        = urgent,
        chat_id       = chat_id,
    ))