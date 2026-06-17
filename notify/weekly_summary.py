"""
notify/weekly_summary.py — Friday weekly radar digest

Sent once per week on Friday, piggybacking on the normal pipeline run.
Queries the last 7 days of the jobs DB and produces a single Telegram message
with 7 non-obvious insights about source quality, market demand, and pipeline health.

Guard: uses weekly_summaries table in DB so only the FIRST Friday run sends it.
"""

import re
import json
import logging
import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM ESCAPING
# ─────────────────────────────────────────────────────────────────────────────

_MD2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')

def _esc(text: str) -> str:
    """Escape a plain string for Telegram MarkdownV2."""
    return _MD2_SPECIAL.sub(r'\\\1', str(text))


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _since(days: int) -> str:
    """ISO datetime string for N days ago (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _normalize_source(source: str) -> str:
    """Collapse freshers_blogs/X → freshers_blogs, keep others as-is."""
    if source.startswith("freshers_blogs"):
        return "freshers_blogs"
    # ATS sub-types → group under "ats"
    if source in ("greenhouse", "greenhouse_eu", "lever", "ashby", "workable"):
        return "ats"
    return source


def _score_emoji(score: int) -> str:
    if score >= 9:  return "🔥🔥"
    if score >= 8:  return "🔥"
    if score >= 7:  return "⚡"
    return "💡"


def _bar(count: int, total: int, width: int = 8) -> str:
    """Simple ASCII fill bar: ████░░░░"""
    if total == 0:
        return "░" * width
    filled = round(count / total * width)
    return "█" * filled + "░" * (width - filled)


def _source_label(source: str) -> str:
    """Human-readable label for a normalized source name."""
    return {
        "ats":           "ATS (GH/Lever/Ashby)",
        "freshers_blogs": "Freshers Blogs",
        "serper":        "Serper",
        "naukri":        "Naukri",
        "internshala":   "Internshala",
        "yc":            "YC Jobs",
        "jobicy":        "Jobicy",
        "remoteok":      "RemoteOK",
        "instahyre":     "Instahyre",
    }.get(source, source.capitalize())


# Map profile.yaml source toggle keys → normalized DB source names
_PROFILE_TO_DB_SOURCE = {
    "ats":           "ats",
    "freshers_blogs": "freshers_blogs",
    "serper":        "serper",
    "naukri":        "naukri",
    "internshala":   "internshala",
    "yc":            "yc",
    "jobicy":        "jobicy",
    "remoteok":      "remoteok",
    "instahyre":     "instahyre",
    "hackernews":    "hackernews",
}


# ─────────────────────────────────────────────────────────────────────────────
# INSIGHT QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def _get_source_yield(conn: sqlite3.Connection, since: str) -> list[dict]:
    """
    Per-source: total scored jobs + how many hit score ≥6.
    Yield% = good / total. Sorted best yield first.
    """
    rows = conn.execute("""
        SELECT
            source,
            COUNT(*)                                        AS total,
            SUM(CASE WHEN score >= 6 THEN 1 ELSE 0 END)   AS good,
            ROUND(AVG(score), 1)                           AS avg_score
        FROM jobs
        WHERE seen_at >= ?
        GROUP BY source
        HAVING total > 0
        ORDER BY CAST(good AS FLOAT) / total DESC, total DESC
    """, (since,)).fetchall()

    result = []
    for source, total, good, avg_score in rows:
        norm = _normalize_source(source)
        # Merge rows that share the same normalized source (e.g. freshers_blogs/X)
        existing = next((r for r in result if r["source"] == norm), None)
        if existing:
            existing["total"] += total
            existing["good"]  += good
        else:
            result.append({"source": norm, "total": total, "good": good, "avg": avg_score})

    result.sort(key=lambda r: r["good"] / r["total"] if r["total"] else 0, reverse=True)
    return result


def _get_active_companies(conn: sqlite3.Connection, since: str) -> list[dict]:
    """
    Companies with ≥1 job scoring ≥7 this week. Sorted by best score then count.
    Cap to top 8. Normalises company name casing.
    """
    rows = conn.execute("""
        SELECT
            TRIM(company)        AS co,
            COUNT(*)             AS cnt,
            MAX(score)           AS best
        FROM jobs
        WHERE score >= 7 AND seen_at >= ?
        GROUP BY LOWER(TRIM(company))
        ORDER BY best DESC, cnt DESC
        LIMIT 8
    """, (since,)).fetchall()
    return [{"company": co.title(), "count": cnt, "best": best} for co, cnt, best in rows]


def _get_best_job(conn: sqlite3.Connection, since: str) -> dict | None:
    """Highest-scoring job in the past 7 days."""
    row = conn.execute("""
        SELECT title, company, score, url, highlights, source
        FROM jobs
        WHERE seen_at >= ?
        ORDER BY score DESC
        LIMIT 1
    """, (since,)).fetchone()
    if not row:
        return None
    title, company, score, url, highlights, source = row
    # Grab just the first highlight bullet if present
    first_highlight = ""
    if highlights:
        parts = [h.strip() for h in highlights.split(",") if h.strip()]
        if parts:
            first_highlight = parts[0]
    return {
        "title":     title,
        "company":   company.title(),
        "score":     score,
        "url":       url,
        "highlight": first_highlight,
        "source":    source,
    }


def _get_stack_demand(conn: sqlite3.Connection, since: str, skills: list[str]) -> list[dict]:
    """
    How many high-scoring jobs (≥6) mention each skill keyword in title or highlights.
    Returns top skills sorted by count descending. Max 6 skills shown.
    """
    rows = conn.execute("""
        SELECT title, highlights FROM jobs WHERE score >= 6 AND seen_at >= ?
    """, (since,)).fetchall()

    total_good = len(rows)
    if total_good == 0:
        return []

    counts: dict[str, int] = {}
    for title, highlights in rows:
        text = ((title or "") + " " + (highlights or "")).lower()
        for skill in skills:
            if re.search(r'\b' + re.escape(skill.lower()) + r'\b', text):
                counts[skill] = counts.get(skill, 0) + 1

    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:6]
    return [{"skill": sk, "count": cnt, "total": total_good} for sk, cnt in ranked]


def _get_urgent_trend(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    (this_week urgents, last_week urgents) from run_stats.
    Falls back to jobs table if run_stats is empty.
    """
    since_7  = _since(7)
    since_14 = _since(14)

    # Try run_stats first (precise per-run counts)
    this_week = conn.execute(
        "SELECT COALESCE(SUM(urgent_count),0) FROM run_stats WHERE run_at >= ?",
        (since_7,)
    ).fetchone()[0]
    last_week = conn.execute(
        "SELECT COALESCE(SUM(urgent_count),0) FROM run_stats WHERE run_at >= ? AND run_at < ?",
        (since_14, since_7)
    ).fetchone()[0]

    # Fallback: count from jobs table (score >= 8 as proxy for "urgent")
    if this_week == 0 and last_week == 0:
        this_week = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE score >= 8 AND seen_at >= ?",
            (since_7,)
        ).fetchone()[0]
        last_week = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE score >= 8 AND seen_at >= ? AND seen_at < ?",
            (since_14, since_7)
        ).fetchone()[0]

    return int(this_week), int(last_week)


def _get_location_split(conn: sqlite3.Connection, since: str) -> list[tuple[str, int]]:
    """Location breakdown for jobs scoring ≥7. Returns top 6."""
    rows = conn.execute("""
        SELECT
            LOWER(TRIM(location)) AS loc,
            COUNT(*)              AS cnt
        FROM jobs
        WHERE score >= 7 AND seen_at >= ? AND location != ''
        GROUP BY loc
        ORDER BY cnt DESC
        LIMIT 6
    """, (since,)).fetchall()
    # Normalise location labels
    normalised = []
    for loc, cnt in rows:
        label = loc.replace("work from home", "remote").replace("wfh", "remote")
        label = label.replace("bengaluru", "bangalore").replace("gurugram", "gurgaon")
        label = label.title()
        normalised.append((label, cnt))
    return normalised


def _get_silent_sources(
    conn: sqlite3.Connection,
    since: str,
    profile: dict,
) -> list[str]:
    """
    Sources enabled in profile that produced ZERO scored jobs this week.
    Uses the jobs table — if a source never made it past scoring with score≥5,
    it has nothing in jobs for this period.
    """
    sources_cfg = profile.get("sources", {})
    enabled = {k for k, v in sources_cfg.items() if v is True}

    # What DB sources appear this week?
    rows = conn.execute(
        "SELECT DISTINCT source FROM jobs WHERE seen_at >= ?", (since,)
    ).fetchall()
    active_db = {_normalize_source(r[0]) for r in rows}

    silent = []
    for profile_key in sorted(enabled):
        db_key = _PROFILE_TO_DB_SOURCE.get(profile_key)
        if db_key and db_key not in active_db:
            # Don't flag sources that rarely produce quality matches by design
            if profile_key not in ("hirist", "wellfound", "hackernews", "reddit", "cutshort"):
                silent.append(_source_label(db_key))
    return silent


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_weekly_summary(db_path: str, profile: dict) -> str:
    """
    Query the DB and build the full MarkdownV2 weekly summary string.
    Returns an empty string if there is insufficient data (<3 scored jobs this week).
    """
    since = _since(7)

    # Date window label  e.g. "11 Jun → 17 Jun"
    start_date = (datetime.now() - timedelta(days=7)).strftime("%-d %b")
    end_date   = datetime.now().strftime("%-d %b")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── Gather all data ───────────────────────────────────────────────────────
    best_job      = _get_best_job(conn, since)
    companies     = _get_active_companies(conn, since)
    source_yields = _get_source_yield(conn, since)
    this_week, last_week = _get_urgent_trend(conn)

    skills = (
        profile.get("candidate", {}).get("skills", {}).get("strong", []) +
        profile.get("candidate", {}).get("skills", {}).get("learning", [])
    )
    stack_demand  = _get_stack_demand(conn, since, skills)
    location_split = _get_location_split(conn, since)
    silent_sources = _get_silent_sources(conn, since, profile)

    # Total scored jobs this week
    total_this_week = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE seen_at >= ?", (since,)
    ).fetchone()[0]

    conn.close()

    # ── Guard: not enough data ────────────────────────────────────────────────
    if total_this_week < 3:
        return (
            f"📊 *WeeklyRadar — {_esc(start_date)} → {_esc(end_date)}*\n\n"
            f"_Quiet week — only {total_this_week} job\\(s\\) scored\\. "
            f"Pipeline is running; not many matching openings posted this week\\._"
        )

    # ── Build message blocks ──────────────────────────────────────────────────
    lines: list[str] = []

    # Header
    lines.append(f"📊 *WeeklyRadar — {_esc(start_date)} → {_esc(end_date)}*\n")

    # ── 1. Best job of the week ───────────────────────────────────────────────
    if best_job:
        emoji = _score_emoji(best_job["score"])
        lines.append("🏆 *Best this week*")
        lines.append(
            f"{emoji} {_esc(best_job['title'])}"
        )
        lines.append(f"     {_esc(best_job['company'])} · {best_job['score']}/10")
        if best_job["highlight"]:
            lines.append(f"     _{_esc(best_job['highlight'])}_")
        lines.append(f"     [Apply ↗]({best_job['url']})")
        lines.append("")

    # ── 2. Companies actively hiring for your stack ───────────────────────────
    if companies:
        lines.append("🎯 *Hiring for your stack this week*")
        for c in companies[:6]:
            emoji = _score_emoji(c["best"])
            lines.append(
                f"  {emoji} {_esc(c['company'])} "
                f"\\({c['count']} job{'s' if c['count']>1 else ''}\\, best {c['best']}/10\\)"
            )
        lines.append("")

    # ── 3. Source yield rate ──────────────────────────────────────────────────
    if source_yields:
        lines.append("📡 *Source quality* \\(score ≥6 / total scored\\)")
        for sy in source_yields[:6]:
            pct    = int(sy["good"] / sy["total"] * 100) if sy["total"] else 0
            bar    = _bar(sy["good"], sy["total"], width=6)
            label  = _esc(_source_label(sy["source"]))
            medal  = " 🏅" if pct >= 50 else (" ⚠️" if pct == 0 and sy["total"] >= 3 else "")
            lines.append(
                f"  `{bar}` {label} — {sy['good']}/{sy['total']} \\({pct}%\\){medal}"
            )
        lines.append("")

    # ── 4. Stack keyword demand ───────────────────────────────────────────────
    if stack_demand:
        lines.append("🛠 *Stack demand* \\(in high\\-scoring jobs\\)")
        parts = [
            f"{_esc(d['skill'])} {d['count']}/{d['total']}"
            for d in stack_demand
        ]
        lines.append("  " + " · ".join(parts))
        lines.append("")

    # ── 5. Location concentration ─────────────────────────────────────────────
    if location_split:
        lines.append("📍 *Where good jobs are* \\(score ≥7\\)")
        loc_parts = [f"{_esc(loc)} {cnt}" for loc, cnt in location_split]
        lines.append("  " + " · ".join(loc_parts))
        lines.append("")

    # ── 6. Urgents trend ─────────────────────────────────────────────────────
    if this_week > 0 or last_week > 0:
        if last_week == 0 and this_week > 0:
            trend = f"↑ from 0"
        elif this_week > last_week:
            trend = f"↑ from {last_week}"
        elif this_week < last_week:
            trend = f"↓ from {last_week}"
        else:
            trend = f"same as last week"
        lines.append(
            f"📈 *Urgents this week* — {this_week} \\({_esc(trend)}\\)"
        )
        lines.append("")

    # ── 7. Silent / no-match sources ─────────────────────────────────────────
    if silent_sources:
        lines.append(
            f"⚠️ *No matches this week* — {_esc(', '.join(silent_sources[:4]))}"
        )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SEND LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def is_friday() -> bool:
    """True if today is Friday (weekday 4) in IST."""
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(IST).weekday() == 4


async def _send_async(text: str, chat_id: str):
    import os
    from telegram import Bot
    from telegram.constants import ParseMode
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN", ""))
    effective_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    try:
        await bot.send_message(
            chat_id    = effective_chat_id,
            text       = text,
            parse_mode = ParseMode.MARKDOWN_V2,
            disable_web_page_preview = True,
        )
        logger.info("Weekly summary sent to Telegram.")
    except Exception as e:
        logger.error(f"Weekly summary Telegram send failed: {e}")
        # Plain-text fallback
        try:
            plain = re.sub(r'[_*\[\]()~`>#+\-=|{}.!\\]', '', text)
            await bot.send_message(chat_id=effective_chat_id, text=plain)
        except Exception as e2:
            logger.error(f"Weekly summary plain-text fallback failed: {e2}")


def send_weekly_summary_if_due(
    db_path: str,
    profile: dict,
    chat_id: str = "",
):
    """
    Main entry point called at the end of every pipeline run.

    Sends the weekly summary if:
      1. Today is Friday (IST), AND
      2. The summary hasn't been sent yet this ISO week.

    Safe to call on every run — the DB guard prevents duplicate sends.
    """
    from storage.db import was_weekly_summary_sent, mark_weekly_summary_sent

    if not is_friday():
        return

    if was_weekly_summary_sent(db_path):
        logger.debug("Weekly summary already sent this week — skipping.")
        return

    logger.info("Friday detected — building weekly summary...")
    try:
        message = build_weekly_summary(db_path, profile)
        if not message:
            logger.info("Weekly summary: no data to send.")
            return

        asyncio.run(_send_async(message, chat_id))
        mark_weekly_summary_sent(db_path)
    except Exception as e:
        logger.error(f"Weekly summary failed: {e}")
