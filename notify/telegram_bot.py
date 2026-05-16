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
    
    score    = job.get("score", 0)
    urgency  = job.get("urgency", "low")
    
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

    url = job.get('url', '')
    source = job.get('source', 'unknown')
    msg += f"🔗 [Apply Here]({url})\n"
    msg += f"_{_esc(source)}_"

    return msg


async def send_job_alert(job: dict):
    """Send a single job alert to Telegram."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    message = format_job_message(job)
    
    try:
        await bot.send_message(
            chat_id    = TELEGRAM_CHAT_ID,
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
                chat_id = TELEGRAM_CHAT_ID,
                text    = plain,
            )
        except Exception as e2:
            logger.error(f"Telegram plain-text fallback also failed: {e2}")


async def send_run_summary(total_raw: int, passed_filter: int, scored: int, urgent: int):
    """Send a short summary message after each run."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    msg = (
        f"🤖 *JobRadar Run Complete*\n"
        f"📥 Raw jobs fetched: {_esc(total_raw)}\n"
        f"🔍 Passed pre\\-filter: {_esc(passed_filter)}\n"
        f"🧠 AI scored: {_esc(scored)}\n"
        f"🔥 High\\-priority alerts: {_esc(urgent)}\n\n"
        f"_Check digest for score 6\\-7 jobs_"
    )

    try:
        await bot.send_message(
            chat_id    = TELEGRAM_CHAT_ID,
            text       = msg,
            parse_mode = ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"Telegram summary send failed: {e}")


def notify_urgent_jobs(urgent_jobs: list[dict]):
    """Send instant alerts for all urgent (score 8+) jobs."""
    async def _send_all():
        for job in urgent_jobs:
            await send_job_alert(job)
            await asyncio.sleep(1)  # 1s gap between messages
    
    asyncio.run(_send_all())
