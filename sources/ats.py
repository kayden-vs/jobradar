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
