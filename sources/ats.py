import requests
import yaml
import logging
import re
import xml.etree.ElementTree as ET
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
# GREENHOUSE US
# ─────────────────────────────────────────────────────────────────

def _fetch_greenhouse_jd(company_slug: str, job_id: int, eu: bool = False) -> str:
    """
    Fetch full JD from Greenhouse single-job endpoint.
    Supports both US (boards.greenhouse.io) and EU (boards.eu.greenhouse.io).
    """
    if eu:
        url = f"https://boards.eu.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}"
    else:
        url = f"https://boards.greenhouse.io/v1/boards/{company_slug}/jobs/{job_id}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        return _strip_html(data.get("content", ""))
    except Exception as e:
        logger.debug(f"Greenhouse JD fetch failed for job {job_id}: {e}")
        return ""


def fetch_greenhouse(company_slug: str, eu: bool = False) -> list[dict]:
    """
    Polls the Greenhouse public API for a company.
    Set eu=True for companies on the European Greenhouse instance
    (boards.eu.greenhouse.io) — e.g. Groww.

    List endpoint: https://boards[.eu].greenhouse.io/v1/boards/{slug}/jobs?content=true
    The ?content=true param returns the full JD inline, saving one extra API call per job.
    """
    if eu:
        url = f"https://boards.eu.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
    else:
        url = f"https://boards.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"

    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for job in data.get("jobs", []):
            location_parts = [loc.get("name", "") for loc in job.get("offices", [])]

            # With ?content=true, the description is already in the list response
            desc_html = job.get("content", "")
            description = _strip_html(desc_html) if desc_html else _fetch_greenhouse_jd(company_slug, job.get("id"), eu=eu)

            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    ", ".join(location_parts) or "Not specified",
                "description": description,
                "url":         job.get("absolute_url", ""),
                "source":      "greenhouse_eu" if eu else "greenhouse",
                "salary":      "",
                "posted_at":   job.get("updated_at", ""),
            })
        return jobs
    except Exception as e:
        region = "EU" if eu else "US"
        logger.warning(f"Greenhouse {region} fetch failed for {company_slug}: {e}")
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
    URL: https://api.ashbyhq.com/posting-api/job-board/{slug}
    Response: { "jobs": [...], "apiVersion": "..." }
    Each job has: title, location, jobUrl, applyUrl, publishedAt,
                  descriptionPlain (already plain text), descriptionHtml (HTML fallback)
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("jobs", []):           # API key is 'jobs', not 'jobPostings'
            # Prefer pre-stripped plain text; fall back to stripping HTML
            description = (
                job.get("descriptionPlain", "").strip()
                or _strip_html(job.get("descriptionHtml", ""))
            )
            jobs.append({
                "title":       job.get("title", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    job.get("location", "Not specified") or "Not specified",
                "description": description,
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
            shortcode    = job.get("shortcode", "")
            location     = job.get("location", {})
            location_str = ", ".join(filter(None, [location.get("city", ""), location.get("country", "")])) or "Not specified"

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


# ─────────────────────────────────────────────────────────────────────
# SMARTRECRUITERS
# ─────────────────────────────────────────────────────────────────────

def _fetch_smartrecruiters_jd(company_slug: str, job_id: str) -> str:
    """
    Fetch full JD from SmartRecruiters single-job endpoint.
    URL: https://api.smartrecruiters.com/v1/companies/{slug}/postings/{job_id}
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings/{job_id}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        sections = data.get("jobAd", {}).get("sections", {})
        # Concatenate all named sections: companyDescription, jobDescription, qualifications, additionalInformation
        parts = []
        for section_key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
            section = sections.get(section_key, {})
            title = section.get("title", "")
            text  = section.get("text", "")
            if text:
                parts.append(f"## {title}\n{_strip_html(text)}" if title else _strip_html(text))
        return "\n\n".join(parts).strip()
    except Exception as e:
        logger.debug(f"SmartRecruiters JD fetch failed for job {job_id}: {e}")
        return ""


def fetch_smartrecruiters(company_slug: str) -> list[dict]:
    """
    Polls SmartRecruiters' public Posting API for a company.
    List endpoint: https://api.smartrecruiters.com/v1/companies/{slug}/postings
    SmartRecruiters slugs are case-sensitive (company identifier from their URL).
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings"
    try:
        r = requests.get(url, params={"limit": 100}, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("content", []):
            job_id   = job.get("id", "")
            location = job.get("location", {})
            loc_str  = location.get("fullLocation", "") or ", ".join(
                filter(None, [location.get("city", ""), location.get("country", "")])
            ) or "Not specified"

            description = _fetch_smartrecruiters_jd(company_slug, job_id) if job_id else ""

            jobs.append({
                "title":       job.get("name", ""),
                "company":     job.get("company", {}).get("name", company_slug),
                "location":    loc_str,
                "description": description,
                "url":         f"https://jobs.smartrecruiters.com/{company_slug}/{job_id}",
                "source":      "smartrecruiters",
                "salary":      "",
                "posted_at":   job.get("releasedDate", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"SmartRecruiters fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────
# RIPPLING ATS
# ─────────────────────────────────────────────────────────────────────

def fetch_rippling(company_slug: str) -> list[dict]:
    """
    Polls Rippling's undocumented public board API.
    List endpoint: https://ats.rippling.com/api/v2/board/{slug}/jobs
    Job detail:    https://ats.rippling.com/api/v2/board/{slug}/jobs/{uuid}
    Career page:   https://ats.rippling.com/{slug}/jobs
    Detail response includes 'createdOn' (ISO datetime) and description.company/role (HTML).
    """
    url = f"https://ats.rippling.com/api/v2/board/{company_slug}/jobs"
    try:
        r = requests.get(url, params={"pageSize": 100}, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("items", []):
            job_uuid  = job.get("id", "")
            locations = job.get("locations", [])
            loc_str   = ", ".join(
                loc.get("name", "") for loc in locations if loc.get("name")
            ) or "Not specified"

            description = ""
            posted_at   = ""
            if job_uuid:
                detail_url = f"https://ats.rippling.com/api/v2/board/{company_slug}/jobs/{job_uuid}"
                try:
                    dr = requests.get(detail_url, timeout=10)
                    dr.raise_for_status()
                    detail = dr.json()
                    desc = detail.get("description", {})
                    parts = []
                    if desc.get("company"):
                        parts.append(_strip_html(desc["company"]))
                    if desc.get("role"):
                        parts.append(_strip_html(desc["role"]))
                    description = "\n\n".join(p for p in parts if p).strip()
                    posted_at   = detail.get("createdOn", "")
                except Exception as e:
                    logger.debug(f"Rippling JD fetch failed for job {job_uuid}: {e}")

            jobs.append({
                "title":       job.get("name", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    loc_str,
                "description": description,
                "url":         job.get("url", f"https://ats.rippling.com/{company_slug}/jobs/{job_uuid}"),
                "source":      "rippling",
                "salary":      "",
                "posted_at":   posted_at,
            })
        return jobs
    except Exception as e:
        logger.warning(f"Rippling fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────
# BAMBOOHR
# ─────────────────────────────────────────────────────────────────────

def fetch_bamboohr(company_slug: str) -> list[dict]:
    """
    Polls BambooHR's public careers JSON endpoint.
    List endpoint: https://{slug}.bamboohr.com/careers/list
    Career page:   https://{slug}.bamboohr.com/careers
    NOTE: The list endpoint does not include full JDs — only metadata.
    The individual job URL is the public-facing HTML page.
    """
    url = f"https://{company_slug}.bamboohr.com/careers/list"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("result", []):
            job_id   = job.get("id", "")
            location = job.get("location", {})
            loc_parts = filter(None, [location.get("city", ""), location.get("state", "")])
            loc_str  = ", ".join(loc_parts) or "Not specified"

            jobs.append({
                "title":       job.get("jobOpeningName", ""),
                "company":     company_slug.replace("-", " ").title(),
                "location":    loc_str,
                "description": "",  # No public JSON description; visit job URL
                "url":         f"https://{company_slug}.bamboohr.com/careers/{job_id}",
                "source":      "bamboohr",
                "salary":      "",
                "posted_at":   "",
            })
        return jobs
    except Exception as e:
        logger.warning(f"BambooHR fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────
# RECRUITEE
# ─────────────────────────────────────────────────────────────────────

def fetch_recruitee(company_slug: str) -> list[dict]:
    """
    Polls Recruitee's public careers API.
    List endpoint: https://{slug}.recruitee.com/api/offers/
    Career page:   https://{slug}.recruitee.com/
    The response includes inline HTML description and requirements fields.
    """
    url = f"https://{company_slug}.recruitee.com/api/offers/"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        jobs = []
        for job in r.json().get("offers", []):
            # Description is inside translations.en.description (preferred) or top-level description
            desc_html = (
                job.get("translations", {}).get("en", {}).get("description")
                or job.get("description", "")
            )
            req_html = (
                job.get("translations", {}).get("en", {}).get("requirements")
                or job.get("requirements", "")
            )
            description = _strip_html(desc_html)
            requirements = _strip_html(req_html)
            full_desc = "\n\n".join(p for p in [description, requirements] if p).strip()

            jobs.append({
                "title":       job.get("title", ""),
                "company":     job.get("company_name", company_slug.replace("-", " ").title()),
                "location":    job.get("location", "Not specified") or "Not specified",
                "description": full_desc,
                "url":         job.get("careers_url", f"https://{company_slug}.recruitee.com/o/{job.get('slug', '')}"),
                "source":      "recruitee",
                "salary":      "",
                "posted_at":   job.get("published_at", ""),
            })
        return jobs
    except Exception as e:
        logger.warning(f"Recruitee fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────
# PERSONIO
# ─────────────────────────────────────────────────────────────────────

def fetch_personio(company_slug: str) -> list[dict]:
    """
    Polls Personio's public XML job feed.
    Endpoint: https://{slug}.jobs.personio.de/xml?language=en
    Career page: https://{slug}.jobs.personio.de/
    The XML has <workzag-jobs><position> elements with <name>, <office>,
    <department>, <jobDescriptions>, <createdAt>, and an implicit job URL.
    """
    url = f"https://{company_slug}.jobs.personio.de/xml"
    try:
        r = requests.get(url, params={"language": "en"}, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        jobs = []
        for pos in root.findall("position"):
            job_id    = pos.findtext("id", "").strip()
            title     = pos.findtext("name", "").strip()
            office    = pos.findtext("office", "").strip()
            dept      = pos.findtext("department", "").strip()
            created   = pos.findtext("createdAt", "").strip()
            location  = ", ".join(filter(None, [office, dept])) or "Not specified"

            # Concatenate all jobDescription sections into one description
            desc_parts = []
            for jd in pos.findall(".//jobDescription"):
                section_name  = jd.findtext("name", "").strip()
                section_value = jd.findtext("value", "").strip()
                if section_value:
                    clean = _strip_html(section_value)
                    desc_parts.append(f"## {section_name}\n{clean}" if section_name else clean)

            jobs.append({
                "title":       title,
                "company":     company_slug.replace("-", " ").title(),
                "location":    location,
                "description": "\n\n".join(desc_parts).strip(),
                "url":         f"https://{company_slug}.jobs.personio.de/job/{job_id}" if job_id else f"https://{company_slug}.jobs.personio.de/",
                "source":      "personio",
                "salary":      "",
                "posted_at":   created,
            })
        return jobs
    except Exception as e:
        logger.warning(f"Personio fetch failed for {company_slug}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────

def fetch_all_ats(companies_config: dict) -> list[dict]:
    """Main function: polls all companies in companies.yaml"""
    all_jobs = []

    # Greenhouse US
    for company in companies_config.get("greenhouse") or []:
        jobs = fetch_greenhouse(company, eu=False)
        all_jobs.extend(jobs)
        logger.info(f"Greenhouse US {company}: {len(jobs)} jobs")

    # Greenhouse EU (e.g. Groww)
    for company in companies_config.get("greenhouse_eu") or []:
        jobs = fetch_greenhouse(company, eu=True)
        all_jobs.extend(jobs)
        logger.info(f"Greenhouse EU {company}: {len(jobs)} jobs")

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

    for company in companies_config.get("smartrecruiters") or []:
        jobs = fetch_smartrecruiters(company)
        all_jobs.extend(jobs)
        logger.info(f"SmartRecruiters {company}: {len(jobs)} jobs")

    for company in companies_config.get("rippling") or []:
        jobs = fetch_rippling(company)
        all_jobs.extend(jobs)
        logger.info(f"Rippling {company}: {len(jobs)} jobs")

    for company in companies_config.get("bamboohr") or []:
        jobs = fetch_bamboohr(company)
        all_jobs.extend(jobs)
        logger.info(f"BambooHR {company}: {len(jobs)} jobs")

    for company in companies_config.get("recruitee") or []:
        jobs = fetch_recruitee(company)
        all_jobs.extend(jobs)
        logger.info(f"Recruitee {company}: {len(jobs)} jobs")

    for company in companies_config.get("personio") or []:
        jobs = fetch_personio(company)
        all_jobs.extend(jobs)
        logger.info(f"Personio {company}: {len(jobs)} jobs")

    logger.info(f"ATS total: {len(all_jobs)} raw jobs")
    return all_jobs
