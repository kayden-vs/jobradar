# JobRadar — Complete Setup & Customisation Guide

> **Who this is for:** Someone from any technical domain — cybersecurity, data engineering, mobile development, ML, DevOps, whatever — who has just cloned the repo and wants to get personalised job alerts delivered to their Telegram. No assumptions about your stack or background.

---

## Table of Contents

1. [What You're Setting Up](#1-what-youre-setting-up)
2. [Prerequisites](#2-prerequisites)
3. [Clone & Install](#3-clone--install)
4. [API Keys — What You Need & How to Get Them](#4-api-keys--what-you-need--how-to-get-them)
5. [Create Your `.env` File](#5-create-your-env-file)
6. [Configure `profile.yaml` — The Complete Field Reference](#6-configure-profileyaml--the-complete-field-reference)
7. [Naukri-Specific Config Block](#7-naukri-specific-config-block)
8. [Ranker Weights — Advanced Tuning](#8-ranker-weights--advanced-tuning)
9. [What Is NOT Profile-Configurable (Honest Limitations)](#9-what-is-not-profile-configurable-honest-limitations)
10. [Validate Your Setup — Dry Run](#10-validate-your-setup--dry-run)
11. [Run the Pipeline](#11-run-the-pipeline)
12. [Automate with Cron or EC2](#12-automate-with-cron-or-ec2)
13. [What Each Source Actually Does](#13-what-each-source-actually-does)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. What You're Setting Up

JobRadar is a pipeline that:

1. **Fetches** jobs from up to 17 sources (ATS APIs, Naukri, Internshala, YC, RSS feeds, Serper Google dorks, Telegram channels, etc.)
2. **Deduplicates** across sources and across runs (SQLite DB — never see the same job twice)
3. **Filters** with pure-Python rules — no AI cost for ~90% of jobs
4. **Ranks** remaining jobs by heuristic relevance score (compiled from your `profile.yaml`)
5. **Scores** top jobs with Gemini AI (free tier) on a 1–10 scale
6. **Alerts** you via Telegram — instant push for score ≥ 8, summary card at the end of every run

```
~8,000 raw jobs  →  ~600 new  →  ~100 eligible  →  ~89 AI-scored  →  2–6 urgent alerts
```

Everything runs in Python. No Docker, no databases to maintain, no cloud infra required to start.

---

## 2. Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Check with `python3 --version`. The code uses `str \| None` union syntax which requires 3.10+, but 3.11 is recommended. |
| **pip** | Comes with Python. |
| **Git** | To clone the repo. |
| **Internet access** | All sources are fetched at runtime. |
| **A Telegram account** | Free. You'll create a bot in 2 minutes. |
| **A Gemini account** | Free. API key from [aistudio.google.com](https://aistudio.google.com). |
| **A Serper account** | Optional but recommended. 2,500 free credits/month at serper.dev. Can disable. |

> **Linux/macOS:** All commands below work as-is.
> **Windows:** Run inside WSL (Windows Subsystem for Linux). The `run.sh` automation script assumes Linux.

---

## 3. Clone & Install

```bash
git clone https://github.com/your-username/jobradar.git
cd jobradar

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate      # On Windows (WSL): same command

# Install all dependencies
pip install -r requirements.txt
```

**Dependencies installed** (`requirements.txt`):
- `scrapling[fetchers]` — HTTP fetching for career pages
- `requests`, `httpx`, `aiohttp` — HTTP clients
- `feedparser` — RSS parsing (freshers blogs)
- `groq` — Groq AI client
- `python-telegram-bot==20.7` — Telegram notifications
- `pyyaml` — YAML config parsing
- `python-dotenv` — `.env` file loading
- `beautifulsoup4`, `lxml` — HTML parsing
- `python-dateutil` — Date parsing (handles "3 days ago", ISO dates, Unix timestamps)
- `schedule` — (imported but run via cron in production)

---

## 4. API Keys — What You Need & How to Get Them

### 4.1 Gemini API Key (Required — free)

Gemini scores your jobs with AI. The free tier is generous enough to run twice daily with room to spare.

1. Go to **https://aistudio.google.com**
2. Sign in with your Google account → **Get API key** → **Create API key**
3. Copy it — looks like `AIzaSy_xxxxxxxxxxxxxxxxxxxx`

**Free tier limits** (gemini-3.1-flash-lite, as of July 2026):
- ~15 RPM → pipeline throttles to 13.3 RPM (4.5s gap between calls)
- ~1,500 RPD → 130 jobs × 2 runs = 260 RPD (17% of budget)
- ~250K TPM → no token bottleneck at this scale

### 4.2 Serper API Key (Recommended — free)

Serper runs Google searches to find jobs on company career pages, alternative ATS platforms (Keka, Freshteam, Zoho), and hidden applications (Google Forms, Notion).

1. Go to **https://serper.dev**
2. Sign up → **Dashboard** → copy your API key
3. Free tier: **2,500 searches/month**. The pipeline uses 25/run × 2 runs/day × 30 days = 1,500/month (60% of free tier).

> **Can I skip Serper?** Yes. Set `serper: false` in `profile.yaml` sources block. You'll lose discovery of jobs not on mainstream ATS platforms.

### 4.3 Telegram Bot (Required for alerts)

You need two things: a **bot token** and your **chat ID**.

**Step 1 — Create the bot:**
1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Follow prompts: give it a name (e.g. "My Job Radar") and a username (e.g. `myjobradur_bot`)
4. BotFather replies with your token: `1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Step 2 — Get your chat ID:**
1. Search for **@userinfobot** in Telegram
2. Send it any message
3. It replies with your user ID (a number like `987654321`)

**Step 3 — Start a chat with your bot:**
- Find your bot by its username and send it `/start` — this is required before it can message you.

### 4.4 Telegram MTProto Credentials (Optional — for Telegram Channels source)

The `telegram_channels` source reads public Indian job channels via the official MTProto API (not HTML scraping). Requires a one-time interactive login to generate a session string.

**Step 1 — Get API credentials** (free, one-time):
1. Go to **https://my.telegram.org** and sign in with your personal phone number
2. Click **API development tools** → create an app (any name/platform)
3. Copy the `App api_id` (integer) and `App api_hash` (hex string)

**Step 2 — Add to `.env`:**
```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
```

**Step 3 — Run the one-time login script** (interactive, run locally — NOT on EC2):
```bash
python tools/telethon_login.py
```
Enter your phone number (international format) and the OTP Telegram sends to your app. The script prints a session string and offers to auto-append it to `.env`:
```env
TELEGRAM_SESSION_STRING=1BQANOTEuA...
```

**After setup:** All subsequent runs are fully headless. Copy the three env vars to your EC2 `.env`. No `.session` file to manage — the `StringSession` lives entirely in the env var, survives reboots.

> **Can I skip this?** Yes. Set `telegram_channels: false` in `profile.yaml`. The pipeline gracefully returns `[]` if the env vars are missing.

---

## 5. Create Your `.env` File

In the root of the repo, create a file named `.env`:

```env
GEMINI_API_KEY=AIzaSy_xxxxxxxxxxxxxxxxxxxx
SERPER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=987654321

# Telegram Channels source (optional — see Section 4.4 above)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_SESSION_STRING=1BQANOTEuA...
```

> **Important:** Never commit `.env` to git. It's already in `.gitignore`.

The pipeline loads these with `python-dotenv` at startup. If any key is missing, the relevant feature fails gracefully (e.g. missing `SERPER_API_KEY` skips all Serper queries with a warning; missing `TELEGRAM_*` causes notifications to fail; missing Telegram MTProto vars causes `telegram_channels` source to return `[]` with a log warning).

---

## 6. Configure `profile.yaml` — The Complete Field Reference

This is the only file you need to edit to fully personalise the pipeline. Open `profile.yaml` and work through each section.

### 6.1 Source Toggles

```yaml
sources:
  ats:            true    # 9 ATS platforms (Greenhouse, Lever, Ashby, Workable, etc.)
  naukri:         true    # Naukri.com — India's largest job board
  internshala:    true    # Internshala — internships & fresher jobs (India)
  yc:             true    # Y Combinator portfolio company jobs
  freshers_blogs: true    # 8+ Indian fresher blogs via RSS
  serper:         true    # Google dork discovery (requires SERPER_API_KEY)
  hackernews:     false   # HN "Who is Hiring?" monthly thread
  hirist:         false   # Hirist.tech — India niche tech board
  reddit:         false   # Reddit job feeds (mostly candidate-seeking posts)
  cutshort:       false   # Cutshort.io (API unreliable)
  instahyre:      false   # Instahyre
  wellfound:      false   # Wellfound/AngelList (blocks bots)
  jobicy:         true    # Jobicy.com — free remote jobs API
  remoteok:       true    # RemoteOK — dev/engineering remote jobs
  hiringcafe:     true    # hiring.cafe — entry-level filtered aggregated jobs
  telegram_channels: true # 6 Indian Telegram job channels via MTProto API
                          # Requires: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING
                          # One-time setup: python tools/telethon_login.py
```

**Turn a source off** by setting it to `false`. The pipeline checks this before making any network calls.

**Recommended starting config:** Keep `ats`, `naukri`, `internshala`, `yc`, `serper`, `jobicy`, `remoteok`, `hiringcafe`, and `telegram_channels` enabled.

### 6.2 Candidate Section

This is the core of personalisation. Fill this in as accurately as possible — the AI reads these fields when scoring every job.

```yaml
candidate:
  name: "Your Full Name"
  email: "you@email.com"
```

#### Roles

List what you're actively looking for. Be specific. These are fed directly into the AI scoring prompt.

```yaml
  roles:
    primary:
      - "Cybersecurity Analyst Intern"
      - "Security Engineering Intern"
      - "SOC Analyst Fresher"
      - "Penetration Tester Intern"
    secondary:
      - "Security Operations Intern"
      - "Information Security Fresher"
```

> The distinction between `primary` and `secondary` is used by the AI prompt — primary roles get stronger matching weight. There's no hard limit on how many you list.

#### Experience

```yaml
  experience:
    years: 0                   # How many years of professional experience you have
    max_required: 1            # HARD REJECT — any job requiring > this many years is dropped
                               # BEFORE any AI call, saving tokens.
    acceptable_labels:
      - "fresher"
      - "0-1 years"
      - "intern"
      - "entry level"
      - "junior"
      - "trainee"
```

> `max_required` is enforced by two mechanisms:
> 1. Naukri Stage-1 filter: checks `minExp` field directly (numeric, free)
> 2. `prefilter.py`: scans description for "2+ years", "3+ years", "senior", "tech lead", etc. (configurable via `hard_reject.experience_keywords`)

#### Skills

```yaml
  skills:
    strong:                    # Your primary skills — listed in order of strength
      - "Python"
      - "C"
      - "Linux"
      - "Networking"
      - "Wireshark"
      - "Burp Suite"
      - "Bash"
    learning:                  # Skills you're actively picking up
      - "Kubernetes"
      - "AWS"
```

**How skills are used:**
- **AI scorer**: Full list passed to the Groq prompt for contextual scoring
- **Heuristic ranker**: `strong[0:3]` → "primary skill" (+5 bonus if in title, +3 if in description only). `strong[3:]` + `learning` → "secondary skill" (+3/+1). Pattern matching is compiled dynamically from your list — no code changes needed.
- **Serper query builder**: Extracts search-friendly tokens (e.g. "Python" → `"python"`, "Networking" → `"networking"`) and injects them into Google dork queries

#### Projects

Each project has a `relevance_signal` field — a comma-separated list of keywords that indicate this project is relevant to a job. The ranker compiles these into regex patterns and checks them against every job description.

```yaml
  projects:
    - name: "Network Intrusion Detection System"
      description: "Built a real-time IDS in Python using Scapy that monitors network traffic, flags anomalous patterns using statistical thresholds, and sends alerts via Telegram."
      relevance_signal: "intrusion detection, network monitoring, IDS, Scapy, traffic analysis, anomaly detection"

    - name: "Web Application Vulnerability Scanner"
      description: "Python tool that automates OWASP Top 10 vulnerability checks including SQL injection, XSS, CSRF, and insecure headers on target web apps."
      relevance_signal: "vulnerability assessment, penetration testing, OWASP, SQL injection, XSS, web security, scanner"

    - name: "CTF Challenge Solutions"
      description: "Solved 40+ CTF challenges across binary exploitation, reverse engineering, web, and crypto categories."
      relevance_signal: "CTF, binary exploitation, reverse engineering, cryptography, capture the flag"
```

> **Tip:** Keep `relevance_signal` focused on concrete technical keywords that would appear in a job description, not abstract descriptions. "Wireshark" is better than "network analysis experience".

#### Education

```yaml
  education:
    degree: "B.Tech Computer Science"
    institution: "Your College Name"
    graduation: "May 2026"
```

Used in the AI prompt for context. The `graduation` field is a plain string — not parsed by code.

#### Location

```yaml
  location:
    base: "Delhi, India"
    acceptable:
      - "Remote"
      - "Work from home"
      - "Anywhere in India"
      - "Bangalore"
      - "Mumbai"
      - "Hyderabad"
      - "Delhi NCR"
      - "Chennai"
      - "Pune"
    hard_reject:
      - "US only"
      - "UK only"
      - "Europe only"
      - "On-site outside India"
```

**How location is used:**
- `hard_reject` list: prefilter scans description text for these exact phrases and drops the job immediately
- `acceptable` list: used by Serper query builder to pick location tokens for dork queries
- ATS jobs have a structured `location` field — prefilter uses regex patterns to reject US/UK/EU locations (e.g. "Austin, TX", "London", "Berlin") and pass India/Remote/ambiguous

#### Industries

```yaml
  industries:
    high_priority:             # Jobs in these domains score higher in the ranker (+3 bonus)
      - "Cybersecurity"
      - "InfoSec"
      - "Threat Intelligence"
      - "SOC"
      - "Network Security"
      - "Cloud Security"
    medium_priority:           # Moderate domain bonus (+1 bonus)
      - "DevSecOps"
      - "Compliance"
      - "SaaS"
      - "Infrastructure"
```

These are used by:
- **Heuristic ranker**: compiled to regexes, checked against full job text
- **AI scorer**: passed as context for domain-relevance scoring

#### Salary

```yaml
  salary:
    min_stipend_inr: 8000      # Internship minimum stipend (INR/month)
    min_ctc_lpa: 4.0           # Full-time minimum CTC (LPA)
```

Used by the AI prompt for context. `min_stipend_inr` also determines whether an Internshala job gets a `source_internshala_stipend_bonus` in the ranker (configurable in `ranker_weights`).

---

### 6.3 Hard Reject Rules

These run before any AI call, with zero token cost.

```yaml
hard_reject:
  max_job_age_days: 45         # Drop jobs older than this. Relative dates ("3 days ago"),
                               # ISO dates, and Unix timestamps are all handled.
                               # Jobs with no date pass through (benefit of the doubt).

  ats_per_company_cap: 25      # Max ATS jobs per company per run. Prevents large companies
                               # (e.g. Stripe, GitLab) from dominating the AI scoring budget.

  max_ai_jobs_per_run: 200     # Hard ceiling on jobs sent to AI. Primary guard is the
                               # token budget (~89 jobs). This is a fallback safety net.

  experience_keywords:         # If ANY of these strings appear in a job description → reject.
    - "2+ years"               # Exact string match (case-insensitive).
    - "2 years experience"
    - "3+ years"
    - "4+ years"
    - "5+ years"
    - "senior engineer"
    - "lead engineer"
    - "tech lead"
    - "principal engineer"
    - "staff engineer"
    # Add or remove entries freely. Go as specific or broad as you need.

  company_blacklist:           # Companies to skip entirely (exact match, case-insensitive).
    # - "TCS"
    # - "Wipro"
    # Uncomment to activate.

  role_blacklist:              # Job titles containing these strings → reject.
    - "Data Scientist"         # Add roles completely outside your scope.
    - "Product Manager"
    - "UI Designer"
    - "Business Analyst"
    - "QA Engineer"
    # For a cybersecurity candidate you might remove "Data Scientist" and keep others.
```

---

### 6.4 Hirist-Specific Config Block

Hirist.tech is a niche India tech job board. If you enable it (`hirist: true` in sources), configure it here:

```yaml
hirist:
  keywords:
    - "cybersecurity"          # Maps to /k/cybersecurity-jobs URL slug
    - "security-engineer"
    - "information-security"
    - "penetration-testing"
  min_exp: 0
  max_exp: 2                   # Pages with roles requiring > 2yr experience are skipped
  pages: 2                     # Pages to fetch per keyword (~10-20 results/page)
  fetch_details: true          # Fetch full JD from detail pages (slower but higher quality)
```

> **Finding the right keyword slug:** Go to https://hirist.tech, search for your domain, and look at the URL — it shows the correct slug (e.g. `/k/cybersecurity-jobs`). Use the part between `/k/` and `-jobs` as your keyword.

---

### 6.5 Scoring Weights Block

These are passed to the Groq AI prompt as part of the scoring rubric — they're narrative hints, not numeric weights applied in code.

```yaml
scoring_weights:
  golang_mentioned: +2         # Rename/remove these — they're just prompt text.
  typescript_mentioned: +1     # The AI reads them as a list of signals to consider.
  backend_focused: +1
  fintech_crypto_company: +2
  remote_or_india: +1
  project_stack_match: +2
  exact_role_match: +2
  mentions_equity_esop: +0.5
  unknown_company: -0.5
```

**For a cybersecurity candidate, change this to something like:**

```yaml
scoring_weights:
  python_security_stack: +2
  security_domain: +2
  india_or_remote: +1
  project_stack_match: +2
  exact_role_match: +2
  bug_bounty_or_ctf_culture: +1
  startup_early_stage: +0.5
  unknown_company: -0.5
```

> These are not parsed programmatically. The AI reads the keys and values as a free-text scoring rubric. Use descriptive key names and any numeric value that makes sense to you.

---

## 7. Naukri-Specific Config Block

Naukri is one of the highest-volume sources. Add a `naukri:` block to control its search queries:

```yaml
naukri:
  keywords:
    - "cybersecurity intern"
    - "security engineer fresher"
    - "information security intern"
    - "SOC analyst fresher"
    - "penetration testing intern"
    - "network security fresher"
    - "ethical hacker intern"
  locations:
    - "india"
    - "bangalore"
    - "work from home"
  pages: 2                     # Pages per keyword × location combo (20 results/page)
```

**Without a `naukri:` block**, the pipeline uses default keywords (`backend developer fresher`, `golang developer fresher`, etc.) — these are backend-specific and will produce poor results for non-backend candidates. **Always set this block.**

> Each keyword × location × page = 1 API call = 20 results. With 7 keywords × 3 locations × 2 pages = 42 calls → up to 840 raw Naukri listings per run. These are Stage-1 filtered immediately (experience cap, age cap) before entering the pipeline.

---

## 8. Ranker Weights — Advanced Tuning

The heuristic ranker runs before any AI call and sorts jobs so the best matches are scored first (protecting your token budget). All numeric values are in `ranker_weights:`.

The ranker detects your skills, domains, and project signals automatically from the profile — you don't define regex patterns. You only tune the numeric weights here.

```yaml
ranker_weights:
  # Positive bonuses
  primary_skill_title:    5    # Your top 3 strong skills found in job title
  primary_skill_desc:     3    # Top 3 strong skills found only in description
  secondary_skill_title:  3    # Remaining skills found in title
  secondary_skill_desc:   1    # Remaining skills found in description only
  backend_title:          2    # "backend", "REST API", "microservice" in title
  backend_desc:           1    # Same, but in description only
  fresher_title:          2    # "intern", "fresher", "junior", "entry level" in title
  high_priority_domain:   3    # Any industries.high_priority keyword in full text
  med_priority_domain:    1    # Any industries.medium_priority keyword in full text
  project_signal_per_hit: 2    # Per matching project relevance_signal keyword
  project_signal_max:     4    # Cap on total project bonus
  desc_quality:           1    # Description ≥ 200 chars (data quality signal)
  has_date:               1    # Job has any posted_at date
  recency_7d:             4    # Posted within 7 days
  recency_14d:            2    # Posted within 14 days
  recency_30d:            1    # Posted within 30 days

  # Penalties — push weak jobs down the queue
  penalty_no_skill_match:   -3  # No skill keyword found anywhere in job text
  penalty_generic_title:    -2  # Title has no recognisable tech/role signal
  penalty_bodyshop_company: -1  # Company name looks like staffing/outsourcing firm
  penalty_ats_stub_desc:    -2  # ATS job with description < ats_stub_desc_threshold chars
  ats_stub_desc_threshold:  300

  # Synergy bonuses — high-confidence combo signals
  synergy_skill_domain:   3    # Primary skill AND high-priority domain both present
  synergy_skill_project:  2    # Primary skill AND a project signal both present

  # Source quality adjustments
  source_internshala_stipend_bonus:  2      # Internshala + stipend ≥ INR threshold
  source_internshala_stipend_min:    10000  # INR/month
  source_freshers_blog_batch_bonus:  1      # freshers_blogs + matching graduation batch tag
  source_freshers_blog_batches:
    - 2025
    - 2026
    - 2027
  source_naukri_stub_penalty:        -1    # Naukri job with very short description
  source_naukri_stub_threshold:       150
  source_serper_penalty:             -1    # Serper dork results (variable quality)
```

**If `backend_title` / `backend_desc` isn't relevant to you**, set both to `0`:
```yaml
  backend_title: 0
  backend_desc:  0
```

**If you want very recent jobs to dominate**, boost the recency values:
```yaml
  recency_7d:  8
  recency_14d: 4
  recency_30d: 1
```

---

## 9. What Is NOT Profile-Configurable (Honest Limitations)

Not everything in the pipeline reads from `profile.yaml`. Here's what has hardcoded logic and what you may need to change in source code for very different use cases:

### 9.1 Serper Dork Query Templates

The Google dork queries in `sources/serper.py` contain terms like `"backend intern"`, `"golang"`, `"typescript"`, `"fintech"`. These templates are partially profile-driven (your skills are injected via `{skill}` placeholders) but the surrounding query text is backend-specific.

**Impact:** Serper will still find jobs, but the queries are less targeted for non-backend domains.

**Options:**
- **Disable Serper** (`serper: false`) — loses discovery of career-page-only jobs but avoids wasted credits
- **Edit `sources/serper.py`** — look for the template lists (e.g. `_GOLANG_TEMPLATES`, `_FINTECH_CRYPTO_TEMPLATES`) near line 182 and replace with domain-appropriate dork templates. The structure is plain Python lists of strings.

### 9.2 ATS Title Allow-List in Prefilter

`pipeline/prefilter.py` has a `_ATS_TITLE_KEEP_SIGNALS` list (around line 234) that defines what counts as a valid technical job title for ATS sources. It includes: `"engineer"`, `"developer"`, `"intern"`, `"backend"`, `"platform"`, `"infrastructure"`, `"devops"`, `"sre"`, `"cloud"`, `"systems"`, `"data engineer"`, `"software"`, `"ml engineer"`, and several language names.

**For most domains this is fine** — a "Security Engineer Intern" passes via "engineer" + "intern". A "SOC Analyst Intern" passes via "intern". A "Data Scientist" (if you want those) does NOT pass — you'd need to add `"scientist"` to the list.

**If your target titles don't match any signal in the list**, add them to `_ATS_TITLE_KEEP_SIGNALS` in `prefilter.py`. It's a plain Python list.

### 9.3 `freshers_blogs` and `hackernews` Sources

These sources fetch content from specific websites that are India-focused (fresher blog aggregators, HN hiring threads). The source URLs and RSS feeds are hardcoded. These work well for India-based job seekers regardless of domain, but if you're outside India they're less useful — just disable them.

### 9.4 AI Few-Shot Calibration Examples

`pipeline/scorer.py` (around line 107) contains two hardcoded example jobs used to calibrate the AI scoring scale: one example of a score-9 Go/fintech intern job and one score-3 job. These are sent as part of the system prompt on every scoring call.

**Impact:** The calibration helps the AI produce consistent scores, but the examples are tuned for a backend/fintech candidate. For very different domains, the model may still calibrate reasonably (Llama 4 is flexible), but if you find scores are consistently off (all jobs scoring 5–7 regardless of actual fit), you can update the examples in `scorer.py` lines 107–150.

---

## 10. Validate Your Setup — Dry Run

Before making any API calls, run the dry-run check:

```bash
# Make sure your venv is active
source venv/bin/activate

python main.py profile.yaml --dry-run
```

Expected output:
```
=======================================================
  DRY RUN - JobRadar profile check
=======================================================
  Profile file : profile.yaml
  Candidate    : Your Full Name
  Education    : May 2026
  Database     : data/profile.db
  Telegram ID  : 987654321
  Sources ON   : ats, serper, internshala, yc, freshers_blogs, naukri, jobicy, remoteok
  Sources OFF  : cutshort, instahyre, wellfound, hackernews, reddit, hirist
  Max job age  : 45 days
  AI cap       : 200 jobs/run
  ATS cap/co   : 25 jobs/company
  Min stipend  : Rs.8000/mo
  Strong stack : Python, C, Linux, Networking, Wireshark
=======================================================
  OK Config loaded and DB initialised - no API calls made.
```

Check that:
- **Candidate name** matches what you put in profile.yaml
- **Telegram ID** shows your actual chat ID (not "NOT SET")
- **Sources ON** shows what you intended to enable
- **Strong stack** shows your actual skills (first few listed)
- No Python errors — if you see `yaml.scanner.ScannerError`, there's a YAML syntax error in your profile

---

## 11. Run the Pipeline

```bash
source venv/bin/activate

# Run with default profile.yaml
python main.py

# Run with a specific profile file (each user/profile gets its own DB and log)
python main.py my_profile.yaml
```

**What happens during a run:**

```
[08:00:01] Fetching ATS endpoints (Greenhouse US/EU, Lever, Ashby, Workable)...
             → Polls 9 ATS platforms concurrently, ~2-4 min
[08:05:12] Fetching Naukri.com...
             → keyword × location × page search calls, ~1-2 min
[08:07:01] Fetching Internshala, YC, freshers_blogs, Serper...
             → ~3-5 min total
[08:12:00] Deduplication: 8,234 raw → 612 new jobs
[08:12:01] Pre-filter: 612 → 94 eligible jobs
[08:12:01] Relevance ranking: 94 jobs ranked. Top scores: [29, 24, 22, ...]
[08:12:06] Scoring job 1/94: "Security Intern – Python" @ CyberCo | score=9
[08:12:11] Scoring job 2/94: "SOC Analyst Fresher" @ SecureBank | score=8
...
[08:19:34] Sending 4 urgent Telegram alerts
[08:19:36] Pipeline complete. 8234 raw → 612 new → 94 eligible → 4 urgent
```

**Log files** are written to `data/profile.log` (rotates at 1MB, keeps last 3 files).

**The SQLite DB** is at `data/profile.db` — this persists seen job hashes so the pipeline never shows you the same job twice, even across runs days apart.

**First run takes longer** (8-20 min) because it processes everything fresh. Subsequent runs are faster because dedup eliminates most seen jobs immediately.

---

## 12. Automate with Cron or EC2

### Option A — Linux Cron (local machine or server)

```bash
# Edit your crontab
crontab -e

# Add this line — runs at 8 AM and 6 PM daily
0 8,18 * * * cd /path/to/jobradar && source venv/bin/activate && python main.py >> data/cron.log 2>&1
```

### Option B — AWS EC2 Spot Instance with `run.sh`

`run.sh` is designed for one-shot EC2 instances: it pulls the latest code, installs any new dependencies, runs the pipeline with a 60-minute hard timeout, sends a Telegram alert on timeout or failure, and shuts down the instance when done (saving cost).

**The script expects these paths on the EC2 instance:**
```
/home/ubuntu/jobradar/         ← project directory
/home/ubuntu/jobradar/.env     ← environment file
/home/ubuntu/jobradar/data/    ← auto-created by main.py
```

**Deploy steps:**
1. Launch an EC2 instance (t3.micro is fine — the pipeline is mostly network I/O, not CPU)
2. SSH in and clone the repo to `/home/ubuntu/jobradar`
3. Create the `.env` file at `/home/ubuntu/jobradar/.env`
4. Create the venv: `cd /home/ubuntu/jobradar && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
5. Set up a scheduled EventBridge rule to start the instance at 8 AM and 6 PM IST daily
6. In EC2 User Data (run on start), add:
   ```bash
   #!/bin/bash
   bash /home/ubuntu/jobradar/run.sh
   ```

`run.sh` handles everything from that point: git pull, pip install, pipeline run, Telegram notification on failure, and `sudo shutdown -h now` at exit.

---

## 13. What Each Source Actually Does

Understanding this helps you decide which sources to enable for your domain:

| Source | What it finds | Recommended for |
|---|---|---|
| `ats` | Jobs from 9 ATS platforms (Greenhouse US/EU, Lever, Ashby, Workable, SmartRecruiters, Rippling, BambooHR, Recruitee, Personio) for companies listed in `companies.yaml` | Everyone — highest quality structured data |
| `naukri` | India's largest job board, keyword-searched | India-based candidates in any domain |
| `internshala` | India internships & fresher jobs, software/web dev category | India-based freshers; may have limited cybersec listings |
| `yc` | Y Combinator portfolio company jobs | Startup-oriented candidates |
| `freshers_blogs` | 8 Indian WordPress blogs aggregating off-campus drives (Freshers360, GeeksforGeeks Jobs, Freshersnow, etc.) | India-based freshers |
| `serper` | Google dork queries to find career pages, alt ATS, hidden applications | Everyone — but query templates are backend-biased (see §9.1) |
| `hackernews` | HN "Who is Hiring?" monthly thread | Global remote jobs, mostly startup-stage |
| `jobicy` | Jobicy.com free remote jobs JSON API | Remote-first candidates |
| `remoteok` | RemoteOK JSON API, dev/engineering roles | Remote-first candidates |
| `hirist` | Hirist.tech niche India tech board | India tech candidates |
| `reddit` | Reddit job subreddits (r/cscareerquestions etc.) | Low signal — mostly candidate-seeking posts, not job postings |

**`companies.yaml`** lists the companies the `ats` source polls. It comes pre-populated with 100+ companies across Greenhouse, Lever, Ashby, Workable, and more. You can add companies from any domain:

```yaml
# How to find the slug for any company:
# 1. Go to the company's Careers page and click any job
# 2. Look at the URL:
#    boards.greenhouse.io/SLUG/jobs/...    → add SLUG to greenhouse list
#    jobs.lever.co/SLUG/...               → add SLUG to lever list
#    jobs.ashbyhq.com/SLUG/...            → add SLUG to ashby list

greenhouse:
  - crowdstrike       # Add this if CrowdStrike uses Greenhouse
  - paloaltonetworks
  - sentinelone

lever:
  - darktrace         # Add cybersecurity companies you want to track
```

> **Verify before adding:** Hit the API directly: `curl -s "https://boards.greenhouse.io/v1/boards/crowdstrike/jobs" | python3 -m json.tool | head -5`. If you get a valid JSON response with jobs, the slug is correct.

---

## 14. Troubleshooting

### "No module named X"

```bash
# Make sure you activated the venv
source venv/bin/activate
pip install -r requirements.txt
```

### "GROQ_API_KEY is not set"

Your `.env` file isn't being loaded. Make sure:
- The file is named `.env` (not `.env.txt` or `env`)
- It's in the root of the project (same directory as `main.py`)
- The key name is exactly `GROQ_API_KEY` (uppercase, no spaces around `=`)

### Telegram alerts aren't arriving

1. Check that you sent `/start` to your bot in Telegram (required before first message)
2. Verify your `TELEGRAM_CHAT_ID` is correct — it should be your **user ID**, not your bot's ID
3. Check `data/profile.log` for "Telegram send failed" errors

### Dry run shows "Telegram ID: NOT SET"

Either `TELEGRAM_CHAT_ID` is missing from `.env`, or `telegram_chat_id` is missing from `profile.yaml`. The pipeline reads `telegram_chat_id` from profile.yaml first, then falls back to the `TELEGRAM_CHAT_ID` env variable. You only need it in `.env`.

### All jobs are being rejected at prefilter

Check `data/profile.log` for lines like:
```
REJECTED 'Security Intern @ CrowdStrike': ATS title has no tech signal: 'Security Intern'
```
If titles like "Security Analyst" or "Threat Intelligence Intern" are being dropped, add the missing signal to `_ATS_TITLE_KEEP_SIGNALS` in `pipeline/prefilter.py`:
```python
_ATS_TITLE_KEEP_SIGNALS = [
    ...existing list...
    "security",          # add this
    "analyst",           # add this if needed
    "threat",
]
```

### Naukri returning irrelevant jobs

Update the `naukri:` block in `profile.yaml` with domain-specific keywords. If the block is missing, the pipeline uses backend-specific defaults (`backend developer fresher`, `golang developer fresher`, etc.).

### Pipeline is slow on first run

Expected — it's fetching up to 9,000 raw jobs. ATS polling alone takes 2–4 minutes (concurrent but rate-limited). Subsequent runs are faster because deduplication eliminates most seen jobs in < 1 second.

### Groq rate limit errors (`rate_limit_exceeded`)

The pipeline throttles at 5 seconds between calls. If you see rate limit errors, increase `REQ_INTERVAL` in `pipeline/scorer.py` line 26 from `5.0` to `6.0` or `7.0`.

### "yaml.scanner.ScannerError" on startup

YAML is whitespace-sensitive. Common mistakes:
- Using tabs instead of spaces (YAML requires spaces)
- Missing a colon after a key (`name "Rohit"` → `name: "Rohit"`)
- Incorrect indentation (list items must line up exactly)

Validate your YAML: `python3 -c "import yaml; yaml.safe_load(open('profile.yaml'))"` — no output means it's valid.

---

## Quick Start Checklist

```
[ ] git clone + cd jobradar
[ ] python3 -m venv venv && source venv/bin/activate
[ ] pip install -r requirements.txt
[ ] Create .env with GROQ_API_KEY, SERPER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
[ ] Send /start to your Telegram bot
[ ] Edit profile.yaml:
    [ ] candidate.name, email
    [ ] candidate.roles (your target roles)
    [ ] candidate.experience.max_required (0 for fresher, 1-2 for junior)
    [ ] candidate.skills.strong + learning
    [ ] candidate.projects with relevance_signal for each
    [ ] candidate.location (base + acceptable)
    [ ] candidate.industries.high_priority + medium_priority
    [ ] candidate.salary.min_stipend_inr
    [ ] hard_reject.experience_keywords (remove irrelevant entries, add your own)
    [ ] hard_reject.role_blacklist (remove roles you'd actually want)
    [ ] naukri: keywords + locations (IMPORTANT — defaults are backend-specific)
    [ ] scoring_weights (rename to match your domain)
    [ ] sources: disable irrelevant sources
[ ] python main.py profile.yaml --dry-run  ← verify config
[ ] python main.py                          ← first real run
[ ] Check Telegram for alerts
[ ] Check data/profile.log for pipeline details
```
