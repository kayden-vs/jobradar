import sqlite3
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
DB_PATH = "data/jobradar.db"


def init_db():
    """Create tables if they don't exist."""
    import os
    os.makedirs("data", exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,   -- MD5 hash
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


def make_job_id(job: dict) -> str:
    """Deterministic hash for deduplication."""
    key = f"{job.get('title','').lower().strip()}" \
          f"{job.get('company','').lower().strip()}" \
          f"{job.get('location','').lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def is_duplicate(job: dict) -> bool:
    """Returns True if this job was already seen."""
    job_id = make_job_id(job)
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return row is not None


def save_job(job: dict, score: int = 0, reason: str = "",
             highlights: str = "", red_flags: str = "", notified: int = 0):
    """Save a job to the database."""
    job_id = make_job_id(job)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO jobs
        (id, title, company, location, description, url, source,
         salary, posted_at, seen_at, score, score_reason, highlights, red_flags, notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        job_id,
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
        dict(zip(["title","company","location","url","salary",
                  "score","reason","highlights"], row))
        for row in rows
    ]
