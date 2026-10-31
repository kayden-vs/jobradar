import json
from unittest.mock import patch,MagicMock
from scrapling.parser import Selector
from sources.yc import _parse_listing_page


def test_yc_accepts_alphanumeric_requisition_ids():
    html='<a href="/companies/acme/jobs/aBc123-backend-intern">Backend Intern</a>'
    assert len(_parse_listing_page(Selector(html),set()))==1


def test_yc_uses_structured_eligibility_not_search_page_labels():
    records=[dict(url='/companies/acme/jobs/aBc123-intern',title='Backend Intern',location='Bengaluru, IN',minExperience='Any (new grads ok)',companyName='Acme'),
             dict(url='/companies/acme/jobs/AbC234-swe',title='Software Engineer',location='Remote (US)',minExperience='Any (new grads ok)'),
             dict(url='/companies/acme/jobs/cDe456-swe',title='Software Engineer',location='India',minExperience='5+ years')]
    import html
    page=Selector('<div data-page="'+html.escape(json.dumps({'props':{'jobPostings':records}}),quote=True)+'"></div>')
    assert len(_parse_listing_page(page,set()))==1


def test_naukri_stops_after_captcha():
    from sources.naukri import fetch_naukri
    response=MagicMock(status_code=406)
    response.json.return_value={'message':'recaptcha required'}
    with patch('sources.naukri.requests.get',return_value=response) as get,patch('sources.naukri.time.sleep'):
        assert fetch_naukri({'naukri':{'keywords':['backend','golang','intern'],'locations':['India'],'pages':2}})==[]
        assert get.call_count==1


def test_naukri_long_snippet_is_not_a_complete_description():
    from sources.naukri import lazy_fetch_naukri_detail
    with patch('sources.naukri._fetch_job_detail',return_value='Full requirements') as detail,patch('sources.naukri.time.sleep'):
        assert lazy_fetch_naukri_detail({'_naukri_job_id':'123','description':'snippet '*60,'description_complete':False})=='Full requirements'
        detail.assert_called_once_with('123')


def test_phonepe_feed_keeps_only_public_posts_and_lazy_details():
    from sources.ats import fetch_phonepe
    response=MagicMock()
    response.json.return_value={'results':[
        {'status':'NOT_PUBLISHED','title':'Secret job','applyUrl':'https://example.org/1'},
        {'status':'PUBLIC','title':'Backend Intern','applyUrl':'https://jobs.smartrecruiters.com/PHONEPELIMITED/12345-backend-intern','location':'Bengaluru','updatedAt':'/Date(1793448000000)/'}]}
    with patch('sources.ats.requests.get',return_value=response):
        jobs=fetch_phonepe()
    assert len(jobs)==1 and jobs[0]['company']=='PhonePe'
    assert jobs[0]['description_complete'] is False
    assert jobs[0]['_smartrecruiters_company']=='PHONEPELIMITED'
    assert jobs[0]['_smartrecruiters_id']=='12345'
    assert jobs[0]['posted_at'].startswith('2026-10-31')


def test_workday_queries_deduplicate_requisitions():
    from sources.workday import fetch_all_workday
    job={'title':'Backend Intern','url':'https://example.org/job/1'}
    config={'workday':[dict(name='Postman',tenant='postman',wd_server='wd108',site='careers',search_terms=['intern','graduate'])]}
    with patch('sources.workday.fetch_workday',return_value=[job]) as fetch,patch('sources.workday.time.sleep'):
        assert fetch_all_workday(config)==[job]
    assert [call.kwargs['search_text'] for call in fetch.call_args_list]==['intern','graduate']


def test_workday_detail_replaces_ambiguous_location():
    from sources.workday import lazy_fetch_workday_detail
    response=MagicMock(status_code=200)
    response.json.return_value={'jobPostingInfo':{'jobDescription':'<p>Backend internship</p>','location':'Arkansas - Remote','additionalLocations':['Ohio - Remote'],'country':{'descriptor':'United States of America'}}}
    job={'_workday_tenant':'salesforce','_workday_wd_server':'wd12','_workday_site':'External_Career_Site','_workday_detail_path':'/job/1','location':'2 Locations'}
    with patch('sources.workday._request_with_retry',return_value=response):
        lazy_fetch_workday_detail(job)
    assert 'United States' in job['location'] and 'Ohio' in job['location']


def test_yc_job_description_ignores_other_openings():
    from sources.yc import _parse_job_page
    import html
    props={'job':{'location':'Bangalore, IN','salaryRange':'','companyName':'Acme'}}
    ld={'@type':'JobPosting','title':'Backend Intern','description':'<p>'+('Build Go APIs. '*15)+'</p>','datePosted':'2026-10-30T00:00:00Z','hiringOrganization':{'name':'Acme'}}
    page=Selector('<div data-page="'+html.escape(json.dumps({'props':props}),quote=True)+'"></div><script type="application/ld+json">'+json.dumps(ld)+'</script><aside>Other role requires 10 years of experience in London</aside>')
    job=_parse_job_page(page,'https://www.ycombinator.com/companies/acme/jobs/aB123-intern','Backend Intern',{})
    assert job['location']=='Bangalore, IN' and '10 years' not in job['description']
    assert job['posted_at']=='2026-10-30T00:00:00Z'


def test_country_filter_is_discovered_and_passed_to_each_workday_query():
    from sources.workday import fetch_all_workday
    config={'workday':[dict(name='Salesforce',tenant='salesforce',wd_server='wd12',site='External_Career_Site',country='India',search_terms=['intern','graduate'])]}
    with patch('sources.workday._country_facets',return_value={'countryFacet':['current-id']}),patch('sources.workday.fetch_workday',return_value=[]) as fetch,patch('sources.workday.time.sleep'):
        fetch_all_workday(config)
    assert fetch.call_count==2
    assert all(call.kwargs['applied_facets']=={'countryFacet':['current-id']} for call in fetch.call_args_list)


def test_country_filter_failure_never_falls_back_to_global_jobs():
    from sources.workday import fetch_all_workday
    config={'workday':[dict(name='Salesforce',tenant='salesforce',wd_server='wd12',site='External_Career_Site',country='India')]}
    with patch('sources.workday._country_facets',return_value=None),patch('sources.workday.fetch_workday') as fetch:
        assert fetch_all_workday(config)==[]
        fetch.assert_not_called()
