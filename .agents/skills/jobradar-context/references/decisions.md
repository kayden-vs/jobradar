# JobRadar — Architecture Decision Records

> Last updated: 2026-06-30

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
