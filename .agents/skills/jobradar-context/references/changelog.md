# JobRadar — Changelog

> Reverse-chronological log of changes. Agents: add new entries at the TOP.

---

## [2026-07-11] Add per-source observability to prefilter and ranker
**What**: Added per-source survival stats to `pipeline/prefilter.py` (logs `telegram_channels: N in -> M passed (X%)` for key sources after each run) and per-source score distribution to `pipeline/ranker.py` (logs median/max ranker score per source and how many jobs survive the AI cap cutoff). Also added Telegram artifact title rejection to `_NON_JOB_CONTENT_RE` — titles like `"Go Careers – Telegram"` (Gemini hallucinating channel header as job title) now get rejected by prefilter instead of wasting a scorer call.
**Why**: Without per-source logging there was no way to tell if `telegram_channels` jobs were being filtered or ranked out. The new log lines definitively answer "are Telegram jobs reaching the scorer?" on every run.
**Files**: `pipeline/prefilter.py`, `pipeline/ranker.py`
**Status**: Complete

---

## [2026-07-11] Fix Gemini output token limit and channel-name-as-company hallucination
**What**: (1) Raised `max_output_tokens` from 2048 → 4096 in `sources/telegram_channels.py` — the new prompt requests full verbatim post text as `description`, which can push 5-post batch output past 2048 tokens, causing silent JSON truncation. (2) Moved `_known_channels_lower` set construction outside the inner job loop (was being rebuilt on every extracted job). (3) Updated `pipeline/gemini_throttle.py` comment to reflect 3-caller math (HN + Telegram + scorer = ~175 calls/run = ~350 RPD).
**Why**: Silent JSON truncation caused entire Telegram batches to be silently lost with only a WARNING log. 
**Files**: `sources/telegram_channels.py`, `pipeline/gemini_throttle.py`
**Status**: Complete

---

## [2026-07-10] Fix Telegram pipeline: remove heuristic, fix Gemini prompt, add ranker boost
**What**: Three root-cause fixes for Telegram jobs never reaching high scores:
1. **Removed two-gate heuristic pre-filter** in `sources/telegram_channels.py` — replaced with `_passes_sanity()` (40-char minimum only). The heuristic blocked 80% of real posts because Indian Telegram phrasing (`"applications open"`, `"batch 2025"`, `"drive"`) didn't match hard-coded English keywords. Now 63/63 posts pass → 56 jobs extracted (was 19/63 → ~12).
2. **Fixed Gemini extraction prompt** — explicit instruction that the channel name in the post header is NOT the company. Added post-extraction validation rejecting single-letter company names and company names matching known channel names. Prompt now requests full verbatim post text as `description` instead of a summary.
3. **Added `telegram_channels` to ranker Layer 5 source-aware offsets** (`source_telegram_boost: +3`). Indian Telegram posts have 2–4 sentence descriptions vs ATS jobs' 500–1500 word JDs — the ranker's skill-density layer was systematically underranking them.
**Why**: All three bugs combined caused high-quality Telegram jobs to either never be extracted, extracted as garbage, or ranked below the AI cap cutoff. Zero Telegram jobs ever scored ≥7 before this fix.
**Files**: `sources/telegram_channels.py`, `pipeline/ranker.py`
**Status**: Complete

---

## [2026-07-09] Consolidate to single DB: profile.db
**What**: Merged all 353 historical jobs from `data/jobradar.db` into `data/profile.db` using column-aware `INSERT OR IGNORE` (349 inserted, 4 true duplicates skipped — final count 420 jobs). Changed `_DEFAULT_DB_PATH` in `storage/db.py` from `"data/jobradar.db"` to `"data/profile.db"`. Deleted `data/jobradar.db`, `data/jobradar_copy.db`, and empty `data/rohit.db`. Updated all `jobradar.db` references in `docs/jobradar_guide.md` and `docs/implementation_guide.md`.
**Why**: Two separate DB files (`jobradar.db` from the old hardcoded default, `profile.db` from the per-profile naming scheme) were confusing and caused data to be split across files. Now there is exactly one DB file: `data/profile.db`. The only code change was one line in `storage/db.py`.
**Files**: `storage/db.py`, `docs/jobradar_guide.md`, `docs/implementation_guide.md`, `data/jobradar.db` [DELETED], `data/jobradar_copy.db` [DELETED], `data/rohit.db` [DELETED]
**Status**: Complete

---

## [2026-07-08] Add 3 Telegram channels + dedup verification
**What**: Added `@jobsinternshipswale`, `@jobsandinternshipsindia`, and `@gocareers` to `CHANNELS` in `sources/telegram_channels.py` (6 → 9 channels). Updated `tests/test_telegram_source.py` to cover all 9 channels and added a dedup verification section (5 cases: emoji variance, company suffix noise, cross-run persistence, URL param stripping, distinct job negative test). All channels connect and fetch correctly. Live test: 62 raw messages → 21 heuristic-filtered → 23 jobs extracted. New channels `@gocareers` and `@jobsinternshipswale` both passed heuristic filter with 7 and 6 posts respectively.
**Why**: More channel coverage = higher chance of catching exclusive off-campus drives. Dedup verification gives confidence that re-fetching the same posts from inactive channels won't send duplicate alerts.
**Files**: `sources/telegram_channels.py`, `tests/test_telegram_source.py`, `README.md`
**Status**: Complete

---

## [2026-07-08] Add Telegram channels source via Telethon MTProto API
**What**:
1. New source `sources/telegram_channels.py` — reads 6 curated Indian job Telegram channels via Telethon (MTProto API), bypassing fragile HTML scraping of `t.me/s/` pages entirely.
2. New tool `tools/telethon_login.py` — one-time interactive login script that generates a `StringSession` string for headless EC2 use.
3. New test `tools/test_telegram_source.py` — standalone validation script (does not touch main.py).
4. `telethon` added to `requirements.txt` (v1.44.0 installed).
5. `main.py` updated to import and call `fetch_telegram_channels()` behind `source_enabled("telegram_channels")`.
6. `profile.yaml` updated with `telegram_channels: true` toggle.
7. `README.md` updated: source count 16→17, new Telegram Channels section with setup guide, architecture diagram, project structure, and API usage table.
8. `docs/setup_guide.md` updated: new Telegram Channels API key section, source toggle docs, `.env` template.
9. Three new `.env` keys documented: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`.
**Why**: Indian Telegram job channels (internfreak, dot_aware, fresheroffcampus, etc.) post exclusive off-campus drives and internship links not available on any job board. MTProto gives clean structured message objects directly — immune to Telegram frontend changes. Tested: 41 raw messages → 8 heuristic-filtered → 12 structured job dicts extracted (verified 3 samples: American Express, Flipkart, Danaher — all correctly parsed).
**Files**: `sources/telegram_channels.py` [NEW], `tools/telethon_login.py` [NEW], `tools/test_telegram_source.py` [NEW], `requirements.txt`, `main.py`, `profile.yaml`, `README.md`, `docs/setup_guide.md`
**Status**: Complete

---

## [2026-07-06] Fix duplicate Telegram notifications and add age penalties
**What**:
1. Added `mark_job_notified(job)` to `storage/db.py` to correctly flag jobs as notified (`notified=1`).
2. Updated `main.py` to call `mark_job_notified` after sending Telegram alerts.
3. Added a new hard rule in `pipeline/scorer.py` `_SYSTEM_PROMPT` to hard cap scores ≤3 for jobs >10 days old or posted in a previous year (e.g. 2024).
4. Added `penalty_old_job: -10` to `pipeline/ranker.py` and updated `_recency_bonus` to apply this massive penalty to old jobs before they reach the AI.
**Why**:
1. `save_job` inserts jobs with `notified=0`, but the bot never updated this flag after sending an alert. If a scraper later pulled the exact same job with a slightly different URL/location hash that bypassed deduplication, the DB treated it as un-notified and re-sent the Telegram alert.
2. The AI was erroneously scoring old (e.g., 2024) jobs 9/10 because it prioritized keyword density over freshness, and the ranker lacked a negative penalty for old jobs to push them out of the AI queue.
**Files**: `storage/db.py`, `main.py`, `pipeline/scorer.py`, `pipeline/ranker.py`

---

## [2026-07-06] Hotfix 2: switch gemini-2.0-flash → gemini-3.1-flash-lite (correct final model)
**What**: Changed `MODEL` from `gemini-2.0-flash` to `gemini-3.1-flash-lite` in `scorer.py` and `hackernews.py`.
**Why**: `gemini-2.0-flash` was deprecated by Google in March 2026 — it should not be used. The correct high-volume free tier model as of July 2026 is `gemini-3.1-flash-lite` (GA stable, released May 7 2026).
**Free-tier limits for gemini-3.1-flash-lite** (confirmed via official docs): ~15 RPM, ~1,500 RPD, ~250K TPM. At 130 jobs × 2 runs/day = 260 RPD (17% of daily budget).
**Model history for this project**: Groq llama-4-scout → gemini-2.5-flash (5 RPM + JSON bugs) → gemini-2.0-flash (deprecated) → **gemini-3.1-flash-lite** (final).

---

## [2026-07-06] Hotfix: switch gemini-2.5-flash → gemini-2.0-flash (v5 post-mortem)
**What**: Changed `MODEL` constant from `gemini-2.5-flash` to `gemini-2.0-flash` in `scorer.py` and `hackernews.py`. Bumped `max_output_tokens` from 768 → 1024.
**Why — two bugs found in the v5 live run**:
1. `gemini-2.5-flash` free tier is actually **5 RPM** (not 10-15 as expected). At 4.5s intervals (13.3 req/min) we were hitting quota every ~7 requests and getting 429 RESOURCE_EXHAUSTED errors with 20-34s retry delays.
2. `gemini-2.5-flash` is a **thinking model** — its internal chain-of-thought tokens leak into `response_mime_type=application/json` responses, causing JSON parse errors (`Expecting property name`, `Unterminated string`) on nearly every successful 200 OK response. This is not documented and only discovered from the live run.
`gemini-2.0-flash` is a non-thinking model with 15 RPM — both problems are eliminated.
**Files**: `pipeline/scorer.py`, `sources/hackernews.py`, `references/decisions.md`, `references/architecture.md`, `SKILL.md`
**Status**: Committed to `feat/gemini-scorer` branch.

---

## [2026-07-06] Migrate AI scorer from Groq → Google Gemini 2.5 Flash (branch: feat/gemini-scorer)
**What**:
1. Replaced Groq `llama-4-scout-17b-16e-instruct` with `gemini-2.5-flash` in `pipeline/scorer.py` (model discontinued by Groq)
2. Replaced `groq` Python SDK with `google-genai` SDK across `requirements.txt`, `pipeline/scorer.py`, `sources/hackernews.py`
3. Removed per-run token budget ceiling (200K tokens) — Gemini TPD is ~1.5M/day vs Groq's 500K, so all 130 ranked jobs now get scored per run (27 were being skipped every run)
4. Expanded JD description limit from 3,000 → 6,000 chars for better AI context
5. Made score reasons mandatory for ALL scores (was empty for <6) — enables debugging
6. Native JSON mode via `response_mime_type="application/json"` — eliminates markdown fence stripping
7. Rate limit recalibrated: 4.5s/req (was 5.0s) — fits under Gemini's ~15 RPM with headroom
8. Updated hackernews.py AI comment parser to Gemini, updated all hackernews tests
9. Added ADR-012 to decisions.md, updated architecture.md scorer section
**Why**: Groq discontinued `llama-4-scout-17b-16e-instruct`. All remaining Groq models have 6K–12K TPM limits (vs Scout's 30K) which would require 15–20s delays and ~34 min scoring phases. Gemini 2.5 Flash free tier offers ~1M TPM — no TPM constraint at all.
**Files**: `pipeline/scorer.py`, `sources/hackernews.py`, `requirements.txt`, `profile.yaml`, `tests/test_sources_hackernews.py`, `references/architecture.md`, `references/decisions.md`, `references/changelog.md`
**Status**: Branch `feat/gemini-scorer` — not yet merged. Needs `pip install google-genai` on EC2 and live test run before merge.
## [2026-07-06] Fix Workday lazy-fetch ranking penalty (design flaw)
**What**: Workday jobs entered the pipeline with `description=""` because JDs are lazy-fetched in `scorer.py` after ranking. This meant the ranker operated on title+location only, giving Workday jobs systematically low heuristic scores. With 793 eligible jobs and a 130-job AI cap, Cisco's 130 Workday jobs (and other curated companies) were mostly cut before the AI could score them.
Fix has two parts:
1. **`sources/workday.py`**: Instead of `description=""`, inject a compact synthetic stub at list-fetch time: `"Role: {title[:70]}. Company: {company[:40]}. Location: {loc_short[:30]}."` (~67–149 chars). Stub is built from data already in the list response — zero extra HTTP calls. The scorer's existing `len(desc) < 150` threshold still fires → full JD is still fetched lazily. Stub is hard-capped at 149 chars with per-field truncation + a safety fallback.
2. **`pipeline/ranker.py` + `profile.yaml`**: Added `source_workday_bonus: +2` in `_source_adjustment()`. Workday companies in `companies.yaml` are curated ATS employers (Cisco, Adobe, Samsung, BrowserStack, Sprinklr, etc.) — structurally reliable data.
**Why**: v4 and v5 logs both showed Cisco (130 jobs) being almost entirely cut by the post-ranking AI cap. Root cause: ranker couldn't evaluate Workday jobs fairly without descriptions. This is the same flaw noted in v3 analysis.
**Files**: `sources/workday.py`, `pipeline/ranker.py`, `profile.yaml`
**Status**: Complete

---

## [2026-07-06] Fix few-shot calibration — 5-point scale anchoring + India market context
**What**: Rewrote the scorer's few-shot examples and system prompt to fix systematic under-scoring:
1. Expanded calibration from 2 examples (9+3) to 5 examples (9, 7, 6, 5, 3) — anchors the full useful decision range
2. Replaced the over-specific Go/gRPC/PostgreSQL/Redis/Koinbase score-9 example with a generic "backend intern at fintech" archetype that accepts Go OR TypeScript — prevents the model from penalising non-Go backend roles
3. Added score-7 (Node.js/TypeScript SaaS intern), score-6 (Python remote-first, 2026 batch), score-5 (full-stack frontend-heavy, Bangalore on-site) examples
4. Injected India market context into the system prompt: July 2026 post-peak-hiring, Go fresher roles are rare → TypeScript/Node.js backend intern with remote+0-exp signals should score 6–8, not 3–4
5. Updated `SYSTEM_PROMPT_TOKENS` from 800 → 1200 to reflect the larger system prompt (prevents token budget underestimation, ~89→82 max jobs/run)
**Why**: v4 run sourced 773 jobs, scored 89, but ALL scored ≤4/10 — zero urgents or digests. Root cause was Go-anchoring + missing mid-range calibration making the model treat "best available" TypeScript/Node.js backend roles as bad matches.
**Files**: `pipeline/scorer.py`
**Status**: Complete

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
