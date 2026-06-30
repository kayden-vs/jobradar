"""
Shared fixtures and helpers for all tests.
"""
import os
import sys
import tempfile
import sqlite3
import pytest

# ─────────────────────────────────────────────────────────────────
# Minimal profile used across multiple test modules.
# Mirrors the real profile.yaml structure without personal data.
# ─────────────────────────────────────────────────────────────────
MINIMAL_PROFILE = {
    "candidate": {
        "name": "Test User",
        "experience": {
            "years": 0,
            "max_required": 1,
            "acceptable_labels": ["fresher", "0-1 years", "intern", "entry level"],
        },
        "skills": {
            "strong": ["Go", "Golang", "TypeScript", "REST APIs", "PostgreSQL", "Redis", "Docker"],
            "learning": ["Kubernetes", "AWS"],
        },
        "location": {
            "base": "Kolkata, India",
            "acceptable": ["Remote", "Work from home", "Anywhere in India", "Bangalore", "Mumbai"],
            "hard_reject": ["US only", "UK only", "Europe only"],
        },
        "industries": {
            "high_priority": ["Fintech", "Crypto", "Payments"],
            "medium_priority": ["SaaS", "Developer tools", "Infrastructure"],
        },
        "salary": {
            "min_stipend_inr": 10000,
            "min_ctc_lpa": 4.0,
        },
        "education": {
            "graduation": "May 2027",
        },
    },
    "hard_reject": {
        "max_job_age_days": 45,
        "ats_per_company_cap": 25,
        "ats_prefilter_safety_cap": 100,
        "max_ai_jobs_per_run": 130,
        "experience_keywords": [
            "2+ years", "3+ years", "4+ years", "5+ years",
            "minimum 2 years", "at least 2 years", "senior engineer",
        ],
        "company_blacklist": [],
        "role_blacklist": [
            "Data Scientist", "Machine Learning Engineer", "ML Engineer",
            "Test Engineer", "Business Analyst", "Product Manager",
            "Senior Software", "Senior Backend", "Senior Engineer",
        ],
    },
    "sources": {},
}


def make_job(
    title="Backend Engineer Intern",
    company="TestCorp",
    location="Remote",
    description="We are hiring a backend engineer intern with Go and TypeScript skills.",
    url="https://example.com/jobs/1",
    source="greenhouse",
    salary="",
    posted_at="",
    **extra,
) -> dict:
    """Build a minimal valid job dict for tests."""
    job = {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "url": url,
        "source": source,
        "salary": salary,
        "posted_at": posted_at,
    }
    job.update(extra)
    return job


@pytest.fixture
def profile():
    """Return the shared minimal profile dict."""
    import copy
    return copy.deepcopy(MINIMAL_PROFILE)


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a fresh temporary SQLite DB path (initialised via init_db)."""
    from storage.db import init_db
    db_path = str(tmp_path / "test_jobradar.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def greenhouse_job():
    return make_job(source="greenhouse")


@pytest.fixture
def ats_job():
    return make_job(source="greenhouse")
