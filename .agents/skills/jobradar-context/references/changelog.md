# JobRadar — Changelog

> Reverse-chronological log of changes. Agents: add new entries at the TOP.

---

## [2026-07-01] Comprehensive test suite — 461 tests, all sources covered
**What**: Built a full test suite from scratch covering all pipeline components and every data source:
- 17 test files, 461 tests, 0 failures — runs in ~2 minutes
- Sources: ATS (Greenhouse/Lever/Ashby/Workable/Workday), Jobicy, RemoteOK, Reddit, freshers_blogs, HackerNews, Naukri, Hirist, Internshala, hiring.cafe, utils
- Pipeline: prefilter (all 15+ rules), dedup, ranker (`ProfilePatterns` NamedTuple, `_resolve_weights`, ranking order)
- Storage: `_normalize`, `make_job_id`, `is_duplicate`, `save_job`, run stats, application tracker CRUD
- All tests use mocked HTTP (no real network calls). Groq AI mocked via `unittest.mock`.
- Added `pyproject.toml` with `pythonpath = ["."]` so bare `pytest` works without `PYTHONPATH`.
**Why**: Enable fast, isolated verification that a source or pipeline component is broken after any code change.
**Files**: `tests/__init__.py`, `tests/conftest.py`, `tests/test_pipeline_dedup.py`, `tests/test_pipeline_prefilter.py`, `tests/test_pipeline_ranker.py`, `tests/test_storage_db.py`, `tests/test_sources_ats.py`, `tests/test_sources_freshers_blogs.py`, `tests/test_sources_hackernews.py`, `tests/test_sources_hiringcafe.py`, `tests/test_sources_hirist.py`, `tests/test_sources_internshala.py`, `tests/test_sources_jobicy.py`, `tests/test_sources_naukri.py`, `tests/test_sources_reddit.py`, `tests/test_sources_remoteok.py`, `tests/test_sources_utils.py`, `pyproject.toml`
**Status**: Complete

---

## [2026-06-30] Ranker v3 improvements: cap, title dedup, seniority penalty
**What**: Three targeted improvements based on v3 run analysis (795 jobs, Jun 30):
1. Lowered `max_ai_jobs_per_run` from 200 → 130 (v3 token budget only scored ~103 jobs, 97 cap slots were wasted)
2. Added title-level dedup in scorer.py — skips duplicate `(company, title)` pairs from ATS sources. v3 had "Staff Production Engineer @ Canva" ×4 and "Technical Services Engineer @ Mongodb" ×3 wasting 11 AI calls
3. Added `_SENIORITY_LEVEL_RE` penalty (`-8`) in ranker.py for Senior/Staff/Principal/Lead/SDE III+ titles. v3 had ~25 senior roles consuming AI tokens, all scored 1-2/10. This is a ranker penalty (not a hard reject) so edge cases survive
**Why**: v3 score distribution was good (spread=38, IQR=10) but token budget was being wasted on obviously-wrong jobs (senior roles, duplicates). These three changes protect the AI budget without losing legitimate matches.
**Files**: `profile.yaml`, `pipeline/scorer.py`, `pipeline/ranker.py`
**Status**: Complete

---

## [2026-06-30] Initial context system baseline
**What**: Established the `.agents/` context system (AGENTS.md, SKILL.md, architecture.md, decisions.md, changelog.md) to eliminate repeated AI codebase scans across chat sessions.
**Why**: Every new Antigravity chat was re-scanning the entire codebase, burning thousands of tokens for context that doesn't change between sessions.
**Files**: `.agents/AGENTS.md`, `.agents/skills/jobradar-context/SKILL.md`, `references/architecture.md`, `references/decisions.md`, `references/changelog.md`
**Status**: Complete

## [2026-06-30] Codebase state snapshot (baseline)
**What**: Documenting the current state of the codebase as the starting point for the changelog.
**Why**: Future entries will be diffs against this baseline.
**Current state**:
- 16 sources implemented, 12 enabled, 5 disabled (cutshort, instahyre, wellfound, reddit, hirist)
- Pipeline: sources → dedup → prefilter → ranker (v2, 6-layer) → scorer (Groq llama-4-scout) → telegram
- Application tracker bot with followup_check (7-day draft, 14-day dead)
- Weekly summary digest (Fridays only, ISO-week guard)
- Deployment: EC2 t2.micro, EventBridge schedule, auto-shutdown
- Per-user profiles supported via CLI arg
- Ranker v2 features: skill density, concordance, holy trinity, title richness, location affinity, company tier
**Files**: All files in the repository
**Status**: Complete — this is a snapshot, not a change
