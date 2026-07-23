<div align="center">
  <img src="docs/jobradar_logo.png" alt="JobRadar Logo" width="200"/>
  <h1>JobRadar</h1>
  <p><strong>Pulls jobs from 17 sources, scores them with AI, drops the irrelevant ones, and pings you on Telegram with what's actually worth applying to.</strong></p>
</div>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white" alt="Python 3.11+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat" alt="MIT License"/></a>
  <a href="#-17-job-sources"><img src="https://img.shields.io/badge/Job%20Sources-17-7c3aed?style=flat" alt="17 Job Sources"/></a>
  <a href="#-performance--api-usage"><img src="https://img.shields.io/badge/API%20Cost-100%25%20Free%20Tier-16a34a?style=flat" alt="100% Free Tier"/></a>
  <a href="docs/setup_guide.md"><img src="https://img.shields.io/badge/docs-Setup%20Guide-0ea5e9?style=flat" alt="Setup Guide"/></a>
</p>

---

The good jobs are never where you'd expect. They're in a Telegram channel with 40 members, buried on some startup's own `/careers` page, or hiding behind a Google Form that no job board ever indexed. By the time they show up on LinkedIn, they're already closed.

JobRadar polls 17 sources twice a day, pulls ~9,000 raw listings, throws away the 95% that don't matter, AI-scores the rest, and sends the winners to your Telegram. The whole thing runs on free-tier APIs.

It's tuned for freshers and early-career devs out of the box, but everything it looks for (skills, industries, project signals) comes from your `profile.yaml`. Change the file, and the entire pipeline adapts. No code changes.

> I built this while looking for my first backend role. The morning routine was always the same: open six tabs, scroll through the same stale listings, maybe find something good that closed two days ago. The jobs worth applying to were never on page one of anything. They were on some startup's careers page, or in a Telegram channel with 200 people, gone before I even knew to look. I got tired of being the bottleneck in my own job search, so I automated it.

<div align="center">
  <img src="docs/telegram_ss.jpg" width="260" alt="A JobRadar Telegram alert showing an AI-scored job match"/>
  <br/>
  <sub><i>What actually shows up in Telegram when it finds something worth your time.</i></sub>
</div>

<br/>

<div align="center">
  <a href="docs/brag.mp4">
    <video src="https://github.com/user-attachments/assets/7d7f1f64-dea0-44e3-bc70-d2fafb59adfb" width="720" autoplay muted loop playsinline poster="docs/brag.jpg">
      <img src="docs/brag.jpg" width="720" alt="JobRadar launch video — 17 sources, 9000 listings, 2-6 Telegram alerts"/>
    </video>
  </a>
  <br/>
</div>

> [!TIP]
> **New here?** Start with [`docs/setup_guide.md`](docs/setup_guide.md). It covers everything from API keys to your first run.

---

## Table of Contents

- [How It Works](#-how-it-works)
- [Features](#-features)
  - [17 Job Sources](#-17-job-sources)
  - [Smart Pre-Filter](#️-smart-pre-filter)
  - [Heuristic Relevance Ranker](#-heuristic-relevance-ranker)
  - [AI Scorer](#-ai-scorer)
  - [Multi-Key Deduplication](#-multi-key-deduplication)
- [Project Structure](#️-project-structure)
- [Quick Start](#-quick-start)
- [Automation](#-automation)
- [Performance & API Usage](#-performance--api-usage)
- [Maintenance & Tuning](#️-maintenance--tuning)
- [Future Goals](#-future-goals)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🔄 How It Works

```
┌─────────────────────────────────────────────────┐
│          17 Job Sources (Concurrent)            │
│  ATS APIs · Workday · YC · hackernews · Naukri  │
│    hiringcafe · Blogs RSS · Serper · HN · More  │
└────────────────────────┬────────────────────────┘
                         │ ~8,000–9,000 raw jobs
                         ▼
┌─────────────────────────────────────────────────┐
│            Multi-Key Deduplication              │
│  Title+Company+Location MD5 · Canonical URL MD5 │
│    Run-level + SQLite persistent — never repeat │
└────────────────────────┬────────────────────────┘
                         │ ~600–800 new jobs
                         ▼
┌─────────────────────────────────────────────────┐
│          Smart Rule-Based Pre-Filter            │
│  Expiry · Blacklists · ATS Allowlist · Location │
│        RSS Tags · Experience · Company Cap      │
│          Drops ~90–95% with zero AI cost        │
└────────────────────────┬────────────────────────┘
                         │ ~50–150 eligible jobs
                         ▼
┌─────────────────────────────────────────────────┐
│          Heuristic Relevance Ranking            │
│     Go/TS stack · Fintech · Fresher · Recency   │
│          Best-fit jobs scored first, free       │
└────────────────────────┬────────────────────────┘
                         │ ranked, best-first
                         ▼
┌─────────────────────────────────────────────────┐
│       AI Scorer — Gemini 3.1 Flash-Lite         │
│    Native JSON mode · 4.5s throttle (~13 RPM)   │
│  130 jobs/run · few-shot calibrated 1–10 scale  │
└────────────────────────┬────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
 Score ≥ 8 (Urgent)            Score 6–7 (Digest)
 Instant Telegram push         Session summary card
```

---

## ✨ Features

### 🔌 17 Job Sources

**Structured ATS APIs** - direct API polling of 10 platforms. No scraping, no headless browser, no JS rendering:

| Platform | Example Companies | Notes |
|---|---|---|
| **Greenhouse US** | Razorpay, PhonePe, Stripe, GitLab, Cloudflare, MongoDB | `?content=true` returns full JD in one call |
| **Greenhouse EU** | Groww | European instance, separate endpoint |
| **Lever** | Meesho, CRED, Paytm, Spotify, Binance | Full JD + lists in a single response |
| **Ashby** | Navi, Linear, Notion, Supabase, Railway | `descriptionPlain` pre-stripped |
| **Workable** | Juspay, Gridlines, Apna | Secondary detail call for full JD |
| **SmartRecruiters** | Upstox, Freshworks, Canva, Cars24, ixigo | Structured per-section JD |
| **Rippling** | Axio, Multiplier, Dub | Job UUID detail endpoint |
| **BambooHR** | Urban Company, Shadowfax, Google, Meta | Subdomain-based URLs |
| **Recruitee** | Unstop, Salesforce, IBM | Inline HTML description in list response |
| **Personio** | Open Financial, Amazon, Basecamp | Public XML feed, no auth |
| **Workday** | Adobe, Samsung, BrowserStack, Cisco, Sprinklr | POST-based API with lazy JD fetch; requires pre-discovered tenant/server/site |

All companies live in `companies.yaml`. There's a per-company cap so a single large org (looking at you, GitLab with 300+ open roles) doesn't eat up the entire scoring budget.

**Additional sources:**

| Source | Description |
|---|---|
| **Naukri.com** | India's largest job board. 10 keywords × 3 locations × 2 pages = up to 1,200 raw cards per run; Stage-1 filters applied inline |
| **Hirist.tech** | India-specific niche tech board targeting backend, Go, Python, and TypeScript roles |
| **Y Combinator Jobs** | Two-phase scraper (card listing → full JD). High-signal for early-stage startups globally |
| **Internshala** | India's #1 internship platform. Optimised plain-HTTP parser, bypasses browser overhead entirely |
| **Fresher Blogs RSS** | 8+ Indian fresher blogs via concurrent `ThreadPoolExecutor`. Lazy JD fetch, full pages only fetched after a job survives prefilter |
| **Serper.dev** | Tiered Google dork discovery focused exclusively on what ATS APIs can't find: custom `/careers` pages, hidden applications (Forms, Notion), India-specific ATS |
| **HackerNews "Who is Hiring?"** | Parses monthly HN thread via Algolia API. Auto-discovers the latest thread, no manual updates needed |
| **Reddit Job Feeds** | r/cscareerquestions, r/IndiaJobs, and related subreddits via RSS |
| **Jobicy / RemoteOK** | Remote jobs JSON APIs. Good for catching remote-first companies open to India timezone candidates |
| **hiring.cafe** | Aggregated ATS jobs via internal Next.js API. Server-side filtered by seniority, department, location. ~50 high-signal jobs per run |
| **Telegram Channels** | 9 curated Indian job channels via **Telethon MTProto API**. Reads messages as structured objects, won't break when Telegram changes their UI. Channels: `@dot_aware` · `@internfreak` · `@getjobss` · `@fresheroffcampus` · `@jobsandinternshipsupdates` · `@CSE_IT_BCA_MCA_Computer_Jobs` · `@jobsinternshipswale` · `@jobsandinternshipsindia` · `@gocareers`. Posts parsed by Gemini AI into structured job dicts. One-time setup required, see [Quick Start → Step 3](#3-optional-telegram-channels-source). |

---

### 🛡️ Smart Pre-Filter

This is where most of the cost savings happen. Pure Python, no API calls, no network requests. Kills **~90-95%** of listings before any AI ever sees them:

| Check | What it catches |
|---|---|
| **Age filter** | Jobs older than `max_job_age_days` (default 45 days). Handles ISO dates, RFC dates, relative strings, and Unix epoch |
| **Expiry signals** | Title/description regex for "application closed", "position filled", "last date: [past date]", etc. |
| **ATS title allowlist** | ATS titles must contain a recognised tech signal (engineer, backend, golang, intern, etc.) |
| **ATS location filter** | Instantly rejects US/UK/EU structured location fields; passes India/Remote/ambiguous |
| **RSS tag filter** | Zero-cost intersection check on experience, batch, and location tags from RSS metadata. No page fetches needed |
| **Experience keyword scan** | Hard rejects descriptions containing "2+ years", "senior engineer", "tech lead", etc. |
| **Location description scan** | Rejects jobs explicitly requiring on-site in non-India geographies |
| **Company/role blacklists** | Configurable lists in `profile.yaml` |
| **ATS company cap** | Max N jobs per company per run (default 25) so one large org doesn't eat the whole scoring budget |

---

### 🏆 Heuristic Relevance Ranker

Before anything hits the AI, every surviving job gets a quick relevance score in pure Python. This decides the order jobs enter the scorer, so the best-looking matches get scored first and the budget isn't wasted on garbage.

The ranker builds all its detection patterns from `profile.yaml` at startup. If you switch from backend to cybersecurity, just update your skills and industries in the config. The ranker picks it up on the next run, no code changes.

**Three scoring layers:**

| Layer | What it does |
|---|---|
| **Layer 1 — Positive bonuses** | Primary skill in title (+5), secondary skill (+3/+1), backend/API (+2), fresher role (+2), high-priority domain (+3), project signals (+2 each), recency (+4/+2/+1) |
| **Layer 2 — Penalties + synergies** | No skill match (−3), generic title (−2), bodyshop company (−1), ATS stub desc (−2); synergy bonuses for primary-skill+domain (+3) and primary-skill+project (+2) |
| **Layer 3 — Source offsets** | Internshala with stipend ≥ ₹10k (+2), fresher blog batch tag match (+1), Naukri stub desc (−1), Serper dork result (−1) |

> [!NOTE]
> Jobs with no `posted_at` date are **not penalised**. They compete on skill/role signals alone. This avoids silently dropping good jobs that don't expose a date (common with Naukri and some ATS endpoints).

All numeric weights are configurable in `profile.yaml → ranker_weights:` without touching code.

---

### 🤖 AI Scorer

**Model**: `gemini-3.1-flash-lite` on Google's free tier (AI Studio). It's the lite variant, fast enough for bulk scoring and the free tier is generous enough that you'll never hit a wall with normal usage.

**Rate limiting:**

| Layer | Mechanism | Value |
|---|---|---|
| Per-minute (RPM) | `REQ_INTERVAL` throttle | 4.5s gap → ~13.3 req/min (under ~15 RPM limit) |
| TPM | Effectively none | Gemini free tier: ~1,000,000 TPM — no bottleneck |
| Daily ceiling | `max_ai_jobs_per_run` | 130 jobs max (all scored — no token budget gate needed) |

**Worth knowing:**
- **Few-shot calibration** with 5 anchor examples (scores 9, 7, 6, 5, 3) so the model has concrete reference points instead of vibing
- **Every score comes with a reason**, even the low ones. Makes it easy to spot when the AI is being dumb
- **Native JSON mode** (`response_mime_type="application/json"`) so you never get markdown-wrapped JSON back
- **Pre-AI expiry check** scans the description for "position filled" / "applications closed" before wasting an API call
- **6,000 char JD limit** (up from 3,000 when this used Groq). Longer context = better scoring for detailed JDs

**Score buckets:**

| Score | Action |
|---|---|
| **8–10** | Urgent — instant Telegram push notification |
| **6–7** | Digest — included in session summary card |
| **5** | Persisted to DB, not notified |
| **< 5** | Dropped |

---

### 🔗 Multi-Key Deduplication

When you're pulling from 17 sources, the same job shows up a lot. This handles it:

- **Hash 1 — Normalised Title+Company+Location MD5** — collapses `Pvt Ltd` / `Private Limited` / `Inc.`, city aliases (`Bengaluru → bangalore`), year noise in titles, and whitespace
- **Hash 2 — Canonical URL MD5** — strips `utm_*`, `ref`, `source`, and other tracking parameters
- **Run-level** — in-memory dedup within the current run (same job from multiple sources)
- **Persistent** — SQLite lookup against all previously seen jobs

---

## 🗂️ Project Structure

```
jobradar/
│
├── main.py                    # Entry point — orchestrates the full pipeline
│
├── profile.yaml               # ← YOUR MAIN CONFIG FILE (roles, skills, location, filters)
├── companies.yaml             # ATS company slugs across 9 platforms
│
├── sources/                   # Job fetchers — one file per source
│   ├── ats.py                 # 9-platform ATS polling (Greenhouse, Lever, Ashby, etc.)
│   ├── workday.py             # Workday ATS — POST-based API with lazy JD fetch
│   ├── naukri.py              # Naukri.com — Stage-1 filtered search
│   ├── hirist.py              # Hirist.tech — India niche tech board
│   ├── yc.py                  # YC jobs board — two-phase scraper
│   ├── internshala.py         # Internshala — optimised plain-HTTP scraper
│   ├── freshers_blogs.py      # 8+ Indian fresher blogs — concurrent RSS + lazy JD fetch
│   ├── serper.py              # Tiered Google dork discovery
│   ├── hackernews.py          # HN "Who is Hiring?" — Algolia auto-discovery
│   ├── reddit.py              # Reddit RSS feeds
│   ├── jobicy.py              # Jobicy.com — remote jobs JSON API
│   ├── remoteok.py            # RemoteOK — JSON API
│   ├── hiringcafe.py          # hiring.cafe — Next.js API, entry-level filtered
│   ├── telegram_channels.py   # 6 Indian Telegram channels — Telethon MTProto API + Gemini parsing
│   ├── cutshort.py            # Cutshort.io API (currently disabled)
│   ├── instahyre.py           # Instahyre API + scraper fallback (currently disabled)
│   └── wellfound.py           # Wellfound/AngelList (currently disabled — blocks bots)
│
├── pipeline/                  # Processing stages
│   ├── dedup.py               # Run-level + persistent SQLite dual-hash deduplication
│   ├── prefilter.py           # Multi-layer rule-based hard filters (zero AI cost)
│   ├── ranker.py              # Heuristic relevance ranker — sorts jobs before AI scoring
│   └── scorer.py              # Gemini AI scorer — native JSON mode, few-shot calibrated, no token budget gate
│
├── notify/
│   └── telegram_bot.py        # Urgent push alerts + session digest card
│
├── storage/
│   └── db.py                  # SQLite schema + dual-hash CRUD helpers
│
├── data/                      # Auto-created at runtime
│   ├── <profile>.db           # Per-user SQLite database
│   └── <profile>.log          # Rotating run logs (1MB × 3 files)
│
├── tools/
│   ├── telethon_login.py      # One-time interactive login to generate TELEGRAM_SESSION_STRING
│   └── test_telegram_source.py  # Standalone test for Telegram channels source
│
├── docs/
│   ├── setup_guide.md         # Complete setup & customisation guide
│   └── telegram_ss.jpg        # Example Telegram alert screenshot
│
├── requirements.txt
└── .env                       # API keys (never commit)
```

---

## 🚀 Quick Start

> [!NOTE]
> This is the short version. For the full walkthrough (API keys, `profile.yaml` reference, Naukri config, ranker weights, troubleshooting), see **[`docs/setup_guide.md`](docs/setup_guide.md)**.

### 1. Clone & Install

```bash
git clone https://github.com/your-username/jobradar.git
cd jobradar
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
GEMINI_API_KEY=AIzaSy_xxxxxxxxxxxxxxxxxxxxxxxx
SERPER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

### 3. *(Optional)* Telegram Channels Source

Skip this if you don't care about Telegram channel sources. If you do want them, there's a one-time login step, and after that everything runs headless (works fine on EC2).

**Why MTProto instead of scraping?** Because `t.me/s/<channel>` is behind Cloudflare and the HTML structure changes all the time. Telethon talks to Telegram's actual API, so you get clean `message.text` + `message.date` objects directly. No HTML parsing, nothing breaks when Telegram redesigns their web preview.

**Step 1** — Get free API credentials from [my.telegram.org](https://my.telegram.org):
> Sign in → "API development tools" → create an app → copy `api_id` (integer) and `api_hash`

**Step 2** — Add to `.env`:
```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
```

**Step 3** — Run the login script **once locally** (needs OTP from your Telegram app):
```bash
python tools/telethon_login.py
```
This prints a `StringSession` string and offers to auto-append it to `.env`:
```env
TELEGRAM_SESSION_STRING=1BQANOTEuA...
```
On EC2: copy all three vars to your server's `.env`. No `.session` file to manage.

> [!CAUTION]
> The session string is equivalent to your Telegram login credentials — treat it like a password. It's already covered by `.gitignore` via `.env`.

**Step 4** — Enable the source in `profile.yaml`:
```yaml
sources:
  telegram_channels: true
```

### 4. Configure `profile.yaml`

Edit to match your skills, target roles, locations, and hard-reject rules:

```yaml
candidate:
  name: "Your Name"
  roles:
    primary:
      - "Backend Engineering Intern"
      - "Go Developer Intern"
  skills:
    strong: ["Go", "TypeScript", "PostgreSQL", "Redis", "Docker"]
    learning: ["Kubernetes", "AWS"]
  location:
    base: "Kolkata, India"
    acceptable: ["Remote", "Bangalore", "Mumbai"]
    hard_reject: ["US only", "UK only", "Europe only"]

hard_reject:
  max_job_age_days: 45
  experience_keywords:
    - "2+ years"
    - "senior engineer"
    - "tech lead"
```

Full field reference → [`docs/setup_guide.md`](docs/setup_guide.md)

### 5. Validate (Dry Run)

```bash
python main.py profile.yaml --dry-run
```

Prints your config summary and confirms the DB initializes. No API calls, no Telegram messages, nothing leaves your machine.

### 6. Run

```bash
python main.py
```

---

## ⏰ Automation

### Option A — Linux Cron (WSL / EC2)

```bash
# Run at 8 AM and 6 PM daily
0 8,18 * * * cd /path/to/jobradar && ./run.sh >> data/cron.log 2>&1
```

### Option B — Windows Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "wsl" -Argument "-d archlinux -- bash /home/user/jobradar/run.sh"
$trigger = New-ScheduledTaskTrigger -Daily -At "8:00AM"
Register-ScheduledTask -TaskName "JobRadar" -Action $action -Trigger $trigger -RunLevel Highest
```

---

## 📊 Performance & API Usage

### What a typical run looks like

| Stage | Count | Time | Notes |
|:---|:---|:---|:---|
| **Raw jobs fetched** | ~8,000–9,000 | ~9–11 min | ATS polling is the bottleneck; Naukri Stage-1 alone scans 1,200 listings |
| **After deduplication** | ~600–800 new | < 1 sec | Fast dual-hash SQLite lookups |
| **After pre-filter** | ~100–150 eligible | < 1 sec | Rule-based, zero AI cost |
| **After heuristic ranking** | same count, sorted | < 1 sec | Pure Python, no network |
| **After AI scorer** | up to 130 scored | ~10 min | 4.5s/request, 130 job cap, no token budget gate |
| **Alerts delivered** | 2–6 urgent | < 1 sec | Telegram push for score ≥ 8 |
| **Total pipeline** | | **~20–22 min** | |

### Free-Tier API Usage

| API | Usage per run | Free tier | Headroom |
|:---|:---|:---|:---|
| **Gemini (3.1-flash-lite)** | ~455K tokens | ~1.5M tokens/day | ~1,500 RPD / ~15 RPM free tier |
| **Serper.dev** | 25 queries | 2,500 queries/month | 1,500/month = 60% of free tier |
| **Telegram Bot** | ~10–15 messages | Unlimited | Free |
| **Telegram MTProto** | ~42 messages fetched, ~5 Gemini calls | Unlimited (public channels) | Free — official API, no rate issues |

**Gemini rate limits** (free tier, as of writing):
- **RPM**: ~15 req/min limit. `REQ_INTERVAL = 4.5s` keeps us at ~13.3 RPM with headroom
- **TPM**: ~250K tokens/min limit. We use ~31K TPM (12%). Not even close
- **RPD**: ~1,500 req/day limit. 130 jobs × 2 runs = 260 RPD (17%). Plenty of room

---

## 🛠️ Maintenance & Tuning

**Adding ATS companies** - just add the slug to the right section in `companies.yaml`. Verify the endpoint actually works first:

```bash
# Greenhouse
curl -s "https://boards.greenhouse.io/v1/boards/SLUG/jobs" | python -m json.tool | head -5
# Lever
curl -s "https://api.lever.co/v0/postings/SLUG" | python -m json.tool | head -5
# SmartRecruiters
curl -s "https://api.smartrecruiters.com/v1/companies/SLUG/postings" | python -m json.tool | head -5
# Workday (POST-based, needs tenant/server/site)
curl -s -X POST "https://TENANT.WDSERVER.myworkdayjobs.com/wday/cxs/TENANT/SITE/jobs" \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -H "Origin: https://TENANT.WDSERVER.myworkdayjobs.com" \
  -d '{"appliedFacets":{},"limit":3,"offset":0,"searchText":""}' | python -m json.tool | head -10
```

**Too many junk jobs getting through?** Tighten `hard_reject.experience_keywords` and `hard_reject.role_blacklist` in `profile.yaml`.

**Ranker not prioritizing well?** Edit the weights in `profile.yaml` under `ranker_weights:`. All the bonus/penalty numbers are right there, no code to touch.

**Switching domains** (say you're into cybersecurity or data engineering now) - update `candidate.skills`, `candidate.industries`, and `candidate.projects.relevance_signal` in `profile.yaml`. The whole pipeline adapts.

**Want more Naukri coverage?** Add keywords or locations under `profile.yaml` in the `naukri:` section. Each keyword × location combo gives you 2 pages × 20 listings.

**Serper budget** - `MAX_SERPER_CALLS` (default 25) and `TIER_1_BUDGET` (default 10) live in `sources/serper.py`. Tier 1 budget makes sure the high-value dorks always run even if you lower the total cap.

**Multiple people using this?** Run `python main.py my_profile.yaml` with a different profile file. Each profile gets its own DB and log file under `data/`.

---

## 🔭 Future Goals

Things I want to get to, roughly in order:

1. **Multi-profile support** - move to a `profiles/` directory where each `.yaml` is fully self-contained. One codebase, multiple people, one hosted instance.

2. **Fix the broken sources** - Wellfound, Instahyre, and Cutshort are disabled right now because they block bots or have flaky APIs. Need to figure out headless browser or proper API workarounds.

3. **Application tracking + insights** - a simple web UI to track where you've applied, which companies are hiring for your stack, and which sources actually produce good matches.

4. **Make it work well outside India** - right now the source mix is heavily India-focused. Want to make those opt-in and add better global coverage.

5. **Host it publicly** - if the above comes together, run it as a service where people just upload a profile and connect Telegram. No setup, no server.

---

## 🤝 Contributing

PRs welcome. The single most useful thing you can contribute is a **new job source**.

**[CONTRIBUTING.md](CONTRIBUTING.md)** has the full guide: the `Job` dataclass contract, how to wire a source into the pipeline, lazy-fetch patterns, error handling, and the PR process.

> [!NOTE]
> If you're adding a source, include a quick note on what it covers and why it's not redundant with existing ones.

---

## 📄 License

Distributed under the [MIT License](LICENSE).
