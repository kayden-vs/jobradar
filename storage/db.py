import sqlite3
import hashlib
import logging
import re
from datetime import datetime

logger          = logging.getLogger(__name__)
_DEFAULT_DB_PATH = "data/profile.db"


def _db(db_path: str | None) -> str:
    """Resolve the effective DB path: explicit arg > module default."""
    return db_path if db_path else _DEFAULT_DB_PATH


# ─────────────────────────────────────────────────────────────────
# NORMALISATION HELPERS
# ─────────────────────────────────────────────────────────────────

_COMPANY_NOISE = re.compile(
    r'\b(pvt\.?|private|limited|ltd\.?|inc\.?|llc|corp\.?|'
    r'technologies|technology|solutions|software|systems|services|'
    r'india|global|group|enterprises|co\.?)\b',
    re.IGNORECASE,
)
_CITY_ALIASES = {
    "bengaluru": "bangalore",
    "gurugram":  "gurgaon",
    "new delhi": "delhi",
}
_YEAR_RE = re.compile(r'\b20\d{2}\b')   # strip years like 2025, 2026
_PUNCT   = re.compile(r'[^a-z0-9 ]')   # keep only alphanumerics + spaces


def _normalize(text: str) -> str:
    """Normalise a string for dedup hashing — strips noise that shouldn't
    distinguish two listings of the same job."""
    s = text.lower().strip()
    # Preserve graduation/requisition years.
    s = _PUNCT.sub(' ', s)         # remove punctuation
    s = ' '.join(s.split())        # collapse whitespace
    return s


def _normalize_company(company: str) -> str:
    s = _normalize(company)
    s = _COMPANY_NOISE.sub('', s)  # strip "Private Limited", "Inc", etc.
    return ' '.join(s.split())


def _normalize_location(location: str) -> str:
    s = _normalize(location)
    return _CITY_ALIASES.get(s, s)


# ─────────────────────────────────────────────────────────────────
# JOB ID FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def make_job_id(job: dict) -> str:
    """Canonical URL identity; requisition or year-preserving metadata fallback.

    Similar titles are not sufficient to merge different application URLs.
    Legacy database rows remain accessible through their stored url_id.
    """
    if job.get('url'):
        return make_url_id(job)
    if job.get('requisition_id'):
        return hashlib.md5((job.get('company','').lower() + ':' + str(job['requisition_id'])).encode()).hexdigest()
    key = (
        _normalize(job.get('title', ''))
        + _normalize_company(job.get('company', ''))
        + _normalize_location(job.get('location', ''))
    )
    return hashlib.md5(key.encode()).hexdigest()


def make_url_id(job: dict) -> str:
    """Secondary hash based on canonical URL.
    Same URL from two sources = same job, regardless of title variation.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    url = job.get('url', '').strip()
    if not url:
        return ''
    parts = urlsplit(url)
    query = sorted((k,v) for k,v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith('utm_') and k.lower() not in {'ref','source'})
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), urlencode(query), ''))
    return hashlib.md5(url.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────────────────────────

def init_db(db_path: str | None = None):
    """Create tables if they don't exist. Safe to call on every run."""
    import os
    os.makedirs("data", exist_ok=True)

    path = _db(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,   -- MD5 of normalised title+company+location
            url_id       TEXT,               -- MD5 of canonical URL (secondary dedup key)
            title        TEXT,
            company      TEXT,
            location     TEXT,
            description  TEXT,
            url          TEXT,
            source       TEXT,
            salary       TEXT,
            posted_at    TEXT,
            seen_at      TEXT,
            score        INTEGER DEFAULT 0,
            score_reason TEXT,
            highlights   TEXT,
            red_flags    TEXT,
            notified     INTEGER DEFAULT 0   -- 0=no, 1=telegram, 2=digest
        )
    """)
    # Schema migrations — safe no-ops if column already exists
    for col_def in [
        "ALTER TABLE jobs ADD COLUMN url_id TEXT",
    ]:
        try:
            conn.execute(col_def)
        except Exception:
            pass
    # Refresh legacy URL hashes without changing primary keys or delivery history.
    for row_id,url,old_hash in conn.execute('SELECT id,url,url_id FROM jobs').fetchall():
        canonical = make_url_id({'url':url or ''})
        if canonical != old_hash:
            conn.execute('UPDATE jobs SET url_id=? WHERE id=?',(canonical,row_id))
    # Indexes for fast lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_url_id ON jobs(url_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at          TEXT    NOT NULL,
            raw_fetched     INTEGER DEFAULT 0,
            after_dedup     INTEGER DEFAULT 0,
            after_prefilter INTEGER DEFAULT 0,
            after_scoring   INTEGER DEFAULT 0,
            urgent_count    INTEGER DEFAULT 0,
            digest_count    INTEGER DEFAULT 0,
            source_breakdown TEXT DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_summaries (
            week_key TEXT PRIMARY KEY,  -- ISO year-week, e.g. '2026-W25'
            sent_at  TEXT NOT NULL      -- full ISO datetime of when it was sent
        )
    """)

    # ── Application tracker ───────────────────────────────────────────────────
    # Tracks jobs you applied to, sends follow-up drafts at day 7, marks
    # applications dead at day 14 with no response.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            url              TEXT NOT NULL UNIQUE,   -- job posting URL (canonical key)
            company          TEXT DEFAULT '',
            title            TEXT DEFAULT '',
            applied_at       TEXT NOT NULL,          -- ISO datetime of /applied command
            status           TEXT DEFAULT 'applied', -- applied | followup_sent | dead | responded
            followup_sent_at TEXT DEFAULT NULL,      -- ISO datetime followup draft was sent
            notes            TEXT DEFAULT ''
        )
    """)
    # Safe no-op migrations for applications table
    for col_def in [
        "ALTER TABLE applications ADD COLUMN notes TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(col_def)
        except Exception:
            pass

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# DEDUP QUERY
# ─────────────────────────────────────────────────────────────────

def is_duplicate(job: dict, db_path: str | None = None) -> bool:
    """Returns True if this job was already seen (by title-hash OR URL)."""
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(_db(db_path))
    # Primary: title-based hash
    row = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None and url_id:
        # Secondary: URL-based match (catches same job with different title)
        row = conn.execute("SELECT id FROM jobs WHERE url_id=?", (url_id,)).fetchone()
    conn.close()
    return row is not None


# ─────────────────────────────────────────────────────────────────
# WRITE / READ
# ─────────────────────────────────────────────────────────────────

def save_job(
    job: dict,
    score:       int  = 0,
    reason:      str  = "",
    highlights:  str  = "",
    red_flags:   str  = "",
    notified:    int  = 0,
    db_path:     str | None = None,
):
    """Persist a scored job to the database.

    Updates evaluation evidence while preserving delivery acknowledgements.
    A listing unseen for 45 days may be surfaced again.
    """
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(_db(db_path))
    existing = conn.execute('SELECT id FROM jobs WHERE url_id=?', (url_id,)).fetchone() if url_id else None
    if existing:
        job_id = existing[0]
    conn.execute("""
        INSERT INTO jobs
        (id, url_id, title, company, location, description, url, source,
         salary, posted_at, seen_at, score, score_reason, highlights, red_flags,
         notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          title=excluded.title, company=excluded.company, location=excluded.location,
          description=excluded.description, salary=excluded.salary, posted_at=excluded.posted_at,
          score=excluded.score, score_reason=excluded.score_reason,
          highlights=excluded.highlights, red_flags=excluded.red_flags,
          seen_at=excluded.seen_at,
          notified=CASE WHEN julianday(jobs.seen_at) < julianday('now','-45 days') THEN 0 ELSE jobs.notified END
    """, (
        job_id, url_id,
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("description", ""),
        job.get("url", ""),
        job.get("source", ""),
        job.get("salary", ""),
        job.get("posted_at", ""),
        datetime.now().isoformat(),
        score,
        reason,
        highlights,
        red_flags,
        notified,
    ))
    conn.commit()
    conn.close()


def mark_job_notified(job: dict, level: int = 1, db_path: str | None = None):
    """Mark a job as notified (e.g., sent to Telegram).
    level=1 means urgent/telegram, level=2 means digest.
    """
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn = sqlite3.connect(_db(db_path))
    # Update by id primarily, or url_id if available
    conn.execute(
        "UPDATE jobs SET notified=? WHERE id=?",
        (level, job_id),
    )
    if url_id:
        conn.execute(
            "UPDATE jobs SET notified=? WHERE url_id=? AND id!=?",
            (level, url_id, job_id),
        )
    conn.commit()
    conn.close()


def get_jobs_by_score(min_score: int = 6, db_path: str | None = None) -> list[dict]:
    """Retrieve jobs above a score threshold for digest."""
    conn = sqlite3.connect(_db(db_path))
    rows = conn.execute("""
        SELECT title, company, location, url, salary, score, score_reason,
               highlights
        FROM jobs WHERE score >= ? AND notified = 0
        ORDER BY score DESC
    """, (min_score,)).fetchall()
    conn.close()
    return [
        dict(zip(["title", "company", "location", "url", "salary",
                  "score", "reason", "highlights"], row))
        for row in rows
    ]


import json as _json


def save_run_stats(
    run_at:          str,
    raw_fetched:     int,
    after_dedup:     int,
    after_prefilter: int,
    urgent_count:    int,
    digest_count:    int,
    low_count:       int,
    source_breakdown: dict,
    db_path:         str | None = None,
):
    """
    Persist pipeline run statistics for weekly summary queries.

    source_breakdown: dict mapping source name to job count that reached the AI scorer.
      e.g. {"greenhouse": 74, "serper": 6, "naukri": 2}
    """
    path = _db(db_path)
    conn = sqlite3.connect(path)
    conn.execute("""
        INSERT INTO run_stats
        (run_at, raw_fetched, after_dedup, after_prefilter,
         after_scoring, urgent_count, digest_count, source_breakdown)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        run_at,
        raw_fetched,
        after_dedup,
        after_prefilter,
        urgent_count + digest_count + low_count,  # after_scoring
        urgent_count,
        digest_count,
        _json.dumps(source_breakdown),
    ))
    conn.commit()
    conn.close()


def was_weekly_summary_sent(db_path: str | None = None) -> bool:
    """
    Returns True if the weekly summary has already been sent for the current
    ISO week (year + week number). Prevents double-sending on multi-run Fridays.
    """
    from datetime import datetime
    week_key = datetime.now().strftime("%G-W%V")   # e.g. '2026-W25'
    conn = sqlite3.connect(_db(db_path))
    row  = conn.execute(
        "SELECT 1 FROM weekly_summaries WHERE week_key=?", (week_key,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_weekly_summary_sent(db_path: str | None = None):
    """Record that the weekly summary was sent for the current ISO week."""
    from datetime import datetime
    week_key = datetime.now().strftime("%G-W%V")
    sent_at  = datetime.now().isoformat()
    conn = sqlite3.connect(_db(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO weekly_summaries (week_key, sent_at) VALUES (?,?)",
        (week_key, sent_at),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# APPLICATION TRACKER
# ─────────────────────────────────────────────────────────────────

def log_application(
    url:     str,
    company: str = "",
    title:   str = "",
    db_path: str | None = None,
) -> bool:
    """Log a job application. Returns True if newly inserted, False if already tracked."""
    from datetime import datetime
    applied_at = datetime.now().isoformat()
    conn = sqlite3.connect(_db(db_path))
    try:
        conn.execute(
            "INSERT INTO applications (url, company, title, applied_at) VALUES (?,?,?,?)",
            (url.strip().rstrip("/"), company, title, applied_at),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # UNIQUE constraint on url — already tracked
        return False
    finally:
        conn.close()


def get_applications_pending_followup(db_path: str | None = None) -> list[dict]:
    """Return applications that are 7+ days old with no response and no followup sent yet."""
    from datetime import datetime, timedelta
    threshold = (datetime.now() - timedelta(days=7)).isoformat()
    conn = sqlite3.connect(_db(db_path))
    rows = conn.execute(
        """
        SELECT id, url, company, title, applied_at
        FROM applications
        WHERE status = 'applied'
          AND followup_sent_at IS NULL
          AND applied_at <= ?
        ORDER BY applied_at ASC
        """,
        (threshold,),
    ).fetchall()
    conn.close()
    return [
        dict(zip(["id", "url", "company", "title", "applied_at"], row))
        for row in rows
    ]


def get_applications_pending_dead(db_path: str | None = None) -> list[dict]:
    """Return applications that are 14+ days old and still active (applied or followup sent)."""
    from datetime import datetime, timedelta
    threshold = (datetime.now() - timedelta(days=14)).isoformat()
    conn = sqlite3.connect(_db(db_path))
    rows = conn.execute(
        """
        SELECT id, url, company, title, applied_at
        FROM applications
        WHERE status IN ('applied', 'followup_sent')
          AND applied_at <= ?
        ORDER BY applied_at ASC
        """,
        (threshold,),
    ).fetchall()
    conn.close()
    return [
        dict(zip(["id", "url", "company", "title", "applied_at"], row))
        for row in rows
    ]


def mark_followup_sent(app_id: int, db_path: str | None = None):
    """Record that the followup draft was sent for this application."""
    from datetime import datetime
    conn = sqlite3.connect(_db(db_path))
    conn.execute(
        "UPDATE applications SET status='followup_sent', followup_sent_at=? WHERE id=?",
        (datetime.now().isoformat(), app_id),
    )
    conn.commit()
    conn.close()


def mark_application_dead(app_id: int, db_path: str | None = None):
    """Mark an application as dead (no response after 14 days)."""
    conn = sqlite3.connect(_db(db_path))
    conn.execute(
        "UPDATE applications SET status='dead' WHERE id=?",
        (app_id,),
    )
    conn.commit()
    conn.close()


def mark_application_responded(url: str, db_path: str | None = None) -> bool:
    """Mark an application as responded. Returns True if it was found."""
    conn = sqlite3.connect(_db(db_path))
    cursor = conn.execute(
        "UPDATE applications SET status='responded' WHERE url=? AND status != 'responded'",
        (url.strip().rstrip("/"),),
    )
    conn.commit()
    found = cursor.rowcount > 0
    conn.close()
    return found


def get_all_applications(db_path: str | None = None) -> list[dict]:
    """Return all tracked applications ordered by applied_at desc."""
    conn = sqlite3.connect(_db(db_path))
    rows = conn.execute(
        """
        SELECT id, url, company, title, applied_at, status, followup_sent_at
        FROM applications
        ORDER BY applied_at DESC
        """
    ).fetchall()
    conn.close()
    return [
        dict(zip(
            ["id", "url", "company", "title", "applied_at", "status", "followup_sent_at"],
            row,
        ))
        for row in rows
    ]


def pending_notifications(db_path=None) -> list[dict]:
    """Unsent matches remain deliverable even when discovery deduplicates them."""
    from datetime import timedelta
    cutoff = (datetime.now()-timedelta(days=45)).isoformat()
    with sqlite3.connect(_db(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            'SELECT *, score_reason AS reason FROM jobs WHERE score>=6 AND notified=0 AND seen_at>=? ORDER BY score DESC', (cutoff,))]


def invalidate_job(job: dict, db_path: str | None = None) -> None:
    """Withdraw an old pending match when newer evidence makes it ineligible."""
    with sqlite3.connect(_db(db_path)) as conn:
        conn.execute('UPDATE jobs SET score=0, score_reason=? WHERE id=? OR (url_id!=\'\' AND url_id=?)',
                     ('New evidence failed eligibility/expiry checks', make_job_id(job), make_url_id(job)))
