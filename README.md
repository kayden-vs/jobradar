# 🎯 JobRadar

An automated, AI-powered job discovery pipeline that aggregates **12 sources**, eliminates noise with zero-cost heuristics, deduplicates across runs, ranks candidates by relevance, scores with AI, and delivers priority alerts to **Telegram** — running twice daily.

Built for freshers, interns, and early-career developers targeting backend, software engineering, and Go/TypeScript roles in India — but fully configurable for any role, stack, or location.

---

## 🚀 How It Works

```
            ┌─────────────────────────────────────────────────┐
            │         12 Job Sources (Concurrent)             │
            │  ATS APIs · YC · Internshala · Naukri · Hirist  │
            │  Fresher Blogs RSS · Serper · HN · Reddit · … │
            └────────────────────────┬────────────────────────┘
                                     │ ~8,000–9,000 raw jobs
                                     ▼
            ┌─────────────────────────────────────────────────┐
            │           Multi-Key Deduplication               │
            │  Title+Company+Location MD5 · Canonical URL MD5 │
            │  Run-level + SQLite persistent — never repeat   │
            └────────────────────────┬────────────────────────┘
                                     │ ~600–800 new jobs
                                     ▼
            ┌─────────────────────────────────────────────────┐
            │         Smart Rule-Based Pre-Filter             │
            │  Expiry · Blacklists · ATS Allowlist · Location │
            │  RSS Tags · Experience · Company Cap            │
            │        Drops ~90–95% with zero AI cost          │
            └────────────────────────┬────────────────────────┘
                                     │ ~50–150 eligible jobs
                                     ▼
            ┌─────────────────────────────────────────────────┐
            │         Heuristic Relevance Ranking             │
            │  Go/TS stack · Fintech · Fresher · Recency      │
            │       Best-fit jobs scored first, free          │
            └────────────────────────┬────────────────────────┘
                                     │ ranked, best-first
                                     ▼
            ┌─────────────────────────────────────────────────┐
            │     AI Scorer — Groq llama-4-scout-17b          │
            │  Token-budget guard · 5s throttle (28.8K TPM)  │
            │  ~89 jobs/run · few-shot calibrated 1–10 scale  │
            └────────────────────────┬────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
            Score ≥ 8 (Urgent)              Score 6–7 (Digest)
          Instant Telegram push         Session summary card
```

---

## ✨ Features & Architecture

### 🔌 12 Job Sources — Rich & Redundant

**Structured ATS APIs** (via `sources/ats.py`):
Direct API polling of four major Applicant Tracking Systems, no scraping needed:
- **Greenhouse** (US + EU endpoints) — the most common ATS at funded startups
- **Lever** — popular at Series A/B companies, millisecond-epoch timestamps handled
- **Ashby** — modern ATS used by many YC-backed companies
- **Workable** — common at Indian-market companies

Each ATS company is listed in `companies.yaml`. Per-company caps prevent any single large company (e.g. GitLab, Stripe) from dominating the scoring budget. Full JDs are always available from the structured API.

**Naukri.com** (`sources/naukri.py`):
India's largest job board. Two-stage pipeline:
- Stage 1: Search API with `keyword × location × page` grid (10 keywords × 3 locations × 2 pages = up to 1,200 raw cards), with in-line experience and age filters to avoid wasting downstream budget.
- Configurable per `profile.yaml → naukri:` block (keywords, locations, pages, exp range).

**Hirist.tech** (`sources/hirist.py`):
India-specific niche tech job board targeting backend, Golang, Python, and TypeScript roles. JS-rendered page handled with full detail page fetching.

**Y Combinator Jobs Board** (`sources/yc.py`):
Scrapes the YC job board with two-phase architecture: card listing → full JD fetch per job. High-signal source for early-stage and Series A startups globally.

**Internshala** (`sources/internshala.py`):
India's #1 internship and fresher job platform. Highly optimized plain-HTTP parser — bypasses browser overhead entirely. Filters by category (software, web dev) and experience label.

**Indian Fresher Blogs RSS Aggregator** (`sources/freshers_blogs.py`):
Aggregates 8+ high-volume off-campus WordPress job blogs concurrently using `ThreadPoolExecutor`. Extracts rich WordPress RSS tag metadata (`entry.tags`) — experience level, graduation batch year, and location tags — enabling zero-network-cost pre-filtering directly from feed data.

Included blogs: Freshers360, GeeksforGeeks Jobs, Freshersnow, Sarkari Result (IT section), Unstop, Cuvette.tech, and others.

**Serper.dev Search Discovery** (`sources/serper.py`):
Runs structured Google search dorks (e.g. `site:greenhouse.io "backend intern" india`) to discover jobs that don't appear on aggregators. Configurable max call budget per run.

**HackerNews "Who is Hiring?"** (`sources/hackernews.py`):
Parses the monthly Ask HN hiring thread via the official Algolia HN search API. **Self-healing auto-discovery**: if the current month's thread ID isn't configured, it automatically queries Algolia to find it — no manual updates ever needed.

**Reddit Job Feeds** (`sources/reddit.py`):
Pulls from relevant hiring subreddits (r/cscareerquestions, r/IndiaJobs, etc.) via Reddit's RSS endpoints.

**Cutshort.io** (`sources/cutshort.py`):
Integrated via public API. Currently disabled (API unreliability) but wired in.

**Instahyre** (`sources/instahyre.py`):
API + scraping fallback for this India-focused platform.

**Wellfound (AngelList)** (`sources/wellfound.py`):
Integrated but currently disabled — blocks automated requests with a verification wall.

---

### 🛡️ Smart Pre-Filter (`pipeline/prefilter.py`)

Drops **~90–95%** of listings before AI — each check is pure Python, zero network cost:

| Check | What it catches |
|---|---|
| **Age filter** | Jobs older than `max_job_age_days` (default 45 days). Handles ISO dates, RFC dates, relative strings ("3 days ago"), and Unix epoch (ms/s). |
| **Expiry signals** | Title/description regex for "application closed", "position filled", "last date: [past date]", etc. |
| **ATS strict title allowlist** | ATS titles must contain a recognised tech signal (engineer, backend, golang, intern, etc.) — safe because ATS titles are clean and structured. |
| **ATS location filter** | Instantly rejects US/UK/EU structured location fields; passes India/Remote/ambiguous. |
| **RSS WordPress tag filter** | Zero-cost intersection check on experience, batch, and location tags from RSS feed metadata — no page fetches needed. |
| **Non-ATS lenient filter** | Blog/RSS titles pass unless they hit an explicit rejection signal (sales, HR, senior, etc.). |
| **Experience keyword scan** | Hard rejects descriptions containing "2+ years", "senior engineer", "tech lead", etc. |
| **Location description scan** | Rejects jobs explicitly requiring on-site in non-India geographies. |
| **Company/role blacklists** | Configurable lists in `profile.yaml`. |
| **ATS company cap** | Max N jobs per company per run (default 25) — prevents GitLab/Stripe dominating the pool. |

---

### 🏆 Heuristic Relevance Ranker (`pipeline/ranker.py`)

Before any AI call, every eligible job gets a fast Python relevance score. This determines the order in which jobs enter the AI scorer — so the token budget is spent on the strongest matches first.

| Signal | Points |
|---|---|
| Golang/Go in title | +5 |
| TypeScript / Node.js in title | +3 |
| Fintech / crypto / payments keywords | +3 |
| Backend / microservice in title | +2 |
| Intern / fresher / junior in title | +2 |
| Project relevance (gRPC, orderbook, proxy, etc.) | +2 each, max +4 |
| Posted within 7 days | +4 |
| Posted within 14 days | +2 |
| No date available | **+0** (not penalised) |

> **Key design**: jobs with no `posted_at` date are **not penalised** — they compete on stack/role signals. This avoids silently dropping good jobs that don't expose a date (common with Naukri and some ATS endpoints).

---

### 🤖 AI Scorer — Budget-Protected (`pipeline/scorer.py`)

**Model**: `meta-llama/llama-4-scout-17b-16e-instruct` (Groq free tier)
- MoE architecture: better quality than 8B, close to 70B

**Two-layer rate system**:

| Layer | Mechanism | Value |
|---|---|---|
| Per-minute (TPM) | `REQ_INTERVAL` throttle | 5.0s gap → 12 req/min → 28,800 TPM (safely under 30K) |
| Per-run daily budget | `TOKEN_BUDGET_PER_RUN` | 200,000 tokens (80% of 500K TPD ÷ 2 runs/day) |

**Observed actual token cost**: ~2,240 tokens/call (system prompt + few-shot examples + full scoring rules + profile + JD + response).

**Scoring features**:
- **Few-shot calibration**: Two fixed examples (score 9 and score 3) embedded in the system prompt anchor the scale so the model doesn't drift across runs.
- **Token-saving rules**: Jobs scoring < 6 return empty `reason`, `highlights`, `red_flags` — cuts response tokens by ~90% for low-relevance jobs.
- **Pre-Groq expiry scan**: After lazy JD fetch, scans the full description for closure/deadline signals before making any Groq call — saves tokens on stale freshers blog posts.
- **Lazy JD fetch**: `freshers_blogs` sources fetch full post pages only *after* a job survives prefilter — not upfront for all 8,000 raw jobs.

**Score buckets**:
- `8–10` → Urgent: instant Telegram push notification
- `6–7` → Digest: included in session summary card
- `5` → Persisted to DB but not notified
- `< 5` → Dropped

---

### 🔗 Multi-Key Deduplication (`pipeline/dedup.py`)

Never see the same job twice across sources or runs:

- **Hash 1 — Normalised Title+Company+Location MD5**: Collapses `Pvt Ltd` / `Private Limited` / `Inc.` / `Technologies`, city aliases (`Bengaluru → bangalore`, `Gurugram → gurgaon`), year noise in titles, and whitespace.
- **Hash 2 — Canonical URL MD5**: Strips `utm_*`, `ref`, `source`, and other tracking parameters.
- **Run-level**: In-memory dedup within the current run (same job from multiple sources).
- **Persistent**: SQLite lookup against all previously seen jobs.

---

## 🗂️ Project Structure

```
jobradar/
│
├── main.py                    # Entry point — orchestrates the full pipeline
│
├── profile.yaml               # ← YOUR MAIN CONFIG FILE (roles, skills, location, filters)
├── companies.yaml             # ATS company slugs (Greenhouse / Lever / Ashby / Workable)
│
├── sources/                   # Job fetchers — one file per source
│   ├── ats.py                 # Greenhouse (US+EU) / Lever / Ashby / Workable API polling
│   ├── naukri.py              # Naukri.com — two-stage search+detail API (Stage-1 filtered)
│   ├── hirist.py              # Hirist.tech — India niche tech board (JS-rendered)
│   ├── yc.py                  # YC jobs board — two-phase scraper (cards → full JD)
│   ├── internshala.py         # Internshala — optimized plain-HTTP scraper
│   ├── freshers_blogs.py      # 8+ Indian fresher blogs — concurrent RSS + lazy JD fetch
│   ├── serper.py              # Google dork discovery via Serper.dev
│   ├── hackernews.py          # HN "Who is Hiring?" — Algolia auto-discovery
│   ├── reddit.py              # Reddit RSS feeds
│   ├── cutshort.py            # Cutshort.io API (currently disabled)
│   ├── instahyre.py           # Instahyre API + scraper fallback (currently disabled)
│   └── wellfound.py           # Wellfound/AngelList (currently disabled — blocks bots)
│
├── pipeline/                  # Processing stages
│   ├── dedup.py               # Run-level + persistent SQLite dual-hash deduplication
│   ├── prefilter.py           # Multi-layer rule-based hard filters (zero AI cost)
│   ├── ranker.py              # Heuristic relevance ranker — sorts jobs before AI scoring
│   └── scorer.py              # Groq AI scorer — token-budgeted, throttled, few-shot calibrated
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
├── requirements.txt
└── .env                       # API keys (never commit)
```

---

## 🛠️ Setup

### 1. Clone & Install

```bash
git clone https://github.com/your-username/jobradar.git
cd jobradar
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
SERPER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

### 3. Configure `profile.yaml`

Edit to match your skills, target roles, locations, and hard-reject rules:

```yaml
sources:
  ats:            true   # Greenhouse / Lever / Ashby / Workable
  naukri:         true   # India's largest job board
  internshala:    true   # India's #1 internship platform
  yc:             true   # YC portfolio company jobs
  freshers_blogs: true   # 8+ Indian fresher blogs (RSS)
  serper:         true   # Google search dork discovery
  hackernews:     false  # HN "Who is Hiring?" monthly thread
  hirist:         false  # Hirist.tech (enable once tested)
  cutshort:       false  # Cutshort.io (API unreliable)
  instahyre:      false  # Instahyre
  reddit:         false  # Reddit job feeds
  wellfound:      false  # Wellfound (blocks bots)

candidate:
  name: "Your Name"
  roles:
    primary:
      - "Backend Engineering Intern"
      - "Go Developer Intern"
      - "Software Engineering Intern"
  skills:
    strong: ["Go", "TypeScript", "PostgreSQL", "Redis", "Docker"]
    learning: ["Kubernetes", "AWS"]
  location:
    base: "Kolkata, India"
    acceptable: ["Remote", "Bangalore", "Mumbai", "Hyderabad", "Delhi NCR"]
    hard_reject: ["US only", "UK only", "Europe only"]

hard_reject:
  max_job_age_days: 45
  ats_per_company_cap: 25
  max_ai_jobs_per_run: 200        # hard fallback — primary guard is token budget
  experience_keywords:
    - "2+ years"
    - "senior engineer"
    - "tech lead"
```

### 4. Validate Config (Dry Run)

```bash
python main.py profile.yaml --dry-run
```

Prints your full config summary and confirms DB initialises correctly — no API calls made.

### 5. Run the Pipeline

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

### Typical Run Stats (observed)

| Stage | Count | Time | Notes |
|:---|:---|:---|:---|
| **Raw jobs fetched** | ~8,000–9,000 | ~9–11 min | ATS polling is the bottleneck; Naukri Stage-1 alone scans 1,200 listings |
| **After deduplication** | ~600–800 new | < 1 sec | Fast dual-hash SQLite lookups |
| **After pre-filter** | ~100–150 eligible | < 1 sec | Rule-based, zero AI cost |
| **After heuristic ranking** | same count, sorted | < 1 sec | Pure Python, no network |
| **After AI scorer** | ~80–100 scored | ~7–8 min | Token budget: ~89 jobs max/run at 5.0s intervals |
| **Alerts delivered** | 2–6 urgent | < 1 sec | Telegram push for score ≥ 8 |
| **Total pipeline** | | **~17–20 min** | |

### API Usage & Free-Tier Safety

| API | Observed usage/run | Free tier | Headroom |
|:---|:---|:---|:---|
| **Groq (llama-4-scout)** | ~200K tokens | 500K tokens/day | 2 runs/day at full budget |
| **Serper.dev** | 10–20 queries | 2,500 queries/month | ~75–125 runs/month |
| **Telegram Bot** | ~10–15 messages | Unlimited | Free |

**Groq rate limits** (llama-4-scout free tier):
- TPM: 30,000 tokens/min → `REQ_INTERVAL = 5.0s` gives 28,800 TPM (4% headroom)
- TPD: 500,000 tokens/day → `TOKEN_BUDGET_PER_RUN = 200K` (2 runs/day × 200K = 400K, 80% of TPD)
- RPD: 1,000 requests/day → 89 req/run × 2 runs = 178 RPD (well within limit)

---

## 🛠️ Maintenance & Tuning

- **Adding ATS companies**: Add to `companies.yaml`. Verify the slug first:
  ```bash
  curl -s "https://boards-api.greenhouse.io/v1/boards/SLUG/jobs" | python -m json.tool | head -20
  curl -s "https://api.lever.co/v0/postings/SLUG" | python -m json.tool | head -20
  ```
- **Tuning pre-filter**: Adjust `hard_reject.experience_keywords` and `hard_reject.role_blacklist` in `profile.yaml` if too many irrelevant jobs slip through.
- **Naukri config**: Add more keywords or locations under `profile.yaml → naukri:` to increase coverage. Each page = up to 20 listings; each keyword × location combo = 2 pages by default.
- **Per-user profiles**: Run `python main.py my_profile.yaml` to use a different profile file. Each profile gets its own DB and log file under `data/`.

---

## 📝 License

MIT
