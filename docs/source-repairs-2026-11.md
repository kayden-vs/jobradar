# Enabled source repairs — November 2026

These changes focus on the existing enabled sources. Hiring.cafe, all disabled
source modules, and profile source toggles are unchanged.

## Verified employer migrations

- **PhonePe**: replaced its dead Greenhouse entry with the public JSON feed
  loaded by https://www.phonepe.com/careers/job-openings/:
  https://www.phonepe.com/apollo/job-postings/latest.json . Only PUBLIC records
  with valid application URLs are imported. The live feed contained 131 public
  jobs. Full descriptions are fetched lazily via the SmartRecruiters Posting
  API, which returned a complete description in validation. The general
  SmartRecruiters poller remains disabled; this is one explicitly configured
  employer feed under ATS.
- **Postman**: its official careers frontend identifies
  https://postman.wd108.myworkdayjobs.com/careers as the current board. Removed
  the obsolete Greenhouse entry and configured this Workday tenant. Targeted
  intern/graduate/associate/backend/software searches are deduplicated by URL.
- **Salesforce**: replaced the dead Recruitee entry with the verified
  https://salesforce.wd12.myworkdayjobs.com/External_Career_Site portal. The portal
  exposes its tenant/site identifiers and a working public list/detail API.
  Country IDs are discovered from its current facets; searches explicitly
  select India and skip the employer if that geography cannot be verified.

Postman and Salesforce returned 44 unique tech-relevant listings together in
final live verification. Eleven passed the initial filters. These are candidates,
not confirmed matches: full descriptions rejected two of the first five checked.
Workday enrichment now replaces labels such as "2 Locations" with actual primary
and additional locations plus country. A US remote internship no longer retains
an ambiguous location through scoring.

The per-company Workday configuration supports optional `search_terms` and
`country`. Existing companies retain their current behavior unless configured.

## YC

The old query-string search URLs returned generic landing pages. The parser also
required numeric job IDs although current IDs are alphanumeric. It now uses actual
role/location routes and embedded job records, filtering structured experience and
geography before requesting descriptions. Detail parsing uses JobPosting JSON-LD,
including the original date and job-specific body rather than page navigation,
company blurbs, and other jobs.

The live check found one suitable-looking listing whose actual publication date
was April 2022. It was correctly rejected by the age filter. More extracted links
should not be mistaken for more useful current opportunities.

## Naukri

The legacy v2 search endpoint returned HTTP 200 and an empty list. The current v3
endpoint returned HTTP 406 with "recaptcha required", including after a normal
session initialization. The adapter now uses v3, sends the experience filter,
reads v3 location/experience placeholders, and stops the remaining search grid
when access is explicitly denied. It no longer labels that case as overly strict
experience filtering or spends all 60 requests on it. Partial successful results
are preserved if access fails later.

Full-description retrieval no longer mistakes a long listing snippet for a
complete job description. **Live Naukri retrieval remains blocked by the provider;
this change does not claim to restore access or automate interactive verification.**

## Validation and remaining limits

Regression tests cover current YC IDs, structured eligibility, job-only JSON-LD,
PhonePe publication status, Naukri block handling and long snippets, Workday search
deduplication, geography propagation, and country-filter failure. The complete
offline suite passed 556 tests. Live checks fetched public listings/descriptions;
they did not send notifications or modify the production database.

Clarisights' official careers page still embeds the same unavailable Ashby board.
No verified replacement feed was found. Other stale registry entries such as Loop
and Belong remain unresolved; they were not replaced with guessed employer slugs.
All live results describe access from the current environment. A changed hosting
IP may receive different anti-bot responses.
