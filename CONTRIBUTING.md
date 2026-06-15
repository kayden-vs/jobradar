# Contributing to JobRadar

Thanks for your interest. The most impactful thing you can contribute right now is a **new job source** — a new site or API that surfaces jobs not already covered by the pipeline.

This guide is written specifically for that use case. It covers everything you need to go from zero to a working, properly-wired source.

---

## Table of Contents

1. [Get the Project Running First](#1-get-the-project-running-first)
2. [The `Job` Dataclass — What Every Source Must Return](#2-the-job-dataclass--what-every-source-must-return)
3. [How to Add a New Source](#3-how-to-add-a-new-source)
   - [Step 1 — Create `sources/mysource.py`](#step-1--create-sourcesmysourcepy)
   - [Step 2 — Wire it into `main.py`](#step-2--wire-it-into-mainpy)
   - [Step 3 — Add a toggle to `profile.yaml`](#step-3--add-a-toggle-to-profileyaml)
   - [Step 4 — Document it](#step-4--document-it)
4. [Source Quality Standards](#4-source-quality-standards)
5. [Fixing an Existing Source](#5-fixing-an-existing-source)
6. [Code Style](#6-code-style)
7. [PR Process](#7-pr-process)
8. [What Not to Contribute Right Now](#8-what-not-to-contribute-right-now)

---

## 1. Get the Project Running First

Before writing any code, make sure the existing pipeline runs on your machine. Follow **[`docs/setup_guide.md`](docs/setup_guide.md)** — specifically the dry-run step:

```bash
python main.py profile.yaml --dry-run
```

If that prints a clean config summary with no errors, you're good to go.

---

## 2. The `Job` Dataclass — What Every Source Must Return

Every source returns a list of `Job` objects. The `Job` dataclass is defined in `pipeline/prefilter.py` (it's imported by everything that touches jobs). Here's what you need to know:

### Required fields (must be non-empty strings)

| Field | Type | Notes |
|---|---|---|
| `title` | `str` | Job title — as clean as possible. Strip HTML, excess whitespace. |
| `company` | `str` | Company name. |
| `url` | `str` | Direct link to the job listing. Must be the canonical URL (skip tracking params). |
| `source` | `str` | Short identifier string — e.g. `"mysite"`. Used in logs, dedup, and source-specific ranker adjustments. |

### Strongly recommended fields

| Field | Type | Notes |
|---|---|---|
| `description` | `str` | Job description text. The more, the better — prefilter and ranker both need it. Empty string if unavailable. |
| `location` | `str` | Location string — e.g. `"Bangalore"`, `"Remote"`, `"India"`. Used by location filters. |
| `posted_at` | `str \| None` | ISO date string (`"2026-05-15"`), relative string (`"3 days ago"`), or `None`. Jobs with `None` are not penalised — they compete on other signals. |

### Optional but useful

| Field | Type | Notes |
|---|---|---|
| `salary` | `str \| None` | Salary/stipend string if available. Used in AI prompt context. |
| `experience` | `str \| None` | Experience requirement string — e.g. `"0-1 years"`. Used in prefilter. |
| `tags` | `list[str]` | Any tags/categories from the source. Used by RSS prefilter for freshers_blogs. |

### Example

```python
from pipeline.prefilter import Job

job = Job(
    title="Backend Engineer Intern",
    company="Acme Corp",
    url="https://acmecorp.com/jobs/backend-intern",
    source="acme_careers",
    description="We're looking for a Go/Python backend intern...",
    location="Bangalore, India",
    posted_at="2026-06-10",
    salary="₹15,000/month",
)
```

---

## 3. How to Add a New Source

### Step 1 — Create `sources/mysource.py`

Each source is a self-contained Python file with a single public function: `fetch_<sourcename>()`.

```python
# sources/mysource.py

import logging
import requests
from pipeline.prefilter import Job

logger = logging.getLogger(__name__)


def fetch_mysource() -> list[Job]:
    """
    Fetch jobs from MySite.

    Returns an empty list on any error — never raises.
    Aim for < 500 raw results; apply source-side filters aggressively.
    """
    jobs: list[Job] = []

    try:
        resp = requests.get(
            "https://api.mysite.com/jobs",
            params={"category": "engineering", "experience": "fresher"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"mysource: fetch failed — {e}")
        return []

    for item in data.get("jobs", []):
        try:
            jobs.append(Job(
                title=item["title"].strip(),
                company=item["company"]["name"].strip(),
                url=item["apply_url"],
                source="mysource",
                description=item.get("description", ""),
                location=item.get("location", ""),
                posted_at=item.get("posted_date"),
            ))
        except (KeyError, TypeError) as e:
            logger.debug(f"mysource: skipping malformed item — {e}")
            continue

    logger.info(f"mysource: fetched {len(jobs)} jobs")
    return jobs
```

**Key patterns to follow:**
- The function signature is always `def fetch_<name>() -> list[Job]:` (or `(profile: dict) -> list[Job]:` if you need profile config)
- Wrap the entire fetch in a `try/except` — return `[]` on failure, never raise
- Log a `WARNING` on fetch failure (so it shows up in `data/<profile>.log`)
- Log an `INFO` at the end with the count — useful for debugging
- Strip and clean strings before putting them in `Job` fields

### Step 2 — Wire it into `main.py`

`main.py` has two things to update:

**Import at the top:**
```python
from sources.mysource import fetch_mysource
```

**Add a `source_enabled` block** in the source layer section (around line 144), grouped with similar sources:
```python
if source_enabled("mysource"):
    logger.info("--- Fetching MySite ---")
    raw_jobs.extend(fetch_mysource())
```

The `source_enabled()` helper reads the toggle from `profile.yaml → sources:` and logs a skip message automatically if disabled.

### Step 3 — Add a toggle to `profile.yaml`

Add your source to the `sources:` block in `profile.yaml`:

```yaml
sources:
  # ... existing sources ...
  mysource: false   # MySite — describe what it provides in 1 line
```

Start with `false` — let users opt in once they've tested it. Change to `true` in the default `profile.yaml` only if the source is reliable and broadly useful.

### Step 4 — Document it

Add a row to the sources table in `docs/setup_guide.md` → Section 13 "What Each Source Actually Does":

```markdown
| `mysource` | MySite.com — describe what jobs it finds and who it's for | India tech candidates / Remote-first candidates / etc. |
```

---

## 4. Source Quality Standards

These are the things that will be checked in review:

### Error handling
- Must return `[]` on any network error, timeout, or unexpected response format — never raise an unhandled exception
- Use `requests.get(..., timeout=15)` — no open-ended requests
- Catch `KeyError` / `TypeError` per-item so one malformed listing doesn't kill the whole fetch

### Volume
- Keep raw output under ~1,000 jobs if possible — apply source-side filters (experience level, category, location) before building `Job` objects
- If the source has pagination, cap at a reasonable page count (2–5 pages by default, configurable via `profile.yaml` if needed)

### Lazy detail fetching
- If getting the full job description requires a second HTTP request (a "detail page"), **do not fetch it upfront for all results**
- Instead, return a stub job with whatever's available from the listing page, and implement a `fetch_detail(job)` method that `scorer.py` or `freshers_blogs.py` can call post-prefilter
- See `sources/freshers_blogs.py` for the lazy-fetch pattern — full pages are only fetched after a job survives the prefilter

### No new hard dependencies
- Prefer `requests`, `httpx`, `aiohttp`, `beautifulsoup4`, `feedparser` — all already in `requirements.txt`
- If your source genuinely needs a new package, mention it in the PR description and explain why existing packages can't handle it

### Rate limiting
- Add a `time.sleep()` between paginated requests if the site is likely to rate-limit
- Respect `Retry-After` headers if you get a 429

---

## 5. Fixing an Existing Source

### Find the logs

Run logs are in `data/<profile>.log` (rotates at 1MB, keeps last 3 files). Look for lines like:

```
[WARNING] sources.naukri: Stage-1 fetch failed — 403 Forbidden
[WARNING] sources.hirist: detail page fetch failed for job XYZ — timeout
```

### Test a single source in isolation

You can test any source without running the full pipeline:

```python
# Quick test script — run from the repo root with venv active
from dotenv import load_dotenv
load_dotenv()

from sources.mysource import fetch_mysource
jobs = fetch_mysource()
for j in jobs[:5]:
    print(j.title, "|", j.company, "|", j.url)
print(f"\nTotal: {len(jobs)} jobs")
```

### Common failure modes

| Symptom | Likely cause |
|---|---|
| `403 Forbidden` | Site added bot detection — may need updated headers or a different approach |
| `Empty list, no error logged` | API schema changed — check the raw response with `print(resp.json())` |
| `Timeout on detail pages` | Site is slow — increase `timeout=` or implement retry with backoff |
| `Jobs all failing prefilter` | Title allowlist issue — check `_ATS_TITLE_KEEP_SIGNALS` in `pipeline/prefilter.py` |

---

## 6. Code Style

- **Python 3.11+** — use `str | None` union syntax, not `Optional[str]`
- **Type hints** on function signatures — at minimum the return type
- **No cross-source imports** — source files should be self-contained. Don't import from other source files.
- **Logging** — use `logger = logging.getLogger(__name__)` at the module level. Use `logger.info` for normal progress, `logger.warning` for recoverable failures, `logger.debug` for per-item noise
- **No global state** — each `fetch_*()` call should be stateless and side-effect free (besides logging)
- **Docstring** on the fetch function — one sentence describing what it fetches, one sentence on failure behaviour

---

## 7. PR Process

### Before opening a PR

1. **Open an issue first** for a new source — describe what the source provides and why it's worth adding. This avoids duplicate work and lets us align on whether the source fits the pipeline.
2. Run `python main.py profile.yaml --dry-run` — if it prints cleanly, your wiring is correct.
3. Do a quick live test with a small script (see §5) to confirm the fetch actually returns jobs.

### PR title format

```
feat(sources): add <SourceName> scraper
fix(sources): fix <SourceName> rate limit / 403 handling
```

### What to include in the PR description

- **What the source provides**: what kinds of jobs, geography, tech focus
- **Why it's not already covered**: what gap it fills vs existing sources
- **Volume observed**: roughly how many jobs it returns per run
- **Any reliability concerns**: rate limits, bot detection, occasional downtime
- **Whether it needs a new dependency**: if yes, justify it

### What reviewers will check

- Error handling (never raises, returns `[]` on failure)
- Lazy detail fetch if second HTTP request is needed
- No open-ended timeouts
- Source disabled by default in `profile.yaml` (let users opt in)
- Entry added to `docs/setup_guide.md` source table

---

## 8. What Not to Contribute Right Now

To keep the project focused, these areas are intentionally out of scope for outside contributions at the moment:

- **UI / web dashboard** — this is a planned future goal, but the architecture isn't defined yet. Contributions here would likely need to be redone from scratch once the design is settled.
- **`profile.yaml` schema changes** — adding new top-level keys or changing existing field names is a breaking change for everyone running the tool. Discuss in an issue first.
- **Major refactors to pipeline stages** (`scorer.py`, `ranker.py`, `prefilter.py`) — these have been carefully calibrated with real run data. Changes here need strong justification and observed data to back them up.
- **Changing the AI model or scoring prompt** — same reason. The few-shot calibration and score distribution are sensitive to prompt changes.

If you're unsure whether something fits, open an issue and ask before writing code.

---

*Questions? Open a [GitHub Issue](../../issues).*
