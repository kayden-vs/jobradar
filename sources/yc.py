"""
sources/yc.py — Y Combinator Jobs board scraper

Why this matters:
  YC portfolio companies in India (Juspay, Setu, Khatabook, etc.) often
  post ONLY on this board. Strong signal for quality startups.

URL pattern:
  https://www.ycombinator.com/jobs?q=backend&type=fulltime,intern&remote=true

The page is mostly server-side rendered (Scrapling plain Fetcher works).
"""

import logging
import time
from scrapling.fetchers import Fetcher, StealthyFetcher

logger = logging.getLogger(__name__)

YC_SEARCH_URLS = [
    # Backend + intern/fulltime + remote
    "https://www.ycombinator.com/jobs?q=backend&type=fulltime,intern&remote=true",
    # TypeScript / Node.js backend
    "https://www.ycombinator.com/jobs?q=typescript+backend&type=fulltime,intern",
    # Golang
    "https://www.ycombinator.com/jobs?q=golang&type=fulltime,intern",
    # India location — catches non-remote India roles
    "https://www.ycombinator.com/jobs?q=backend&location=india",
]

REQUEST_DELAY = 1.5  # seconds between requests


def _fetch_page(url: str):
    """Fetch a YC jobs page. Try plain Fetcher first, fall back to Stealthy."""
    try:
        page = Fetcher().get(url, timeout=20)
        body = page.get_all_text(ignore_tags=["script", "style", "nav", "footer"])
        if len(body) > 500:
            return page
    except Exception:
        pass

    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        return page
    except Exception as e:
        logger.debug(f"YC fetch failed for {url}: {e}")
        return None


def fetch_yc() -> list[dict]:
    """
    Scrape YC jobs board and return structured job dicts.
    YC job cards use consistent class names — we target the job listing rows.
    """
    all_jobs: list[dict] = []
    seen: set[str] = set()

    for url in YC_SEARCH_URLS:
        time.sleep(REQUEST_DELAY)
        page = _fetch_page(url)
        if page is None:
            continue

        try:
            # YC jobs are rendered in <a> tags with job-specific classes.
            # Primary selector: job listing cards
            # YC uses: <a class="...job..." href="/companies/...">
            cards = page.css("a[href*='/jobs/']")

            if not cards:
                # Fallback: any <a> linking to a job or company jobs page
                cards = page.css("div[class*='job'] a, li[class*='job'] a")

            for card in cards:
                href = card.attrib.get("href", "")
                if not href:
                    continue

                # Resolve relative URLs
                job_url = ("https://www.ycombinator.com" + href) if href.startswith("/") else href

                # Skip non-job URLs (company pages, blog, etc.)
                if "/jobs/" not in job_url and "/companies/" not in job_url:
                    continue
                if job_url in seen:
                    continue
                seen.add(job_url)

                # Extract text from the card
                card_text = card.get_all_text() if hasattr(card, "get_all_text") else card.text
                if not card_text:
                    card_text = card.text or ""
                card_text = card_text.strip()

                # Try to parse title / company from card text
                lines = [l.strip() for l in card_text.splitlines() if l.strip()]
                title   = lines[0] if lines else "Software Engineer"
                company = lines[1] if len(lines) > 1 else "YC Company"
                location = "Remote"

                # Check for location hints in card text
                text_lower = card_text.lower()
                if "india" in text_lower:
                    location = "India"
                elif "remote" in text_lower:
                    location = "Remote"
                elif "san francisco" in text_lower or "new york" in text_lower:
                    continue  # US only — skip

                all_jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    location,
                    "description": card_text[:2000],
                    "url":         job_url,
                    "source":      "yc",
                    "salary":      "",
                    "posted_at":   "",
                })

        except Exception as e:
            logger.warning(f"YC parsing failed for {url}: {e}")
            continue

    logger.info(f"YC Jobs: {len(all_jobs)} jobs found")
    return all_jobs
