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
