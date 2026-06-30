"""
tests/test_pipeline_prefilter.py — Unit tests for pipeline/prefilter.py

Covers every individual check function plus the main prefilter() orchestrator:
  - _parse_posted_at        (ISO, relative, epoch, edge cases)
  - check_expiry_signals    (closure phrases + past deadlines)
  - check_experience        (keyword + regex patterns)
  - check_location          (description-based hard reject)
  - check_company_blacklist
  - check_role_blacklist
  - check_non_job_content   (govt exam noise)
  - check_candidate_post    (CANDIDATE_POST + looking-for-work signals)
  - check_has_meaningful_title
  - check_title_relevance   (ATS strict / Internshala / lenient modes)
  - check_no_description
  - check_ats_location      (ATS structured location field)
  - check_rss_tags          (freshers_blogs tag-based filter)
  - check_is_old_post
  - prefilter()             (end-to-end orchestration + per-company cap)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from pipeline.prefilter import (
    _parse_posted_at,
    check_expiry_signals,
    check_experience,
    check_location,
    check_company_blacklist,
    check_role_blacklist,
    check_non_job_content,
    check_candidate_post,
    check_has_meaningful_title,
    check_title_relevance,
    check_no_description,
    check_ats_location,
    check_rss_tags,
    check_is_old_post,
    prefilter,
)
from tests.conftest import make_job, MINIMAL_PROFILE


# ─────────────────────────────────────────────────────────────────
# _parse_posted_at
# ─────────────────────────────────────────────────────────────────

class TestParsePostedAt:
    def test_empty_returns_none(self):
        assert _parse_posted_at("") is None
        assert _parse_posted_at(None) is None

    def test_iso_datetime(self):
        result = _parse_posted_at("2026-05-01T10:00:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 5

    def test_relative_days_ago(self):
        result = _parse_posted_at("3 days ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 2 <= diff.days <= 4

    def test_relative_weeks_ago(self):
        result = _parse_posted_at("2 weeks ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 13 <= diff.days <= 15

    def test_relative_months_ago(self):
        result = _parse_posted_at("1 month ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 28 <= diff.days <= 32

    def test_relative_hours_ago(self):
        result = _parse_posted_at("5 hours ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert diff.total_seconds() < 6 * 3600

    def test_relative_an_hour_ago(self):
        result = _parse_posted_at("an hour ago")
        assert result is not None

    def test_posted_prefix_stripped(self):
        result = _parse_posted_at("Posted 3 days ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert 2 <= diff.days <= 4

    def test_about_prefix_stripped(self):
        result = _parse_posted_at("about 2 weeks ago")
        assert result is not None

    def test_unix_epoch_seconds(self):
        import time
        ts = int(time.time()) - 86400  # 1 day ago
        result = _parse_posted_at(str(ts))
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert diff.days <= 2

    def test_unix_epoch_milliseconds(self):
        import time
        ts_ms = int(time.time() * 1000) - 86400000  # 1 day ago in ms
        result = _parse_posted_at(str(ts_ms))
        assert result is not None

    def test_rfc_date(self):
        result = _parse_posted_at("Mon, 01 Jan 2026 10:00:00 +0000")
        assert result is not None
        assert result.year == 2026

    def test_unparseable_returns_none(self):
        result = _parse_posted_at("some random garbage text")
        assert result is None

    def test_years_ago(self):
        result = _parse_posted_at("2 years ago")
        assert result is not None
        now = datetime.now(timezone.utc)
        diff = now - result
        assert diff.days > 700


# ─────────────────────────────────────────────────────────────────
# check_expiry_signals
# ─────────────────────────────────────────────────────────────────

class TestCheckExpirySignals:
    def test_application_closed_phrase(self):
        # The regex matches "application(s)? ... closed" with optional "is/now" but NOT "are"
        job = make_job(description="Application is now closed.")
        should_reject, reason = check_expiry_signals(job)
        assert should_reject is True
        assert "closed" in reason.lower()

    def test_application_closed_simple(self):
        job = make_job(description="application closed for this role")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True

    def test_position_filled_phrase(self):
        job = make_job(title="Position has been filled")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True

    def test_no_longer_accepting(self):
        job = make_job(description="no longer accepting applications")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True

    def test_drive_over_phrase(self):
        job = make_job(description="drive is over")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True

    def test_we_are_no_longer_hiring(self):
        job = make_job(description="we are no longer hiring at this time")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True

    def test_past_deadline_in_description(self):
        past_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%B %d, %Y")
        job = make_job(description=f"Last Date to Apply: {past_date}")
        should_reject, reason = check_expiry_signals(job)
        assert should_reject is True
        assert "deadline" in reason.lower() or "passed" in reason.lower()

    def test_future_deadline_passes(self):
        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%B %d, %Y")
        job = make_job(description=f"Application deadline: {future_date}")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is False

    def test_normal_job_passes(self):
        job = make_job(description="We are actively hiring a backend engineer.")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is False

    def test_vacancy_closed(self):
        job = make_job(description="vacancy closed")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True

    def test_hiring_is_closed(self):
        job = make_job(description="hiring is closed for this position")
        should_reject, _ = check_expiry_signals(job)
        assert should_reject is True


# ─────────────────────────────────────────────────────────────────
# check_experience
# ─────────────────────────────────────────────────────────────────

class TestCheckExperience:
    def test_experience_keyword_reject(self, profile):
        job = make_job(description="Minimum 2 years experience required.")
        should_reject, reason = check_experience(job["description"], job["title"], profile)
        assert should_reject is True
        assert "experience" in reason.lower() or "years" in reason.lower()

    def test_2plus_years_keyword(self, profile):
        job = make_job(description="You need 2+ years of backend experience.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is True

    def test_3_years_regex(self, profile):
        job = make_job(description="3 years of experience in Go.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is True

    def test_fresher_job_passes(self, profile):
        job = make_job(description="Fresher candidates welcome. 0-1 years experience.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is False

    def test_no_experience_mention_passes(self, profile):
        job = make_job(description="Build REST APIs using Go and TypeScript.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is False

    def test_10plus_years_regex(self, profile):
        # The regex pattern requires 'experience' or 'exp' word
        job = make_job(description="10+ years experience required.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is True

    def test_senior_engineer_keyword(self, profile):
        job = make_job(description="We are looking for a senior engineer.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is True

    def test_1_year_passes(self, profile):
        """1 year of experience is allowed (max_required=1)."""
        job = make_job(description="1 year of experience preferred.")
        should_reject, _ = check_experience(job["description"], job["title"], profile)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_location
# ─────────────────────────────────────────────────────────────────

class TestCheckLocation:
    def test_us_only_rejected(self, profile):
        job = make_job(description="This role is US only.")
        should_reject, _ = check_location(job["description"], job["title"], profile)
        assert should_reject is True

    def test_uk_only_rejected(self, profile):
        job = make_job(description="Applicants must be in UK only.")
        should_reject, _ = check_location(job["description"], job["title"], profile)
        assert should_reject is True

    def test_india_remote_passes(self, profile):
        job = make_job(description="Remote, India-based candidates preferred.")
        should_reject, _ = check_location(job["description"], job["title"], profile)
        assert should_reject is False

    def test_no_location_mention_passes(self, profile):
        job = make_job(description="Build distributed systems using Go.")
        should_reject, _ = check_location(job["description"], job["title"], profile)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_company_blacklist
# ─────────────────────────────────────────────────────────────────

class TestCheckCompanyBlacklist:
    def test_blacklisted_company_rejected(self):
        profile = {"hard_reject": {"company_blacklist": ["BlockedCorp"]}}
        should_reject, reason = check_company_blacklist("BlockedCorp", profile)
        assert should_reject is True
        assert "blockedcorp" in reason.lower()

    def test_case_insensitive(self):
        profile = {"hard_reject": {"company_blacklist": ["blockedcorp"]}}
        should_reject, _ = check_company_blacklist("BlockedCorp", profile)
        assert should_reject is True

    def test_non_blacklisted_passes(self):
        profile = {"hard_reject": {"company_blacklist": ["BlockedCorp"]}}
        should_reject, _ = check_company_blacklist("AllowedCorp", profile)
        assert should_reject is False

    def test_empty_blacklist_passes(self):
        profile = {"hard_reject": {"company_blacklist": []}}
        should_reject, _ = check_company_blacklist("AnyCorp", profile)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_role_blacklist
# ─────────────────────────────────────────────────────────────────

class TestCheckRoleBlacklist:
    def test_data_scientist_rejected(self, profile):
        should_reject, _ = check_role_blacklist("Data Scientist", profile)
        assert should_reject is True

    def test_ml_engineer_rejected(self, profile):
        should_reject, _ = check_role_blacklist("Machine Learning Engineer", profile)
        assert should_reject is True

    def test_backend_intern_passes(self, profile):
        should_reject, _ = check_role_blacklist("Backend Engineering Intern", profile)
        assert should_reject is False

    def test_case_insensitive(self, profile):
        should_reject, _ = check_role_blacklist("data scientist", profile)
        assert should_reject is True


# ─────────────────────────────────────────────────────────────────
# check_non_job_content
# ─────────────────────────────────────────────────────────────────

class TestCheckNonJobContent:
    def test_question_paper_rejected(self):
        job = make_job(title="SSC CGL Question Papers 2025")
        should_reject, _ = check_non_job_content(job)
        assert should_reject is True

    def test_admit_card_rejected(self):
        job = make_job(title="IBPS Admit Card 2026 Download")
        should_reject, _ = check_non_job_content(job)
        assert should_reject is True

    def test_answer_key_rejected(self):
        job = make_job(title="SSC Answer Key 2025")
        should_reject, _ = check_non_job_content(job)
        assert should_reject is True

    def test_police_constable_rejected(self):
        job = make_job(title="Police Constable Recruitment 2025")
        should_reject, _ = check_non_job_content(job)
        assert should_reject is True

    def test_normal_job_title_passes(self):
        job = make_job(title="Backend Engineer Intern at Razorpay")
        should_reject, _ = check_non_job_content(job)
        assert should_reject is False

    def test_ssc_prefix_rejected(self):
        job = make_job(title="SSC CHSL Notification 2025")
        should_reject, _ = check_non_job_content(job)
        assert should_reject is True


# ─────────────────────────────────────────────────────────────────
# check_candidate_post
# ─────────────────────────────────────────────────────────────────

class TestCheckCandidatePost:
    def test_candidate_post_company_rejected(self):
        job = make_job(company="CANDIDATE_POST")
        should_reject, _ = check_candidate_post(job)
        assert should_reject is True

    def test_for_hire_title_rejected(self):
        job = make_job(title="[For Hire] Backend Developer")
        should_reject, _ = check_candidate_post(job)
        assert should_reject is True

    def test_seeking_work_rejected(self):
        job = make_job(title="Seeking Backend Engineering Role")
        should_reject, _ = check_candidate_post(job)
        assert should_reject is True

    def test_open_to_work_rejected(self):
        job = make_job(title="Open to Work — Backend Developer")
        should_reject, _ = check_candidate_post(job)
        assert should_reject is True

    def test_hiring_post_passes(self):
        job = make_job(title="[Hiring] Backend Engineer at Razorpay")
        should_reject, _ = check_candidate_post(job)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_has_meaningful_title
# ─────────────────────────────────────────────────────────────────

class TestCheckHasMeaningfulTitle:
    def test_empty_title_rejected(self):
        job = make_job(title="")
        should_reject, _ = check_has_meaningful_title(job)
        assert should_reject is True

    def test_too_short_title_rejected(self):
        job = make_job(title="Go")
        should_reject, _ = check_has_meaningful_title(job)
        assert should_reject is True

    def test_normal_title_passes(self):
        job = make_job(title="Backend Engineer Intern")
        should_reject, _ = check_has_meaningful_title(job)
        assert should_reject is False

    def test_three_char_title_passes(self):
        job = make_job(title="SDE")
        should_reject, _ = check_has_meaningful_title(job)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_title_relevance
# ─────────────────────────────────────────────────────────────────

class TestCheckTitleRelevance:
    # ── ATS strict mode ───────────────────────────────────────────
    def test_ats_backend_engineer_passes(self):
        should_reject, _ = check_title_relevance("Backend Engineer Intern", "greenhouse")
        assert should_reject is False

    def test_ats_sde_passes(self):
        should_reject, _ = check_title_relevance("SDE Intern", "lever")
        assert should_reject is False

    def test_ats_software_passes(self):
        should_reject, _ = check_title_relevance("Software Engineer", "ashby")
        assert should_reject is False

    def test_ats_sales_rejected(self):
        should_reject, _ = check_title_relevance("Sales Manager", "greenhouse")
        assert should_reject is True

    def test_ats_hr_rejected(self):
        should_reject, _ = check_title_relevance("HR Business Partner", "workable")
        assert should_reject is True

    def test_ats_no_tech_signal_rejected(self):
        should_reject, _ = check_title_relevance("Office Coordinator", "greenhouse")
        assert should_reject is True

    # ── Internshala strict mode ───────────────────────────────────
    def test_internshala_backend_development_passes(self):
        should_reject, _ = check_title_relevance("Backend Development", "internshala")
        assert should_reject is False

    def test_internshala_golang_passes(self):
        should_reject, _ = check_title_relevance("Golang Internship", "internshala")
        assert should_reject is False

    def test_internshala_business_dev_rejected(self):
        """'Business Development' has 'dev' but the reject regex should catch it."""
        should_reject, _ = check_title_relevance("Business Development (Sales)", "internshala")
        assert should_reject is True

    def test_internshala_php_rejected(self):
        should_reject, _ = check_title_relevance("PHP Developer Intern", "internshala")
        assert should_reject is True

    def test_internshala_no_tech_rejected(self):
        should_reject, _ = check_title_relevance("Marketing Intern", "internshala")
        assert should_reject is True

    # ── Non-ATS lenient mode ──────────────────────────────────────
    def test_lenient_backend_passes(self):
        should_reject, _ = check_title_relevance("Backend Developer", "reddit")
        assert should_reject is False

    def test_lenient_sales_rejected(self):
        should_reject, _ = check_title_relevance("Sales Executive", "hackernews")
        assert should_reject is True

    def test_lenient_golang_passes(self):
        should_reject, _ = check_title_relevance("Golang Developer Fresher", "freshers_blogs")
        assert should_reject is False

    def test_aggregate_listing_rejected(self):
        should_reject, _ = check_title_relevance("3,500 Backend Engineer Jobs")
        assert should_reject is True

    def test_yc_nav_page_rejected(self):
        should_reject, _ = check_title_relevance("Jobs in Bangalore")
        assert should_reject is True

    def test_yc_remote_nav_rejected(self):
        should_reject, _ = check_title_relevance("Remote software engineer jobs")
        assert should_reject is True


# ─────────────────────────────────────────────────────────────────
# check_no_description
# ─────────────────────────────────────────────────────────────────

class TestCheckNoDescription:
    def test_no_desc_short_title_rejected(self):
        job = {"description": "", "title": "Go"}
        should_reject, _ = check_no_description(job)
        assert should_reject is True

    def test_no_desc_but_good_title_passes(self):
        job = {"description": "", "title": "Backend Engineer Intern"}
        should_reject, _ = check_no_description(job)
        assert should_reject is False

    def test_has_description_passes(self):
        job = {"description": "We are hiring a backend engineer.", "title": "Go"}
        should_reject, _ = check_no_description(job)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_ats_location
# ─────────────────────────────────────────────────────────────────

class TestCheckAtsLocation:
    def test_us_location_rejected(self):
        job = make_job(location="San Francisco, CA", source="greenhouse")
        should_reject, _ = check_ats_location(job)
        assert should_reject is True

    def test_india_location_passes(self):
        job = make_job(location="Bangalore, India", source="greenhouse")
        should_reject, _ = check_ats_location(job)
        assert should_reject is False

    def test_remote_passes(self):
        job = make_job(location="Remote", source="lever")
        should_reject, _ = check_ats_location(job)
        assert should_reject is False

    def test_not_specified_passes(self):
        job = make_job(location="Not specified", source="ashby")
        should_reject, _ = check_ats_location(job)
        assert should_reject is False

    def test_non_ats_source_skipped(self):
        """Non-ATS jobs are never rejected by this check (it only applies to ATS)."""
        job = make_job(location="New York, NY", source="reddit")
        should_reject, _ = check_ats_location(job)
        assert should_reject is False

    def test_uk_london_rejected(self):
        job = make_job(location="London, UK", source="workable")
        should_reject, _ = check_ats_location(job)
        assert should_reject is True

    def test_europe_rejected(self):
        job = make_job(location="Berlin, Germany", source="greenhouse")
        should_reject, _ = check_ats_location(job)
        assert should_reject is True

    def test_new_york_rejected(self):
        job = make_job(location="New York, NY", source="greenhouse")
        should_reject, _ = check_ats_location(job)
        assert should_reject is True

    def test_mumbai_passes(self):
        job = make_job(location="Mumbai", source="lever")
        should_reject, _ = check_ats_location(job)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_rss_tags
# ─────────────────────────────────────────────────────────────────

class TestCheckRssTags:
    def test_non_blog_source_skipped(self):
        job = make_job(source="greenhouse", experience_tags=["5-7 Years"])
        should_reject, _ = check_rss_tags(job)
        assert should_reject is False

    def test_experienced_only_tag_rejected(self):
        job = make_job(
            source="freshers_blogs",
            experience_tags=["5-7 Years Experience"],
        )
        should_reject, _ = check_rss_tags(job)
        assert should_reject is True

    def test_fresher_tag_passes(self):
        job = make_job(
            source="freshers_blogs",
            experience_tags=["0-1 Years Experience", "Fresher"],
        )
        should_reject, _ = check_rss_tags(job)
        assert should_reject is False

    def test_no_tags_passes(self):
        job = make_job(source="freshers_blogs")
        should_reject, _ = check_rss_tags(job)
        assert should_reject is False

    def test_non_india_location_tags_rejected(self):
        job = make_job(
            source="freshers_blogs",
            location_tags=["USA", "United States"],
        )
        should_reject, _ = check_rss_tags(job)
        assert should_reject is True

    def test_india_location_tag_passes(self):
        job = make_job(
            source="freshers_blogs",
            location_tags=["Bangalore", "Remote"],
        )
        should_reject, _ = check_rss_tags(job)
        assert should_reject is False

    def test_experienced_role_tag_rejected(self):
        job = make_job(
            source="freshers_blogs",
            role_tags=["Experienced Jobs"],
        )
        should_reject, _ = check_rss_tags(job)
        assert should_reject is True

    def test_experienced_role_tag_with_fresher_passes(self):
        job = make_job(
            source="freshers_blogs",
            role_tags=["Experienced Jobs", "Fresher Jobs"],
        )
        should_reject, _ = check_rss_tags(job)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# check_is_old_post
# ─────────────────────────────────────────────────────────────────

class TestCheckIsOldPost:
    def test_no_date_passes(self, profile):
        job = make_job(posted_at="")
        should_reject, _ = check_is_old_post(job, profile)
        assert should_reject is False

    def test_recent_post_passes(self, profile):
        recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        job = make_job(posted_at=recent)
        should_reject, _ = check_is_old_post(job, profile)
        assert should_reject is False

    def test_old_post_rejected(self, profile):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        job = make_job(posted_at=old)
        should_reject, reason = check_is_old_post(job, profile)
        assert should_reject is True
        assert "days ago" in reason

    def test_relative_stale_string_rejected(self, profile):
        job = make_job(posted_at="3 months ago")
        should_reject, _ = check_is_old_post(job, profile)
        assert should_reject is True

    def test_relative_years_ago_rejected(self, profile):
        job = make_job(posted_at="2 years ago")
        should_reject, _ = check_is_old_post(job, profile)
        assert should_reject is True

    def test_unparseable_date_passes(self, profile):
        job = make_job(posted_at="some-garbage-date")
        should_reject, _ = check_is_old_post(job, profile)
        assert should_reject is False


# ─────────────────────────────────────────────────────────────────
# prefilter() — end-to-end orchestrator
# ─────────────────────────────────────────────────────────────────

class TestPrefilter:
    def test_empty_input_returns_empty(self, profile):
        assert prefilter([], profile) == []

    def test_valid_ats_job_passes(self, profile):
        jobs = [make_job(
            title="Backend Engineer Intern",
            company="Razorpay",
            location="Remote",
            description="We use Go, gRPC, PostgreSQL. Fresher-friendly.",
            source="greenhouse",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 1

    def test_experienced_job_rejected(self, profile):
        jobs = [make_job(
            title="Backend Engineer",
            description="5+ years of Go experience required.",
            source="greenhouse",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_non_tech_ats_title_rejected(self, profile):
        jobs = [make_job(
            title="Sales Manager",
            description="Drive sales and manage accounts.",
            source="greenhouse",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_old_post_rejected(self, profile):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        jobs = [make_job(posted_at=old, source="greenhouse")]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_ats_location_us_rejected(self, profile):
        jobs = [make_job(
            title="Backend Engineer",
            description="Go developer needed.",
            location="San Francisco, CA",
            source="greenhouse",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_mixed_batch_only_valid_passes(self, profile):
        valid_job = make_job(
            title="Backend Intern",
            description="Fresher-friendly Go role.",
            location="Remote",
            source="greenhouse",
        )
        invalid_job = make_job(
            title="Sales Director",
            description="Manage enterprise sales.",
            source="greenhouse",
        )
        result = prefilter([valid_job, invalid_job], profile)
        assert len(result) == 1
        assert result[0]["title"] == "Backend Intern"

    def test_ats_per_company_safety_cap(self, profile):
        """More than ats_prefilter_safety_cap (100) jobs from same ATS company → capped."""
        profile["hard_reject"]["ats_prefilter_safety_cap"] = 3
        jobs = [
            make_job(
                title=f"Backend Engineer {i}",
                company="BigCorp",
                url=f"https://example.com/job/{i}",
                description="Go and TypeScript. Fresher friendly.",
                source="greenhouse",
            )
            for i in range(5)
        ]
        result = prefilter(jobs, profile)
        assert len(result) == 3

    def test_candidate_post_filtered(self, profile):
        jobs = [make_job(title="[For Hire] Backend Developer", company="CANDIDATE_POST")]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_closed_application_rejected(self, profile):
        # "application closed" (without "are") matches the regex
        jobs = [make_job(description="application closed — no longer accepting.")]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_internshala_business_dev_rejected(self, profile):
        jobs = [make_job(
            title="Business Development (Sales)",
            source="internshala",
            description="Sales role in fintech.",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 0

    def test_internshala_backend_development_passes(self, profile):
        jobs = [make_job(
            title="Backend Development Internship",
            source="internshala",
            description="Build REST APIs.",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 1

    def test_exam_noise_from_rss_rejected(self, profile):
        jobs = [make_job(
            title="SSC Question Papers 2025 – Download PDF",
            source="freshers_blogs",
            description="Download previous year question papers.",
        )]
        result = prefilter(jobs, profile)
        assert len(result) == 0
