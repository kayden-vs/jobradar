"""
tests/test_pipeline_dedup.py — Unit tests for pipeline/dedup.py

Covers:
  - In-memory deduplication (same batch)
  - Database deduplication (seen in previous runs)
  - Mixed batch with new and duplicate jobs
  - URL-based deduplication
  - Empty batch handling
"""

import pytest
from pipeline.dedup import deduplicate
from tests.conftest import make_job


class TestDeduplicateInMemory:
    """Deduplication within a single batch (no DB lookup needed)."""

    def test_empty_batch_returns_empty(self, tmp_db):
        result = deduplicate([], tmp_db)
        assert result == []

    def test_single_job_passes_through(self, tmp_db):
        jobs = [make_job()]
        result = deduplicate(jobs, tmp_db)
        assert len(result) == 1

    def test_duplicate_in_same_batch_removed(self, tmp_db):
        job = make_job(url="https://example.com/job/dup1")
        # Two identical jobs in the same batch
        jobs = [job, dict(job)]
        result = deduplicate(jobs, tmp_db)
        assert len(result) == 1

    def test_location_alias_dedup_in_batch(self, tmp_db):
        """Bengaluru vs Bangalore same title+company → same hash → deduped."""
        job1 = make_job(location="Bengaluru", url="https://example.com/job/ben1")
        job2 = make_job(location="Bangalore", url="https://example.com/job/ben2")
        result = deduplicate([job1, job2], tmp_db)
        assert len(result) == 1

    def test_company_variation_dedup_in_batch(self, tmp_db):
        """Razorpay vs Razorpay Software Pvt Ltd → same hash → deduped."""
        job1 = make_job(company="Razorpay", url="https://example.com/razorpay1")
        job2 = make_job(company="Razorpay Software Pvt Ltd", url="https://example.com/razorpay2")
        result = deduplicate([job1, job2], tmp_db)
        assert len(result) == 1

    def test_truly_different_jobs_both_kept(self, tmp_db):
        job1 = make_job(title="Backend Intern", company="Corp A", url="https://example.com/a")
        job2 = make_job(title="Frontend Intern", company="Corp B", url="https://example.com/b")
        result = deduplicate([job1, job2], tmp_db)
        assert len(result) == 2


class TestDeduplicateDatabase:
    """Deduplication against previously seen jobs in the DB."""

    def test_job_seen_in_db_is_filtered(self, tmp_db):
        from storage.db import save_job
        job = make_job(url="https://example.com/db_seen1")
        save_job(job, db_path=tmp_db)
        result = deduplicate([job], tmp_db)
        assert len(result) == 0

    def test_new_job_not_in_db_passes(self, tmp_db):
        job = make_job(url="https://example.com/new1")
        result = deduplicate([job], tmp_db)
        assert len(result) == 1

    def test_url_based_db_dedup(self, tmp_db):
        """Same URL different title → caught by url_id secondary key."""
        from storage.db import save_job
        job1 = make_job(title="Backend Eng", url="https://example.com/url_dedup1")
        job2 = make_job(title="Backend Dev", url="https://example.com/url_dedup1")
        save_job(job1, db_path=tmp_db)
        result = deduplicate([job2], tmp_db)
        assert len(result) == 0

    def test_mixed_batch_filters_seen_keeps_new(self, tmp_db):
        from storage.db import save_job
        old_job = make_job(title="Old Job", url="https://example.com/old1")
        new_job = make_job(title="New Job", url="https://example.com/new2")
        save_job(old_job, db_path=tmp_db)

        result = deduplicate([old_job, new_job], tmp_db)
        assert len(result) == 1
        assert result[0]["title"] == "New Job"

    def test_no_db_path_uses_default(self):
        """deduplicate with None db_path should not raise (falls back to default)."""
        import os
        os.makedirs("data", exist_ok=True)
        from storage.db import init_db
        init_db()
        job = make_job(url="https://example.com/no_db_path_test")
        # Should not raise; result depends on default db state
        result = deduplicate([job], None)
        assert isinstance(result, list)
