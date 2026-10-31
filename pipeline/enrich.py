"""Fetch full descriptions only for a bounded shortlist."""
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def enrich(job):
    if job.get('description_complete') is True or job.get('_enrichment_attempted'):
        return job
    job['_enrichment_attempted'] = True
    source = job.get('source','').split('/')[0]
    text = ''
    try:
        if source == 'freshers_blogs':
            from sources.freshers_blogs import fetch_full_description
            text = fetch_full_description(job['url'])
        elif source == 'internshala':
            response = requests.get(job['url'], timeout=15, headers={'User-Agent':'Mozilla/5.0'})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            node = soup.select_one('#details_container, .internship_details, .detail_view')
            text = node.get_text(' ', strip=True) if node else ''
        elif job.get('_naukri_job_id'):
            from sources.naukri import lazy_fetch_naukri_detail
            text = lazy_fetch_naukri_detail(job)
        elif job.get('_workday_detail_path'):
            from sources.workday import lazy_fetch_workday_detail
            text = lazy_fetch_workday_detail(job)
        elif source == 'thejobcompany':
            from sources.thejobcompany import _fetch, _parse_detail_page
            soup = _fetch(job['url'])
            if soup is not None:
                from urllib.parse import urlsplit, parse_qs
                job_id = parse_qs(urlsplit(job['url']).query).get('job_id',[''])[0]
                detail = _parse_detail_page(soup, job_id)
                job.update(detail)
                text = detail.get('description','')
    except Exception as exc:
        logger.warning('Detail fetch failed for %s (%s)', source, type(exc).__name__)
    if text and len(text) > len(job.get('description','')):
        job['description'] = text[:12000]
        job['description_complete'] = True
    return job
