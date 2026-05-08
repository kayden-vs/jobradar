import logging
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Ensure data/ directory exists before FileHandler tries to open the log file
os.makedirs("data", exist_ok=True)

# Configure logging
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/jobradar.log"),
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
    # Each source is independent — a failure in one doesn't stop the rest
    raw_jobs = []
    
    logger.info("--- Fetching ATS endpoints ---")
    raw_jobs.extend(fetch_all_ats(companies))
    
    logger.info("--- Fetching Cutshort ---")
    raw_jobs.extend(fetch_cutshort())
    
    logger.info("--- Fetching Instahyre ---")
    raw_jobs.extend(fetch_instahyre())
    
    logger.info("--- Fetching Wellfound ---")
    raw_jobs.extend(fetch_wellfound())
    
    logger.info("--- Fetching via Serper discovery ---")
    raw_jobs.extend(fetch_serper_jobs())
    
    logger.info("--- Fetching HackerNews ---")
    raw_jobs.extend(fetch_hn_hiring())
    
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
    logger.info(f"Summary: {total_raw} raw → {len(new_jobs)} new → {len(eligible_jobs)} eligible → {len(urgent_jobs)} urgent")


if __name__ == "__main__":
    run()
