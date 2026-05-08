# JobRadar — Everything You Need to Know

---

## 1. Tweaks to Make It Significantly Better Right Now

### 🔴 High Impact

**a) Expand `companies.yaml` carefully — only add VERIFIED slugs**
The log showed that ~90% of the original company slugs were wrong guesses (returning 404). Now only confirmed-working slugs are in the file. Before adding any new company, always test its slug first:
```
# Greenhouse test:
curl https://boards.greenhouse.io/v1/boards/SLUG/jobs

# Lever test:
curl https://api.lever.co/v0/postings/SLUG

# Ashby test:
curl https://api.ashbyhq.com/posting-api/job-board/SLUG

# Workable test:
# POST to https://apply.workable.com/api/v3/accounts/SLUG/jobs
```
If it returns JSON with jobs (not a 404), the slug is correct. Add it.

**b) ATS full JD fetching is now implemented ✅**
`sources/ats.py` now fetches the full job description for every Greenhouse and Workable job via a second API call per job. Lever already returns full JDs. Ashby now uses the correct public API endpoint. This is a major accuracy improvement for the AI scorer.

**c) HackerNews is now self-healing ✅**  
`hackernews.py` now has auto-discovery via the Algolia HN API as a fallback. Even if you forget to update the manual dict, it will find the current month's thread automatically.

**d) Update `hard_reject` in `profile.yaml` after first few real runs**
After running for a few days, look at `data/jobradar.db` to see what's being filtered. You can open it with any SQLite viewer (e.g. DB Browser for SQLite — free). If good jobs are being incorrectly rejected, loosen the experience regex in `pipeline/prefilter.py`.

---

## 2. Future Steps to Make It a Very Reliable Tool

### Phase 1 — Data Quality (Do first)
- **LinkedIn via RapidAPI**: LinkedIn has the best Indian intern/fresher job volume. Use the `LinkedIn Jobs Search` endpoint on RapidAPI (free tier: 100 calls/month). This alone could 5x your job volume.
- **Naukri.com RSS feed**: Naukri has undocumented RSS feeds. Search `naukri rss feed golang backend fresher` — many exist and are scraping-free.
- **Fix more ATS slugs**: The biggest immediate win. Every company you correctly add to `companies.yaml` is 100% reliable, structured job data with full JDs.

### Phase 2 — Intelligence Upgrades
- **Apply tracking**: After you apply, mark it in the DB (`applied = 1`). Add a Telegram command `/applied <url>` so you can track applications without leaving Telegram.
- **Company intelligence**: Before scoring, quick lookup of the company (funding stage, tech blog) using Crunchbase free API or Serper. Feed context into Gemini prompt. A Series A fintech using Go is very different from a 2-person startup.
- **Feedback loop**: Add Telegram inline buttons — "👍 Applied" / "👎 Not relevant" — on each job card. Save and use to tune the scoring prompt.

### Phase 3 — Robustness
- **Rate limiting / backoff**: If Serper or Gemini returns 429, the run crashes silently. Add exponential backoff with `tenacity`.
- **Source health monitoring**: After each run, log which sources returned 0 results. If a source is dead for 3 consecutive days, send a Telegram alert.

---

## 3. How to Make It Run Every Day at 8 AM Automatically

### Option A — Windows Task Scheduler (Recommended for now)

Open PowerShell as Administrator and run:

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "main.py" -WorkingDirectory "c:\Users\rohit\OneDrive\Desktop\jobradar"
$trigger = New-ScheduledTaskTrigger -Daily -At "8:00AM"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName "JobRadar" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest
```

Runs every day at 8 AM. If your PC is off, runs when it wakes up (due to `-StartWhenAvailable`). ✅

### Option B — GitHub Actions (Cloud, already configured)

Your `.github/workflows/jobradar.yml` runs at 8:00 AM IST (2:30 AM UTC). Requires:
1. Push repo to GitHub (`.env` is already in `.gitignore` ✅)
2. Add secrets in GitHub → Settings → Secrets → Actions: `SERPER_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. Enable Actions.

> [!WARNING]
> Scrapling's browser-based fetchers need Playwright + Chromium in the CI environment. The `scrapling install --browsers chromium` step handles this but adds ~2-3 minutes per run.

**Verdict**: Use Windows Task Scheduler now. Move to GitHub Actions when you want zero-touch cloud automation.

---

## 4. How Long Does a Full Run Take?

| Stage | Time | Notes |
|---|---|---|
| ATS polling (verified companies) | 30–90 sec | Now includes JD fetches — ~1 extra call per job |
| Cutshort scraping | 60–120 sec | Browser-based |
| Instahyre API | 5–10 sec | Fast |
| Wellfound scraping | 60–120 sec | Browser-based |
| Serper queries (15 max) | 30–60 sec | Capped at 15 API calls |
| HackerNews (150 comments) | 30–60 sec | HTTP + 7-8 Gemini calls |
| Reddit RSS | 5–10 sec | Fast |
| Dedup + pre-filter | 1–5 sec | In-memory, instant |
| AI Scoring (per job) | 2–5 sec/job | Depends on how many pass pre-filter |
| Telegram notifications | ~1 sec/job | |

**Total estimate: 8–20 minutes.** Now that ATS JDs are fetched, runs with many ATS jobs may be slightly longer — but the scoring accuracy is significantly improved.

> [!NOTE]
> On a fresh first run, expect closer to 20 minutes. On subsequent days, most jobs are already deduped and it finishes much faster.

---

## 5. Is It Deployable on Free Platforms Like Render?

**Short answer: Yes, but SQLite needs a workaround.**

### The SQLite Problem
Render's free tier has an **ephemeral filesystem** — `data/jobradar.db` gets wiped on every restart. Deduplication stops working.

### Solutions (in order of ease)

| Option | Cost | Effort |
|---|---|---|
| **GitHub Actions** (already configured) | Free | Already done ✅ |
| **Render + Render Disk** | $1/month | Mount at `/data`, change `DB_PATH` |
| **Turso** (cloud SQLite) | Free | Change `sqlite3` → `libsql` in `db.py` |
| **Railway + PostgreSQL** | Free | Swap `sqlite3` → `psycopg2` |

**Recommendation**: GitHub Actions is already set up, free, and SQLite persistence works via `actions/cache`.

---

## 6. Can You Rely Completely on This for Job Searching?

**Honest answer: ~65% reliable now. Better after today's fixes.**

### What it does well ✅
- ATS polling now returns **full job descriptions** for scoring
- Greenhouse, Lever: reliable structured API data
- Pre-filter is fast and accurate
- HackerNews is now self-healing (auto-discovers thread ID)
- Serper is now capped at 15 calls/run with full JD fetching via Scrapling
- Deduplication works across runs

### What it still misses ❌
- **LinkedIn** — largest source of Indian intern jobs, not covered
- **Naukri.com** — second largest Indian job board, not covered
- **Wellfound/Cutshort scraping is fragile** — these sites actively fight scrapers
- **Only 3 verified ATS companies** — needs slug expansion (the single biggest gap)
- **No feedback mechanism** — doesn't learn from your apply/reject decisions

### To make it fully reliable:
1. Verify and add 10-20 more companies to `companies.yaml` (this is the #1 highest-ROI task)
2. Add LinkedIn via RapidAPI
3. Add Telegram inline apply buttons for feedback loop

> [!IMPORTANT]
> Until LinkedIn is covered, keep a weekly 30-minute LinkedIn session as a backup. Don't drop manual search completely yet.

---

## 7. Everything Else You Need to Know

### 🔑 API Keys & Costs

| Service | Free Tier | Run Budget |
|---|---|---|
| **Serper.dev** | 2,500 searches/month | 15 queries/day × 30 = 450/month ✅ |
| **Gemini 1.5 Flash** | 1,500 requests/day, 1M tokens/day | ~8 calls (HN) + 1/scored job. Very safe. |
| **Telegram Bot** | Free, unlimited | Never a concern. |
| **GitHub Actions** | 2,000 min/month private | ~15 min/run × 30 = 450 min/month ✅ |

**Total monthly cost: $0 if everything is free-tier.**

---

### 🏗️ How to Find Correct ATS Slugs (Critical for companies.yaml)
This is the most important maintenance task. Here's the fastest workflow:

1. Go to a company's Careers page (e.g. `razorpay.com/jobs`)
2. Click any open job posting
3. Look at the URL of the job or "Apply" button:
   - `boards.greenhouse.io/...` → Greenhouse, slug is after `/boards/`
   - `jobs.lever.co/...` → Lever, slug is after `jobs.lever.co/`
   - `jobs.ashbyhq.com/...` → Ashby, slug is after `jobs.ashbyhq.com/`
   - `apply.workable.com/...` → Workable, slug is after `apply.workable.com/`
4. Test the slug with curl before adding it

---

### ⚠️ The Telegram Markdown Pitfall
`notify/telegram_bot.py` uses `ParseMode.MARKDOWN`. Telegram's Markdown is strict — job titles with `*`, `_`, `[`, `]`, or `` ` `` will **silently fail to send**. Fix: switch to `ParseMode.HTML` in `telegram_bot.py` (more forgiving) and wrap title/company in `<b>` tags instead of `*`.

---

### 📅 HackerNews Thread — Now Auto-Discovers
The code now automatically finds the current month's "Who is Hiring?" thread via Algolia. But keeping the manual dict updated is faster and more reliable. To update manually:
1. Go to `news.ycombinator.com/submitted?id=whoishiring`
2. Find current month's "Ask HN: Who is Hiring?" thread
3. Copy the ID from the URL (e.g. `item?id=47975571`)
4. Add to `HN_THREAD_IDS` in `sources/hackernews.py`

---

### 🗂️ First-Run Checklist
- [ ] `.env` has all 4 keys: `SERPER_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- [ ] `profile.yaml` — your name is filled in (`Rohit Kumar Roy` ✅), skills/projects are accurate
- [ ] `companies.yaml` — only use verified slugs (test with curl before adding)
- [ ] Telegram bot created via [@BotFather](https://t.me/BotFather), you've messaged it at least once
- [ ] `data/` directory exists (auto-created on first run)
- [ ] Run: `python main.py`

---

### 🔒 Security Note
`.env` is already in `.gitignore` ✅. Never push it to GitHub. For GitHub Actions, use Secrets — your workflow already reads from `${{ secrets.* }}` ✅.
