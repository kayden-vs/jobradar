import feedparser
import logging

logger = logging.getLogger(__name__)

# Reddit RSS feeds — free, no auth required
REDDIT_FEEDS = [
    "https://www.reddit.com/r/developersIndia/search.rss?q=hiring+intern&sort=new&t=week",
    "https://www.reddit.com/r/developersIndia/search.rss?q=backend+fresher&sort=new&t=week",
    "https://www.reddit.com/r/IndiaHiring/search.rss?q=backend+golang&sort=new&t=week",
    "https://www.reddit.com/r/IndiaHiring/new.rss",
    "https://www.reddit.com/r/forhire/search.rss?q=golang+backend+remote&sort=new&t=week",
]

def fetch_reddit() -> list[dict]:
    all_jobs = []
    seen = set()
    
    for feed_url in REDDIT_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                url = entry.get("link", "")
                if url in seen:
                    continue
                seen.add(url)
                
                # Reddit posts are often [HIRING] prefixed
                title = entry.get("title", "")
                if not any(kw in title.lower() for kw in
                           ["hiring", "backend", "golang", "go ", "intern", "fresher", "job"]):
                    continue  # Skip non-job posts early
                
                all_jobs.append({
                    "title":       title,
                    "company":     _extract_company_from_reddit(title),
                    "location":    _extract_location_hint(title + entry.get("summary", "")),
                    "description": entry.get("summary", ""),
                    "url":         url,
                    "source":      "reddit",
                    "salary":      "",
                    "posted_at":   entry.get("published", ""),
                })
        except Exception as e:
            logger.warning(f"Reddit feed failed {feed_url}: {e}")
    
    logger.info(f"Reddit: {len(all_jobs)} posts found")
    return all_jobs


def _extract_company_from_reddit(title: str) -> str:
    """
    Reddit job posts often follow patterns like:
    '[HIRING] Backend Intern @ CompanyName'
    '[FOR HIRE]...' (ignore these — they're someone looking for work)
    """
    import re
    if "[for hire]" in title.lower():
        return "CANDIDATE_POST"  # Will be filtered out in pre-filter
    
    # Look for @ symbol
    m = re.search(r'@\s*([A-Za-z0-9\s]+)', title)
    if m:
        return m.group(1).strip()
    return ""


def _extract_location_hint(text: str) -> str:
    text_lower = text.lower()
    if "remote" in text_lower:
        return "Remote"
    if "india" in text_lower:
        return "India"
    return "Not specified"
