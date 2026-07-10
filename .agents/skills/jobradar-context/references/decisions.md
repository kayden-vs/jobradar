# JobRadar — Architecture Decision Records

> Last updated: 2026-07-11

---

## ADR-014: Remove Telegram Heuristic Pre-Filter; Add Source-Level Ranker Boost

**Date**: 2026-07-10
**Decision**: Remove the two-gate keyword heuristic pre-filter from `sources/telegram_channels.py` and replace with a minimal sanity check (40-char minimum). Add a `+3` source-level ranker offset for `telegram_channels` in `pipeline/ranker.py`.
**Context**: After deploying the Telegram source, zero Telegram jobs were ever scoring ≥7. Log analysis (v6_ec2run.log) revealed 5 compounding bugs: (1) the heuristic was dropping 80% of real posts, (2) Gemini was using the channel name as the company name, (3) descriptions were being summarised instead of kept verbatim, (4) `max_output_tokens=2048` was too small for full-text batch responses, (5) Telegram jobs had no source-level ranker boost so they sank below the 150-job AI cap cutoff against ATS jobs with long keyword-rich JDs.
**Rationale**:
- The heuristic's `_JOB_INTENT_KEYWORDS` and `_TECH_ROLE_KEYWORDS` gates were designed for English ATS phrasing. Indian Telegram channels use phrasing like `"applications open"`, `"batch 2025/26 eligible"`, `"drive"`, `"positions available"` — none of which matched. Channels like `@dot_aware`, `@fresheroffcampus`, `@CSE_IT_BCA_MCA_Computer_Jobs` were getting 0/7 posts through.
- The downstream prefilter (`prefilter.py`) and AI scorer already handle noise correctly — prefilter rejects off-topic posts via role/experience/location rules; the scorer gives 1-2/10 to genuine noise. There is nothing to protect by running a heuristic before Gemini for this source.
- The `+3` source boost mirrors the existing Workday `+2` bonus rationale: structurally thin data (short Telegram posts vs long ATS JDs) systematically depresses skill-density scores through no fault of the job quality. The boost corrects the structural bias without affecting relative ranking within the source.
- Gemini `max_output_tokens=4096` is well within the model's 8192 output token limit. The 2048 ceiling was causing silent JSON truncation (silently caught as `JSONDecodeError` → batch dropped).
**Alternatives considered**:
- Improve the heuristic keywords: fragile — Indian channels change phrasing frequently and the heuristic maintenance burden is high for a source with only 63 messages/run.
- Raise the AI cap (`max_ai_jobs_per_run`) instead of a source boost: would increase scorer cost proportionally across all sources, not just Telegram.
**Impact**: 63/63 posts now pass to Gemini (was 19/63). ~56 jobs extracted per run (was ~12). Per-source observability added to `prefilter.py` and `ranker.py` logs to monitor survival rate going forward.

---

## ADR-013: Telegram Job Channels via Telethon MTProto API

**Date**: 2026-07-08
**Decision**: Use Telethon (MTProto API client) to read Telegram job channels instead of HTML scraping `t.me/s/<channel>` pages. Use `StringSession` stored in `.env` for headless operation.
**Context**: Indian Telegram channels (`internfreak`, `dot_aware`, `fresheroffcampus`, etc.) post exclusive off-campus drives and internship links not available on any structured job board. These are high-value, India-specific signals that complement ATS/Naukri coverage.
**Rationale**:
- `t.me/s/<channel>` pages use Cloudflare and CSP headers — scrapers break on HTML structure changes. MTProto is the official API (same protocol the Telegram apps use) — immune to frontend changes.
- `StringSession` stores auth key in a single env var — no `.session` file to manage or persist across EC2 reboots.
- Telethon is pure Python, MIT-licensed, well-maintained (v1.44.0, 15K+ stars). No binary deps.
- `asyncio.run()` bridge in the sync entry point keeps the rest of the pipeline synchronous.
- Per-channel error isolation: `FloodWaitError` → skip channel (don't block pipeline). Invalid username → log and continue.
- Lightweight heuristic pre-filter (job-intent keyword + tech-role keyword) runs before Gemini calls, cutting noise by ~80%.
- Gemini budget impact: ~4-5 calls/run (8-10 RPD — negligible vs 1,500 RPD budget).
**Test results**: 41 raw messages fetched → 8 passed heuristic → 12 structured job dicts extracted. Sample verification: American Express SDE-I, Flipkart GRiD 8.0, Danaher SDE-I — all correctly parsed with title/company/location/url.
**Alternatives considered**:
- HTML scraping `t.me/s/<channel>`: fragile, blocked by Cloudflare, breaks on layout changes.
- Telegram Bot API (`getUpdates`): bots cannot read channel history unless added as admin — not suitable for public channel monitoring.
- RSS feed via third-party services: unreliable, often rate-limited, stale data.
**Final choice**: Telethon with `StringSession` + `asyncio.run()` + shared `gemini_throttle()`.
**New env vars**: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`
**New dependency**: `telethon` (added to `requirements.txt`)

---

## ADR-012: Migrate AI Scorer from Groq to Google Gemini 3.1 Flash-Lite

**Date**: 2026-07-06
**Decision**: Replace Groq (`meta-llama/llama-4-scout-17b-16e-instruct`) with Google Gemini (`gemini-3.1-flash-lite`) as the AI scoring provider, and remove the per-run token budget ceiling.
**Context**: Groq discontinued the `llama-4-scout-17b-16e-instruct` model. All remaining Groq free-tier models have significantly lower TPM limits (6K–12K vs Scout's 30K), which would require 15–20s delays per request and ~34 minute scoring phases.
**Rationale**:
- Gemini 3.1 Flash-Lite free tier: ~15 RPM, ~1,500 RPD, ~250K TPM. At 130 jobs/run × 2 runs = 260 RPD (well within limit).
- RPM controlled via 4.5s inter-request interval (13.3 RPM, safe headroom).
- Native JSON mode (`response_mime_type="application/json"`) eliminates markdown fence stripping.
- Gemini's generous TPD means the old 200K per-run token budget ceiling is no longer needed. All 130 ranked jobs are now scored per run (previously 27 were skipped every run due to budget exhaustion).
- Description truncation expanded from 3,000 → 6,000 chars — better context for long JDs.
- Score reasons are now mandatory for ALL scores (including <6) — enables debugging without burning significant extra tokens.
- The 130-job cap (`max_ai_jobs_per_run`) is retained rather than raising to 150+. The v4 analysis showed the ranker is working well (score spread=40). The problem was scorer calibration (fixed by few-shot examples), not insufficient coverage.
- Also migrated `sources/hackernews.py` comment parser from Groq to Gemini (same model, same rate limiting).
**Why NOT gemini-2.5-flash**: 5 RPM free tier + thinking model (chain-of-thought tokens contaminate JSON mode output, causing parse errors on every 200 OK response).
**Why NOT gemini-2.0-flash**: Deprecated by Google in March 2026. Would stop working at any time.
**Why NOT gemini-3.5-flash**: Tighter free-tier rate limits than 3.1-flash-lite; overkill for 1-10 scoring tasks.
**Final choice**: `gemini-3.1-flash-lite` — GA stable (May 2026), 15 RPM, ~1,500 RPD, JSON mode works cleanly.
**SDK**: `google-genai` (pip install google-genai) — the official unified Python client.
**Env var**: `GEMINI_API_KEY` (previously `GROQ_API_KEY` for scorer — the Groq key stays in `.env` but scorer no longer uses it).
**Alternatives**: Groq qwen3-32b (same 500K TPD but only 6K TPM → 20s delay, 34 min/run). Groq llama-3.1-8b (poor quality). gemini-2.5-flash (5 RPM + JSON parse bugs). gemini-2.0-flash won on all dimensions.

---

Each entry documents a significant design decision: what was decided, why, and what alternatives were considered.

---

## ADR-001: Groq Free Tier over OpenAI / Anthropic

**Date**: 2025 (initial build)
**Decision**: Use Groq's free tier with Llama-4-Scout-17b for AI scoring.
**Context**: Need AI scoring for job relevance. Budget is zero — this is a personal tool.
**Rationale**:
- Groq free tier: 500K tokens/day, 30K TPM — enough for 2 runs/day at 200K tokens each.
- Llama-4 Scout (MoE architecture) quality is between 8B and 70B — good enough for structured scoring.
- Zero cost. OpenAI/Anthropic would cost $5–15/month for equivalent usage.
**Alternatives**: OpenAI GPT-4o-mini ($0.15/1M input), Anthropic Haiku ($0.25/1M). Both rejected for cost.

---

## ADR-002: SQLite over PostgreSQL

**Date**: 2025 (initial build)
**Decision**: Use SQLite for all persistent storage.
**Context**: Single-user tool running on EC2. No concurrent writes.
**Rationale**:
- Zero infrastructure — no DB server to manage.
- SQLite file lives in `data/` alongside logs. Easy to backup, inspect, copy.
- Performance is more than sufficient for ~10K total jobs.
**Alternatives**: PostgreSQL (planned for Phase 1 multi-user). For single-user, it's unnecessary complexity.
**Note**: Roadmap plans PostgreSQL migration when multi-user support is added (Phase 1).

---

## ADR-003: Dual-Hash Deduplication

**Date**: 2025
**Decision**: Use two independent hashes for dedup — title+company+location MD5 and canonical URL MD5.
**Context**: Same job appears across multiple sources with slightly different titles or URLs.
**Rationale**:
- Hash 1 (title+company+location): catches the same job from different sources (e.g., Greenhouse + Serper).
- Hash 2 (canonical URL): catches the same URL with different tracking params or minor title variations.
- Both normalise aggressively: strip company suffixes (Pvt Ltd, Inc), city aliases (Bengaluru→Bangalore), year noise (2025, 2026), punctuation.
- Run-level (in-memory set) + persistent (SQLite lookup) prevents both intra-run and cross-run duplicates.
**Alternatives**: URL-only dedup (misses same job with different tracking URLs). Title-only (misses same job with different titles across sources). Using both catches ~99% of real duplicates.

---

## ADR-004: Heuristic Ranking Before AI Scoring

**Date**: 2026
**Decision**: Add a 6-layer heuristic ranker before the AI scorer to sort jobs by likely relevance.
**Context**: Token budget limits AI scoring to ~89 jobs/run. Previously sorted by recency only — missing dateless jobs and spending budget on mediocre matches.
**Rationale**:
- Ensures the limited AI budget is spent on the strongest candidates first.
- Jobs without `posted_at` dates now compete fairly on skill/role signals instead of being dropped.
- All patterns compiled dynamically from `profile.yaml` — no hardcoded preferences.
- Zero cost (pure Python, no API calls).
**Alternatives**: Sort by recency only (old approach — missed good dateless jobs). Skip ranking entirely and score all (impossible under token budget).

---

## ADR-005: Per-Company ATS Caps

**Date**: 2026
**Decision**: Cap ATS jobs per company per run at 25 (configurable via `hard_reject.ats_per_company_cap`).
**Context**: Large companies (GitLab, Stripe, Spotify) list 200+ jobs. Without caps, a single company consumes the entire scoring budget.
**Rationale**:
- 25 jobs/company is enough to catch relevant roles.
- Prevents any single source from dominating the pipeline.
- Configurable in `profile.yaml` — can be raised for specific needs.
**Alternatives**: No cap (budget consumed by 2–3 large companies). Dynamic cap based on company size (too complex for minimal benefit).

---

## ADR-006: Token Budget Strategy (Two-Layer Rate System)

**Date**: 2026
**Decision**: Dual rate limiting — per-minute throttle (5.0s interval) + per-run budget (200K tokens).
**Context**: Groq free tier has both TPM (30K/min) and TPD (500K/day) limits.
**Rationale**:
- Layer 1 (TPM): 5.0s gap → 12 req/min × ~2,400 tokens = 28,800 TPM (under 30K limit with 4% headroom).
- Layer 2 (per-run): 200K tokens = 80% of (500K TPD ÷ 2 runs/day). Guards against overly permissive prefilter.
- Observed per-job cost: ~2,240 tokens (system prompt ~800 + user prompt ~1,100 + response ~340).
- Max ~89 jobs scored per run.
**Alternatives**: Per-minute only (risk exceeding daily limit). Higher interval (fewer jobs scored). Lower budget (misses good jobs).

---

## ADR-007: Lazy JD Fetch Pattern

**Date**: 2026
**Decision**: Some sources (freshers_blogs, Naukri) return stub descriptions. Full JD is fetched lazily only after the job survives prefilter and reaches the scorer.
**Context**: Fetching full JDs for all ~8,000 raw jobs would be extremely slow and wasteful.
**Rationale**:
- Prefilter drops ~90% with stub descriptions alone (title, tags, metadata are enough for most filter checks).
- Only ~50–150 jobs reach the scorer — fetching full JDs for these is fast.
- Reduces pipeline runtime by ~10x for these sources.
**Alternatives**: Eager fetch (too slow, 8000+ page loads). Skip full JD (AI scores inaccurately on stubs).

---

## ADR-008: Profile-Driven Architecture

**Date**: 2025–2026
**Decision**: All user preferences, filter rules, ranker weights, and source toggles live in `profile.yaml`. No hardcoded preferences in code.
**Context**: Started as a personal tool, but designed for extensibility.
**Rationale**:
- Switching domains (e.g., from backend/Go to cybersecurity) requires zero code changes.
- Multiple users can run with different `profile.yaml` files (`python main.py my_profile.yaml`).
- Each profile gets its own DB and log file (derived from filename).
- Ranker patterns are compiled dynamically from profile at runtime.
**Alternatives**: Hardcode preferences (faster to build, impossible to share). Config in DB (planned for multi-user Phase 1).

---

## ADR-009: Application Tracker via Telegram Bot

**Date**: 2026
**Decision**: Add a standalone Telegram polling bot for tracking job applications (`/applied`, `/responded`, `/status`).
**Context**: Need lightweight application tracking without a web UI.
**Rationale**:
- Telegram is already the notification channel — natural place for tracking commands.
- Standalone bot (`tracker_bot.py`) runs alongside the pipeline, not inside it.
- SQLite `applications` table with status lifecycle: applied → followup_sent → dead | responded.
- Automated follow-up check (`followup_check.py`): sends draft at day 7, marks dead at day 14.
**Alternatives**: Web UI (planned for Phase 2, overkill for personal use). Manual spreadsheet (no automation).

---

## ADR-010: Few-Shot Calibration for AI Scorer

**Date**: 2026
**Decision**: Include two fixed examples (score 9 and score 3) in the AI system prompt.
**Context**: Without calibration, the model's scores drift across runs — sometimes generous, sometimes harsh.
**Rationale**:
- Two examples anchor the scale endpoints, making scores consistent across runs.
- Score 9 example shows what a perfect match looks like.
- Score 3 example shows a clearly poor match.
- Combined with token-saving rules (empty fields for score <6), this keeps response costs low.
**Alternatives**: Zero-shot (inconsistent scores). Fine-tuning (not available on Groq free tier). More examples (diminishing returns, wastes tokens).

---

## ADR-011: Test Suite — Mocking Strategy

**Date**: 2026-07-01
**Decision**: All tests mock HTTP at the `requests.get` / session layer; feedparser entries use `SimpleNamespace`; Groq calls use `unittest.mock.patch`.
**Context**: Sources make real network calls to many external APIs. Tests must be deterministic, fast, and offline.
**Rationale**:
- `@patch("sources.X.requests.get")` with `side_effect` lists allows precise per-call control without real I/O.
- `feedparser` entries must be `SimpleNamespace` objects (not `MagicMock`) because source code calls `re.sub()` on entry attributes directly — `MagicMock` attributes return `MagicMock` objects, causing `TypeError: expected string or bytes-like object`.
- `hiring.cafe` uses `requests.Session` internally (`_get_session()`), not bare `requests.get`. Tests patch both `requests.get` (used by `_fetch_build_id`) and `_get_session` (used by `_fetch_page`) separately.
- `ProfilePatterns` is a `NamedTuple`, not a `dict` — tests access fields by attribute (`.primary_skill_re`), not by string key.
- Module-level caches (`_cached_build_id`, `_session`, `_PLAYWRIGHT_AVAILABLE`) are reset in `setup_method` to prevent inter-test leakage.
**Alternatives**: Real integration tests (too slow, flaky, require live API keys). Full `requests_mock` library (adds a dependency; `unittest.mock` is sufficient).
**Status**: Established pattern — follow for all future source tests.

