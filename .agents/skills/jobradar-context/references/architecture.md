# JobRadar — Architecture Reference

> Last updated: 2026-07-08

## Module Map & Key Functions

### `main.py` — Pipeline Orchestrator

| Function | Signature | Purpose |
|---|---|---|
| `run()` | `run(profile_path: str, dry_run: bool = False)` | Main entry point. Loads profile, runs all sources, dedup, prefilter, scorer, notifications, stats. |
| `source_enabled()` | `source_enabled(name: str) -> bool` | Checks `profile.yaml → sources:` block. Non-boolean values treated as enabled. |
| `_print_dry_run_summary()` | `_print_dry_run_summary(profile, db_path, chat_id, profile_path)` | Prints config summary without making API calls. |

CLI: `python main.py [profile.yaml] [--dry-run]`

---

### `sources/ats.py` — Multi-Platform ATS Polling

Polls 10 ATS platforms via public JSON APIs (no auth, no scraping):
- Greenhouse US (`boards.greenhouse.io`), Greenhouse EU (`boards-api.greenhouse.io`)
- Lever (`api.lever.co`)
- Ashby (API endpoint)
- Workable, SmartRecruiters, Rippling, BambooHR, Recruitee, Personio

| Function | Purpose |
|---|---|
| `fetch_all_ats(companies)` | Iterates `companies.yaml`, polls each company's ATS endpoint, returns `list[dict]` |
| `load_companies()` | Loads `companies.yaml` → dict of platform → list of company slugs |

---

### `sources/workday.py` — Workday ATS

POST-based API — different pattern from other ATS (requires tenant, wd_server, site params).

| Function | Purpose |
|---|---|
| `fetch_all_workday(companies, profile)` | Iterates Workday entries in `companies.yaml`, returns `list[dict]` |

---

### `sources/naukri.py` — Naukri.com

India's largest job board. Keyword × location × pagination search.

| Function | Purpose |
|---|---|
| `fetch_naukri(profile)` | Reads `profile.yaml → naukri:` config for keywords/locations. Stage-1 inline filtering. Returns `list[dict]` |
| `lazy_fetch_naukri_detail()` | Called by scorer to fetch full JD for stub descriptions |

---

### `sources/serper.py` — Google Dork Discovery

Tiered Google search via Serper.dev API.

| Key constant | Value | Purpose |
|---|---|---|
| `MAX_SERPER_CALLS` | 25 | Total queries per run |
| `TIER_1_BUDGET` | 10 | Reserved for unique sources (custom career pages, Google Forms) |

| Function | Purpose |
|---|---|
| `fetch_serper_jobs(profile)` | Runs tiered dork queries, extracts job data from search results |

---

### `sources/freshers_blogs.py` — Indian Fresher Blogs (RSS)

Concurrent RSS feed polling (8+ blogs) via `ThreadPoolExecutor`. Uses lazy JD fetch — full page fetched only after job survives prefilter.

| Function | Purpose |
|---|---|
| `fetch_freshers_blogs()` | Returns `list[dict]` with stub descriptions. Tags include `batch_year`. |
| `fetch_full_description(url)` | Lazy-fetches full JD from blog post URL. Called by scorer. |

---

### `sources/hackernews.py` — HN "Who is Hiring?"

Self-healing auto-discovery of monthly thread via Algolia API.

| Function | Purpose |
|---|---|
| `fetch_hn_hiring()` | Finds latest thread, parses comments into job dicts |

---

### `sources/telegram_channels.py` — Indian Telegram Job Channels

Fetches 6 curated public Indian job channels via Telethon MTProto API (not HTML scraping). Unstructured posts parsed by Gemini AI into structured job dicts. Requires `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` in `.env`.

| Key constant | Value | Purpose |
|---|---|---|
| `CHANNELS` | 6 usernames | dot_aware, internfreak, getjobss, fresheroffcampus, jobsandinternshipsupdates, CSE_IT_BCA_MCA_Computer_Jobs |
| `MESSAGES_PER_CHANNEL` | 7 | Messages fetched per channel per run |
| `INTER_CHANNEL_DELAY` | 1.5s | `asyncio.sleep` between channels to avoid FloodWaitError |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Same model as scorer/hackernews |

| Function | Purpose |
|---|---|
| `fetch_telegram_channels()` | Public sync entry point. Uses `asyncio.run()` to bridge to async Telethon. Returns `list[dict]`. Gracefully returns `[]` if env vars missing. |
| `_fetch_all_channels()` | Async: connects via `TelegramClient(StringSession(...))`, calls `get_messages(channel, limit=7)` per channel. FloodWaitError → skip channel. ValueError/UsernameInvalidError → log and skip. |
| `_passes_heuristic(text)` | Lightweight pre-filter: requires job-intent keyword + tech-role keyword, rejects noise patterns (course ads, WhatsApp promos, government exams). Zero AI cost. |
| `_parse_posts_with_gemini(posts)` | Batches of 5 posts → Gemini JSON extraction → structured job dicts. Uses shared `gemini_throttle()`. |

**Budget impact**: ~42 messages → ~20 filtered → ~4-5 Gemini calls/run (8-10 RPD impact — negligible vs 1,500 RPD budget).

**Tools**: `tools/telethon_login.py` (one-time interactive session string generation), `tools/test_telegram_source.py` (standalone validation).

---

### `sources/hiringcafe.py` — hiring.cafe

Next.js internal API. Server-side filtered by seniority, department, location.

| Function | Purpose |
|---|---|
| `fetch_hiringcafe()` | Returns ~50 high-signal entry-level jobs per run |

---

### `pipeline/dedup.py` — Deduplication

| Function | Signature | Purpose |
|---|---|---|
| `deduplicate()` | `deduplicate(jobs: list[dict], db_path: str \| None) -> list[dict]` | Run-level in-memory + persistent SQLite dedup |

---

### `pipeline/prefilter.py` — Rule-Based Pre-Filter

Drops ~90–95% of jobs with zero AI cost. Checks (in order):
1. Age filter (`max_job_age_days`, default 45)
2. Expiry signals (regex for "application closed", "position filled", etc.)
3. ATS title allowlist (must contain tech signal)
4. ATS location filter (rejects US/UK/EU structured fields)
5. RSS tag filter (experience, batch, location tags)
6. Experience keyword scan (hard reject on "2+ years", "senior engineer", etc.)
7. Location description scan
8. Company/role blacklists
9. ATS per-company cap (default 25)

| Function | Signature | Purpose |
|---|---|---|
| `load_profile()` | `load_profile(path="profile.yaml") -> dict` | Loads YAML profile |
| `prefilter()` | `prefilter(jobs: list[dict], profile: dict) -> list[dict]` | Applies all filter stages |
| `_parse_posted_at()` | `_parse_posted_at(posted_at: str) -> datetime \| None` | Handles ISO, RFC, relative ("3 days ago"), epoch |

---

### `pipeline/ranker.py` — 6-Layer Heuristic Ranker (v2)

Fully profile-driven — all patterns compiled from `profile.yaml` at runtime.

**Layers:**
1. **Positive bonuses** — skill in title/desc, backend signal, fresher signal, domain, project, recency
2. **Skill Density** — counts distinct skills found (Go + gRPC + PostgreSQL = 3 hits)
3. **Concordance & Multiplicative** — same skill in title AND desc, holy trinity (fresher + primary skill + backend in title), title richness
4. **Penalties** — no skill match, generic title, bodyshop, ATS stub, role mismatch
5. **Source-aware** — per-source offsets (Internshala stipend bonus, Naukri stub penalty, etc.)
6. **Location Affinity** — India city / Remote / WFH bonus

| Function | Signature | Purpose |
|---|---|---|
| `rank_eligible_jobs()` | `rank_eligible_jobs(jobs: list[dict], profile: dict) -> list[dict]` | Returns jobs sorted by heuristic score (descending) |
| `build_profile_patterns()` | `build_profile_patterns(profile: dict) -> ProfilePatterns` | Compiles regexes from profile |

All weights configurable in `profile.yaml → ranker_weights:`.

---

### `pipeline/scorer.py` — AI Scorer (Gemini)

| Key constant | Value |
|---|---|
| `MODEL` | `gemini-3.1-flash-lite` |
| `REQ_INTERVAL` | 4.5s (~13.3 req/min, under Gemini's ~15 RPM) |
| `SYSTEM_PROMPT_TOKENS` | ~1,400 (system prompt + 5 few-shot examples) |
| `RESPONSE_TOKENS` | ~500 (full reasons always returned) |
| `DESC_CHAR_LIMIT` | 6,000 chars (doubled from old Groq limit of 3,000) |
| `CHARS_PER_TOKEN` | 4 |

Rate limits (Gemini free tier, approximate):
- TPM: ~1,000,000 — no TPM bottleneck at all
- TPD: ~1,500,000 — no per-run token budget needed
- RPM: ~10–15 — controlled via REQ_INTERVAL
- RPD: ~1,500 — enough for 3+ runs/day at 130 jobs each

| Function | Signature | Purpose |
|---|---|---|
| `score_all()` | `score_all(jobs: list[dict], profile: dict, db_path: str) -> tuple[list, list, list]` | Returns (urgent, digest, low) job lists. Calls ranker first, then scores ALL jobs up to max_ai_jobs_per_run (no token budget gate). |

Features:
- **5-point few-shot calibration** (score 9, 7, 6, 5, 3 examples) anchors full decision range
- **Native JSON mode** via `response_mime_type="application/json"` — no markdown fence stripping
- **Mandatory score reasons** for ALL scores (including low scores) — fully debuggable
- **Pre-Gemini expiry scan** before each API call (zero token cost)
- **Lazy JD fetch** for freshers_blogs, Naukri, and Workday stub descriptions
- **No token budget gate** — all ranked jobs scored up to max_ai_jobs_per_run cap
- Uses `google-genai` SDK (`pip install google-genai`); needs `GEMINI_API_KEY` in `.env`

---

### `storage/db.py` — SQLite Database

| Function | Purpose |
|---|---|
| `init_db(db_path)` | Creates tables + indexes if not exist. Safe to call every run. |
| `make_job_id(job)` | MD5 of normalised title+company+location |
| `make_url_id(job)` | MD5 of canonical URL (strips tracking params) |
| `is_duplicate(job, db_path)` | Checks both hash keys |
| `save_job(job, score, reason, ...)` | INSERT OR IGNORE — never resets notified flag |
| `save_run_stats(...)` | Persists per-run pipeline statistics |
| `was_weekly_summary_sent()` | ISO week guard for Friday digest |
| `log_application(url, company, title)` | Application tracker — INSERT with UNIQUE url |
| `get_applications_pending_followup()` | Apps 7+ days old, no followup |
| `get_applications_pending_dead()` | Apps 14+ days old, still active |

---

## Database Schema

### `jobs` table
```sql
id           TEXT PRIMARY KEY    -- MD5(normalised title+company+location)
url_id       TEXT                -- MD5(canonical URL)
title        TEXT
company      TEXT
location     TEXT
description  TEXT
url          TEXT
source       TEXT
salary       TEXT
posted_at    TEXT
seen_at      TEXT
score        INTEGER DEFAULT 0
score_reason TEXT
highlights   TEXT
red_flags    TEXT
notified     INTEGER DEFAULT 0  -- 0=no, 1=telegram, 2=digest
```

### `run_stats` table
```sql
id              INTEGER PRIMARY KEY AUTOINCREMENT
run_at          TEXT NOT NULL
raw_fetched     INTEGER DEFAULT 0
after_dedup     INTEGER DEFAULT 0
after_prefilter INTEGER DEFAULT 0
after_scoring   INTEGER DEFAULT 0
urgent_count    INTEGER DEFAULT 0
digest_count    INTEGER DEFAULT 0
source_breakdown TEXT DEFAULT NULL  -- JSON dict: {"greenhouse": 74, "serper": 6}
```

### `weekly_summaries` table
```sql
week_key TEXT PRIMARY KEY  -- ISO year-week e.g. '2026-W25'
sent_at  TEXT NOT NULL
```

### `applications` table
```sql
id               INTEGER PRIMARY KEY AUTOINCREMENT
url              TEXT NOT NULL UNIQUE
company          TEXT DEFAULT ''
title            TEXT DEFAULT ''
applied_at       TEXT NOT NULL
status           TEXT DEFAULT 'applied'  -- applied | followup_sent | dead | responded
followup_sent_at TEXT DEFAULT NULL
notes            TEXT DEFAULT ''
```

---

## Job Dict Shape

Every pipeline stage works with `list[dict]`. Standard keys:

```python
{
    "title":       str,   # Job title
    "company":     str,   # Company name
    "location":    str,   # Location string
    "url":         str,   # Apply/detail URL
    "description": str,   # Full JD text (may be stub for lazy-fetch sources)
    "source":      str,   # e.g. "greenhouse", "naukri", "serper"
    "salary":      str,   # Salary/stipend string (optional)
    "posted_at":   str,   # Date string in various formats (optional)
    # Source-specific optional keys:
    "tags":        list,  # RSS tags (freshers_blogs)
    "batch_year":  int,   # Graduation year tag (freshers_blogs)
    "stipend":     int,   # Monthly stipend INR (internshala)
}
```

---

## Configuration Structure (`profile.yaml`)

| Top-level key | Purpose |
|---|---|
| `sources:` | Toggle each source on/off (bool) |
| `candidate:` | Name, email, roles (primary/secondary), experience, skills (strong/learning), projects, education, location, industries, salary |
| `hard_reject:` | `max_job_age_days`, `ats_per_company_cap`, `max_ai_jobs_per_run`, `experience_keywords`, `company_blacklist`, `role_blacklist` |
| `scoring_weights:` | Weights used in AI prompt (golang_mentioned, backend_focused, etc.) |
| `ranker_weights:` | All heuristic ranker numeric config (see ranker.py docstring) |
| `naukri:` | Keywords, locations, pages, experience range |
| `hirist:` | Keywords, min/max exp, pages, fetch_details flag |

---

## API Integrations

| Service | Key env var | Free tier limits |
|---|---|---|
| **Gemini** | `GEMINI_API_KEY` | ~15 RPM, ~1,500 RPD, ~250K TPM |
| **Serper.dev** | `SERPER_API_KEY` | 2,500 queries/month |
| **Telegram Bot** | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Unlimited |
| **Telegram MTProto (Telethon)** | `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` + `TELEGRAM_SESSION_STRING` | Free, official API. StringSession — no file to persist. One-time login via `tools/telethon_login.py`. |

---

## Deployment

- **EC2 t2.micro** (AWS free tier)
- **EventBridge** schedule triggers instance start (8 AM and 6 PM IST)
- `run.sh` runs on boot: git pull → pip install → start tracker bot → pipeline → followup check → auto-shutdown
- Pipeline timeout: 60 minutes (via `timeout 3600`)
- On failure/timeout: sends Telegram error alert with last 20 log lines
