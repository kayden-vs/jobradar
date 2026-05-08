from storage.db import is_duplicate, make_job_id
import logging

logger = logging.getLogger(__name__)


def deduplicate(jobs: list[dict]) -> list[dict]:
    """
    Removes:
    1. Jobs already seen in the database (persisted dedup)
    2. Duplicates within the current batch (in-memory dedup)
    """
    seen_this_run = set()
    new_jobs = []
    
    for job in jobs:
        job_id = make_job_id(job)
        
        if job_id in seen_this_run:
            continue
        if is_duplicate(job):
            continue
        
        seen_this_run.add(job_id)
        new_jobs.append(job)
    
    logger.info(f"Dedup: {len(jobs)} raw -> {len(new_jobs)} new")
    return new_jobs
