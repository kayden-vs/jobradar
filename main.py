import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# CLI ARGS — parsed before imports so logging can be per-user
#
# Usage:
#   python main.py                              # defaults to profiles/rohit.yaml
#   python main.py profiles/alice.yaml
#   python main.py profiles/rohit.yaml --dry-run
# ─────────────────────────────────────────────────────────────────
_profile_path = "profiles/rohit.yaml"
_dry_run = False
for _arg in sys.argv[1:]:
    if _arg == "--dry-run":
        _dry_run = True
    elif not _arg.startswith("--"):
        _profile_path = _arg

# Derive a short username from the profile filename for per-user log naming
# e.g. "profiles/rohit.yaml" → "rohit"
_username = os.path.splitext(os.path.basename(_profile_path))[0]

# Ensure data/ exists before FileHandler tries to open the log file
os.makedirs("data", exist_ok=True)

# Force UTF-8 on Windows console to prevent cp1252 UnicodeEncodeError
stream_handler = logging.StreamHandler(stream=open(
    sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False
))

# Per-user rotating log: data/<username>.log
# max 1 MB per file, keep last 3 files — prevents unbounded growth on AWS
file_handler = RotatingFileHandler(
    f"data/{_username}.log",
    maxBytes   = 1 * 1024 * 1024,   # 1 MB
    backupCount= 3,
    encoding   = "utf-8",
)

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[stream_handler, file_handler],
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
from pipeline.dedup import deduplicate
from pipeline.prefilter import prefilter, load_profile
from pipeline.scorer import score_all
from notify.telegram_bot import notify_urgent_jobs, send_session_divider


def _print_dry_run_summary(profile: dict, db_path: str, chat_id: str, profile_path: str):
    """Print a config summary and exit — no API calls made."""
    candidate = profile.get("candidate", {})
    sources   = profile.get("sources", {})
    enabled   = [s for s, v in sources.items() if isinstance(v, bool) and v]
    disabled  = [s for s, v in sources.items() if isinstance(v, bool) and not v]
    hr        = profile.get("hard_reject", {})

    print("\n" + "=" * 55)
    print("  DRY RUN - JobRadar profile check")
    print("=" * 55)
    print(f"  Profile file : {profile_path}")
    print(f"  Candidate    : {candidate.get('name', '?')}")
    print(f"  Education    : {candidate.get('education', {}).get('graduation', '?')}")
    print(f"  Database     : {db_path}")
    print(f"  Telegram ID  : {chat_id or 'NOT SET - notifications will fail'}")
    print(f"  Sources ON   : {', '.join(enabled) or 'none'}")
    print(f"  Sources OFF  : {', '.join(disabled) or 'none'}")
    print(f"  Max job age  : {hr.get('max_job_age_days', '?')} days")
    print(f"  AI cap       : {hr.get('max_ai_jobs_per_run', '?')} jobs/run")
    print(f"  ATS cap/co   : {hr.get('ats_per_company_cap', '?')} jobs/company")
    print(f"  Min stipend  : Rs.{candidate.get('salary', {}).get('min_stipend_inr', '?')}/mo")
    skills = candidate.get("skills", {})
    print(f"  Strong stack : {', '.join(skills.get('strong', []))}")
    print("=" * 55)
    print("  OK Config loaded and DB initialised - no API calls made.\n")


def run(profile_path: str, dry_run: bool = False):
    logger.info("=" * 50)
    logger.info(f"JobRadar pipeline starting — profile: {profile_path}")
    logger.info("=" * 50)

    # ── Setup: load profile, extract per-user routing info ───────────────────
    profile = load_profile(profile_path)
    db_path = profile.get("db_path", f"data/{_username}.db")
    chat_id = str(profile.get("telegram_chat_id", "")).strip()

    init_db(db_path)

    if not chat_id:
        logger.warning(
            "telegram_chat_id is not set in profile — Telegram notifications will fail"
        )

    # ── Dry-run: validate config and exit without hitting any APIs ────────────
    if dry_run:
        logger.info("Dry run requested — skipping all API calls")
        _print_dry_run_summary(profile, db_path, chat_id, profile_path)
        return

    companies = load_companies()

    # ── SOURCE LAYER ──────────────────────────────────────────────────────────
    # Control which sources run via profile.yaml `sources:` block.
    sources_cfg = profile.get("sources", {})

    def source_enabled(name: str) -> bool:
        """Returns True unless the source is explicitly set to false.
        Non-boolean config keys (like serper_max_calls) are ignored."""
        val = sources_cfg.get(name, True)
        if not isinstance(val, bool):
            return True     # not a toggle — treat as enabled
        if not val:
            logger.info(f"--- Skipping {name} (disabled in profile) ---")
        return val

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
        serper_max = sources_cfg.get("serper_max_calls", 20)
        raw_jobs.extend(fetch_serper_jobs(max_calls=serper_max))

    if source_enabled("hackernews"):
        logger.info("--- Fetching HackerNews ---")
        raw_jobs.extend(fetch_hn_hiring())

    if source_enabled("reddit"):
        logger.info("--- Fetching Reddit ---")
        raw_jobs.extend(fetch_reddit())

    if source_enabled("naukri"):
        logger.info("--- Fetching Naukri.com ---")
        raw_jobs.extend(fetch_naukri(profile))

    total_raw = len(raw_jobs)
    logger.info(f"Total raw jobs from all sources: {total_raw}")

    # ── DEDUPLICATION ─────────────────────────────────────────────────────────
    new_jobs = deduplicate(raw_jobs, db_path)

    # ── PRE-FILTER ────────────────────────────────────────────────────────────
    # Drops ~90% of remaining jobs with zero AI cost
    eligible_jobs = prefilter(new_jobs, profile)

    if not eligible_jobs:
        logger.info("No new eligible jobs after pre-filter. Done.")
        send_session_divider(total_raw=total_raw, passed=0, scored=0, urgent=0, chat_id=chat_id)
        return

    # ── GLOBAL SCORER CAP ─────────────────────────────────────────────────────
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

    # ── AI SCORING ────────────────────────────────────────────────────────────
    urgent_jobs, digest_jobs, low_jobs = score_all(eligible_jobs, profile, db_path)

    # ── NOTIFICATIONS ─────────────────────────────────────────────────────────
    if urgent_jobs:
        logger.info(f"Sending {len(urgent_jobs)} urgent Telegram alerts")
        notify_urgent_jobs(urgent_jobs, chat_id)

    # Session-end divider — always the last message, carries stats for the run.
    # Sent even when urgent=0 so there's always a visible session boundary.
    scored_count = len(urgent_jobs) + len(digest_jobs) + len(low_jobs)
    send_session_divider(
        total_raw = total_raw,
        passed    = len(eligible_jobs),
        scored    = scored_count,
        urgent    = len(urgent_jobs),
        chat_id   = chat_id,
    )

    logger.info("Pipeline complete.")
    logger.info(
        f"Summary: {total_raw} raw -> {len(new_jobs)} new -> "
        f"{len(eligible_jobs)} eligible -> {len(urgent_jobs)} urgent"
    )


if __name__ == "__main__":
    run(_profile_path, _dry_run)
