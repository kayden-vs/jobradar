import yaml
import re
import logging

logger = logging.getLogger(__name__)


def load_profile(path="profile.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ─────────────────────────────────────────────────────────────────

def check_experience(description: str, title: str, profile: dict) -> tuple[bool, str]:
    """
    Reject if any hard-reject experience keyword is found, or if a regex
    pattern detects 2+ years experience requirement.
    """
    text = (description + " " + title).lower()

    for kw in (profile["hard_reject"].get("experience_keywords") or []):
        if kw.lower() in text:
            return True, f"Experience keyword: '{kw}'"

    year_patterns = [
        r'\b([2-9]|\d{2,})\+?\s*years?\s*(of\s+)?(experience|exp)\b',
        r'experience[:\s]+([2-9]|\d{2,})\+?\s*years?',
        r'minimum\s+([2-9]|\d{2,})\s*years?',
        r'([2-9]|\d{2,})\+\s*yrs?\b',
    ]
    for pat in year_patterns:
        m = re.search(pat, text)
        if m:
            return True, f"Experience regex: {m.group(0)}"

    return False, ""


def check_location(description: str, title: str, profile: dict) -> tuple[bool, str]:
    """Reject jobs that are explicitly outside India in-office only."""
    text = (description + " " + title).lower()
    for loc_kw in (profile["candidate"]["location"].get("hard_reject") or []):
        if loc_kw.lower() in text:
            return True, f"Location rejected: '{loc_kw}'"
    return False, ""


def check_company_blacklist(company: str, profile: dict) -> tuple[bool, str]:
    blacklist = [c.lower() for c in (profile["hard_reject"].get("company_blacklist") or [])]
    if company.lower() in blacklist:
        return True, f"Company blacklisted: {company}"
    return False, ""


def check_role_blacklist(title: str, profile: dict) -> tuple[bool, str]:
    title_lower = title.lower()
    for role in (profile["hard_reject"].get("role_blacklist") or []):
        if role.lower() in title_lower:
            return True, f"Role blacklisted: {role}"
    return False, ""


def check_candidate_post(job: dict) -> tuple[bool, str]:
    """Filter out candidate posts (people looking for work, not companies hiring)."""
    if job.get("company", "") == "CANDIDATE_POST":
        return True, "Candidate post"
    title = job.get("title", "").lower()
    if title.startswith("[for hire]") or title.startswith("seeking"):
        return True, "Candidate post"
    return False, ""


def check_has_meaningful_title(job: dict) -> tuple[bool, str]:
    """
    NEW: Reject jobs with no or empty title. These slip through other
    filters and waste an AI scoring call.
    """
    title = job.get("title", "").strip()
    if len(title) < 3:
        return True, "Missing or empty title"
    return False, ""


def check_title_relevance(title: str) -> tuple[bool, str]:
    """
    NEW: Tighter positive filter — job title must contain at least one
    signal word indicating it could be a backend/software role.
    This blocks obvious non-tech roles (HR, marketing, sales, designer)
    before they reach the AI scorer.
    """
    title_lower = title.lower()

    # Any of these in the title = keep it
    keep_signals = [
        "backend", "software", "engineer", "developer", "dev",
        "intern", "fresher", "full stack", "fullstack", "sde",
        "golang", "go ", " go,", "python", "api", "server",
        "platform", "infrastructure", "data engineer", "ml engineer",
    ]
    for signal in keep_signals:
        if signal in title_lower:
            return False, ""  # Keep

    # Common non-tech roles that waste scoring budget
    reject_roles = [
        "sales", "marketing", "hr ", "human resources", "recruiter",
        "accountant", "finance manager", "business analyst", "graphic",
        "content writer", "seo", "social media", "operations manager",
        "customer success", "account manager",
    ]
    for role in reject_roles:
        if role in title_lower:
            return True, f"Non-tech role title: '{role}'"

    # If title has no tech signals AND is not a clearly tech role, keep it
    # (don't over-filter, just kill obvious non-tech)
    return False, ""


def check_no_description(job: dict) -> tuple[bool, str]:
    """
    NEW: If a job has absolutely no description AND no meaningful title
    context, there's nothing for the AI to score — skip it.
    Only applies when description is truly empty (not just short).
    """
    desc = job.get("description", "").strip()
    title = job.get("title", "").strip()
    # Only reject if both are empty/very short — can't score without any text
    if len(desc) == 0 and len(title) < 10:
        return True, "No description and no meaningful title"
    return False, ""


# ─────────────────────────────────────────────────────────────────
# MAIN PRE-FILTER
# ─────────────────────────────────────────────────────────────────

def prefilter(jobs: list[dict], profile: dict) -> list[dict]:
    """
    Hard filters run BEFORE the AI scorer to reduce API cost.
    Order matters — cheapest checks first, most expensive last.

    Goal: push <30 jobs/day to the AI scorer to stay within Groq's
    1,000 req/day and 100k token/day limits.
    """

    passed = []

    for job in jobs:
        title       = job.get("title", "")
        company     = job.get("company", "")
        description = job.get("description", "")

        checks = [
            # Cheapest first
            check_has_meaningful_title(job),           # NEW: no title = skip
            check_no_description(job),                 # NEW: no text = skip
            check_candidate_post(job),                 # Not a job posting
            check_company_blacklist(company, profile), # Blacklisted company
            check_role_blacklist(title, profile),      # Blacklisted role type
            check_title_relevance(title),              # NEW: obvious non-tech role
            check_experience(description, title, profile),  # Overqualified
            check_location(description, title, profile),    # Wrong geography
        ]

        rejected = False
        for should_reject, reason in checks:
            if should_reject:
                logger.debug(f"REJECTED '{title}' @ '{company}': {reason}")
                rejected = True
                break

        if not rejected:
            passed.append(job)

    logger.info(f"Pre-filter: {len(jobs)} jobs -> {len(passed)} passed (sent to AI scorer)")
    return passed
