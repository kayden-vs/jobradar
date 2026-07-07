"""
pipeline/gemini_throttle.py — Shared Gemini API rate-limit throttle.
Both hackernews.py (HN comment parsing) and scorer.py (job scoring) call
the same Gemini API key and count toward the same 15 RPM / 1,500 RPD quota.
This module holds a single process-level timestamp so both callers share one
inter-request interval and never collide on the rate limit.
Usage:
    from pipeline.gemini_throttle import gemini_throttle
    gemini_throttle()   # blocks until it is safe to call Gemini
Rate limit math (gemini-3.1-flash-lite free tier):
    REQ_INTERVAL = 4.5 s  →  ~13.3 req/min  →  safely under 15 RPM
    HN parse    : 12 calls/run  × 4.5 s  =  ~54 s
    AI scoring  : 150 calls/run × 4.5 s  =  ~11.25 min
    Combined    : 162 calls/run × 2 runs/day  =  324 RPD  (of ~1,500 RPD budget)
"""
import time
import logging
logger = logging.getLogger(__name__)
# Seconds between any two Gemini API calls across the entire process.
# 60 / 4.5 = 13.3 req/min — comfortably below the 15 RPM free-tier ceiling.
REQ_INTERVAL: float = 4.5
# Process-level last-call timestamp. A single float shared by all callers
# in the same Python process (hackernews parser + job scorer).
_last_call_ts: float = 0.0
def gemini_throttle() -> None:
    """
    Block until at least REQ_INTERVAL seconds have elapsed since the last
    Gemini call anywhere in the process, then record the current time.
    Thread-safety note: JobRadar runs single-threaded (no concurrent sources
    call Gemini at the same time), so a bare global is sufficient here.
    """
    global _last_call_ts
    elapsed = time.time() - _last_call_ts
    if elapsed < REQ_INTERVAL:
        wait = REQ_INTERVAL - elapsed
        logger.debug(f"Gemini throttle: sleeping {wait:.2f}s")
        time.sleep(wait)
    _last_call_ts = time.time()