# 🎯 JobRadar

An automated job discovery pipeline that scrapes multiple sources, scores every listing with AI, and sends the best matches directly to your **Telegram** — every morning at 8 AM.

Built for freshers and interns targeting backend/Go roles in India, but fully configurable for any role, stack, or location.

---

## How It Works

```
Sources (ATS / Serper / HN / Reddit / Cutshort)
        ↓
  Deduplication  (SQLite hash — skip already-seen jobs)
        ↓
  Pre-filter     (rule-based: experience, location, role type)
        ↓
  AI Scorer      (Groq llama-4-scout — scores 1-10, only keeps ≥5)
        ↓
  Telegram Alert (urgent ≥8 → instant DM, digest 6-7 → summary)
```

---

## Features

- **7 job sources** — ATS APIs (Greenhouse, Lever, Ashby, Workable), Google dork search via Serper, HackerNews "Who is Hiring" thread, Reddit job feeds, Cutshort, Instahyre
- **AI scoring** — every job gets a 1–10 relevance score with reasoning, highlights, and red flags
- **Telegram delivery** — urgent jobs sent immediately, digest summary for borderline matches
- **Smart pre-filter** — rule-based rejection before AI (saves API tokens): experience keywords, non-tech roles, blacklisted companies
- **Deduplication** — never see the same job twice across runs
- **Source toggles** — enable/disable any source in one line of config
- **Token-efficient** — splits models by task: llama-4-scout for scoring, llama-3.1-8b for extraction
- **Fully configurable** — one `profile.yaml` controls everything

---

## Project Structure

```
jobradar/
│
├── main.py                    # Entry point — orchestrates the full pipeline
│
├── profile.yaml               # ← YOUR MAIN CONFIG FILE (roles, skills, location, filters)
├── companies.yaml             # ATS company slugs to poll (Greenhouse, Lever, etc.)
│
├── sources/                   # Job fetchers — one file per source
│   ├── ats.py                 # Greenhouse / Lever / Ashby / Workable API polling
│   ├── serper.py              # Google dork search via Serper.dev
│   ├── hackernews.py          # HN "Who is Hiring?" monthly thread
│   ├── cutshort.py            # Cutshort.io scraper
│   ├── instahyre.py           # Instahyre API + scraping fallback
│   ├── wellfound.py           # Wellfound scraper (currently disabled)
│   └── reddit.py              # Reddit RSS feeds
│
├── pipeline/                  # Processing stages
│   ├── dedup.py               # SQLite-based deduplication
│   ├── prefilter.py           # Rule-based hard filters (no AI cost)
│   └── scorer.py              # Groq AI scoring (1–10 with reasoning)
│
├── notify/
│   └── telegram_bot.py        # Sends alerts and digest to Telegram
│
├── storage/
│   └── db.py                  # SQLite schema + CRUD helpers
│
├── data/                      # Auto-created at runtime
│   ├── jobradar.db            # SQLite database (jobs, run log)
│   └── jobradar.log           # Run logs
│
├── docs/
│   └── jobradar_guide.md      # Detailed guide & maintenance notes
│
├── .github/workflows/
│   └── jobradar.yml           # GitHub Actions — runs daily at 8 AM UTC
│
├── requirements.txt
└── .env                       # API keys (never commit this)
```

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/your-username/jobradar.git
cd jobradar
pip install -r requirements.txt
```

### 2. Get API keys

| Key | Where to get it | Free tier |
|-----|----------------|-----------|
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) | 500K tokens/day |
| `SERPER_API_KEY` | [serper.dev](https://serper.dev) | 2,500 searches/month |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram | Free |
| `TELEGRAM_CHAT_ID` | Send `/start` to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` | Free |

### 3. Create `.env`

```env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
SERPER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

### 4. Configure your profile

Edit `profile.yaml` — this is the most important step. At minimum update:

```yaml
candidate:
  name: "Your Name"
  roles:
    primary:
      - "Backend Engineering Intern"
      - "Go Developer Intern"
  skills:
    strong: ["Go", "Python", "PostgreSQL"]
  location:
    base: "Your City, India"
```

### 5. Run

```bash
python -m main
```

First run takes **8–15 minutes** (fetching 7 sources + AI scoring ~90 jobs). Subsequent runs are faster due to deduplication.

---

## Automate (Run Daily at 8 AM)

### Option A — Windows Task Scheduler

Create `run_jobradar.bat`:
```batch
@echo off
cd /d C:\path\to\jobradar
python -m main >> data\jobradar.log 2>&1
```

Then in Task Scheduler:
- **Trigger:** Daily at 8:00 AM
- **Action:** Start `run_jobradar.bat`
- **Settings:** Run whether user is logged on or not

### Option B — GitHub Actions (cloud, free)

Already set up in `.github/workflows/jobradar.yml`. Just add your secrets in:

`GitHub repo → Settings → Secrets and variables → Actions`

Add: `GROQ_API_KEY`, `SERPER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

---

## Tweakable Settings

### `profile.yaml` — Full Control Panel

#### 🔌 Source Toggles
Turn any source on/off without touching code:

```yaml
sources:
  ats:        true    # Greenhouse / Lever / Ashby / Workable ATS polling
  cutshort:   true    # Cutshort.io scraping
  instahyre:  true    # Instahyre API
  wellfound:  false   # Disabled — blocks bots; enable if it works for you
  serper:     true    # Google dork search (uses Serper API credits)
  hackernews: true    # HN "Who is Hiring?" thread
  reddit:     true    # Reddit job feeds
```

#### 👤 Candidate Profile
```yaml
candidate:
  roles:
    primary:           # Highest weight in scoring
      - "Backend Engineering Intern"
    secondary:         # Also acceptable
      - "Full Stack Intern"

  experience:
    max_required: 1    # Hard reject any job requiring more than N years

  skills:
    strong: ["Go", "Python"]   # Matched positively in AI scoring
    learning: ["Kubernetes"]   # Mentioned as context

  location:
    acceptable:
      - "Remote"
      - "Bangalore"
    hard_reject:
      - "US only"        # Instantly filtered, no AI call wasted
```

#### 🏭 Industry Bonuses
```yaml
industries:
  high_priority:         # +2 score bonus from AI
    - "Fintech"
    - "Crypto"
  medium_priority:       # +1 score bonus
    - "SaaS"
    - "Infrastructure"
```

#### 🚫 Hard Reject Rules (saves AI tokens — runs before scoring)
```yaml
hard_reject:
  experience_keywords:   # Job description contains any of these → instant reject
    - "3+ years"
    - "senior engineer"
    - "tech lead"

  company_blacklist:     # Uncomment to block specific companies
    # - "TCS"
    # - "Infosys"

  role_blacklist:        # Job title contains these → instant reject
    - "Data Scientist"
    - "DevOps Engineer"
    - "Product Manager"
```

#### 💰 Salary Floor
```yaml
salary:
  min_stipend_inr: 10000    # ₹/month minimum for internships
  min_ctc_lpa: 4.0          # LPA minimum for full-time
```

---

### `companies.yaml` — ATS Companies to Poll

Add companies by their ATS slug (found in their career page URL):

```yaml
greenhouse:
  - groww          # boards.greenhouse.io/v1/boards/groww/jobs
  - razorpay       # verify first: curl the URL and check for 200

lever:
  - meesho         # api.lever.co/v0/postings/meesho

ashby:
  - setu           # api.ashbyhq.com/posting-api/job-board/setu

workable:
  - juspay         # apply.workable.com/juspay
```

> **How to find a slug:** Go to a company's careers page → click any open role → look at the URL. The company-specific segment is the slug.

---

### `sources/serper.py` — Search Queries

```python
MAX_SERPER_CALLS = 15    # Max Google searches per run (each = 1 API credit)

DORK_QUERIES = [
  '"backend intern" "golang" india',
  '"software engineer intern" "go" "bangalore" OR "remote"',
  # Add your own dorks here
]
```

---

### `pipeline/scorer.py` — AI Model & Thresholds

```python
MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"  # Change Groq model here
REQ_INTERVAL = 3.0   # Seconds between AI calls (lower = faster but risks rate limit)
```

In `score_all()` the score thresholds:
```python
if scored_job["score"] >= 8:   # → Telegram instant alert
elif scored_job["score"] >= 6: # → Digest summary
# score < 5 → not saved to DB
```

---

## Runtime Stats (typical run)

| Stage | Count | Time |
|-------|-------|------|
| Raw jobs fetched | ~250–300 | 8–12 min |
| After dedup | ~100–150 | instant |
| After pre-filter | ~60–90 | instant |
| After AI scoring | ~20–30 kept (score ≥5) | 3–5 min |
| Urgent alerts sent | ~3–8 | instant |
| **Total** | | **~12–18 min** |

---

## API Usage (free tiers)

| API | Usage/run | Free limit | Headroom |
|-----|-----------|------------|---------|
| Groq (llama-4-scout) | ~60–80K tokens | 500K/day | ~6 runs/day |
| Groq (llama-3.1-8b, HN parsing) | ~8K tokens | 500K/day | Unlimited |
| Serper | 15 searches | 2,500/month | 166 runs/month |
| Telegram | ~10 messages | Unlimited | ✅ |

---

## Maintenance

- **Monthly:** Update `HN_THREAD_IDS` in `sources/hackernews.py` with the new "Who is Hiring" thread ID from [news.ycombinator.com/submitted?id=whoishiring](https://news.ycombinator.com/submitted?id=whoishiring) *(or let the auto-discovery handle it)*
- **As needed:** Verify ATS slugs in `companies.yaml` still return 200 — companies switch ATS providers
- **Tuning:** If too many irrelevant jobs get through, tighten `hard_reject.experience_keywords` or `hard_reject.role_blacklist` in `profile.yaml`

---

## Requirements

- Python 3.11+
- Playwright (installed automatically via `scrapling[fetchers]`)
- SQLite (built into Python — no setup needed)

---

## License

MIT
