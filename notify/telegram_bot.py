import os
import asyncio
import logging
from telegram import Bot
from telegram.constants import ParseMode

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
            highlight_lines += f"  ✅ {h}\n"
    
    red_flags = job.get("red_flags", "")
    red_flag_lines = ""
    if red_flags and red_flags != "None":
        for rf in red_flags.split(", ")[:2]:
            red_flag_lines += f"  ⚠️ {rf}\n"
    
    salary_line = f"\n💰 {job.get('salary', '')}" if job.get("salary") else ""
    
    msg = f"""{score_emoji} *{job.get('title', 'N/A')}*
🏢 {job.get('company', 'N/A')}
📍 {job.get('location', 'N/A')}{salary_line}
📊 Score: *{score}/10* — {urgency_label}

*Why it matches:*
{highlight_lines if highlight_lines else "  — See job description"}"""
    
    if red_flag_lines:
        msg += f"\n*Watch out:*\n{red_flag_lines}"
    
    msg += f"\n🔗 [Apply Here]({job.get('url', '')})"
    msg += f"\n_Source: {job.get('source', 'unknown')}_"
    
    return msg


async def send_job_alert(job: dict):
    """Send a single job alert to Telegram."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    message = format_job_message(job)
    
    try:
        await bot.send_message(
            chat_id    = TELEGRAM_CHAT_ID,
            text       = message,
            parse_mode = ParseMode.MARKDOWN,
            disable_web_page_preview = True,
        )
        logger.info(f"Telegram: sent alert for {job['title']} @ {job['company']}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


async def send_run_summary(total_raw: int, passed_filter: int, scored: int, urgent: int):
    """Send a short summary message after each run."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    msg = f"""🤖 *JobRadar Run Complete*
📥 Raw jobs fetched: {total_raw}
🔍 Passed pre-filter: {passed_filter}
🧠 AI scored: {scored}
🔥 High-priority alerts: {urgent}

_Check digest for score 6–7 jobs_"""
    
    try:
        await bot.send_message(
            chat_id    = TELEGRAM_CHAT_ID,
            text       = msg,
            parse_mode = ParseMode.MARKDOWN,
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
