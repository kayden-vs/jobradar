---
name: jobradar-context
description: Complete context for the JobRadar automated job discovery pipeline — architecture, modules, decisions, and current state. Read this before any task involving JobRadar code.
---

# JobRadar — Project Context

## What is JobRadar?

JobRadar is a **fully automated job discovery pipeline** built in Python. It aggregates jobs from 16 sources (ATS APIs, job boards, RSS feeds, Google dorking), deduplicates across runs, filters with zero-cost heuristic rules, ranks by relevance, scores the top candidates with AI (Google Gemini), and delivers urgent matches via Telegram. Built for freshers/interns but fully configurable for any role via `profile.yaml`.

## Tech Stack

- **Language**: Python 3.11+
- **AI**: Google Gemini free tier — `gemini-2.0-flash` via `google-genai` SDK
- **Database**: SQLite (via `storage/db.py`)
- **Notifications**: Telegram Bot API (`python-telegram-bot>=21.0`)
- **Config**: YAML (`profile.yaml` for user prefs, `companies.yaml` for ATS slugs)
- **Search**: Serper.dev (Google dorking)
- **Scraping**: `scrapling`, `beautifulsoup4`, `lxml`, `httpx`, `aiohttp`
- **Deployment**: AWS EC2 t2.micro, runs via `run.sh` with auto-shutdown after completion

## Project Structure

```
jobradar/
├── main.py                  # Entry point — orchestrates full pipeline
├── profile.yaml             # User config (roles, skills, location, filters, weights)
├── companies.yaml           # ATS company slugs for 10 platforms
├── .env                     # API keys (GEMINI_API_KEY, GROQ_API_KEY, SERPER, TELEGRAM)
├── requirements.txt         # Python dependencies
├── run.sh                   # EC2 boot script (git pull → pip install → pipeline → followup → shutdown)
│
├── sources/                 # Job fetchers — one file per source
│   ├── ats.py               # 10-platform ATS polling (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Rippling, BambooHR, Recruitee, Personio)
│   ├── workday.py           # Workday — POST-based API (separate pattern)
│   ├── naukri.py            # Naukri.com — keyword × location search with Stage-1 inline filters
│   ├── hirist.py            # Hirist.tech — India niche tech board (currently disabled: TODO test and fix)
│   ├── yc.py                # YC jobs — two-phase scraper (card listing → full JD)
│   ├── internshala.py       # Internshala — optimised plain-HTTP parser
│   ├── freshers_blogs.py    # 8+ Indian fresher blogs — concurrent RSS + lazy JD fetch
│   ├── serper.py            # Tiered Google dork discovery (Tier 1 + Tier 2 budget split)
│   ├── hackernews.py        # HN "Who is Hiring?" — Algolia auto-discovery
│   ├── reddit.py            # Reddit RSS feeds (disabled: returns people-looking-for-work posts)
│   ├── hiringcafe.py        # hiring.cafe — Next.js API, entry-level filtered
│   ├── jobicy.py            # Jobicy.com — remote jobs JSON API
│   ├── remoteok.py          # RemoteOK — JSON API
│   ├── cutshort.py          # Cutshort.io (disabled: public API unreliable)
│   ├── instahyre.py         # Instahyre (disabled: API 404)
│   ├── wellfound.py         # Wellfound (disabled: blocks bots)
│   └── utils.py             # Shared source utilities
│
├── pipeline/                # Processing stages (order matters)
│   ├── dedup.py             # Run-level (in-memory) + persistent (SQLite) dual-hash dedup
│   ├── prefilter.py         # Multi-layer rule-based hard filters (age, experience, location, blacklists, ATS caps)
│   ├── ranker.py            # 6-layer heuristic relevance ranker (profile-driven, zero AI cost)
│   └── scorer.py            # Gemini AI scorer — fully scored (no token budget gate), few-shot calibrated, native JSON mode
│
├── notify/                  # Notification layer
│   ├── telegram_bot.py      # Urgent push alerts + session divider card
│   ├── tracker_bot.py       # Standalone Telegram polling bot (/applied, /responded, /status, /help)
│   ├── followup_check.py    # 7-day followup drafts + 14-day dead marking
│   └── weekly_summary.py    # Friday weekly radar digest (7 insights, source quality, market demand)
│
├── storage/
│   └── db.py                # SQLite schema, dedup functions, job CRUD, run stats, application tracker
│
├── data/                    # Auto-created at runtime (gitignored)
│   ├── <profile>.db         # Per-user SQLite database
│   └── <profile>.log        # Rotating run logs (1MB × 3 files)
│
└── docs/                    # Human-facing documentation
    ├── setup_guide.md       # Full setup walkthrough
    ├── implementation_guide.md  # Deep technical reference
    └── roadmap.md           # Product roadmap (Phase 0–5)
```

## Pipeline Flow

```
16 Sources (concurrent) → ~8,000–9,000 raw jobs
         ↓
Dual-Hash Deduplication → ~600–800 new jobs
         ↓
Rule-Based Pre-Filter (drops ~90–95%) → ~50–150 eligible
         ↓
6-Layer Heuristic Ranker (sorts best-first) → same count, reordered
         ↓
AI Scorer (Gemini 2.5 Flash, up to 130 jobs/run) → score 1–10
         ↓
Score ≥8 → Telegram push  |  Score 6–7 → Session digest  |  Score <6 → DB only
```

## Current State (as of July 2026)

**Working sources (12)**: ATS (10 platforms), Workday, Naukri, YC, Internshala, freshers_blogs, Serper, HN, hiring.cafe, Jobicy, RemoteOK
**Disabled sources (5)**: Cutshort (broken API), Instahyre (API 404), Wellfound (bot blocking), Reddit (wrong content), Hirist (untested/TODO)
**Deployment**: EC2 t2.micro, auto-starts via EventBridge schedule, runs pipeline, then auto-shuts down
**Recent focus**: Gemini migration (branch: feat/gemini-scorer — not yet merged), scorer calibration fix, ranker v3

## Key Numbers

| Metric | Value |
|---|---|
| Gemini model | gemini-2.0-flash (free tier via AI Studio, 15 RPM confirmed) |
| Token budget/run | None (Gemini ~1.5M TPD — no per-run ceiling) |
| Request interval | 4.5s (~13.3 req/min, under Gemini's ~15 RPM) |
| Max AI jobs/run | 130 (all scored — no budget-based skipping) |
| JD description limit | 6,000 chars (was 3,000 with Groq) |
| Serper budget | 25 queries/run (of 2,500/month free) |
| Pipeline duration | ~20–22 minutes total |
| GEMINI_API_KEY | Required in `.env` |

## Deep-Dive References

For module-level detail, DB schema, and API specifics → read `references/architecture.md`
For design decisions and rationale → read `references/decisions.md`
For recent changes → read `references/changelog.md`
