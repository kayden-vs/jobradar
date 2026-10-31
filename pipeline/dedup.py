from storage.db import is_duplicate, make_job_id, make_url_id
import logging

logger = logging.getLogger(__name__)


def deduplicate(jobs: list[dict], db_path: str | None = None, profile: dict | None = None) -> list[dict]:
    """
    Removes:
    1. Jobs already seen in the database (persisted dedup)
    2. Duplicates within the current batch (in-memory dedup)
    """
    seen = {}
    new_jobs = []
    for job in jobs:
        job_id, url_id = make_job_id(job), make_url_id(job)
        existing = seen.get(('url',url_id)) if url_id else None
        if existing is None:
            existing = seen.get(('title',job_id))
            # Distinct requisition URLs remain distinct even with identical titles.
            if existing and url_id and make_url_id(existing) != url_id:
                existing = None
        if existing is not None:
            provenance = set(existing.get('sources', [existing.get('source','')]))
            provenance.add(job.get('source',''))
            if len(job.get('description','')) > len(existing.get('description','')):
                existing.update(job)
            existing['sources'] = sorted(provenance)
            seen[('title',job_id)] = existing
            if url_id:
                seen[('url',url_id)] = existing
            continue
        if profile is None and is_duplicate(job, db_path):
            continue
        seen[('title',job_id)] = job
        if url_id:
            seen[('url',url_id)] = job
        new_jobs.append(job)

    logger.info(f"Dedup: {len(jobs)} raw -> {len(new_jobs)} new")
    return new_jobs
