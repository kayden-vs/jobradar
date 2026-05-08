import requests
import yaml
import logging
import re
from datetime import datetime
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Load your companies list
def load_companies(path="companies.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _strip_html(html: str) -> str:
    """Strip HTML tags from a JD string."""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text(separator="\n").strip()
    except Exception:
        # Fallback: simple regex strip
        return re.sub(r"<[^>]+>", " ", html).strip()


# ─────────────────────────────────────────────────────────────────
# GREENHOUSE
# ─────────────────────────────────────────────────────────────────

def _fetch_greenhouse_jd(company_slug: str, job_id: int) -> str:
    """
    Fetch full JD from Greenhouse single-job endpoint.
    URL: https://boards.greenhouse.io/v1/boards/{slug}/jobs/{job_id}
    The 'content' field contains the full HTML job description.
    """
    url = f"https://boards.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        return _strip_html(data.get("content", ""))
    except Exception as e:
        logger.debug(f"Greenhouse JD fetch failed for job {job_id}: {e}")
        return ""


def fetch_greenhouse(company_slug: str) -> list[dict]:
    """
    Polls the Greenhouse public API for a company.
    List endpoint: https://boards.greenhouse.io/v1/boards/{slug}/jobs
    JD endpoint:   https://boards.greenhouse.io/v1/boards/{slug}/jobs/{id}
    """
    url = f"https://boards.greenhouse.io/v1/boards/{company_slug}/jobs"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for job in data.get("jobs", []):
            location_parts = [loc.get("name", "") for loc in job.get("offices", [])]
            job_id = job.get("id")
            
            # Fetch full JD (one extra API call per job — still fast, no scraping)
            description = _fetch_greenhouse_jd(company_slug, job_id) if job_id else ""
            
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    ", ".join(location_parts) or "Not specified",
                "description": description,
                "url":         job.get("absolute_url", ""),
                "source":      "greenhouse",
                "salary":      "",
                "posted_at":   job.get("updated_at", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Greenhouse fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# LEVER
# ─────────────────────────────────────────────────────────────────

def fetch_lever(company_slug: str) -> list[dict]:
    """
    Polls Lever's public API.
    Lever returns full JD in descriptionPlain + lists — no second call needed.
    URL: https://api.lever.co/v0/postings/{slug}
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json():
            desc_parts = []
            # Lists contain requirements, responsibilities, etc.
            for section in job.get("lists", []):
                section_title = section.get("text", "")
                items_html    = section.get("content", "")
                items_text    = _strip_html(items_html)
                if section_title:
                    desc_parts.append(f"## {section_title}\n{items_text}")
                else:
                    desc_parts.append(items_text)
            # Plain text description
            desc_parts.append(job.get("descriptionPlain", ""))

            location   = job.get("categories", {}).get("location", "Not specified")
            commitment = job.get("categories", {}).get("commitment", "")

            jobs.append({
                "title":       job.get("text", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    f"{location} ({commitment})" if commitment else location,
                "description": "\n\n".join(p for p in desc_parts if p).strip(),
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


# ─────────────────────────────────────────────────────────────────
# ASHBY
# ─────────────────────────────────────────────────────────────────

def fetch_ashby(company_slug: str) -> list[dict]:
    """
    Polls Ashby HQ's public API.
    Ashby returns descriptionHtml in the list endpoint — full JD is available.
    URL: https://api.ashbyhq.com/posting-api/job-board/{slug}
    """
    # Ashby's correct public API endpoint
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("jobPostings", []):
            desc_html = job.get("descriptionHtml", "") or job.get("description", "")
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    job.get("locationName", "Not specified"),
                "description": _strip_html(desc_html),
                "url":         job.get("jobUrl", f"https://jobs.ashbyhq.com/{company_slug}/{job.get('id', '')}"),
                "source":      "ashby",
                "salary":      "",
                "posted_at":   job.get("publishedAt", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Ashby fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# WORKABLE
# ─────────────────────────────────────────────────────────────────

def _fetch_workable_jd(company_slug: str, shortcode: str) -> str:
    """Fetch full JD from Workable single-job endpoint."""
    url = f"https://apply.workable.com/api/v3/accounts/{company_slug}/jobs/{shortcode}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        # Workable full job has 'full_description' or 'description'
        return _strip_html(data.get("full_description", "") or data.get("description", ""))
    except Exception as e:
        logger.debug(f"Workable JD fetch failed for {shortcode}: {e}")
        return ""


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
            shortcode   = job.get("shortcode", "")
            location    = job.get("location", {})
            location_str = ", ".join(filter(None, [location.get("city", ""), location.get("country", "")])) or "Not specified"
            
            # Fetch full JD
            description = _fetch_workable_jd(company_slug, shortcode) if shortcode else ""
            
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    location_str,
                "description": description,
                "url":         f"https://apply.workable.com/{company_slug}/j/{shortcode}",
                "source":      "workable",
                "salary":      "",
                "posted_at":   job.get("published_on", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Workable fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────

def fetch_all_ats(companies_config: dict) -> list[dict]:
    """Main function: polls all companies in companies.yaml"""
    all_jobs = []

    for company in companies_config.get("greenhouse") or []:
        jobs = fetch_greenhouse(company)
        all_jobs.extend(jobs)
        logger.info(f"Greenhouse {company}: {len(jobs)} jobs")

    for company in companies_config.get("lever") or []:
        jobs = fetch_lever(company)
        all_jobs.extend(jobs)
        logger.info(f"Lever {company}: {len(jobs)} jobs")

    for company in companies_config.get("ashby") or []:
        jobs = fetch_ashby(company)
        all_jobs.extend(jobs)
        logger.info(f"Ashby {company}: {len(jobs)} jobs")

    for company in companies_config.get("workable") or []:
        jobs = fetch_workable(company)
        all_jobs.extend(jobs)
        logger.info(f"Workable {company}: {len(jobs)} jobs")

    logger.info(f"ATS total: {len(all_jobs)} raw jobs")
    return all_jobs
