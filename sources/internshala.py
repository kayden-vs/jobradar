"""
sources/internshala.py — Internshala internship & fresher job scraper

Why plain Fetcher:
  Internshala is server-side rendered (no JS execution needed).
  Plain HTTP is ~10x faster than browser-based scraping.

Coverage:
  - Backend development internships (WFH + on-site)
  - Web development internships (WFH)
  - Golang-specific internships
  - Backend fresher jobs
  - TypeScript / Node.js internships
"""

import logging
import time
from scrapling.fetchers import Fetcher

logger = logging.getLogger(__name__)

# All URLs to crawl. Internshala filters are URL-based — no JS needed.
SEARCH_URLS = [
    # Backend internships
    "https://internshala.com/internships/backend-development-internship/",
    "https://internshala.com/internships/backend-development-internship/work-from-home-internships/",
    # Web / full-stack (includes Node, TypeScript backends)
    "https://internshala.com/internships/web-development-internship/work-from-home-internships/",
    "https://internshala.com/internships/web-development-internship/",
    # Golang-specific keyword search
    "https://internshala.com/internships/keywords-golang/",
    "https://internshala.com/internships/keywords-go-developer/",
    # TypeScript / Node.js keyword search
    "https://internshala.com/internships/keywords-nodejs/",
    "https://internshala.com/internships/keywords-typescript/",
    # Fresher software jobs (not just internships)
    "https://internshala.com/jobs/backend-developer-intern-jobs/",
    "https://internshala.com/jobs/software-developer-jobs/",
]

# Internshala rate-limit: 1 request per second is safe
REQUEST_DELAY = 1.2


def fetch_internshala() -> list[dict]:
    """
    Scrape Internshala job listing pages and return structured job dicts.
    Uses plain Fetcher (HTTP only) — Internshala is fully server-side rendered.
    """
    # Use the class-level shared client (avoids deprecated per-call constructor)
    all_jobs: list[dict] = []
    seen: set[str] = set()

    for url in SEARCH_URLS:
        try:
            time.sleep(REQUEST_DELAY)
            page = Fetcher.get(url, timeout=20)

            # Primary card selector — Internshala uses .individual_internship
            cards = page.css(".individual_internship")

            if not cards:
                # Fallback: try .internship_meta (older layout variant)
                cards = page.css(".internship_meta")

            if not cards:
                logger.debug(f"Internshala: no cards found at {url}")
                continue

            for card in cards:
                # Title — now in <h2 class="job-internship-name"><a class="job-title-href">
                # (old layout used <h3> inside a .profile wrapper; that class no longer exists)
                title_el = card.css("h2.job-internship-name a, .job-internship-name a")
                title = title_el[0].text.strip() if title_el else ""

                # Company name — now in <p class="company-name">
                # (old layout used <h4 class="company-name"> or <h4> inside .company_name)
                company_el = card.css("p.company-name")
                company = company_el[0].text.strip() if company_el else ""

                # Location — text lives inside <a> nested inside .locations span
                # e.g. <div class="locations"><span><a>Work From Home</a></span></div>
                # Selecting just `.locations span` gives the <span> whose .text is empty;
                # we need the nested <a> text instead.
                loc_el = card.css(".locations span a, .locations a")
                location = loc_el[0].text.strip() if loc_el else "India"
                # Normalise "Work From Home" → "Remote"
                if "home" in location.lower() or "wfh" in location.lower():
                    location = "Remote"

                # Stipend — .stipend still works
                stipend_el = card.css(".stipend")
                stipend = stipend_el[0].text.strip() if stipend_el else ""

                # Application URL — <a class="job-title-href"> is the canonical link;
                # also matches /job/detail/ paths (jobs page) in addition to /internship/detail/
                link_el = card.css(
                    "a.job-title-href, "
                    "a[href*='/internship/detail/'], "
                    "a[href*='/job/detail/']"
                )
                href = link_el[0].attrib.get("href", "") if link_el else ""
                job_url = ("https://internshala.com" + href) if href.startswith("/") else href

                if not job_url or job_url in seen:
                    continue
                seen.add(job_url)

                if not title:
                    continue

                all_jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    location,
                    "description": f"{title} at {company}. Location: {location}. Stipend: {stipend}",
                    "url":         job_url,
                    "source":      "internshala",
                    "salary":      stipend,
                    "posted_at":   "",   # Internshala doesn't expose exact dates in listing
                })

        except Exception as e:
            logger.warning(f"Internshala failed for {url}: {e}")
            continue

    logger.info(f"Internshala: {len(all_jobs)} jobs found")
    return all_jobs
