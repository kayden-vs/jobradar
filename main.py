import logging
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Ensure data/ directory exists before FileHandler tries to open the log file
os.makedirs("data", exist_ok=True)

# Force UTF-8 on Windows console to prevent cp1252 UnicodeEncodeError
stream_handler = logging.StreamHandler(stream=open(
    sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False
))

# Configure logging
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        stream_handler,
        logging.FileHandler("data/jobradar.log", mode="w", encoding="utf-8"),
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

    if source_enabled("yc"):
        logger.info("--- Fetching YC Jobs ---")
        raw_jobs.extend(fetch_yc())

    if source_enabled("serper"):
        logger.info("--- Fetching via Serper discovery ---")
        raw_jobs.extend(fetch_serper_jobs())

    if source_enabled("hackernews"):
        logger.info("--- Fetching HackerNews ---")
        raw_jobs.extend(fetch_hn_hiring())

    if source_enabled("reddit"):
        logger.info("--- Fetching Reddit ---")
        raw_jobs.extend(fetch_reddit())

    total_raw = len(raw_jobs)
    logger.info(f"Total raw jobs from all sources: {total_raw}")

    # --- DEDUPLICATION ---
    new_jobs = deduplicate(raw_jobs)

    # --- PRE-FILTER ---
    # Drops ~80% of remaining jobs with zero AI cost
    eligible_jobs = prefilter(new_jobs, profile)

    if not eligible_jobs:
        logger.info("No new eligible jobs after pre-filter. Done.")
        asyncio.run(send_run_summary(total_raw, 0, 0, 0))
        return

    # --- AI SCORING ---
    urgent_jobs, digest_jobs, low_jobs = score_all(eligible_jobs)

    # --- NOTIFICATIONS ---
    if urgent_jobs:
        logger.info(f"Sending {len(urgent_jobs)} urgent Telegram alerts")
        notify_urgent_jobs(urgent_jobs)

    # Send run summary
    asyncio.run(send_run_summary(
        total_raw     = total_raw,
        passed_filter = len(eligible_jobs),
        scored        = len(eligible_jobs),
        urgent        = len(urgent_jobs),
    ))

    logger.info("Pipeline complete.")
    logger.info(f"Summary: {total_raw} raw -> {len(new_jobs)} new -> {len(eligible_jobs)} eligible -> {len(urgent_jobs)} urgent")



if __name__ == "__main__":
    run()
