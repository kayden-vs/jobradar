import logging
import asyncio
import os
import sys
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

# Ensure data/ directory exists before FileHandler tries to open the log file
os.makedirs("data", exist_ok=True)

# Force UTF-8 on Windows console to prevent cp1252 UnicodeEncodeError
stream_handler = logging.StreamHandler(stream=open(
    sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False
))

# Rotating log: max 1 MB per file, keep last 3 files (jobradar.log, .1, .2)
# — prevents unbounded growth on AWS while retaining ~3 runs of history.
file_handler = RotatingFileHandler(
    "data/jobradar.log",
    maxBytes   = 1 * 1024 * 1024,   # 1 MB
    backupCount= 3,
    encoding   = "utf-8",
)

# Configure logging
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        stream_handler,
        file_handler,
    ]
)
logger = logging.getLogger("jobradar")

from storage.db import init_db
from sources.ats import fetch_all_ats, load_companies
from sources.cutshort import fetch_cutshort
from sources.instahyre import fetch_instahyre
from sources.wellfound import fetch_wellfound
from sources.serper import fetch_serper_jobs
from sources.hackernews import fetch_hn_hiring
from sources.reddit import fetch_reddit
from sources.internshala import fetch_internshala
from sources.yc import fetch_yc
from sources.freshers_blogs import fetch_freshers_blogs
from sources.naukri import fetch_naukri
from sources.hirist import fetch_hirist
from pipeline.dedup import deduplicate
from pipeline.prefilter import prefilter, load_profile
from pipeline.scorer import score_all
from notify.telegram_bot import notify_urgent_jobs, send_run_summary


def run():
    logger.info("=" * 50)
    logger.info("JobRadar pipeline starting")
    logger.info("=" * 50)

    # --- Setup ---
    init_db()
    profile   = load_profile()
    companies = load_companies()

    # --- SOURCE LAYER ---
    # Control which sources run via profile.yaml `sources:` block.
    sources_cfg = profile.get("sources", {})

    def source_enabled(name: str) -> bool:
        """Returns True unless the source is explicitly set to false."""
        enabled = sources_cfg.get(name, True)
        if not enabled:
            logger.info(f"--- Skipping {name} (disabled in profile.yaml) ---")
        return enabled

    raw_jobs = []

    if source_enabled("ats"):
        logger.info("--- Fetching ATS endpoints (Greenhouse US/EU, Lever, Ashby, Workable) ---")
        raw_jobs.extend(fetch_all_ats(companies))

    if source_enabled("cutshort"):
        logger.info("--- Fetching Cutshort ---")
        raw_jobs.extend(fetch_cutshort())

    if source_enabled("instahyre"):
        logger.info("--- Fetching Instahyre ---")
        raw_jobs.extend(fetch_instahyre())

    if source_enabled("wellfound"):
        logger.info("--- Fetching Wellfound ---")
        raw_jobs.extend(fetch_wellfound())

    if source_enabled("internshala"):
        logger.info("--- Fetching Internshala ---")
        raw_jobs.extend(fetch_internshala())

    if source_enabled("freshers_blogs"):
        logger.info("--- Fetching Indian Fresher Blogs (RSS + Cuvette) ---")
        raw_jobs.extend(fetch_freshers_blogs())

    if source_enabled("yc"):
        logger.info("--- Fetching YC Jobs ---")
        raw_jobs.extend(fetch_yc())

    if source_enabled("serper"):
        logger.info("--- Fetching via Serper discovery ---")
        raw_jobs.extend(fetch_serper_jobs(profile))

    if source_enabled("hackernews"):
        logger.info("--- Fetching HackerNews ---")
        raw_jobs.extend(fetch_hn_hiring())

    if source_enabled("reddit"):
        logger.info("--- Fetching Reddit ---")
        raw_jobs.extend(fetch_reddit())

    if source_enabled("naukri"):
        logger.info("--- Fetching Naukri.com ---")
        raw_jobs.extend(fetch_naukri(profile))

    if source_enabled("hirist"):
        logger.info("--- Fetching Hirist.tech ---")
        raw_jobs.extend(fetch_hirist(profile))

    total_raw = len(raw_jobs)
    logger.info(f"Total raw jobs from all sources: {total_raw}")

    # --- DEDUPLICATION ---
    new_jobs = deduplicate(raw_jobs)

    # --- PRE-FILTER ---
    # Drops ~90% of remaining jobs with zero AI cost
    eligible_jobs = prefilter(new_jobs, profile)

    if not eligible_jobs:
        logger.info("No new eligible jobs after pre-filter. Done.")
        asyncio.run(send_run_summary(total_raw=total_raw, passed_filter=0, scored=0, urgent=0))
        return

    # --- GLOBAL SCORER CAP ---
    # Last-resort budget guard: if pre-filter still lets through more than
    # max_ai_jobs_per_run jobs, keep only the freshest ones.
    max_ai_jobs = profile.get("hard_reject", {}).get("max_ai_jobs_per_run", 80)
    if len(eligible_jobs) > max_ai_jobs:
        # Sort by posted_at descending (newest first); missing dates go to end
        def _sort_key(j):
            pa = j.get("posted_at", "")
            return pa if pa else "0"
        eligible_jobs.sort(key=_sort_key, reverse=True)
        dropped = len(eligible_jobs) - max_ai_jobs
        eligible_jobs = eligible_jobs[:max_ai_jobs]
        logger.info(
            f"Scorer cap: dropped {dropped} oldest jobs — sending {max_ai_jobs} to AI"
        )

    # --- AI SCORING ---
    urgent_jobs, digest_jobs, low_jobs = score_all(eligible_jobs)

    # --- NOTIFICATIONS ---
    if urgent_jobs:
        logger.info(f"Sending {len(urgent_jobs)} urgent Telegram alerts")
        notify_urgent_jobs(urgent_jobs)

    # Session-end divider — always the last message, carries stats for the run.
    # Sent even when urgent=0 so there's always a visible session boundary.
    scored_count = len(urgent_jobs) + len(digest_jobs) + len(low_jobs)
    asyncio.run(send_run_summary(
        total_raw    = total_raw,
        passed_filter = len(eligible_jobs),
        scored       = scored_count,
        urgent       = len(urgent_jobs),
    ))

    logger.info("Pipeline complete.")
    logger.info(f"Summary: {total_raw} raw -> {len(new_jobs)} new -> {len(eligible_jobs)} eligible -> {len(urgent_jobs)} urgent")



if __name__ == "__main__":
    run()
