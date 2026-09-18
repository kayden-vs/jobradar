# Graph Report - jobradar  (2026-09-16)

## Corpus Check
- 76 files · ~106,331 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 3, .bak 1)

## Summary
- 1327 nodes · 2644 edges · 88 communities (54 shown, 34 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 54 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Lever ATS Harvester
- System Architecture Reference
- Hirist Job Scraper
- RemoteOK API Client
- HiringCafe Job Harvester
- HackerNews Hiring Scraper
- Fresher RSS Feeds
- Fresher RSS Feeds
- Greenhouse ATS Harvester
- Jobicy Job API
- Fresher RSS Feeds
- ATS Harvester Suite
- Reddit Hiring Scraper
- Job Relevance Ranker
- Internshala Portal Scraper
- ATS Harvester Suite
- Job Deduplication Hashes
- Startup Job Portals
- SQLite Database Layer
- Job Relevance Ranker
- Job Relevance Ranker
- Internshala Portal Scraper
- Job Relevance Ranker
- Internshala Portal Scraper
- ATS Harvester Suite
- Telegram Notification UI
- SQLite Database Layer
- Job Deduplication Hashes
- Pre-Filter Pipeline
- RSS Tag Filter
- Data Normalization Layer
- Setup & Diagnostic Guide
- Lever ATS Harvester
- Lever ATS Harvester
- Data Normalization Layer
- Experience Filter Logic
- Job Deduplication Hashes
- Fresher RSS Feeds
- ATS Harvester Suite
- Telegram MTProto Client
- Job Relevance Ranker
- Pre-Filter Pipeline
- Non-Job Content Filter
- Job Deduplication Hashes
- System Architecture Reference
- Fresher RSS Feeds
- Candidate Post Filter
- Job Title Validator
- Location Filter Logic
- Telegram MTProto Client
- JobRadar Visual Identity
- SQLite Database Layer
- Data Normalization Layer
- System Architecture Reference
- JobRadar Visual Identity
- Pipeline Execution Script
- Changelog Size Management Rule
- EC2 Deployment Architecture
- Changelog: Comprehensive 461-Test Test Suite
- Changelog: Removal of Tracker Bot
- ADR-001: Groq Free Tier Selection
- ADR-006: Two-Layer Token Budget Strategy
- ADR-009: Application Tracker via Telegram
- ADR-011: Test Suite Mocking Strategy
- JobRadar Operational Metrics
- Out-of-Scope Contribution Boundaries
- JobRadar System Architecture & Deep
- Deployment Options & Persistence Analysis
- Data Quality & Intelligence Upgrades
- Roadmap Phase 2: Next.js Web
- Roadmap Phase 4: Growth, Channels,
- Roadmap Phase 5: Feedback Data
- Automation via Linux Cron and
- Prerequisites and Installation Procedure
- Setup Troubleshooting and Diagnostics
- Experience Boundary Filters
- Industry Domain Scoring Priorities
- Location Filtering and Affinity Preferences
- Source Activation Toggles
- Gemini AI Scorer System
- Heuristic Relevance Ranker System
- Dual-Hash Deduplication System
- Rule-Based Pre-Filter System

## God Nodes (most connected - your core abstractions)
1. `make_job()` - 121 edges
2. `prefilter()` - 38 edges
3. `run()` - 34 edges
4. `_mock_response()` - 27 edges
5. `rank_eligible_jobs()` - 24 edges
6. `_is_dev_job()` - 24 edges
7. `save_job()` - 23 edges
8. `deduplicate()` - 22 edges
9. `check_title_relevance()` - 21 edges
10. `TestIsDevJob` - 21 edges

## Surprising Connections (you probably didn't know these)
- `6-Layer Heuristic Ranker Numeric Weights` --shares_data_with--> `rank_eligible_jobs()`  [INFERRED]
  profile.yaml → pipeline/ranker.py
- `Hard Reject Rules and Role Blacklists` --shares_data_with--> `prefilter()`  [INFERRED]
  profile.yaml → pipeline/prefilter.py
- `Candidate Identity and Skills Profile` --shares_data_with--> `build_profile_patterns()`  [INFERRED]
  profile.yaml → pipeline/ranker.py
- `Gemini AI Scoring Weights` --shares_data_with--> `score_all()`  [INFERRED]
  profile.yaml → pipeline/scorer.py
- `Dual-Hash Deduplication and SQLite Storage` --references--> `save_job()`  [EXTRACTED]
  docs/implementation_guide.md → storage/db.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **JobRadar End-to-End Pipeline Execution Flow** — main_run, pipeline_dedup_deduplicate, pipeline_prefilter_prefilter, pipeline_ranker_rank_eligible_jobs, pipeline_scorer_score_all, notify_telegram_bot [EXTRACTED 1.00]
- **Profile-Driven Dual-Phase Ranking and Scoring Pattern** — profile_config, profile_candidate_profile, profile_ranker_weights, profile_scoring_weights, pipeline_ranker, pipeline_scorer [INFERRED 0.95]
- **Telegram MTProto Automated Job Channel Ingestion System** — sources_telegram_channels, tools_telethon_login, requirements_telethon, _agents_skills_jobradar_context_references_decisions_adr_013 [EXTRACTED 1.00]

## Communities (88 total, 34 thin omitted)

### Community 0 - "Lever ATS Harvester"
Cohesion: 0.06
Nodes (45): Emerging ATS Platforms Registry, fetch_all_ats(), fetch_ashby(), fetch_bamboohr(), fetch_greenhouse(), _fetch_greenhouse_jd(), fetch_lever(), fetch_personio() (+37 more)

### Community 1 - "System Architecture Reference"
Cohesion: 0.05
Nodes (31): Roadmap Phase 0: Personal Stabilization, _build_salary(), _fetch_job_detail(), fetch_naukri(), _fetch_search_page(), _get_with_backoff(), _is_too_old(), _parse_naukri_date() (+23 more)

### Community 2 - "Hirist Job Scraper"
Cohesion: 0.05
Nodes (32): Exception, _exp_overlaps(), fetch_hirist(), _fetch_hirist_body(), _fetch_job_detail(), _fetch_listing_page(), _parse_exp_range(), sources/hirist.py — Hirist.tech job source Architecture: Hirist.tech is a React… (+24 more)

### Community 3 - "RemoteOK API Client"
Cohesion: 0.07
Nodes (20): _clean_html(), fetch_remoteok(), _format_salary(), _is_dev_job(), sources/remoteok.py — RemoteOK job feed RemoteOK provides a public JSON API at…, Strip HTML tags and collapse whitespace., Format salary range into a human-readable string., Main entry point: fetches remote dev/engineering jobs from RemoteOK's tag-… (+12 more)

### Community 4 - "HiringCafe Job Harvester"
Cohesion: 0.06
Nodes (34): _clean_company_name(), _fetch_build_id(), fetch_hiringcafe(), _fetch_page(), _fetch_query(), _format_salary(), _get_build_id(), _get_session() (+26 more)

### Community 5 - "HackerNews Hiring Scraper"
Cohesion: 0.07
Nodes (29): High-Impact Operational Tweaks, _autodiscover_thread_id(), fetch_hn_comments(), fetch_hn_hiring(), _gemini_client(), get_current_thread_id(), _is_valid_job(), parse_comments_with_ai() (+21 more)

### Community 6 - "Fresher RSS Feeds"
Cohesion: 0.06
Nodes (50): Gemini Rate Limiting Strategy, Changelog: Migration to Gemini 3.1 Flash-Lite, Changelog: Workday Synthetic Stub Fix, ADR-010: Few-Shot Calibration for AI Scorer, ADR-012: Migrate AI Scorer to Gemini 3.1 Flash-Lite, Source Status Matrix, Workday Multi-Tenant Configuration, Technical Debt & Scaling Risks Analysis (+42 more)

### Community 7 - "Fresher RSS Feeds"
Cohesion: 0.08
Nodes (29): ADR-007: Lazy JD Fetch Pattern, SimpleNamespace, fetch_freshers_blogs(), _rss_task(), _fetch_rss(), _parse_rss_date(), sources/freshers_blogs.py — Indian fresher job blog aggregator Strategy (lazy-…, Parse an Indian fresher blog post title into {company, role, location}. Handles… (+21 more)

### Community 8 - "Greenhouse ATS Harvester"
Cohesion: 0.08
Nodes (35): Application, DEFAULT_TYPE, Dual-Hash Deduplication and SQLite Storage, cmd_applications(), _entry(), cmd_applied(), cmd_help(), cmd_responded() (+27 more)

### Community 9 - "Jobicy Job API"
Cohesion: 0.12
Nodes (18): _clean_html(), _fetch_endpoint(), fetch_jobicy(), sources/jobicy.py — Jobicy remote job API Jobicy provides a free, public JSON…, Main entry point: fetches remote jobs from Jobicy across multiple tag/industry…, # NOTE: tag="go" returns 400 Bad Request from Jobicy (too short/ambiguous)., Strip HTML tags and collapse whitespace., Fetch a single Jobicy API endpoint and return normalised job dicts. (+10 more)

### Community 10 - "Fresher RSS Feeds"
Cohesion: 0.09
Nodes (36): Connection, _bar(), build_weekly_summary(), _esc(), _get_active_companies(), _get_best_job(), _get_location_split(), _get_silent_sources() (+28 more)

### Community 11 - "ATS Harvester Suite"
Cohesion: 0.08
Nodes (33): External API Integrations, Serper Google Dorking Engine, Multi-Source Extraction Layer, build_dork_queries(), _build_location_variants(), _build_skill_variants(), _build_tiered_queries(), _detect_ats_source() (+25 more)

### Community 12 - "Reddit Hiring Scraper"
Cohesion: 0.14
Nodes (13): _extract_company_from_reddit(), _extract_location_hint(), fetch_reddit(), Reddit job posts often follow patterns like: '[HIRING] Backend Intern @…, _make_feed(), _make_feed_entry(), patch, tests/test_sources_reddit.py — Unit tests for sources/reddit.py Covers: -… (+5 more)

### Community 13 - "Job Relevance Ranker"
Cohesion: 0.11
Nodes (26): 6-Layer Heuristic Ranker Architecture, Ranker Weight Tuning Guide, NamedTuple, _company_tier(), _concordance_and_boosters(), _heuristic_score(), _is_lazy_fetch_source(), _location_affinity() (+18 more)

### Community 14 - "Internshala Portal Scraper"
Cohesion: 0.17
Nodes (14): BeautifulSoup, fetch_internshala(), _fetch_page(), _get_session(), Session, sources/internshala.py — Internshala internship & fresher job scraper Why…, Fetch a URL and return a BeautifulSoup object, or None on failure., Scrape Internshala job listing pages and return structured job dicts. Uses… (+6 more)

### Community 15 - "ATS Harvester Suite"
Cohesion: 0.13
Nodes (12): Changelog: Per-Source Observability Logging, check_company_blacklist(), check_no_description(), check_role_blacklist(), load_profile(), datetime, # NOTE: these are raw substrings, which is safe for clean ATS titles like, If a job has absolutely no description AND no meaningful title context, there's… (+4 more)

### Community 16 - "Job Deduplication Hashes"
Cohesion: 0.12
Nodes (12): ADR-003: Dual-Hash Deduplication, deduplicate(), Removes: 1. Jobs already seen in the database (persisted dedup) 2. Duplicates…, tests/test_pipeline_dedup.py — Unit tests for pipeline/dedup.py Covers: - In-…, Deduplication within a single batch (no DB lookup needed)., Bengaluru vs Bangalore same title+company → same hash → deduped., Razorpay vs Razorpay Software Pvt Ltd → same hash → deduped., Deduplication against previously seen jobs in the DB. (+4 more)

### Community 17 - "Startup Job Portals"
Cohesion: 0.12
Nodes (21): fetch_cutshort(), fetch_job_description(), Scrapes Cutshort job search results. Cutshort uses client-side rendering, so we…, Fetches the full JD for a Cutshort job listing. Called only for jobs that pass…, fetch_instahyre(), _fetch_instahyre_scrapling(), Scrape Instahyre job listings via Scrapling (StealthyFetcher). The internal API…, # NOTE: Instahyre's internal API endpoint /api/v1/opportunity/ now returns 404. (+13 more)

### Community 18 - "SQLite Database Layer"
Cohesion: 0.14
Nodes (16): Changelog: Single DB Consolidation, ADR-002: SQLite Over PostgreSQL, Roadmap Phase 1: Multi-User Backend Foundation, _db(), get_all_applications(), log_application(), mark_application_dead(), mark_application_responded() (+8 more)

### Community 19 - "Job Relevance Ranker"
Cohesion: 0.16
Nodes (12): ADR-004: Heuristic Ranking Before AI Scoring, _log_score_distribution(), rank_eligible_jobs(), Log score distribution statistics for observability. Shows min, max, median,…, Rank a list of pre-filtered jobs by heuristic relevance score. Args: jobs: Pre-…, A job mentioning 5 skills should outrank a job with 0 matching skills, all else…, fresher' in title should boost ranking., A 'Senior Engineer' title should be ranked below a 'Backend Intern' title. (+4 more)

### Community 20 - "Job Relevance Ranker"
Cohesion: 0.12
Nodes (13): Keyword and Domain Signal Taxonomy, build_profile_patterns(), _deduplicate_skills(), _escape_keywords(), _load_curated_companies(), Join a list of strings into a regex alternation, escaping each term., Remove conceptual duplicates from a skill list. "Go" and "Golang" are the same…, Load company names from companies.yaml to build the company tier set. Returns a… (+5 more)

### Community 21 - "Internshala Portal Scraper"
Cohesion: 0.17
Nodes (4): check_title_relevance(), Positive / negative title filter before the AI scorer. Four modes in sequence:…, Business Development' has 'dev' but the reject regex should catch it., TestCheckTitleRelevance

### Community 22 - "Job Relevance Ranker"
Cohesion: 0.15
Nodes (18): Changelog: Telegram Pipeline Overhaul, ADR-014: Remove Telegram Heuristic Pre-Filter and Add Ranker Boost, gemini_throttle(), pipeline/gemini_throttle.py — Shared Gemini API rate-limit throttle.…, Block until at least REQ_INTERVAL seconds have elapsed since the last Gemini…, _fetch_all_channels(), fetch_telegram_channels(), _gemini_client() (+10 more)

### Community 23 - "Internshala Portal Scraper"
Cohesion: 0.16
Nodes (7): ADR-005: Per-Company ATS Caps, Rule-Based Hard Pre-Filter Specifications, prefilter(), Hard filters run BEFORE the AI scorer to reduce API cost. Order matters —…, Hard Reject Rules and Role Blacklists, More than ats_prefilter_safety_cap (100) jobs from same ATS company → capped., TestPrefilter

### Community 24 - "ATS Harvester Suite"
Cohesion: 0.14
Nodes (17): JobRadar Pipeline Flow Architecture, AI Scoring Prompt and Evaluation Rubric, Pipeline Flow Implementation Details, JobRadar Product Roadmap and Vision, _print_dry_run_summary(), Print a config summary and exit — no API calls made., run(), load_profile() (+9 more)

### Community 25 - "Telegram Notification UI"
Cohesion: 0.14
Nodes (18): Telegram Notification Layer, Telegram Markdown Formatting Pitfall, Roadmap Phase 3: Razorpay Freemium Monetization, _esc(), format_job_message(), notify_urgent_jobs(), _send_all(), Escape a plain string for Telegram MarkdownV2. (+10 more)

### Community 26 - "SQLite Database Layer"
Cohesion: 0.14
Nodes (15): _build_followup_draft(), _days_since(), followup_check.py — Check application tracker for overdue follow-ups and dead…, Entry point for synchronous callers (e.g. run.sh via python -m)., Return the number of full days since an ISO datetime string., Build a ready-to-copy follow-up email draft for a given application., _run_checks_async(), run_followup_check() (+7 more)

### Community 27 - "Job Deduplication Hashes"
Cohesion: 0.17
Nodes (10): is_duplicate(), Returns True if this job was already seen (by title-hash OR URL)., Persist a scored job to the database. Uses INSERT OR IGNORE — re-inserting the…, save_job(), Same URL, different title → duplicate via url_id., Razorpay vs Razorpay Software Pvt Ltd → same hash → duplicate., Re-inserting the same job should not raise and not duplicate., The notified flag should not be reset on second INSERT OR IGNORE. (+2 more)

### Community 28 - "Pre-Filter Pipeline"
Cohesion: 0.21
Nodes (3): _parse_posted_at(), Parse a posted_at string into a timezone-aware datetime. Handles: - ISO 8601 /…, TestParsePostedAt

### Community 29 - "RSS Tag Filter"
Cohesion: 0.29
Nodes (5): check_expiry_signals(), Reject jobs where the title or RSS summary contains clear evidence that the…, make_job(), Build a minimal valid job dict for tests., TestCheckExpirySignals

### Community 30 - "Data Normalization Layer"
Cohesion: 0.26
Nodes (14): _get_telegram_credentials(), Reads Telegram credentials from env. Returns (api_id, api_hash, session_string)…, _check_env(), main(), _make_job_id(), _make_url_id(), _normalize(), _normalize_company() (+6 more)

### Community 31 - "Setup & Diagnostic Guide"
Cohesion: 0.13
Nodes (8): tests/test_sources_utils.py — Unit tests for sources/utils.py Covers: -…, Reset the cached result before each test., Second call should return the cached value without re-running checks., If StealthyFetcher cannot be imported, returns False., Result should always be a bool., On Linux, missing libcups.so.2 should return False., On Linux with libcups present, tries to import StealthyFetcher., TestIsPlaywrightAvailable

### Community 32 - "Lever ATS Harvester"
Cohesion: 0.15
Nodes (14): ADR-008: Profile-Driven Architecture, Ashby ATS Company Slugs, Curated ATS Companies Configuration, Greenhouse US and EU Company Slugs, Lever ATS Company Slugs, SmartRecruiters ATS Company Slugs, Workable ATS Company Slugs, New Source Addition Protocol (+6 more)

### Community 33 - "Lever ATS Harvester"
Cohesion: 0.26
Nodes (4): check_ats_location(), Zero-cost location filter for ATS jobs only. ATS sources (Greenhouse, Lever,…, Non-ATS jobs are never rejected by this check (it only applies to ATS)., TestCheckAtsLocation

### Community 34 - "Data Normalization Layer"
Cohesion: 0.23
Nodes (5): _normalize(), _normalize_company(), Normalise a string for dedup hashing — strips noise that shouldn't distinguish…, TestNormalize, TestNormalizeCompany

### Community 35 - "Experience Filter Logic"
Cohesion: 0.27
Nodes (4): check_experience(), Reject if any hard-reject experience keyword is found, or if a regex pattern…, 1 year of experience is allowed (max_required=1)., TestCheckExperience

### Community 36 - "Job Deduplication Hashes"
Cohesion: 0.24
Nodes (6): make_job_id(), Deterministic hash for deduplication. Primary key: normalised (title + company…, Razorpay vs Razorpay Software Pvt Ltd → same hash., Bengaluru vs Bangalore → same hash., SDE 2025' vs 'SDE' → same hash., TestMakeJobId

### Community 37 - "Fresher RSS Feeds"
Cohesion: 0.31
Nodes (3): check_rss_tags(), Zero-cost tag-based filter for jobs from freshers_blogs RSS feeds. WordPress…, TestCheckRssTags

### Community 38 - "ATS Harvester Suite"
Cohesion: 0.25
Nodes (6): Persist pipeline run statistics for weekly summary queries. source_breakdown:…, Returns True if the weekly summary has already been sent for the current ISO…, save_run_stats(), was_weekly_summary_sent(), tests/test_storage_db.py — Unit tests for storage/db.py Covers: - _normalize /…, TestRunStats

### Community 39 - "Telegram MTProto Client"
Cohesion: 0.20
Nodes (10): Agent Coding Conventions, JobRadar Tech Stack, JobRadar Contributing Guidelines, Source Quality Standards, Complete Setup & Customization Guide, Python Dependencies Specification, Google GenAI SDK Dependency, Web Scraping Stack Dependencies (+2 more)

### Community 40 - "Job Relevance Ranker"
Cohesion: 0.31
Nodes (4): Merge caller-provided ranker_weights with module defaults. Any key present in…, _resolve_weights(), tests/test_pipeline_ranker.py — Unit tests for pipeline/ranker.py Covers: -…, TestResolveWeights

### Community 41 - "Pre-Filter Pipeline"
Cohesion: 0.36
Nodes (3): check_is_old_post(), Reject jobs that are older than the max_job_age_days threshold. Uses the smart…, TestCheckIsOldPost

### Community 42 - "Non-Job Content Filter"
Cohesion: 0.36
Nodes (3): check_non_job_content(), Reject non-job content — exam prep posts, govt notifications, question papers,…, TestCheckNonJobContent

### Community 43 - "Job Deduplication Hashes"
Cohesion: 0.31
Nodes (5): make_url_id(), mark_job_notified(), Mark a job as notified (e.g., sent to Telegram). level=1 means urgent/telegram,…, Secondary hash based on canonical URL. Same URL from two sources = same job,…, TestMakeUrlId

### Community 44 - "System Architecture Reference"
Cohesion: 0.36
Nodes (8): Context-First Workflow, Mandatory Update Protocol, JobRadar Agent Rules, Architecture Reference Overview, JobRadar Project Changelog, Architecture Decision Records Index, JobRadar Project Context Skill, Graphify Workflow Rules

### Community 45 - "Fresher RSS Feeds"
Cohesion: 0.39
Nodes (8): Bright Money SDE Intern Backend Job Alert, Backend Freshers Hub Telegram Channel, Telegram Notification Screenshot, Personalized Match Reasons and Warnings, Job Fit Scoring and Recommendation UI, Pipeline Processing Funnel Metrics, JobRadar Pipeline Daily Summary Digest, Stripe Software Engineer Intern Job Alert

### Community 46 - "Candidate Post Filter"
Cohesion: 0.39
Nodes (3): check_candidate_post(), Filter out candidate posts (people looking for work, not companies hiring)., TestCheckCandidatePost

### Community 47 - "Job Title Validator"
Cohesion: 0.43
Nodes (3): check_has_meaningful_title(), Reject jobs with no or empty title., TestCheckHasMeaningfulTitle

### Community 48 - "Location Filter Logic"
Cohesion: 0.43
Nodes (3): check_location(), Reject jobs that are explicitly outside India in-office only., TestCheckLocation

### Community 49 - "Telegram MTProto Client"
Cohesion: 0.33
Nodes (4): ADR-013: Telegram Channels via Telethon MTProto, API Credentials & Secrets Configuration, Telegram Channels MTProto Setup, tools/telethon_login.py — ONE-TIME interactive login to generate a Telegram…

### Community 50 - "JobRadar Visual Identity"
Cohesion: 0.47
Nodes (6): JobRadar Visual Branding, JobRadar Logo, Radar Screen Motif, Radar Sweep Scanner, Signal Target Detection, Winking Face Mascot

### Community 51 - "SQLite Database Layer"
Cohesion: 0.47
Nodes (3): get_jobs_by_score(), Retrieve jobs above a score threshold for digest., TestGetJobsByScore

### Community 53 - "System Architecture Reference"
Cohesion: 0.50
Nodes (4): Job Dict Shape Contract, SQLite Database Schema, Pipeline Job Dict Specification, Job Dataclass Specification

### Community 54 - "JobRadar Visual Identity"
Cohesion: 0.67
Nodes (4): JobRadar Brag Banner, JobRadar Typography Logo, Network Mesh Topology Background, Concentric Radar Scan Pattern

## Knowledge Gaps
- **61 isolated node(s):** `run.sh script`, `Mandatory Update Protocol`, `Changelog Size Management Rule`, `Job Dict Shape Contract`, `JobRadar Operational Metrics` (+56 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 420 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **34 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `prefilter()` connect `Internshala Portal Scraper` to `Lever ATS Harvester`, `Lever ATS Harvester`, `Experience Filter Logic`, `Fresher RSS Feeds`, `Pre-Filter Pipeline`, `Non-Job Content Filter`, `Candidate Post Filter`, `ATS Harvester Suite`, `Job Title Validator`, `Location Filter Logic`, `Internshala Portal Scraper`, `ATS Harvester Suite`, `RSS Tag Filter`?**
  _High betweenness centrality (0.104) - this node is a cross-community bridge._
- **Why does `run()` connect `ATS Harvester Suite` to `Lever ATS Harvester`, `System Architecture Reference`, `Hirist Job Scraper`, `RemoteOK API Client`, `HiringCafe Job Harvester`, `HackerNews Hiring Scraper`, `Fresher RSS Feeds`, `Fresher RSS Feeds`, `Greenhouse ATS Harvester`, `Jobicy Job API`, `Fresher RSS Feeds`, `ATS Harvester Suite`, `Reddit Hiring Scraper`, `Internshala Portal Scraper`, `ATS Harvester Suite`, `Job Deduplication Hashes`, `Startup Job Portals`, `Job Relevance Ranker`, `Internshala Portal Scraper`, `Telegram Notification UI`, `Lever ATS Harvester`, `ATS Harvester Suite`, `Job Deduplication Hashes`?**
  _High betweenness centrality (0.093) - this node is a cross-community bridge._
- **Why does `fetch_all_ats()` connect `Lever ATS Harvester` to `Lever ATS Harvester`, `ATS Harvester Suite`, `ATS Harvester Suite`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **What connects `run.sh script`, `Mandatory Update Protocol`, `Changelog Size Management Rule` to the rest of the system?**
  _61 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Lever ATS Harvester` be split into smaller, more focused modules?**
  _Cohesion score 0.05635964912280702 - nodes in this community are weakly interconnected._
- **Should `System Architecture Reference` be split into smaller, more focused modules?**
  _Cohesion score 0.052884615384615384 - nodes in this community are weakly interconnected._
- **Should `Hirist Job Scraper` be split into smaller, more focused modules?**
  _Cohesion score 0.05245901639344262 - nodes in this community are weakly interconnected._