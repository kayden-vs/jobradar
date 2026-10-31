# November 2026 maintenance

The October 31 run evaluated 43 jobs successfully; 106 scoring requests failed
with Gemini HTTP 503 and one job expired before scoring. All 13 Telegram
extraction batches also failed with 503 after Telegram fetched 62 messages.
These were provider availability failures, not evidence of exhausted quota.

## Implemented behavior

- All Gemini callers use `pipeline/ai.py`: configurable model, one shared
  monotonic throttle, 45-second request timeout, at most three attempts for
  transient errors, jitter, and a five-minute circuit breaker after three
  exhausted requests. SDK retries are disabled so each attempt is throttled.
  No paid provider fallback is enabled.
- Raw Telegram posts and extracted jobs are persisted before acknowledgement.
  Successful extraction uses the original post text and exact source date.
  Repeated unchanged messages skip extraction. Missing/invalid post attribution
  leaves the batch pending rather than silently losing it.
- Pending scoring survives restarts, including candidates outside the AI cap.
  Evaluations cache for seven days, keyed by content, profile, model, and
  evaluator version. Failed requests never enter quality buckets.
- A bounded detail shortlist is enriched before ranking again. Thin sources
  receive detail-fetch opportunities in rotation. Incomplete descriptions stay
  pending. Internshala and RSS summaries now have explicit completeness flags.
- Director/staff/PhD requirements and explicit graduation restrictions are
  checked. Mentoring alongside senior engineers no longer rejects an internship.
  Foreign remote locations no longer imply Indian eligibility.
- Recency uses one parser for relative dates, ISO dates and epoch values.
- Blog sub-sources receive reserved slots; unused slots are backfilled.
  Distinct application URLs remain distinct requisitions. Same-URL duplicates
  merge richer descriptions and retain source attribution. Fallback metadata
  identity preserves years. Existing rows are updated via their stored URL hash.
- Scores 6–7 are sent in compact digest chunks. Urgent and digest deliveries are
  marked sent only after success; pending deliveries retry on a later run.
  Telegram lacks a caller-supplied idempotency key, so a network timeout after
  server acceptance can still result in a duplicate delivery.
- Successful low scores are retained so source-quality denominators include
  rejected evaluations. Source outcome counts, durations and filter rejection
  reasons are logged. Empty results are labeled `empty_or_failed`, since an
  adapter returning [] cannot distinguish those states reliably.
- TheJobCompany detail requests moved out of discovery. A 429 opens a cooldown
  respecting Retry-After rather than continuing requests for every listing.
- Greenhouse uses the documented API hostname and falls back to its structured
  location field when offices are absent.

## Running and deploying

Deploy code and install dependencies separately:

```sh
venv/bin/python -m pip install -r requirements.lock
scripts/run_pipeline.sh profile.yaml
```

`requirements.lock` captures the tested local environment (Python 3.14), including
its test tools. Validate it on the chosen host's Python version before cutover.
The portable runner uses flock to prevent overlapping runs and a one-hour wall
limit. Existing run.sh remains an EC2-only power-management wrapper. It no longer
pulls code or installs packages at every boot. Deploy updates explicitly.

Keep data/profile.db and .env when updating an existing instance. New work/cache
tables are additive. No external database or cloud resources are required by this
change. Hosting migration remains undecided.

Optional settings: GEMINI_MODEL changes the model without editing source;
GEMINI_REQUEST_INTERVAL can increase the 4.5-second minimum request spacing.
Set hard_reject.max_detail_fetches_per_run to control enrichment cost (default 60).
Changing the evaluator prompt requires bumping storage.work.VERSION.

## Verified and outstanding

A live Serper search with the configured key returned HTTP 200 and three organic
results. One live synthetic scoring request returned a valid Gemini score/reason.
Neither check sent a Telegram notification or scored real listings.

Live rechecks still returned 404 for Greenhouse phonepe/postman/loop and Ashby
clarisights, and 403 for hiring.cafe. The canonical API hostname does not repair
those missing boards. Their official replacement feeds still need verification.
Naukri's zero-yield condition needs a new production run with the added per-query
counts. YC coverage also needs a rendered-page comparison. Do not treat these
adapters as repaired solely because unit tests pass.

Adaptive exploration quotas, employer promotion, and a human-labeled ranking
benchmark remain evaluation experiments. The repair keeps the calibrated quality
thresholds rather than changing scores without outcome evidence.
