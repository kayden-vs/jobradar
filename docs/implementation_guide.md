# JobRadar — Complete Implementation Guide
### A smart, India-first personal job discovery system for backend intern/fresher roles

---

## Table of Contents

1. [Overview & Goals](#1-overview--goals)
2. [System Architecture](#2-system-architecture)
3. [Project Structure](#3-project-structure)
4. [Prerequisites & Setup](#4-prerequisites--setup)
5. [Configuration — `profile.yaml`](#5-configuration--profileyaml)
6. [Source Layer](#6-source-layer)
   - 6.1 [ATS Endpoint Polling](#61-ats-endpoint-polling)
   - 6.2 [Cutshort Scraping](#62-cutshort-scraping)
   - 6.3 [Instahyre Scraping](#63-instahyre-scraping)
   - 6.4 [Wellfound Scraping](#64-wellfound-scraping)
   - 6.5 [Serper.dev Discovery + Scrapling Extraction](#65-serperdev-discovery--scrapling-extraction)
   - 6.6 [HackerNews "Who's Hiring"](#66-hackernews-whos-hiring)
   - 6.7 [Reddit India Sources](#67-reddit-india-sources)
7. [Deduplication & Storage](#7-deduplication--storage)
8. [AI Filtering Engine](#8-ai-filtering-engine)
9. [Notification Layer](#9-notification-layer)
10. [Main Orchestrator](#10-main-orchestrator)
11. [Scheduler — GitHub Actions](#11-scheduler--github-actions)
12. [Keyword Reference](#12-keyword-reference)
13. [Running Locally](#13-running-locally)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Overview & Goals

JobRadar is a fully automated job discovery pipeline designed around one person's search profile. It runs every morning (or on-demand), pulls from multiple sources, deduplicates, runs AI relevance scoring, and pushes only the best matches to your Telegram.

**Your search profile in plain English:**
- Backend intern or fresher roles (0–1 year experience max)
- Golang preferred; full-stack acceptable if backend-heavy
- India-based companies, or remote-friendly anywhere
- Strong preference for fintech, payments, crypto companies
- Hard reject: any role requiring 1+ year of experience

**What makes this different from just using job boards:**
- Pulls from company ATS systems directly (catches jobs before aggregators index them)
- Google dorking finds Google Form applications and obscure listings
- AI reads your actual preferences + resume context to score each job
- Experience requirement is a hard pre-filter — 80% of postings get dropped before AI even sees them
- You get at most a handful of notifications per day, all relevant

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SCHEDULER (GitHub Actions)                    │
│                    Cron: every morning 8 AM IST                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌─────────────────┐ ┌──────────────┐ ┌──────────────────┐
    │  ATS POLLING    │ │ SCRAPLING    │ │  DISCOVERY       │
    │                 │ │ SOURCES      │ │  LAYER           │
    │ • Greenhouse    │ │              │ │                  │
    │ • Lever         │ │ • Cutshort   │ │ • Serper.dev     │
    │ • Ashby         │ │ • Instahyre  │ │   dork queries   │
    │                 │ │ • Wellfound  │ │ • HN Who's Hiring│
    │ (plain JSON,    │ │              │ │ • Reddit India   │
    │  no scraping)   │ │ (anti-bot    │ │   RSS feeds      │
    │                 │ │  bypass)     │ │                  │
    └────────┬────────┘ └──────┬───────┘ └────────┬─────────┘
             └────────────────┼───────────────────┘
                              ▼
             ┌────────────────────────────────┐
             │       RAW JOB POOL             │
             │  All listings from all sources │
             └───────────────┬────────────────┘
                             ▼
             ┌────────────────────────────────┐
             │    DEDUPLICATION ENGINE        │
             │  Hash(title+company+location)  │
             │  Check SQLite → skip if seen   │
             └───────────────┬────────────────┘
                             ▼
             ┌────────────────────────────────┐
             │    HARD PRE-FILTER (free)      │
             │                                │
             │  ❌ Experience > 1 year        │  ← most important
             │  ❌ Outside India, in-office   │
             │  ❌ Blacklisted companies      │
             │  ❌ Blacklisted keywords       │
             └───────────────┬────────────────┘
                             ▼
             ┌────────────────────────────────┐
             │    AI RELEVANCE SCORER         │
             │    (Gemini 1.5 Flash, free)    │
             │                                │
             │  Reads: your profile + JD      │
             │  Output: score 1–10 + reason   │
             │          + highlights          │
             │          + red flags           │
             └───────────────┬────────────────┘
                             ▼
              ┌─────────────────────────────┐
              │     SCORE ROUTING           │
              │                             │
              │  Score 8–10 → Telegram NOW  │
              │  Score 6–7  → Daily digest  │
              │  Score < 6  → DB only       │
              └──────────┬──────────────────┘
                         ▼
              ┌──────────────────────┐
              │   NOTIFICATIONS      │
              │  Telegram Bot (push) │
              │  Email digest (8 AM) │
              └──────────────────────┘
```

**Data flow summary:**
1. Sources produce raw job dicts `{title, company, location, description, url, source}`
2. Dedup engine filters already-seen jobs using SQLite
3. Hard pre-filter drops ineligible jobs with zero AI cost
4. Remaining jobs (typically 5–20% of raw pool) go to Gemini for scoring
5. High-scoring jobs trigger instant Telegram notification

---

## 3. Project Structure

```
jobradar/
│
├── main.py                    # Orchestrator — runs the full pipeline
├── profile.yaml               # YOUR search preferences (edit this)
├── companies.yaml             # Target companies list with ATS type
├── requirements.txt
├── .env                       # API keys (never commit this)
│
├── sources/
│   ├── __init__.py
│   ├── ats.py                 # Greenhouse + Lever + Ashby polling
│   ├── cutshort.py            # Cutshort scraper
│   ├── instahyre.py           # Instahyre scraper
│   ├── wellfound.py           # Wellfound scraper
│   ├── serper.py              # Serper.dev search + Scrapling extraction
│   ├── hackernews.py          # HN Who's Hiring parser
│   └── reddit.py              # Reddit India RSS
│
├── pipeline/
│   ├── __init__.py
│   ├── dedup.py               # Deduplication engine
│   ├── prefilter.py           # Hard rule-based filter
│   └── scorer.py              # Gemini AI scoring
│
├── notify/
│   ├── __init__.py
│   ├── telegram_bot.py        # Telegram push notifications
│   └── email_digest.py        # Daily email digest
│
├── storage/
│   ├── __init__.py
│   └── db.py                  # SQLite operations
│
├── .github/
│   └── workflows/
│       └── jobradar.yml       # GitHub Actions scheduler
│
└── data/
    └── jobradar.db            # SQLite database (auto-created)
```

---

## 4. Prerequisites & Setup

### 4.1 Required API Keys (all free)

| Key | Where to get | Free tier |
|-----|-------------|-----------|
| `SERPER_API_KEY` | serper.dev → Sign up | 2,500 queries/month |
| `GEMINI_API_KEY` | aistudio.google.com | 1M tokens/day |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` | Unlimited |
| `TELEGRAM_CHAT_ID` | Message @userinfobot after starting your bot | — |

### 4.2 Local Setup

```bash
# Clone or create the project
mkdir jobradar && cd jobradar

# Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install scrapling requests feedparser google-generativeai \
            python-telegram-bot pyyaml python-dotenv schedule \
            httpx aiohttp
            
# Install Scrapling browsers (needed for StealthyFetcher)
scrapling install
```

### 4.3 `.env` file

```env
SERPER_API_KEY=your_serper_key_here
GEMINI_API_KEY=your_gemini_key_here
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 4.4 `requirements.txt`

```
scrapling[fetchers]
requests
feedparser
google-generativeai
python-telegram-bot==20.7
pyyaml
python-dotenv
schedule
httpx
aiohttp
```

---

## 5. Configuration — `profile.yaml`

This is the most important file. The AI scorer reads this verbatim to understand who you are. Be specific — vague profiles produce mediocre scores.

```yaml
# profile.yaml — YOUR job search preferences
# Keep this accurate. The AI uses this to score every job.

candidate:
  name: "Your Name"
  
  # What you're looking for
  roles:
    primary:
      - "Backend Engineering Intern"
      - "Software Engineering Intern"
      - "Junior Backend Developer"
      - "Backend Developer Fresher"
      - "Go Developer Intern"
      - "Golang Developer Fresher"
    secondary:
      - "Full Stack Intern"
      - "Full Stack Developer Fresher"
      - "Software Developer Fresher"

  # Experience level — CRITICAL
  experience:
    years: 0                        # You are a fresher
    max_required: 1                 # HARD REJECT if job requires more than this
    acceptable_labels:
      - "fresher"
      - "0-1 years"
      - "intern"
      - "entry level"
      - "junior"
      - "0 years"

  # Tech stack (in order of preference)
  skills:
    strong:
      - "Go"
      - "Golang"
      - "REST APIs"
      - "PostgreSQL"
      - "Docker"
      - "Git"
    learning:
      - "Kubernetes"
      - "Redis"
      - "gRPC"
      - "AWS"

  # Notable projects for context-based scoring
  projects:
    - name: "Crypto Exchange"
      description: "Built a full crypto exchange from scratch in Go with order matching engine, real-time price feeds, wallet management, and trade execution"
      relevance_signal: "Fintech, crypto, trading, payments, Go, high-performance systems"

  # Location preferences
  location:
    base: "Kolkata, India"
    acceptable:
      - "Remote"
      - "Work from home"
      - "Anywhere in India"           # open to relocate
      - "Bangalore"
      - "Mumbai"
      - "Hyderabad"
      - "Delhi NCR"
      - "Chennai"
    hard_reject:
      - "US only"
      - "UK only"
      - "Europe only"
      - "On-site outside India"

  # Industry preferences (affects bonus scoring)
  industries:
    high_priority:                   # +2 score bonus
      - "Fintech"
      - "Crypto"
      - "Blockchain"
      - "Payments"
      - "Trading"
      - "Banking technology"
      - "Financial services"
    medium_priority:                 # +1 score bonus
      - "SaaS"
      - "Developer tools"
      - "Infrastructure"
      - "API-first companies"
      - "E-commerce backend"

  # Compensation
  salary:
    min_stipend_inr: 15000           # per month for internships
    min_ctc_lpa: 4.0                 # for full-time fresher roles

# Hard reject rules — checked BEFORE AI, saves tokens
hard_reject:
  experience_keywords:
    # If ANY of these appear in the job description → immediate reject
    - "2+ years"
    - "2 years experience"
    - "3+ years"
    - "4+ years"
    - "5+ years"
    - "minimum 2 years"
    - "at least 2 years"
    - "senior engineer"
    - "senior developer"
    - "lead engineer"
    - "tech lead"
    - "principal engineer"
    - "staff engineer"

  company_blacklist:
    # IT services / consultancies — typically not product work
    - "TCS"
    - "Infosys"
    - "Wipro"
    - "HCL"
    - "Cognizant"
    - "Accenture"
    - "Capgemini"
    - "Tech Mahindra"
    - "Mphasis"

  role_blacklist:
    # Unrelated roles that might appear in searches
    - "Data Scientist"
    - "Machine Learning Engineer"
    - "DevOps Engineer"               # unless it also has backend component
    - "QA Engineer"
    - "Test Engineer"
    - "Business Analyst"
    - "Product Manager"
    - "UI Designer"
    - "Frontend Developer"            # unless full-stack

# Scoring weights (used in AI prompt)
scoring_weights:
  golang_mentioned: +2
  fintech_crypto_company: +2
  remote_or_india: +1
  project_stack_match: +2           # if your crypto exchange is relevant
  exact_role_match: +2
  mentions_equity_esop: +0.5
  unknown_company: -0.5             # slight penalty for no-info companies
```

---

## 6. Source Layer

Each source module returns a list of job dicts in the same standard format:

```python
# Standard job dict — every source must return this shape
{
    "title":       str,   # "Backend Engineering Intern"
    "company":     str,   # "Razorpay"
    "location":    str,   # "Bangalore / Remote"
    "description": str,   # Full job description text
    "url":         str,   # Direct apply link
    "source":      str,   # "greenhouse", "cutshort", "serper", etc.
    "salary":      str,   # "15k/month" or "" if unknown
    "posted_at":   str,   # ISO date string or "" if unknown
}
```

---

### 6.1 ATS Endpoint Polling

**What this does:** Directly calls the public JSON APIs of Greenhouse, Lever, and Ashby for your list of target companies. No scraping, no browser, no anti-bot — just plain HTTP GET requests returning structured JSON.

**Why this is your best source:** Jobs appear here within minutes of being posted. Job aggregators like LinkedIn take 24–72 hours to index the same listings.

**`sources/ats.py`**

```python
import requests
import yaml
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Load your companies list
def load_companies(path="companies.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

def fetch_greenhouse(company_slug: str) -> list[dict]:
    """
    Polls the Greenhouse public API for a company.
    URL pattern: https://boards.greenhouse.io/v1/boards/{slug}/jobs
    """
    url = f"https://boards.greenhouse.io/v1/boards/{company_slug}/jobs"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for job in data.get("jobs", []):
            # Greenhouse gives us title, location, absolute_url, updated_at
            location_parts = [loc.get("name", "") for loc in job.get("offices", [])]
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    ", ".join(location_parts) or "Not specified",
                "description": "",   # Greenhouse list endpoint doesn't include full JD
                "url":         job.get("absolute_url", ""),
                "source":      "greenhouse",
                "salary":      "",
                "posted_at":   job.get("updated_at", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Greenhouse fetch failed for {company_slug}: {e}")
        return []


def fetch_lever(company_slug: str) -> list[dict]:
    """
    Polls Lever's public API.
    URL pattern: https://api.lever.co/v0/postings/{slug}
    Returns all open postings with full description.
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json():
            # Lever gives full text, categories, lists (requirements, etc.)
            desc_parts = []
            for section in job.get("lists", []):
                desc_parts.append(section.get("text", ""))
                items = section.get("content", "")
                desc_parts.append(items)
            desc_parts.append(job.get("descriptionPlain", ""))
            
            location = job.get("categories", {}).get("location", "Not specified")
            commitment = job.get("categories", {}).get("commitment", "")
            
            jobs.append({
                "title":       job.get("text", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    f"{location} ({commitment})" if commitment else location,
                "description": "\n".join(desc_parts),
                "url":         job.get("hostedUrl", ""),
                "source":      "lever",
                "salary":      "",
                "posted_at":   datetime.fromtimestamp(
                                   job["createdAt"] / 1000
                               ).isoformat() if job.get("createdAt") else "",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Lever fetch failed for {company_slug}: {e}")
        return []


def fetch_ashby(company_slug: str) -> list[dict]:
    """
    Polls Ashby HQ's public API.
    URL pattern: https://{slug}.jobs.ashbyhq.com/api/jobs
    """
    url = f"https://{company_slug}.jobs.ashbyhq.com/api/jobs"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("jobs", []):
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    job.get("location", "Not specified"),
                "description": job.get("descriptionHtml", "").replace("<br>", "\n"),
                "url":         f"https://{company_slug}.jobs.ashbyhq.com/{job.get('slug', '')}",
                "source":      "ashby",
                "salary":      "",
                "posted_at":   job.get("publishedDate", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Ashby fetch failed for {company_slug}: {e}")
        return []


def fetch_workable(company_slug: str) -> list[dict]:
    """
    Polls Workable's public API.
    URL: https://apply.workable.com/api/v3/accounts/{slug}/jobs
    """
    url = f"https://apply.workable.com/api/v3/accounts/{company_slug}/jobs"
    try:
        r = requests.post(url, json={"query": "", "location": [], "department": [], "worktype": []}, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("results", []):
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    job.get("location", {}).get("city", ""),
                "description": job.get("description", ""),
                "url":         f"https://apply.workable.com/{company_slug}/j/{job.get('shortcode', '')}",
                "source":      "workable",
                "salary":      "",
                "posted_at":   job.get("published_on", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Workable fetch failed for {company_slug}: {e}")
        return []


def fetch_all_ats(companies_config: dict) -> list[dict]:
    """Main function: polls all companies in companies.yaml"""
    all_jobs = []
    
    for company in companies_config.get("greenhouse", []):
        jobs = fetch_greenhouse(company)
        all_jobs.extend(jobs)
        logger.info(f"Greenhouse {company}: {len(jobs)} jobs")
    
    for company in companies_config.get("lever", []):
        jobs = fetch_lever(company)
        all_jobs.extend(jobs)
        logger.info(f"Lever {company}: {len(jobs)} jobs")
    
    for company in companies_config.get("ashby", []):
        jobs = fetch_ashby(company)
        all_jobs.extend(jobs)
        logger.info(f"Ashby {company}: {len(jobs)} jobs")
    
    for company in companies_config.get("workable", []):
        jobs = fetch_workable(company)
        all_jobs.extend(jobs)
        logger.info(f"Workable {company}: {len(jobs)} jobs")
    
    logger.info(f"ATS total: {len(all_jobs)} raw jobs")
    return all_jobs
```

**`companies.yaml`** — Build this list over time. These are companies confirmed to use each ATS:

```yaml
# companies.yaml
# Add/remove as you discover more companies using each ATS
# Find a company's ATS by checking their careers page URL

greenhouse:
  - razorpay
  - browserstack
  - innovaccer
  - unacademy
  - mpl-gaming
  - groww
  - zepto
  - cred
  - slice-card
  - open-financial-technologies
  - smallcase
  - niyo
  - fi-money
  - jupiter-money
  - kreditbee

lever:
  - meesho
  - swiggy
  - zomato
  - urban-company
  - ola
  - navi-technologies
  - lendingkart
  - indmoney
  - cube-wealth
  - recur-club

ashby:
  - setu                   # payments infra startup
  - decentro
  - cashfree-payments

workable:
  - juspay
  - sarvam-ai
```

**How to find more companies + their ATS type:**
Look at the careers page URL:
- `company.greenhouse.io` or `boards.greenhouse.io/company` → Greenhouse
- `jobs.lever.co/company` → Lever
- `company.jobs.ashbyhq.com` → Ashby
- `apply.workable.com/company` → Workable

---

### 6.2 Cutshort Scraping

**What this does:** Cutshort is the best Indian platform for product company roles. It has less spam than Naukri and verified company profiles. We use Scrapling's `StealthyFetcher` to extract job listings.

**`sources/cutshort.py`**

```python
import logging
from scrapling.fetchers import StealthyFetcher

logger = logging.getLogger(__name__)

# Search queries tuned for backend intern/fresher in India
CUTSHORT_QUERIES = [
    "golang backend intern",
    "backend developer intern",
    "software engineer intern golang",
    "backend engineer fresher",
    "go developer fresher india",
    "backend intern fintech",
    "full stack intern golang",
]

BASE_URL = "https://cutshort.io/jobs"


def fetch_cutshort() -> list[dict]:
    """
    Scrapes Cutshort job search results.
    Cutshort uses client-side rendering, so we need StealthyFetcher.
    """
    all_jobs = []
    seen_urls = set()
    
    fetcher = StealthyFetcher()

    for query in CUTSHORT_QUERIES:
        try:
            search_url = f"{BASE_URL}?q={query.replace(' ', '+')}&remote=true"
            page = fetcher.fetch(search_url, headless=True, network_idle=True)
            
            # Job cards on Cutshort
            job_cards = page.css(".job-card") or page.css("[data-testid='job-card']")
            
            for card in job_cards:
                url_el = card.css("a[href*='/jobs/']")
                if not url_el:
                    continue
                    
                url = "https://cutshort.io" + url_el[0].attrib.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                title = card.css(".job-title, h3, [data-testid='job-title']")
                company = card.css(".company-name, [data-testid='company-name']")
                location = card.css(".location, [data-testid='location']")
                salary = card.css(".salary, [data-testid='salary']")
                
                all_jobs.append({
                    "title":       title[0].text if title else "",
                    "company":     company[0].text if company else "",
                    "location":    location[0].text if location else "India",
                    "description": "",   # fetch full JD separately if passes pre-filter
                    "url":         url,
                    "source":      "cutshort",
                    "salary":      salary[0].text if salary else "",
                    "posted_at":   "",
                })
        except Exception as e:
            logger.warning(f"Cutshort scrape failed for query '{query}': {e}")
    
    logger.info(f"Cutshort: {len(all_jobs)} jobs found")
    return all_jobs


def fetch_job_description(url: str) -> str:
    """
    Fetches the full JD for a Cutshort job listing.
    Called only for jobs that pass the pre-filter.
    """
    try:
        fetcher = StealthyFetcher()
        page = fetcher.fetch(url, headless=True, network_idle=True)
        desc = page.css(".job-description, [data-testid='job-description']")
        return desc[0].text if desc else ""
    except Exception as e:
        logger.warning(f"Failed to fetch Cutshort JD for {url}: {e}")
        return ""
```

> **Note on Cutshort selectors:** Cutshort may update their CSS classes. If selectors break, open Cutshort in DevTools → Inspect → find the class names of job cards and update the `.css()` calls above. Scrapling's adaptive feature helps here — once it has seen the element, it can re-locate it even after minor DOM changes.

---

### 6.3 Instahyre Scraping

**What this does:** Instahyre focuses on curated Indian product company roles. Their job listing data is often loaded via XHR API calls, which we can intercept directly.

**`sources/instahyre.py`**

```python
import requests
import logging

logger = logging.getLogger(__name__)

# Instahyre's internal API (found via DevTools Network tab)
# This is a public, unauthenticated endpoint
INSTAHYRE_API = "https://www.instahyre.com/api/v1/opportunity/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer":    "https://www.instahyre.com/jobs/",
    "Accept":     "application/json",
}

# Skills to search for (maps to Instahyre's skill filter)
SKILL_IDS = {
    "golang": "go",
    "python": "python",
    "backend": "backend",
}

def fetch_instahyre() -> list[dict]:
    """
    Fetches job listings from Instahyre's internal API.
    If this API changes, fall back to the Scrapling approach below.
    """
    all_jobs = []
    
    params = {
        "format":        "json",
        "skills":        "golang,go,backend",
        "experience":    "0,1",          # 0 to 1 year
        "locations":     "work-from-home,bangalore,mumbai,hyderabad",
        "limit":         50,
        "offset":        0,
    }
    
    try:
        r = requests.get(INSTAHYRE_API, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        for job in data.get("results", []):
            company = job.get("company", {})
            role = job.get("role", {})
            
            all_jobs.append({
                "title":       role.get("title", ""),
                "company":     company.get("name", ""),
                "location":    job.get("location_display", "India"),
                "description": job.get("description", "") or role.get("description", ""),
                "url":         f"https://www.instahyre.com/jobs/{job.get('id', '')}",
                "source":      "instahyre",
                "salary":      f"{job.get('salary_min', '')} - {job.get('salary_max', '')} LPA",
                "posted_at":   job.get("created", ""),
            })
    except Exception as e:
        logger.warning(f"Instahyre API fetch failed: {e}")
        # Fallback: use Scrapling if the API changes
        return _fetch_instahyre_scrapling()
    
    logger.info(f"Instahyre: {len(all_jobs)} jobs found")
    return all_jobs


def _fetch_instahyre_scrapling() -> list[dict]:
    """Fallback: scrape Instahyre with Scrapling if the API breaks"""
    from scrapling.fetchers import StealthyFetcher
    jobs = []
    try:
        fetcher = StealthyFetcher()
        page = fetcher.fetch(
            "https://www.instahyre.com/jobs/?skills=golang&exp=0-1",
            headless=True,
            network_idle=True
        )
        cards = page.css(".job-card, .opportunity-card")
        for card in cards:
            title_el = card.css("h2, h3, .job-title")
            company_el = card.css(".company-name")
            jobs.append({
                "title":       title_el[0].text if title_el else "",
                "company":     company_el[0].text if company_el else "",
                "location":    "India",
                "description": "",
                "url":         "",
                "source":      "instahyre",
                "salary":      "",
                "posted_at":   "",
            })
    except Exception as e:
        logger.error(f"Instahyre Scrapling fallback also failed: {e}")
    return jobs
```

---

### 6.4 Wellfound Scraping

**What this does:** Wellfound (formerly AngelList Talent) has strong startup coverage in India. Many early-stage funded Indian startups only post here.

**`sources/wellfound.py`**

```python
import logging
from scrapling.fetchers import DynamicFetcher  # JS-heavy, needs full browser

logger = logging.getLogger(__name__)

SEARCH_URLS = [
    "https://wellfound.com/jobs?q=golang+backend+intern&l=India&remote=true",
    "https://wellfound.com/jobs?q=backend+engineer+intern&l=India&remote=true",
    "https://wellfound.com/jobs?q=go+developer+fresher&remote=true",
]


def fetch_wellfound() -> list[dict]:
    """
    Scrapes Wellfound using DynamicFetcher (Playwright).
    Wellfound requires JavaScript rendering and has aggressive bot detection.
    """
    all_jobs = []
    seen = set()

    fetcher = DynamicFetcher()

    for url in SEARCH_URLS:
        try:
            page = fetcher.fetch(url, headless=True, network_idle=True)
            
            # Wellfound job cards
            cards = page.css("[data-test='StartupResult'], .job-listing, [class*='JobResult']")
            
            for card in cards:
                link = card.css("a[href*='/jobs/']")
                if not link:
                    continue
                job_url = "https://wellfound.com" + link[0].attrib.get("href", "")
                if job_url in seen:
                    continue
                seen.add(job_url)
                
                title_el    = card.css("[data-test='job-title'], h2, .job-title")
                company_el  = card.css("[data-test='company-name'], .company-name")
                loc_el      = card.css("[data-test='location'], .location")
                salary_el   = card.css("[data-test='compensation'], .compensation")
                
                all_jobs.append({
                    "title":       title_el[0].text.strip() if title_el else "",
                    "company":     company_el[0].text.strip() if company_el else "",
                    "location":    loc_el[0].text.strip() if loc_el else "Remote",
                    "description": "",
                    "url":         job_url,
                    "source":      "wellfound",
                    "salary":      salary_el[0].text.strip() if salary_el else "",
                    "posted_at":   "",
                })
        except Exception as e:
            logger.warning(f"Wellfound scrape failed for {url}: {e}")

    logger.info(f"Wellfound: {len(all_jobs)} jobs found")
    return all_jobs
```

---

### 6.5 Serper.dev Discovery + Scrapling Extraction

**What this does:** Two-step process. Serper.dev sends Google dork queries and returns a list of URLs. Scrapling then fetches each discovered URL to extract the actual job data. This is how you find jobs on obscure company sites, Google Forms, and career pages not indexed by any aggregator.

**`sources/serper.py`**

```python
import os
import requests
import logging
from scrapling.fetchers import StealthyFetcher, Fetcher

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"

# --- Dork templates ---
# These are tuned specifically for:
# Backend intern/fresher + Golang + India/Remote + Fintech preferred
# Rotate through all of them in a single morning run

DORK_QUERIES = [
    # Core role dorks
    '"backend intern" OR "backend fresher" "golang" OR "go" india',
    '"software engineer intern" "go" OR "golang" "bangalore" OR "remote"',
    '"backend developer" "0-1 years" OR "fresher" "golang" india',
    '"backend engineering intern" india -site:linkedin.com -site:naukri.com',
    '"junior backend developer" "go" OR "golang" india 2025',
    
    # Fintech/crypto specific — your crypto exchange project is directly relevant
    '"backend intern" "fintech" OR "payments" OR "crypto" india',
    '"software intern" "crypto" OR "blockchain" OR "defi" "golang" OR "go"',
    '"backend engineer" "fresher" "payments" india -site:linkedin.com',
    '"go developer" intern OR fresher "india" OR "remote"',
    
    # Google Form job applications (hidden from all aggregators)
    '"docs.google.com/forms" "backend intern" "golang" OR "go"',
    '"forms.gle" "apply" "software engineer" "intern" india',
    '"google form" "backend developer" "fresher" OR "intern" india 2025',
    
    # Company career pages directly
    'intitle:"careers" "backend intern" "golang" site:*.in',
    'intitle:"join us" "backend engineer" "fresher" site:*.io',
    '"we are hiring" "backend intern" "go" OR "golang" -site:linkedin.com',
    
    # Job posted on company blog/about page
    '"now hiring" "backend" "intern" "golang" india',
    '"open position" "backend engineer" "0-1" years india',
]


def search_serper(query: str) -> list[dict]:
    """Run a single Serper.dev Google search, return list of results."""
    headers = {
        "X-API-KEY":    SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q":      query,
        "gl":     "in",           # Google India results
        "hl":     "en",
        "num":    10,
    }
    try:
        r = requests.post(SERPER_URL, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get("organic", [])
    except Exception as e:
        logger.warning(f"Serper query failed '{query[:50]}': {e}")
        return []


def is_job_related_url(url: str) -> bool:
    """Quick check to avoid wasting Scrapling fetches on irrelevant pages."""
    skip_domains = ["naukri.com", "linkedin.com", "indeed.com", "glassdoor.com",
                    "shine.com", "timesjobs.com", "monsterindia.com"]
    job_signals  = ["careers", "jobs", "hiring", "apply", "forms.gle",
                    "docs.google.com/forms", "greenhouse.io", "lever.co",
                    "job", "opening", "position"]
    
    url_lower = url.lower()
    if any(d in url_lower for d in skip_domains):
        return False   # Already covered by dedicated scrapers
    return any(s in url_lower for s in job_signals)


def extract_job_from_page(url: str, title_hint: str, company_hint: str) -> dict | None:
    """
    Uses Scrapling to fetch a discovered URL and extract job details.
    Falls back to plain Fetcher for non-JS pages, uses StealthyFetcher for the rest.
    """
    try:
        # Try fast plain fetch first
        fetcher = Fetcher()
        page = fetcher.get(url, timeout=15)
        
        # Look for job-related content signals
        body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])
        if len(body_text) < 200:
            # Page might need JS — retry with stealthy browser
            fetcher2 = StealthyFetcher()
            page = fetcher2.fetch(url, headless=True, network_idle=True)
            body_text = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])
        
        # If it's a Google Form, extract the form title and description
        if "docs.google.com/forms" in url or "forms.gle" in url:
            return {
                "title":       title_hint,
                "company":     company_hint,
                "location":    "India (Google Form)",
                "description": body_text[:3000],
                "url":         url,
                "source":      "serper_google_form",
                "salary":      "",
                "posted_at":   "",
            }
        
        return {
            "title":       title_hint,
            "company":     company_hint,
            "location":    _extract_location(body_text),
            "description": body_text[:5000],  # First 5000 chars for AI
            "url":         url,
            "source":      "serper",
            "salary":      _extract_salary(body_text),
            "posted_at":   "",
        }
    except Exception as e:
        logger.warning(f"Failed to extract job from {url}: {e}")
        return None


def _extract_location(text: str) -> str:
    """Simple heuristic to find location in job description."""
    keywords = ["remote", "bangalore", "bengaluru", "mumbai", "hyderabad",
                "delhi", "ncr", "pune", "chennai", "kolkata", "india"]
    text_lower = text.lower()
    found = [k.title() for k in keywords if k in text_lower]
    return " / ".join(found[:3]) if found else "Not specified"


def _extract_salary(text: str) -> str:
    """Simple heuristic to extract salary info."""
    import re
    patterns = [
        r'\₹[\d,]+\s*[-–]\s*₹[\d,]+',
        r'[\d]+\s*[-–]\s*[\d]+\s*LPA',
        r'[\d]+k\s*[-–]\s*[\d]+k\s*per\s*month',
        r'stipend.*?₹[\d,]+',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return ""


def fetch_serper_jobs() -> list[dict]:
    """Main function: runs all dork queries and extracts jobs from discovered pages."""
    all_jobs = []
    seen_urls = set()
    
    for query in DORK_QUERIES:
        results = search_serper(query)
        
        for result in results:
            url   = result.get("link", "")
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            
            if not url or url in seen_urls:
                continue
            if not is_job_related_url(url):
                continue
                
            seen_urls.add(url)
            
            # Extract company name heuristic from URL or title
            company = _guess_company(url, title)
            
            job = extract_job_from_page(url, title, company)
            if job:
                all_jobs.append(job)
    
    logger.info(f"Serper discovery: {len(all_jobs)} jobs extracted")
    return all_jobs


def _guess_company(url: str, title: str) -> str:
    """Best-effort company name extraction from URL."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc
    domain = domain.replace("www.", "").split(".")[0]
    return domain.title()
```

**Serper.dev query budget math (for once-per-morning runs):**
```
17 dork queries × 1 run/day × 30 days = 510 queries/month
Free tier: 2,500 queries/month
Remaining: ~1,990 queries for ad-hoc manual runs
```
You will not exceed the free tier even running 4x per day.

---

### 6.6 HackerNews "Who's Hiring"

**What this does:** Every first Tuesday of the month, HN posts a "Who is Hiring?" thread where founders and engineers post directly. These are often companies that don't post anywhere else. The AI extracts structured job data from the raw comment text.

**`sources/hackernews.py`**

```python
import requests
import logging
import google.generativeai as genai
import json
import os
from datetime import datetime

logger = logging.getLogger(__name__)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# HN "Who is Hiring" thread IDs — update monthly
# Find it at: news.ycombinator.com/submitted?id=whoishiring
HN_THREAD_IDS = {
    "2025-05": 43888624,  # May 2025
    "2025-04": 43603014,  # April 2025
    # Add each month's thread ID here
}

HN_API = "https://hacker-news.firebaseio.com/v0"


def get_current_thread_id() -> int | None:
    """Returns the most recent HN hiring thread ID."""
    now = datetime.now()
    key = f"{now.year}-{now.month:02d}"
    return HN_THREAD_IDS.get(key)


def fetch_hn_comments(thread_id: int) -> list[str]:
    """Fetch all top-level comments from an HN thread."""
    # Get thread item
    r = requests.get(f"{HN_API}/item/{thread_id}.json", timeout=10)
    thread = r.json()
    
    kid_ids = thread.get("kids", [])[:150]  # Top 150 comments
    comments = []
    
    for kid_id in kid_ids:
        try:
            r2 = requests.get(f"{HN_API}/item/{kid_id}.json", timeout=5)
            item = r2.json()
            if item and item.get("text") and not item.get("deleted"):
                comments.append(item["text"])
        except Exception:
            continue
    
    return comments


def parse_comments_with_ai(comments: list[str]) -> list[dict]:
    """
    Send batches of HN comments to Gemini to extract structured job data.
    """
    model = genai.GenerativeModel("gemini-1.5-flash")
    all_jobs = []
    
    # Process in batches of 20 comments to fit context window
    batch_size = 20
    for i in range(0, len(comments), batch_size):
        batch = comments[i:i+batch_size]
        combined = "\n\n---\n\n".join(batch)
        
        prompt = f"""You are extracting job postings from HackerNews "Who Is Hiring" thread comments.
Each comment may contain zero or more job opportunities.

Extract ALL job opportunities from these comments. For each job, return a JSON array of objects with these exact fields:
- title: job title
- company: company name (look for "| CompanyName |" pattern in HN posts)
- location: location or "Remote" if remote-friendly
- description: full job description text
- url: application URL or email if mentioned
- salary: salary/stipend if mentioned, else ""
- requires_experience: estimated years of experience required as a number (0 if internship/fresher)
- tech_stack: comma-separated list of mentioned technologies

Return ONLY a JSON array. No markdown, no explanation.

Comments:
{combined}"""
        
        try:
            response = model.generate_content(prompt)
            text = response.text.strip()
            # Clean up if model wrapped in markdown
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            jobs = json.loads(text)
            for job in jobs:
                job["source"] = "hackernews"
                job["posted_at"] = datetime.now().isoformat()
            all_jobs.extend(jobs)
        except Exception as e:
            logger.warning(f"HN AI parsing batch {i} failed: {e}")
    
    return all_jobs


def fetch_hn_hiring() -> list[dict]:
    """Main function: fetches and parses the current HN Who's Hiring thread."""
    thread_id = get_current_thread_id()
    if not thread_id:
        logger.info("No HN thread ID for current month — skipping")
        return []
    
    logger.info(f"Fetching HN thread {thread_id}")
    comments = fetch_hn_comments(thread_id)
    logger.info(f"Got {len(comments)} comments — parsing with AI")
    jobs = parse_comments_with_ai(comments)
    logger.info(f"HackerNews: {len(jobs)} jobs extracted")
    return jobs
```

---

### 6.7 Reddit India Sources

**`sources/reddit.py`**

```python
import feedparser
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Reddit RSS feeds — free, no auth required
REDDIT_FEEDS = [
    "https://www.reddit.com/r/developersIndia/search.rss?q=hiring+intern&sort=new&t=week",
    "https://www.reddit.com/r/developersIndia/search.rss?q=backend+fresher&sort=new&t=week",
    "https://www.reddit.com/r/IndiaHiring/search.rss?q=backend+golang&sort=new&t=week",
    "https://www.reddit.com/r/IndiaHiring/new.rss",
    "https://www.reddit.com/r/forhire/search.rss?q=golang+backend+remote&sort=new&t=week",
]

def fetch_reddit() -> list[dict]:
    all_jobs = []
    seen = set()
    
    for feed_url in REDDIT_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                url = entry.get("link", "")
                if url in seen:
                    continue
                seen.add(url)
                
                # Reddit posts are often [HIRING] prefixed
                title = entry.get("title", "")
                if not any(kw in title.lower() for kw in
                           ["hiring", "backend", "golang", "go ", "intern", "fresher", "job"]):
                    continue  # Skip non-job posts early
                
                all_jobs.append({
                    "title":       title,
                    "company":     _extract_company_from_reddit(title),
                    "location":    _extract_location_hint(title + entry.get("summary", "")),
                    "description": entry.get("summary", ""),
                    "url":         url,
                    "source":      "reddit",
                    "salary":      "",
                    "posted_at":   entry.get("published", ""),
                })
        except Exception as e:
            logger.warning(f"Reddit feed failed {feed_url}: {e}")
    
    logger.info(f"Reddit: {len(all_jobs)} posts found")
    return all_jobs


def _extract_company_from_reddit(title: str) -> str:
    """
    Reddit job posts often follow patterns like:
    '[HIRING] Backend Intern @ CompanyName'
    '[FOR HIRE]...' (ignore these — they're someone looking for work)
    """
    import re
    if "[for hire]" in title.lower():
        return "CANDIDATE_POST"  # Will be filtered out in pre-filter
    
    # Look for @ symbol
    m = re.search(r'@\s*([A-Za-z0-9\s]+)', title)
    if m:
        return m.group(1).strip()
    return ""


def _extract_location_hint(text: str) -> str:
    text_lower = text.lower()
    if "remote" in text_lower:
        return "Remote"
    if "india" in text_lower:
        return "India"
    return "Not specified"
```

---

## 7. Deduplication & Storage

**`storage/db.py`**

```python
import sqlite3
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
DB_PATH = "data/jobradar.db"


def init_db():
    """Create tables if they don't exist."""
    import os
    os.makedirs("data", exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,   -- MD5 hash
            title        TEXT,
            company      TEXT,
            location     TEXT,
            description  TEXT,
            url          TEXT,
            source       TEXT,
            salary       TEXT,
            posted_at    TEXT,
            seen_at      TEXT,
            score        INTEGER DEFAULT 0,
            score_reason TEXT,
            highlights   TEXT,
            red_flags    TEXT,
            notified     INTEGER DEFAULT 0   -- 0=no, 1=telegram, 2=digest
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_log (
            run_at       TEXT,
            total_raw    INTEGER,
            after_dedup  INTEGER,
            after_filter INTEGER,
            after_score  INTEGER,
            notified     INTEGER
        )
    """)
    conn.commit()
    conn.close()


def make_job_id(job: dict) -> str:
    """Deterministic hash for deduplication."""
    key = f"{job.get('title','').lower().strip()}" \
          f"{job.get('company','').lower().strip()}" \
          f"{job.get('location','').lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def is_duplicate(job: dict) -> bool:
    """Returns True if this job was already seen."""
    job_id = make_job_id(job)
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return row is not None


def save_job(job: dict, score: int = 0, reason: str = "",
             highlights: str = "", red_flags: str = "", notified: int = 0):
    """Save a job to the database."""
    job_id = make_job_id(job)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO jobs
        (id, title, company, location, description, url, source,
         salary, posted_at, seen_at, score, score_reason, highlights, red_flags, notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        job_id,
        job.get("title", ""),
        job.get("company", ""),
        job.get("location", ""),
        job.get("description", ""),
        job.get("url", ""),
        job.get("source", ""),
        job.get("salary", ""),
        job.get("posted_at", ""),
        datetime.now().isoformat(),
        score,
        reason,
        highlights,
        red_flags,
        notified,
    ))
    conn.commit()
    conn.close()


def get_jobs_by_score(min_score: int = 6) -> list[dict]:
    """Retrieve jobs above a score threshold for digest."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT title, company, location, url, salary, score, score_reason, highlights
        FROM jobs WHERE score >= ? AND notified = 0
        ORDER BY score DESC
    """, (min_score,)).fetchall()
    conn.close()
    return [
        dict(zip(["title","company","location","url","salary",
                  "score","reason","highlights"], row))
        for row in rows
    ]
```

**`pipeline/dedup.py`**

```python
from storage.db import is_duplicate, make_job_id
import logging

logger = logging.getLogger(__name__)


def deduplicate(jobs: list[dict]) -> list[dict]:
    """
    Removes:
    1. Jobs already seen in the database (persisted dedup)
    2. Duplicates within the current batch (in-memory dedup)
    """
    seen_this_run = set()
    new_jobs = []
    
    for job in jobs:
        job_id = make_job_id(job)
        
        if job_id in seen_this_run:
            continue
        if is_duplicate(job):
            continue
        
        seen_this_run.add(job_id)
        new_jobs.append(job)
    
    logger.info(f"Dedup: {len(jobs)} raw → {len(new_jobs)} new")
    return new_jobs
```

---

## 8. AI Filtering Engine

This is the brain. It has two stages: a **hard pre-filter** (rule-based, free, instant) and an **AI scorer** (Gemini, smart, costs tokens). Run them in this order — most jobs get dropped in pre-filter, saving Gemini quota.

### 8.1 Hard Pre-filter

**`pipeline/prefilter.py`**

```python
import yaml
import re
import logging

logger = logging.getLogger(__name__)

def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def check_experience(description: str, title: str, profile: dict) -> tuple[bool, str]:
    """
    Returns (should_reject, reason).
    This is the most important filter — rejects 80% of postings for you.
    """
    text = (description + " " + title).lower()
    
    reject_keywords = profile["hard_reject"]["experience_keywords"]
    for kw in reject_keywords:
        if kw.lower() in text:
            return True, f"Experience requirement: '{kw}' found"
    
    # Also check for numeric patterns like "5 years" or "3+ years"
    year_patterns = [
        r'\b([2-9]|\d{2,})\+?\s*years?\s*(of\s+)?(experience|exp)\b',
        r'experience[:\s]+([2-9]|\d{2,})\+?\s*years?',
        r'minimum\s+([2-9]|\d{2,})\s*years?',
    ]
    for pat in year_patterns:
        m = re.search(pat, text)
        if m:
            return True, f"Experience regex matched: {m.group(0)}"
    
    return False, ""


def check_location(description: str, title: str, profile: dict) -> tuple[bool, str]:
    """Reject in-office jobs outside India."""
    text = (description + " " + title).lower()
    
    for loc_kw in profile["candidate"]["location"]["hard_reject"]:
        if loc_kw.lower() in text:
            return True, f"Location rejected: '{loc_kw}'"
    
    return False, ""


def check_company_blacklist(company: str, profile: dict) -> tuple[bool, str]:
    blacklist = [c.lower() for c in profile["hard_reject"]["company_blacklist"]]
    if company.lower() in blacklist:
        return True, f"Company blacklisted: {company}"
    return False, ""


def check_role_blacklist(title: str, profile: dict) -> tuple[bool, str]:
    title_lower = title.lower()
    for role in profile["hard_reject"]["role_blacklist"]:
        if role.lower() in title_lower:
            return True, f"Role blacklisted: {role}"
    return False, ""


def check_candidate_post(job: dict) -> tuple[bool, str]:
    """Filter out people looking for jobs (not companies hiring)."""
    company = job.get("company", "")
    if company == "CANDIDATE_POST":
        return True, "This is a candidate post, not a job opening"
    title = job.get("title", "").lower()
    if title.startswith("[for hire]"):
        return True, "Candidate post"
    return False, ""


def prefilter(jobs: list[dict], profile: dict) -> list[dict]:
    """
    Runs all hard filters. Jobs that pass all checks go to AI scorer.
    Jobs that fail are saved to DB with score=0 for reference.
    """
    from storage.db import save_job
    
    passed = []
    
    for job in jobs:
        title       = job.get("title", "")
        company     = job.get("company", "")
        description = job.get("description", "")
        
        checks = [
            check_candidate_post(job),
            check_company_blacklist(company, profile),
            check_role_blacklist(title, profile),
            check_experience(description, title, profile),
            check_location(description, title, profile),
        ]
        
        rejected = False
        for should_reject, reason in checks:
            if should_reject:
                # Save to DB with score 0 so we know it was seen
                save_job(job, score=0, reason=f"Pre-filtered: {reason}")
                logger.debug(f"REJECTED '{title}' @ '{company}': {reason}")
                rejected = True
                break
        
        if not rejected:
            passed.append(job)
    
    logger.info(f"Pre-filter: {len(jobs)} jobs → {len(passed)} passed")
    return passed
```

### 8.2 AI Scorer

**`pipeline/scorer.py`**

```python
import os
import json
import yaml
import logging
import google.generativeai as genai
from storage.db import save_job

logger = logging.getLogger(__name__)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_scoring_prompt(job: dict, profile: dict) -> str:
    candidate = profile["candidate"]
    
    return f"""You are a job relevance scorer for a specific candidate. Your job is to score how relevant a job posting is for this person.

## CANDIDATE PROFILE

Name: {candidate['name']}
Current level: Fresher / 0 years experience

Target roles (in priority order):
{chr(10).join('- ' + r for r in candidate['roles']['primary'])}
Also acceptable: {', '.join(candidate['roles']['secondary'])}

Tech stack:
- Strong: {', '.join(candidate['skills']['strong'])}
- Learning: {', '.join(candidate['skills']['learning'])}

Key project: {candidate['projects'][0]['name']}
Project description: {candidate['projects'][0]['description']}
Project relevance signal: {candidate['projects'][0]['relevance_signal']}

Location: {candidate['location']['base']}
Acceptable locations: {', '.join(candidate['location']['acceptable'])}

High-priority industries (give bonus):
{', '.join(candidate['industries']['high_priority'])}

Medium-priority industries:
{', '.join(candidate['industries']['medium_priority'])}

## JOB POSTING TO SCORE

Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Location: {job.get('location', 'N/A')}
Salary/Stipend: {job.get('salary', 'Not mentioned')}
Source: {job.get('source', 'N/A')}

Job Description:
{job.get('description', 'No description available')[:4000]}

## SCORING INSTRUCTIONS

Score from 1 to 10 where:
- 10 = Perfect match (Golang + fintech/crypto + intern/fresher + remote/India, crypto exchange project directly relevant)
- 8-9 = Very strong match (backend intern, uses Go or compatible stack)  
- 6-7 = Good match (backend adjacent, could be relevant)
- 4-5 = Weak match (tangentially related)
- 1-3 = Not relevant (wrong role, wrong stack, unclear)

IMPORTANT rules:
- If the role requires more than 1 year of experience: score MUST be 0-2 max (pre-filter should have caught this but double-check)
- If the company is in fintech/crypto/payments AND uses Go: add +2 to base score
- If location is outside India AND in-office: score MUST be 1
- If the candidate's crypto exchange project is directly relevant to this company (fintech, crypto, trading, payments): add +2 to base score
- If Golang/Go is mentioned in requirements or stack: add +2 to base score

Return ONLY a valid JSON object with these exact keys:
{{
  "score": <integer 1-10>,
  "reason": "<2-3 sentence explanation of the score>",
  "highlights": ["<key reason 1>", "<key reason 2>", "<key reason 3>"],
  "red_flags": ["<issue 1 if any>"],
  "golang_match": <true/false>,
  "fintech_match": <true/false>,
  "apply_urgency": "<high/medium/low>",
  "estimated_experience_required": "<0 / 0-1 / 1-2 / unknown>"
}}"""


def score_job(job: dict, profile: dict) -> dict:
    """Score a single job with Gemini. Returns job dict with score fields added."""
    model = genai.GenerativeModel("gemini-1.5-flash")
    
    try:
        prompt = build_scoring_prompt(job, profile)
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # Clean markdown code fences if model adds them
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()
        
        result = json.loads(text)
        
        job["score"]       = result.get("score", 0)
        job["reason"]      = result.get("reason", "")
        job["highlights"]  = ", ".join(result.get("highlights", []))
        job["red_flags"]   = ", ".join(result.get("red_flags", []))
        job["urgency"]     = result.get("apply_urgency", "low")
        
        logger.info(f"Scored: {job['title']} @ {job['company']} → {job['score']}/10")
        return job
        
    except Exception as e:
        logger.error(f"Gemini scoring failed for {job.get('title', '?')}: {e}")
        job["score"]    = -1  # Flag as unscored
        job["reason"]   = f"Scoring error: {e}"
        job["highlights"] = ""
        job["red_flags"]  = ""
        job["urgency"]    = "low"
        return job


def score_all(jobs: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Score all jobs and split into buckets.
    Returns: (urgent_jobs, digest_jobs, low_jobs)
    """
    profile = load_profile()
    urgent  = []  # score 8-10
    digest  = []  # score 6-7
    low     = []  # score < 6
    
    for job in jobs:
        scored_job = score_job(job, profile)
        
        # Save to DB regardless of score
        save_job(
            scored_job,
            score      = scored_job["score"],
            reason     = scored_job.get("reason", ""),
            highlights = scored_job.get("highlights", ""),
            red_flags  = scored_job.get("red_flags", ""),
        )
        
        if scored_job["score"] >= 8:
            urgent.append(scored_job)
        elif scored_job["score"] >= 6:
            digest.append(scored_job)
        else:
            low.append(scored_job)
    
    logger.info(f"Scoring complete: {len(urgent)} urgent, {len(digest)} digest, {len(low)} low")
    return urgent, digest, low
```

---

## 9. Notification Layer

### 9.1 Telegram Bot

**`notify/telegram_bot.py`**

```python
import os
import asyncio
import logging
from telegram import Bot
from telegram.constants import ParseMode
from storage.db import save_job

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def format_job_message(job: dict) -> str:
    """Format a job into a rich Telegram message."""
    
    score    = job.get("score", 0)
    urgency  = job.get("urgency", "low")
    
    # Score emoji
    if score >= 9:
        score_emoji = "🔥🔥"
    elif score >= 8:
        score_emoji = "🔥"
    elif score >= 7:
        score_emoji = "⚡"
    else:
        score_emoji = "💡"
    
    # Urgency label
    urgency_label = {"high": "Apply Today", "medium": "Apply Soon", "low": "Review"}.get(urgency, "Review")
    
    highlights = job.get("highlights", "")
    highlight_lines = ""
    if highlights:
        for h in highlights.split(", ")[:3]:
            highlight_lines += f"  ✅ {h}\n"
    
    red_flags = job.get("red_flags", "")
    red_flag_lines = ""
    if red_flags and red_flags != "None":
        for rf in red_flags.split(", ")[:2]:
            red_flag_lines += f"  ⚠️ {rf}\n"
    
    salary_line = f"\n💰 {job.get('salary', '')}" if job.get("salary") else ""
    
    msg = f"""{score_emoji} *{job.get('title', 'N/A')}*
🏢 {job.get('company', 'N/A')}
📍 {job.get('location', 'N/A')}{salary_line}
📊 Score: *{score}/10* — {urgency_label}

*Why it matches:*
{highlight_lines if highlight_lines else "  — See job description"}"""
    
    if red_flag_lines:
        msg += f"\n*Watch out:*\n{red_flag_lines}"
    
    msg += f"\n🔗 [Apply Here]({job.get('url', '')})"
    msg += f"\n_Source: {job.get('source', 'unknown')}_"
    
    return msg


async def send_job_alert(job: dict):
    """Send a single job alert to Telegram."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    message = format_job_message(job)
    
    try:
        await bot.send_message(
            chat_id    = TELEGRAM_CHAT_ID,
            text       = message,
            parse_mode = ParseMode.MARKDOWN,
            disable_web_page_preview = True,
        )
        logger.info(f"Telegram: sent alert for {job['title']} @ {job['company']}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


async def send_run_summary(total_raw: int, passed_filter: int, scored: int, urgent: int):
    """Send a short summary message after each run."""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    msg = f"""🤖 *JobRadar Run Complete*
📥 Raw jobs fetched: {total_raw}
🔍 Passed pre-filter: {passed_filter}
🧠 AI scored: {scored}
🔥 High-priority alerts: {urgent}

_Check digest for score 6–7 jobs_"""
    
    try:
        await bot.send_message(
            chat_id    = TELEGRAM_CHAT_ID,
            text       = msg,
            parse_mode = ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Telegram summary send failed: {e}")


def notify_urgent_jobs(urgent_jobs: list[dict]):
    """Send instant alerts for all urgent (score 8+) jobs."""
    async def _send_all():
        for job in urgent_jobs:
            await send_job_alert(job)
            await asyncio.sleep(1)  # 1s gap between messages
    
    asyncio.run(_send_all())
```

### 9.2 How to set up your Telegram Bot

```
1. Open Telegram → search @BotFather
2. Send: /newbot
3. Choose a name (e.g. "JobRadar Bot") and username (e.g. "my_jobradar_bot")
4. BotFather gives you a token — copy it to .env as TELEGRAM_BOT_TOKEN

5. Start your bot (send it any message)
6. Open: https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
7. Find "chat":{"id": XXXXXXX} — that's your TELEGRAM_CHAT_ID
8. Copy it to .env as TELEGRAM_CHAT_ID
```

---

## 10. Main Orchestrator

**`main.py`**

```python
import logging
import asyncio
import yaml
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/jobradar.log"),
    ]
)
logger = logging.getLogger("jobradar")

from storage.db import init_db
from sources.ats import fetch_all_ats, load_companies
from sources.cutshort import fetch_cutshort
from sources.instahyre import fetch_instahyre
from sources.wellfound import fetch_wellfound
from sources.serper import fetch_serper_jobs
from sources.hackernews import fetch_hn_hiring
from sources.reddit import fetch_reddit
from pipeline.dedup import deduplicate
from pipeline.prefilter import prefilter, load_profile
from pipeline.scorer import score_all
from notify.telegram_bot import notify_urgent_jobs, send_run_summary


def run():
    logger.info("=" * 50)
    logger.info("JobRadar pipeline starting")
    logger.info("=" * 50)
    
    # --- Setup ---
    init_db()
    profile   = load_profile()
    companies = load_companies()
    
    # --- SOURCE LAYER ---
    # Each source is independent — a failure in one doesn't stop the rest
    raw_jobs = []
    
    logger.info("--- Fetching ATS endpoints ---")
    raw_jobs.extend(fetch_all_ats(companies))
    
    logger.info("--- Fetching Cutshort ---")
    raw_jobs.extend(fetch_cutshort())
    
    logger.info("--- Fetching Instahyre ---")
    raw_jobs.extend(fetch_instahyre())
    
    logger.info("--- Fetching Wellfound ---")
    raw_jobs.extend(fetch_wellfound())
    
    logger.info("--- Fetching via Serper discovery ---")
    raw_jobs.extend(fetch_serper_jobs())
    
    logger.info("--- Fetching HackerNews ---")
    raw_jobs.extend(fetch_hn_hiring())
    
    logger.info("--- Fetching Reddit ---")
    raw_jobs.extend(fetch_reddit())
    
    total_raw = len(raw_jobs)
    logger.info(f"Total raw jobs from all sources: {total_raw}")
    
    # --- DEDUPLICATION ---
    new_jobs = deduplicate(raw_jobs)
    
    # --- PRE-FILTER ---
    # Drops ~80% of remaining jobs with zero AI cost
    eligible_jobs = prefilter(new_jobs, profile)
    
    if not eligible_jobs:
        logger.info("No new eligible jobs after pre-filter. Done.")
        asyncio.run(send_run_summary(total_raw, 0, 0, 0))
        return
    
    # --- AI SCORING ---
    urgent_jobs, digest_jobs, low_jobs = score_all(eligible_jobs)
    
    # --- NOTIFICATIONS ---
    if urgent_jobs:
        logger.info(f"Sending {len(urgent_jobs)} urgent Telegram alerts")
        notify_urgent_jobs(urgent_jobs)
    
    # Send run summary
    asyncio.run(send_run_summary(
        total_raw     = total_raw,
        passed_filter = len(eligible_jobs),
        scored        = len(eligible_jobs),
        urgent        = len(urgent_jobs),
    ))
    
    logger.info("Pipeline complete.")
    logger.info(f"Summary: {total_raw} raw → {len(new_jobs)} new → {len(eligible_jobs)} eligible → {len(urgent_jobs)} urgent")


if __name__ == "__main__":
    run()
```

---

## 11. Scheduler — GitHub Actions

**`.github/workflows/jobradar.yml`**

```yaml
name: JobRadar — Daily Pipeline

on:
  schedule:
    # 8:00 AM IST = 2:30 AM UTC
    - cron: '30 2 * * *'
  
  # Also allow manual trigger from GitHub UI
  workflow_dispatch:

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Cache pip dependencies
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          scrapling install --browsers chromium
      
      # Restore the SQLite database from previous run
      - name: Restore database cache
        uses: actions/cache@v4
        with:
          path: data/jobradar.db
          key: jobradar-db-${{ github.run_number }}
          restore-keys: |
            jobradar-db-
      
      - name: Run JobRadar pipeline
        env:
          SERPER_API_KEY:       ${{ secrets.SERPER_API_KEY }}
          GEMINI_API_KEY:       ${{ secrets.GEMINI_API_KEY }}
          TELEGRAM_BOT_TOKEN:   ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:     ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          mkdir -p data
          python main.py
      
      - name: Upload run log as artifact
        uses: actions/upload-artifact@v4
        with:
          name: jobradar-log-${{ github.run_number }}
          path: data/jobradar.log
          retention-days: 7
```

**Setting GitHub Secrets:**
```
Repository → Settings → Secrets and variables → Actions → New repository secret

Add these four secrets:
  SERPER_API_KEY
  GEMINI_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
```

> **Note on DB persistence:** GitHub Actions doesn't have permanent disk storage. The `actions/cache` trick above persists the SQLite DB between runs using GitHub's cache API (free, up to 10GB). This is how deduplication works across daily runs. The cache key increments each run (`jobradar-db-${{ github.run_number }}`), so old caches are automatically retired.

---

## 12. Keyword Reference

These are the most effective search keywords for your specific profile. Use these in `profile.yaml`, dork queries, and Cutshort/Instahyre search filters.

### High-signal role keywords
```
backend engineering intern
backend developer intern
software engineering intern
junior backend developer
go developer intern
golang developer fresher
backend intern
software developer fresher
junior software engineer
entry level backend developer
```

### Golang-specific signals
```
golang backend
go developer
go lang
golang rest api
go microservices
gin framework
echo framework
fiber golang
goroutine
go concurrency
```

### Fintech/crypto company signals (your crypto exchange project is relevant here)
```
payments infrastructure
payment gateway
crypto exchange
blockchain backend
defi protocol
trading platform
order management system
financial technology
neobank backend
wallet infrastructure
remittance tech
lending tech
```

### Experience level filter keywords (reject if found)
```
2+ years          ← HARD REJECT
3+ years          ← HARD REJECT
senior engineer   ← HARD REJECT
lead developer    ← HARD REJECT
principal         ← HARD REJECT
1-3 years         ← BORDERLINE (check full JD)
```

### Positive experience signals (accept these)
```
fresher
0-1 years
recent graduate
entry level
no experience required
intern
trainee
associate developer (sometimes OK)
```

---

## 13. Running Locally

```bash
# First time setup
cd jobradar
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
scrapling install

# Copy and fill in your secrets
cp .env.example .env
# → edit .env with your actual keys

# Run the full pipeline once
python main.py

# Run a specific source only (for testing)
python -c "from sources.ats import fetch_all_ats, load_companies; print(fetch_all_ats(load_companies())[:2])"
python -c "from sources.cutshort import fetch_cutshort; print(fetch_cutshort()[:2])"
python -c "from sources.serper import fetch_serper_jobs; print(fetch_serper_jobs()[:2])"

# Check what's in the database
sqlite3 data/jobradar.db "SELECT title, company, score FROM jobs ORDER BY score DESC LIMIT 20;"

# Manually trigger a Telegram test
python -c "
import asyncio
from notify.telegram_bot import send_run_summary
asyncio.run(send_run_summary(100, 20, 10, 3))
"
```

---

## 14. Troubleshooting

### Scrapling browser not found
```bash
scrapling install --force   # Reinstall browsers
scrapling install --browsers chromium firefox  # Install specific browsers
```

### Gemini API rate limit hit
The free tier allows 15 requests per minute. If you're hitting this:
```python
import time
# In scorer.py, add between jobs:
time.sleep(4)  # 4s gap = max 15/min
```

### GitHub Actions DB not persisting
Check the cache hit in Actions logs. If it says `Cache not found`, the previous run's cache expired (GitHub caches expire after 7 days of no access). First run after expiry will re-initialize a fresh DB — jobs will be re-evaluated but no harm done.

### ATS endpoint returns 404
The company may have changed their ATS or slug. Check their careers page URL directly. Update `companies.yaml` accordingly.

### Cutshort/Wellfound CSS selectors broke
Open the site → DevTools → Inspect the job card → find the current class name → update the `.css()` call in the source file. Scrapling's adaptive mode will then remember the new location for future runs.

### Too many irrelevant jobs getting through
Tighten `profile.yaml`:
- Add more `experience_keywords` to `hard_reject`
- Add companies to `company_blacklist`
- Increase the score threshold from 6 to 7 in `main.py`

### No jobs showing up at all
1. Check `data/jobradar.log` for errors
2. Run each source individually (see §13)
3. Check SQLite DB — jobs might be there but all scored < 6
4. Verify API keys are set correctly

---

*Last updated: May 2025 | Built for backend intern/fresher search in India*