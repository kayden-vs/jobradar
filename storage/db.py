import sqlite3
import hashlib
import logging
import re
from datetime import datetime

logger  = logging.getLogger(__name__)
DB_PATH = "data/jobradar.db"


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
    s = _YEAR_RE.sub('', s)        # "Backend Engineer 2025" → "Backend Engineer "
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
    """Deterministic hash for deduplication.

    Primary key: normalised (title + company + location).
    Resilient to:
      - Company name variations: 'Razorpay' vs 'Razorpay Software Pvt Ltd'
      - Location aliases: 'Bengaluru' vs 'Bangalore'
      - Year noise in titles: 'SDE 2026' vs 'SDE'
      - Punctuation / whitespace differences
    """
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
    url = job.get('url', '').strip().rstrip('/')
    # Strip common tracking/source params
    url = re.sub(r'[?&](utm_[^&]+|ref=[^&]+|source=[^&]+)', '', url)
    return hashlib.md5(url.encode()).hexdigest() if url else ""


# ─────────────────────────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist. Safe to call on every run."""
    import os
    os.makedirs("data", exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
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
    # Add url_id column if upgrading from old schema (no-op if already exists)
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN url_id TEXT")
    except Exception:
        pass
    # Index for fast URL-based lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_url_id ON jobs(url_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            run_at       TEXT,
            total_raw    INTEGER,
            after_dedup  INTEGER,
            after_filter INTEGER,
            after_score  INTEGER,
            notified     INTEGER
        )
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# DEDUP QUERY
# ─────────────────────────────────────────────────────────────────

def is_duplicate(job: dict) -> bool:
    """Returns True if this job was already seen (by title-hash OR URL)."""
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(DB_PATH)
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

def save_job(job: dict, score: int = 0, reason: str = "",
             highlights: str = "", red_flags: str = "", notified: int = 0):
    """Persist a scored job to the database.

    Uses INSERT OR IGNORE — re-inserting the same job never resets
    the `notified` flag, preventing duplicate Telegram alerts.
    """
    job_id = make_job_id(job)
    url_id = make_url_id(job)
    conn   = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR IGNORE INTO jobs
        (id, url_id, title, company, location, description, url, source,
         salary, posted_at, seen_at, score, score_reason, highlights, red_flags, notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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


def get_jobs_by_score(min_score: int = 6) -> list[dict]:
    """Retrieve jobs above a score threshold for digest."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT title, company, location, url, salary, score, score_reason, highlights
        FROM jobs WHERE score >= ? AND notified = 0
        ORDER BY score DESC
    """, (min_score,)).fetchall()
    conn.close()
    return [
        dict(zip(["title", "company", "location", "url", "salary",
                  "score", "reason", "highlights"], row))
        for row in rows
    ]
