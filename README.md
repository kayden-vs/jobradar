# 🎯 JobRadar

An automated, budget-optimized job discovery pipeline that aggregates **10+ sources**, filters out noise with zero-cost rules, deduplicates listings, scores matches with AI, and sends premium alerts directly to **Telegram** — every morning at 8 AM.

Built for freshers, interns, and early-career developers targeting backend, software engineering, and Go/TypeScript roles in India (but fully configurable for any location, role, or stack).

---

## 🚀 How It Works

```
                        [ Job Fetchers (10 Sources) ]
      (ATS APIs / YC / Internshala / RSS Blogs / Serper / HN / Reddit)
                                    │
                                    ▼
                      [ Multi-Key Deduplication ]
     (Normalised Title-Company-Location MD5 + Stripped Canonical URL MD5)
                                    │
                                    ▼
                        [ Smart Rule Prefilter ]
      (Temporal Expiry, Strict ATS Allowlist, Zero-Cost RSS WordPress Tags, 
         Location Match, Blacklists, Company Caps — Saves 95% AI Cost)
                                    │
                                    ▼
                       [ Budget-Protected Scorer ]
      (Fresshest Sorting Cap -> Groq llama-4-scout with Token-Saving JSON)
                                    │
                                    ▼
                         [ Telegram Alerts bot ]
        (Urgent ≥ 8 -> Instant Push alert | Digest 6–7 -> Daily Summary)
```

---

## ✨ Crucial Features & Architecture

### 🔌 10 Native Job Sources
*   **Structured ATS APIs**: Direct polling of Greenhouse (US/EU), Lever, Ashby, and Workable. Automatically fetches the **full job description (JD)** via secondary API queries for high-scoring accuracy.
*   **Y Combinator (YC) Jobs Board** (`sources/yc.py`): Scrapes high-quality startups posting on YC. Uses a two-phase architecture (card scraping followed by full description fetching) to parse rich tech stack & experience requirements.
*   **Internshala** (`sources/internshala.py`): Scrapes internships & entry-level jobs in India. Highly optimized plain HTTP parser, bypassing browser overhead to scrape 10x faster.
*   **Indian Fresher Blogs RSS Aggregator** (`sources/freshers_blogs.py`): Scrapes 8 high-volume off-campus WordPress sites concurrently (ThreadPoolExecutor), extracting rich WordPress category tag metadata (experience, batch, location tags) from `entry.tags` directly in the feed.
*   **Serper.dev Web Discovery**: Discovers obscure job posts via Google search dorks.
*   **HackerNews monthly 'Who is Hiring' thread**: Features smart self-healing auto-discovery via the Algolia HN API if the thread ID is not manually updated.
*   **Reddit job feeds**: Pulls relevant subreddits.
*   **Cutshort & Instahyre**: Integrated via API/Scraper.

### 🛡️ Smart, Token-Saving Pre-Filter (`pipeline/prefilter.py`)
Drops **~90-95%** of irrelevant/stale listings before sending anything to the AI, keeping Groq free-tier limits perfectly clear.
*   **Relative & Epoch Date Parsing**: Relies on a robust parser that understands ISO/RFC dates, relative strings ("3 days ago", "an hour ago", "2 weeks ago"), and Unix epoch timestamps (such as Lever's ms timestamps). Reject posts older than configured limit (`max_job_age_days`, default 45).
*   **Expiry & Deadline Detection**: Pre-screen title and description using regex to parse explicit deadline dates (e.g. "Apply before: Dec 20, 2025") and hard closure signals ("application is closed", "position filled"). Drops expired listings before AI scoring.
*   **Zero-Cost RSS WordPress Tag Filtering**: Pure list intersection check on experience/batch/location tags directly from RSS feed data without making single-page network calls.
*   **Dual Title-Relevance Strategy**: Strict positive allow-list for structured ATS titles (requires software/tech signals) versus a lenient, pass-by-default blocklist for RSS blogs to avoid missing unique roles.
*   **Zero-Cost ATS Location Check**: Pre-filter structured ATS locations to instantly reject US/UK/EU jobs while passing Remote/India posts.
*   **ATS Company Cap**: Limits a single company from dominating the AI budget (`ats_per_company_cap`, default 25).
*   **Description Keyword Scans**: Hard filters for maximum experience required and location hard-rejects inside descriptions.

### 🔗 Robust Multi-Key Deduplication (`pipeline/dedup.py`, `storage/db.py`)
Ensures you never see the same job twice, regardless of title variations or differing crawl sources.
*   **Primary Hash (Title-Company-Location MD5)**: Highly normalized and resilient to:
    *   Company noise variations (collapses `Pvt Ltd`, `Private Limited`, `Inc.`, `Technologies`, `Software Solutions`).
    *   Location/City aliases (collapses `Bengaluru` -> `bangalore`, `Gurugram` -> `gurgaon`, `New Delhi` -> `delhi`).
    *   Year noise in titles (strips years like `2025`, `2026`).
    *   Whitespace and punctuation normalization.
*   **Secondary Hash (Canonical URL MD5)**: Strips tracking/referral parameters (`utm_` parameters, `ref`, `source`, etc.) to prevent duplicate alerts for identical jobs across different sources or runs.
*   **Run-Level & Persistent Dedup**: Deduplicates inside the current run in-memory and queries against SQLite historically stored jobs.

### 🤖 Budget-Protected Groq Scoring (`pipeline/scorer.py`)
*   **Groq meta-llama/llama-4-scout**: Scores jobs 1-10 with low-temperature JSON outputs. Takes candidate's custom resume projects (e.g. Zaraba, CipherBin, Sentinel-Proxy) into context to score relevance.
*   **Global Scorer Cap**: Implements `max_ai_jobs_per_run` (default 80) in `main.py` to prevent runaway costs by sorting jobs by date and sending only the freshest candidates to the AI model.
*   **Token Saving Rule**: For jobs scoring < 6, the reason, highlights, and red flags are returned empty, dramatically cutting response token consumption by **90%** for borderline matches.
*   **Throttled Crawl**: Implements a 3.0s request interval for Groq to fully comply with free-tier 30 req/min limit.

---

## 🗂️ Project Structure

```
jobradar/
│
├── main.py                    # Entry point — orchestrates the full pipeline
│
├── profile.yaml               # ← YOUR MAIN CONFIG FILE (roles, skills, location, filters)
├── companies.yaml             # ATS company slugs to poll (Greenhouse, Lever, etc.)
│
├── sources/                   # Job fetchers — one file per source
│   ├── ats.py                 # Greenhouse / Lever / Ashby / Workable API polling & full JD fetcher
│   ├── yc.py                  # YC jobs board two-phase scraper (cards -> full JD)
│   ├── internshala.py         # Internshala scraper (highly optimized plain HTTP parser)
│   ├── freshers_blogs.py      # Indian fresher blogs RSS aggregator (ThreadPoolExecutor + category tags)
│   ├── serper.py              # Google dork search via Serper.dev
│   ├── hackernews.py          # HN "Who is Hiring?" monthly thread with Algolia auto-discovery
│   ├── cutshort.py            # Cutshort.io API fetcher
│   ├── instahyre.py           # Instahyre API + scraping fallback
│   ├── wellfound.py           # Wellfound scraper (currently disabled)
│   └── reddit.py              # Reddit RSS feeds
│
├── pipeline/                  # Processing stages
│   ├── dedup.py               # SQLite-based run-level and persistent deduplication
│   ├── prefilter.py           # Multi-layered rule-based hard filters (no AI cost)
│   └── scorer.py              # Groq AI scoring (1–10 with reasoning, highlights & red flags)
│
├── notify/
│   └── telegram_bot.py        # Sends alerts and digest to Telegram
│
├── storage/
│   └── db.py                  # SQLite schema + double-hash CRUD helpers
│
├── data/                      # Auto-created at runtime
│   ├── jobradar.db            # SQLite database (jobs, run log)
│   └── jobradar.log           # Rotating run logs
│
├── docs/
│   ├── jobradar_guide.md      # Detailed guide & maintenance notes
│   ├── future_intergrations.txt # Future source roadmaps
│   └── roadmap.md             # Development milestones
│
├── requirements.txt
└── .env                       # API keys (never commit this)
```

---

## 🛠️ Setup

### 1. Clone & Install

```bash
git clone https://github.com/your-username/jobradar.git
cd jobradar
pip install -r requirements.txt
```

### 2. Configure Environment `.env`

Create a `.env` file in the root directory:

```env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
SERPER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321
```

### 3. Configure Your Profile (`profile.yaml`)

Edit `profile.yaml` to specify your details, skills, target roles, acceptable locations, custom resume projects, and hard reject rules:

```yaml
sources:
  ats:            true
  cutshort:       false
  instahyre:      true
  wellfound:      false
  serper:         true
  hackernews:     false
  reddit:         false
  internshala:    true
  yc:             true
  freshers_blogs: true

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
  max_ai_jobs_per_run: 80
  experience_keywords:
    - "2+ years"
    - "senior engineer"
    - "tech lead"
```

### 4. Run the Pipeline

```bash
python main.py
```

*First run crawls all enabled sources and seeds the SQLite DB, taking **8-15 minutes**. Subsequent runs take **1-3 minutes** as duplicate jobs are instantly bypassed.*

---

## ⏰ Automation (Run Daily at 8 AM)

### Option A — Windows Task Scheduler (Recommended)

Open PowerShell as Administrator and run the following commands to register a scheduled task that executes every day at 8 AM, even waking up the machine if needed:

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "main.py" -WorkingDirectory "C:\path\to\jobradar"
$trigger = New-ScheduledTaskTrigger -Daily -At "8:00AM"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "JobRadar" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

### Option B — GitHub Actions (Cloud, Free)

Already pre-configured in `.github/workflows/jobradar.yml`.
1. Push your repository to GitHub (ensure `.env` is omitted via `.gitignore`).
2. Go to your GitHub repository -> `Settings` -> `Secrets and variables` -> `Actions`.
3. Add secrets: `GROQ_API_KEY`, `SERPER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
4. Enable Actions.

---

## 📊 Performance & API Costs

### Typical Runtime Stats

| Stage | Count | Time | Notes |
| :--- | :--- | :--- | :--- |
| **Raw Jobs Fetched** | ~350–500 | 4–6 min | Threaded RSS + plain HTTP Internshala is very fast |
| **After Deduplication** | ~100–180 | < 1 sec | Checked via fast dual-hash SQLite lookups |
| **After Pre-Filter** | ~30–50 | < 1 sec | Rule-based pruning reduces pool by ~75% |
| **After AI Scorer** | ~5–12 kept (score ≥5) | 1–3 min | Meta-Llama-4-Scout scoring, throttled at 3.0s intervals |
| **Alerts Delivered** | ~2–5 urgent alerts | < 1 sec | Telegram API instantly delivers digest and push cards |
| **Total Pipeline Run** | | **~5–9 min** | **Highly optimized, fast concurrent runs** |

### API Usage & Free-Tier Safety

| API | Usage/Run | Free Tier Limit | Headroom |
| :--- | :--- | :--- | :--- |
| **Groq (llama-4-scout)** | ~40K–65K tokens | 500,000 tokens / day | ~8-10 runs/day |
| **Serper.dev** | 10-15 queries | 2,500 queries / month | ~166 runs/month |
| **Telegram Bot** | ~10 messages | Unlimited | 100% Free |

---

## 🛠️ Maintenance & Tuning

*   **Verified ATS Slugs**: To add companies to `companies.yaml`, always verify their career slug works first. Curl their board API (e.g. `https://boards.greenhouse.io/v1/boards/SLUG/jobs` or `https://api.lever.co/v0/postings/SLUG`) to verify a `200 OK` status before committing it.
*   **Adjusting Reject Rules**: If you notice too many junior/tech-adjacent roles slipping through or too many good jobs getting rejected, fine-tune the lists in `profile.yaml` under `hard_reject.experience_keywords` or `hard_reject.role_blacklist`.
*   **HackerNews Auto-Discovery**: If you forget to update `HN_THREAD_IDS` in `sources/hackernews.py` with the month's Ask HN ID, the system automatically calls Algolia's search API to auto-discover it on the fly!

---

## 📝 License

MIT
