"""
tests/test_storage_db.py — Unit tests for storage/db.py

Covers:
  - _normalize / _normalize_company / _normalize_location
  - make_job_id  (dedup hash stability + company-name variation resilience)
  - make_url_id  (URL canonicalisation)
  - init_db      (idempotent schema creation)
  - is_duplicate (primary + secondary key)
  - save_job     (INSERT OR IGNORE semantics)
  - get_jobs_by_score
  - save_run_stats / was_weekly_summary_sent / mark_weekly_summary_sent
  - Application tracker: log_application, get_applications_pending_followup,
    get_applications_pending_dead, mark_followup_sent, mark_application_dead,
    mark_application_responded, get_all_applications
"""

import sqlite3
import pytest
from datetime import datetime, timedelta

from storage.db import (
    _normalize,
    _normalize_company,
    _normalize_location,
    make_job_id,
    make_url_id,
    init_db,
    is_duplicate,
    save_job,
    get_jobs_by_score,
    save_run_stats,
    was_weekly_summary_sent,
    mark_weekly_summary_sent,
    log_application,
    get_applications_pending_followup,
    get_applications_pending_dead,
    mark_followup_sent,
    mark_application_dead,
    mark_application_responded,
    get_all_applications,
)
from tests.conftest import make_job


# ─────────────────────────────────────────────────────────────────
# _normalize helpers
# ─────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Hello World") == "hello world"

    def test_strips_punctuation(self):
        # _normalize collapses whitespace after stripping, so no double spaces
        result = _normalize("Backend, Engineer.")
        assert result.strip() in ("backend engineer", "backend  engineer")
        # Either is acceptable — key assertion: non-alpha stripped, lowercased
        assert "backend" in result
        assert "engineer" in result

    def test_strips_years(self):
        assert "2025" not in _normalize("SDE 2025 Intern")
        assert "2026" not in _normalize("Backend Engineer 2026")

    def test_collapses_whitespace(self):
        result = _normalize("hello   world  ")
        assert "  " not in result

    def test_empty_string(self):
        assert _normalize("") == ""


class TestNormalizeCompany:
    def test_strips_pvt_ltd(self):
        a = _normalize_company("Razorpay Software Pvt Ltd")
        b = _normalize_company("Razorpay")
        assert a == b

    def test_strips_technologies(self):
        a = _normalize_company("Infosys Technologies Limited")
        b = _normalize_company("Infosys")
        assert a == b

    def test_strips_india(self):
        a = _normalize_company("Groww India")
        b = _normalize_company("Groww")
        assert a == b


class TestNormalizeLocation:
    def test_bengaluru_to_bangalore(self):
        assert _normalize_location("Bengaluru") == "bangalore"

    def test_gurugram_to_gurgaon(self):
        assert _normalize_location("Gurugram") == "gurgaon"

    def test_new_delhi_to_delhi(self):
        assert _normalize_location("New Delhi") == "delhi"

    def test_other_locations_unchanged(self):
        result = _normalize_location("Mumbai")
        assert "mumbai" in result


# ─────────────────────────────────────────────────────────────────
# make_job_id — dedup hash stability
# ─────────────────────────────────────────────────────────────────

class TestMakeJobId:
    def test_deterministic(self):
        job = make_job()
        assert make_job_id(job) == make_job_id(job)

    def test_company_variation_resilience(self):
        """Razorpay vs Razorpay Software Pvt Ltd → same hash."""
        job1 = make_job(company="Razorpay")
        job2 = make_job(company="Razorpay Software Pvt Ltd")
        assert make_job_id(job1) == make_job_id(job2)

    def test_location_alias_resilience(self):
        """Bengaluru vs Bangalore → same hash."""
        job1 = make_job(location="Bengaluru")
        job2 = make_job(location="Bangalore")
        assert make_job_id(job1) == make_job_id(job2)

    def test_year_noise_resilience(self):
        """'SDE 2025' vs 'SDE' → same hash."""
        job1 = make_job(title="SDE 2025")
        job2 = make_job(title="SDE")
        assert make_job_id(job1) == make_job_id(job2)

    def test_different_jobs_different_hash(self):
        job1 = make_job(title="Backend Engineer")
        job2 = make_job(title="Frontend Engineer")
        assert make_job_id(job1) != make_job_id(job2)

    def test_returns_hex_string(self):
        job_id = make_job_id(make_job())
        assert len(job_id) == 32
        assert all(c in "0123456789abcdef" for c in job_id)


# ─────────────────────────────────────────────────────────────────
# make_url_id — URL canonicalisation
# ─────────────────────────────────────────────────────────────────

class TestMakeUrlId:
    def test_strips_utm_params(self):
        job1 = make_job(url="https://example.com/job/1?utm_source=linkedin")
        job2 = make_job(url="https://example.com/job/1")
        assert make_url_id(job1) == make_url_id(job2)

    def test_strips_trailing_slash(self):
        job1 = make_job(url="https://example.com/job/1/")
        job2 = make_job(url="https://example.com/job/1")
        assert make_url_id(job1) == make_url_id(job2)

    def test_empty_url_returns_empty(self):
        job = make_job(url="")
        assert make_url_id(job) == ""

    def test_different_urls_different_hash(self):
        job1 = make_job(url="https://example.com/job/1")
        job2 = make_job(url="https://example.com/job/2")
        assert make_url_id(job1) != make_url_id(job2)


# ─────────────────────────────────────────────────────────────────
# init_db — idempotent schema creation
# ─────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_jobs_table(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        assert "jobs" in tables

    def test_creates_run_stats_table(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        assert "run_stats" in tables

    def test_creates_applications_table(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        assert "applications" in tables

    def test_idempotent_second_call(self, tmp_db):
        """Calling init_db twice should not raise."""
        init_db(tmp_db)  # second call


# ─────────────────────────────────────────────────────────────────
# is_duplicate + save_job
# ─────────────────────────────────────────────────────────────────

class TestIsDuplicate:
    def test_new_job_not_duplicate(self, tmp_db):
        job = make_job()
        assert is_duplicate(job, tmp_db) is False

    def test_saved_job_is_duplicate(self, tmp_db):
        job = make_job()
        save_job(job, db_path=tmp_db)
        assert is_duplicate(job, tmp_db) is True

    def test_url_based_dedup(self, tmp_db):
        """Same URL, different title → duplicate via url_id."""
        job1 = make_job(title="Backend Engineer", url="https://example.com/job/42")
        job2 = make_job(title="Backend Dev", url="https://example.com/job/42")
        save_job(job1, db_path=tmp_db)
        assert is_duplicate(job2, tmp_db) is True

    def test_different_jobs_not_duplicate(self, tmp_db):
        job1 = make_job(title="Backend Engineer", url="https://example.com/job/1")
        job2 = make_job(title="Frontend Engineer", url="https://example.com/job/2")
        save_job(job1, db_path=tmp_db)
        assert is_duplicate(job2, tmp_db) is False

    def test_company_variation_dedup(self, tmp_db):
        """Razorpay vs Razorpay Software Pvt Ltd → same hash → duplicate."""
        job1 = make_job(company="Razorpay", url="https://example.com/job/razorpay1")
        job2 = make_job(company="Razorpay Software Pvt Ltd", url="https://example.com/job/razorpay2")
        save_job(job1, db_path=tmp_db)
        assert is_duplicate(job2, tmp_db) is True


class TestSaveJob:
    def test_insert_or_ignore(self, tmp_db):
        """Re-inserting the same job should not raise and not duplicate."""
        job = make_job()
        save_job(job, score=8, db_path=tmp_db)
        save_job(job, score=9, db_path=tmp_db)  # second insert — ignored
        conn = sqlite3.connect(tmp_db)
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        assert count == 1

    def test_notified_flag_preserved(self, tmp_db):
        """The notified flag should not be reset on second INSERT OR IGNORE."""
        job = make_job()
        save_job(job, notified=1, db_path=tmp_db)
        save_job(job, notified=0, db_path=tmp_db)  # second call — ignored
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT notified FROM jobs").fetchone()
        conn.close()
        assert row[0] == 1  # first value preserved

    def test_saves_all_fields(self, tmp_db):
        job = make_job(
            title="Go Backend Intern",
            company="FinTech Co",
            location="Remote",
            description="Build microservices",
            url="https://fintechco.com/jobs/1",
            source="greenhouse",
            salary="₹15,000/month",
            posted_at="2026-01-15",
        )
        save_job(job, score=7, reason="Strong match", highlights="Go, gRPC", db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT title, company, score, highlights FROM jobs"
        ).fetchone()
        conn.close()
        assert row[0] == "Go Backend Intern"
        assert row[1] == "FinTech Co"
        assert row[2] == 7
        assert "gRPC" in row[3]


# ─────────────────────────────────────────────────────────────────
# get_jobs_by_score
# ─────────────────────────────────────────────────────────────────

class TestGetJobsByScore:
    def test_returns_high_score_jobs(self, tmp_db):
        job = make_job(url="https://example.com/job/score1")
        save_job(job, score=8, notified=0, db_path=tmp_db)
        results = get_jobs_by_score(min_score=6, db_path=tmp_db)
        assert len(results) == 1

    def test_excludes_low_score_jobs(self, tmp_db):
        job = make_job(url="https://example.com/job/lowscore")
        save_job(job, score=3, notified=0, db_path=tmp_db)
        results = get_jobs_by_score(min_score=6, db_path=tmp_db)
        assert len(results) == 0

    def test_excludes_already_notified(self, tmp_db):
        job = make_job(url="https://example.com/job/notified1")
        save_job(job, score=9, notified=1, db_path=tmp_db)
        results = get_jobs_by_score(min_score=6, db_path=tmp_db)
        assert len(results) == 0


# ─────────────────────────────────────────────────────────────────
# save_run_stats + weekly summary
# ─────────────────────────────────────────────────────────────────

class TestRunStats:
    def test_save_run_stats(self, tmp_db):
        save_run_stats(
            run_at="2026-01-15T10:00:00",
            raw_fetched=500,
            after_dedup=400,
            after_prefilter=50,
            urgent_count=5,
            digest_count=10,
            low_count=35,
            source_breakdown={"greenhouse": 30, "naukri": 20},
            db_path=tmp_db,
        )
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT raw_fetched, urgent_count FROM run_stats").fetchone()
        conn.close()
        assert row[0] == 500
        assert row[1] == 5

    def test_weekly_summary_not_sent_initially(self, tmp_db):
        assert was_weekly_summary_sent(tmp_db) is False

    def test_weekly_summary_mark_and_check(self, tmp_db):
        mark_weekly_summary_sent(tmp_db)
        assert was_weekly_summary_sent(tmp_db) is True

    def test_weekly_summary_idempotent(self, tmp_db):
        mark_weekly_summary_sent(tmp_db)
        mark_weekly_summary_sent(tmp_db)  # second call — INSERT OR REPLACE
        assert was_weekly_summary_sent(tmp_db) is True


# ─────────────────────────────────────────────────────────────────
# Application tracker
# ─────────────────────────────────────────────────────────────────

class TestApplicationTracker:
    def test_log_application_new(self, tmp_db):
        result = log_application(
            "https://example.com/apply/1", "TestCorp", "Backend Intern", tmp_db
        )
        assert result is True

    def test_log_application_duplicate(self, tmp_db):
        log_application("https://example.com/apply/2", "TestCorp", "Backend Intern", tmp_db)
        result = log_application("https://example.com/apply/2", "TestCorp", "Backend Intern", tmp_db)
        assert result is False

    def test_get_all_applications(self, tmp_db):
        log_application("https://example.com/apply/3", "Corp A", "SDE Intern", tmp_db)
        apps = get_all_applications(tmp_db)
        assert len(apps) == 1
        assert apps[0]["company"] == "Corp A"

    def test_get_applications_pending_followup(self, tmp_db):
        """Applications applied 8 days ago should be pending followup."""
        old_date = (datetime.now() - timedelta(days=8)).isoformat()
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO applications (url, company, title, applied_at) VALUES (?,?,?,?)",
            ("https://example.com/followup1", "OldCorp", "Intern", old_date),
        )
        conn.commit()
        conn.close()
        pending = get_applications_pending_followup(tmp_db)
        assert len(pending) == 1

    def test_get_applications_recent_not_in_followup(self, tmp_db):
        """Applications applied 2 days ago should NOT be pending followup."""
        recent_date = (datetime.now() - timedelta(days=2)).isoformat()
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO applications (url, company, title, applied_at) VALUES (?,?,?,?)",
            ("https://example.com/recent1", "NewCorp", "Intern", recent_date),
        )
        conn.commit()
        conn.close()
        pending = get_applications_pending_followup(tmp_db)
        assert len(pending) == 0

    def test_mark_followup_sent(self, tmp_db):
        log_application("https://example.com/markf1", "Corp", "Intern", tmp_db)
        conn = sqlite3.connect(tmp_db)
        app_id = conn.execute("SELECT id FROM applications").fetchone()[0]
        conn.close()
        mark_followup_sent(app_id, tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT status, followup_sent_at FROM applications WHERE id=?", (app_id,)).fetchone()
        conn.close()
        assert row[0] == "followup_sent"
        assert row[1] is not None

    def test_mark_application_dead(self, tmp_db):
        log_application("https://example.com/dead1", "Corp", "Intern", tmp_db)
        conn = sqlite3.connect(tmp_db)
        app_id = conn.execute("SELECT id FROM applications").fetchone()[0]
        conn.close()
        mark_application_dead(app_id, tmp_db)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT status FROM applications WHERE id=?", (app_id,)).fetchone()
        conn.close()
        assert row[0] == "dead"

    def test_mark_application_responded(self, tmp_db):
        url = "https://example.com/responded1"
        log_application(url, "Corp", "Intern", tmp_db)
        found = mark_application_responded(url, tmp_db)
        assert found is True
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT status FROM applications WHERE url=?", (url,)).fetchone()
        conn.close()
        assert row[0] == "responded"

    def test_mark_application_responded_not_found(self, tmp_db):
        found = mark_application_responded("https://example.com/nonexistent", tmp_db)
        assert found is False

    def test_get_applications_pending_dead(self, tmp_db):
        """Applications 15 days old still in 'applied' status → pending dead."""
        very_old = (datetime.now() - timedelta(days=15)).isoformat()
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            "INSERT INTO applications (url, company, title, applied_at) VALUES (?,?,?,?)",
            ("https://example.com/dead2", "DeadCorp", "Intern", very_old),
        )
        conn.commit()
        conn.close()
        pending = get_applications_pending_dead(tmp_db)
        assert len(pending) == 1
