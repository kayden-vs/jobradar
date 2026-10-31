import os
import re
import asyncio
import logging
import requests as _requests
from telegram import Bot
from telegram.constants import ParseMode


_MD2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')

def _esc(text: str) -> str:
    """Escape a plain string for Telegram MarkdownV2."""
    return _MD2_SPECIAL.sub(r'\\\1', str(text))

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Retry settings -- tuned for transient EC2/Telegram network blips
_MAX_RETRIES  = 3        # total attempts (1 original + 2 retries)
_RETRY_DELAYS = [5, 15]  # seconds to wait before attempt 2 and 3


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
        f"📊 Score: *{_esc(score)}/10* — {_esc(urgency_label)}\n"
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
    """Send a single job alert to Telegram.

    Retries up to _MAX_RETRIES times with backoff to survive transient
    network blips (e.g. brief Telegram API or EC2 routing outages).
    Falls back to plain text after all retries are exhausted.
    """
    effective_chat_id = chat_id or TELEGRAM_CHAT_ID
    bot     = Bot(token=TELEGRAM_BOT_TOKEN)
    message = format_job_message(job)

    last_err = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            await bot.send_message(
                chat_id    = effective_chat_id,
                text       = message,
                parse_mode = ParseMode.MARKDOWN_V2,
                disable_web_page_preview = True,
            )
            logger.info(
                "Telegram: sent alert for %s @ %s",
                job.get("title", "?"), job.get("company", "?"),
            )
            return True  # confirmed delivery
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "Telegram send attempt %d/%d failed: %s -- retrying in %ds",
                    attempt, _MAX_RETRIES, e, delay,
                )
                await asyncio.sleep(delay)

    logger.error("Telegram send failed after %d attempts: %s", _MAX_RETRIES, last_err)

    # Plain-text fallback -- covers both network timeouts and bad Markdown
    try:
        title   = job.get("title", "?")
        company = job.get("company", "?")
        score   = job.get("score", "?")
        url     = job.get("url", "")
        newline = "\n"
        plain   = f"{title} @ {company} | Score: {score}/10{newline}{url}"
        await bot.send_message(chat_id=effective_chat_id, text=plain)
        return True
    except Exception as e2:
        logger.error("Telegram plain-text fallback also failed (%s)", type(e2).__name__)
        return False


async def _send_run_summary_async(
    total_raw: int,
    passed_filter: int,
    scored: int,
    urgent: int,
    chat_id: str = "",
    digest: int = 0,
    failed: int = 0,
):
    """Async implementation of the run-summary Telegram message."""
    from datetime import datetime, timezone, timedelta

    effective_chat_id = chat_id or TELEGRAM_CHAT_ID
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)

    date_str = now.strftime("%-d %b")   # e.g. "29 May"
    time_str = now.strftime("%H:%M")    # e.g. "19:52"


    if urgent == 0:
        urgent_line = "✅ No urgent alerts today"
    else:
        urgent_line = _esc(f"🚨 Scroll up — {urgent} high-priority job(s) above!")

    msg = (
        f"🔥 {_esc(urgent)} urgent · {_esc(digest)} to review — JobRadar {_esc(date_str)}\n"
        f"📥 {_esc(total_raw)} fetched → {_esc(passed_filter)} filtered → {_esc(scored)} scored\n"
        f"⏳ {_esc(failed)} evaluations deferred\n"
        f"{urgent_line}\n"
        f"🕐 {_esc(time_str)} IST"
    )

    last_err = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            await bot.send_message(
                chat_id    = effective_chat_id,
                text       = msg,
                parse_mode = ParseMode.MARKDOWN_V2,
            )
            return True  # confirmed delivery
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "Telegram summary send attempt %d/%d failed: %s -- retrying in %ds",
                    attempt, _MAX_RETRIES, e, delay,
                )
                await asyncio.sleep(delay)

    logger.error("Telegram summary send failed after %d attempts: %s", _MAX_RETRIES, last_err)


async def send_run_summary(total_raw: int, passed_filter: int, scored: int, urgent: int):
    """Legacy async wrapper -- kept for backwards compatibility."""
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
    digest: int = 0,
    failed: int = 0,
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
        digest        = digest,
        failed        = failed,
    ))


def deliver_pending(db_path: str, chat_id: str = '', profile: dict | None = None) -> tuple[int, int]:
    """Send urgent alerts and compact digest chunks; acknowledge successful sends only."""
    from storage.db import pending_notifications, mark_job_notified
    async def send():
        from storage.db import make_url_id
        rows = list({make_url_id(j): j for j in pending_notifications(db_path)}.values())
        if profile is not None:
            from pipeline.prefilter import prefilter
            rows = prefilter(rows, profile)
        rows = [j for j in rows if j.get("url", "").startswith(("https://", "http://"))]
        urgent = [j for j in rows if j['score'] >= 8]
        digest = [j for j in rows if 6 <= j['score'] < 8]
        sent_urgent = sent_digest = 0
        for job in urgent:
            try:
                if await send_job_alert(job, chat_id):
                    mark_job_notified(job, 1, db_path)
                    sent_urgent += 1
            except Exception as exc:
                logger.error('Alert delivery deferred (%s)', type(exc).__name__)
            await asyncio.sleep(1)
        # Plain text avoids Markdown URL parsing failures; keep chunks well below 4096.
        chunks, chunk, size = [], [], 0
        for job in digest:
            line = f"{job['score']}/10 {job['title'][:140]} @ {job['company'][:100]}\n{job['url'][:1200]}\n"
            if size + len(line) > 3000:
                chunks.append(chunk)
                chunk, size = [], 0
            chunk.append((job,line))
            size += len(line)
        if chunk:
            chunks.append(chunk)
        for chunk in chunks:
            for attempt in range(_MAX_RETRIES):
                try:
                    async with Bot(token=TELEGRAM_BOT_TOKEN) as bot:
                        await bot.send_message(chat_id=chat_id or TELEGRAM_CHAT_ID,
                            text='JobRadar — worthwhile matches\n\n' + '\n'.join(line for _,line in chunk),
                            disable_web_page_preview=True)
                    for job,_ in chunk:
                        mark_job_notified(job, 2, db_path)
                        sent_digest += 1
                    break
                except Exception as exc:
                    logger.warning('Digest delivery failed (%s)', type(exc).__name__)
                    if attempt < _MAX_RETRIES-1:
                        await asyncio.sleep(_RETRY_DELAYS[attempt])
        return sent_urgent, sent_digest
    return asyncio.run(send())
