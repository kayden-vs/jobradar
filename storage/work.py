"""Durable pending work and versioned evaluation cache, separate from shortlist."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from storage.db import _db, make_url_id, make_job_id

VERSION = 'audit-v1'


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(_db(path))
    conn.execute('CREATE TABLE IF NOT EXISTS work (kind TEXT, key TEXT, payload TEXT, updated TEXT, PRIMARY KEY(kind,key))')
    conn.execute('CREATE TABLE IF NOT EXISTS evaluations (key TEXT PRIMARY KEY, source TEXT, score INTEGER, evaluated_at TEXT, payload TEXT)')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def identity(job):
    return make_url_id(job) or make_job_id(job)


def put(kind, key, payload, path=None):
    with connect(path) as conn:
        conn.execute('INSERT INTO work VALUES (?,?,?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload', (kind, key, json.dumps({k:v for k,v in payload.items() if k != '_enrichment_attempted'} if isinstance(payload,dict) else payload), datetime.now(timezone.utc).isoformat()))


def remove(kind, key, path=None):
    with connect(path) as conn:
        conn.execute('DELETE FROM work WHERE kind=? AND key=?', (kind, key))


def pending(kind, path=None, days=45):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect(path) as conn:
        conn.execute('DELETE FROM work WHERE kind=? AND updated<?', (kind, cutoff))
        return [(k, json.loads(p)) for k,p in conn.execute('SELECT key,payload FROM work WHERE kind=? ORDER BY updated', (kind,))]


def cache_key(job, profile):
    fields = {k:job.get(k) for k in ('title','company','location','description','salary')}
    from pipeline.ai import MODEL
    return hashlib.sha256(json.dumps([VERSION, MODEL, identity(job), fields, profile], sort_keys=True).encode()).hexdigest()


def cached(job, profile, path=None):
    cutoff = (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    with connect(path) as conn:
        row = conn.execute('SELECT payload FROM evaluations WHERE key=? AND evaluated_at>=?', (cache_key(job,profile),cutoff)).fetchone()
        return json.loads(row[0]) if row else None


def record(job, profile, path=None):
    with connect(path) as conn:
        conn.execute('INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?)',
                     (cache_key(job,profile),job.get('source',''),job['score'],datetime.now(timezone.utc).isoformat(),json.dumps(job)))
