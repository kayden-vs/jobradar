# JobRadar — Beta Operations Guide

Internal reference for managing the multi-user beta.  
**Not for sharing with users — contains infra details.**

---

## Table of Contents

1. [Adding a New Friend](#1-adding-a-new-friend-end-to-end)
2. [Recommended Schedule Slots](#2-recommended-schedule-slots-8-users)
3. [Monitoring All Users](#3-monitoring-all-users)
4. [Changing a User's Preferences](#4-changing-a-users-preferences)
5. [Removing a User](#5-removing-a-user)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Adding a New Friend (End to End)

### Step 1 — Create their profile

```bash
# On your local machine, in the jobradar/ directory
cp profiles/template.yaml profiles/<username>.yaml
```

Open `profiles/<username>.yaml` and fill in every `TODO` field:

| Field | Where to find it |
|---|---|
| `telegram_chat_id` | See Step 2 below |
| `db_path` | Set to `"data/<username>.db"` |
| `candidate.name` | Ask them |
| `candidate.email` | Ask them |
| `candidate.roles.primary` | Ask them what they're targeting |
| `candidate.skills.strong` | From their resume |
| `candidate.education.graduation` | From their resume |
| `candidate.location.base` | Their current city |
| `candidate.salary.min_stipend_inr` | Ask them (default: 10000) |

Validate the profile with a dry run before doing anything else:

```bash
python main.py profiles/<username>.yaml --dry-run
```

Expected output: all fields populated, no `NOT SET` warnings, exit code 0.

---

### Step 2 — Get their Telegram chat_id

**Ask the user to:**
1. Open Telegram and search for the bot (share the bot username with them)
2. Send any message to the bot (e.g. `/start` or `hello`)

**Then run this curl to see the incoming message:**

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
```

Look for the `"chat"` object in the response — the `"id"` field is their `chat_id`:

```json
{
  "message": {
    "chat": {
      "id": 974951618,        ← this is their chat_id
      "first_name": "Alice",
      "type": "private"
    }
  }
}
```

> [!IMPORTANT]
> If `getUpdates` returns an empty `result: []`, the user hasn't messaged the bot yet.
> Ask them to send any message first, then re-run the curl.

Set the value in their profile yaml:

```yaml
telegram_chat_id: "974951618"   # Alice
```

**Send a test message to confirm it works:**

```bash
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="974951618" \
  -d text="Hi from JobRadar! Your notifications are set up."
```

---

### Step 3 — Import their resume (coming soon)

```bash
# TODO: Not yet implemented.
# python resume_to_profile.py profiles/<username>.yaml path/to/resume.pdf
#
# This will pre-fill skills, projects, and education from the resume PDF.
# Until then, fill in those sections manually from their resume.
```

---

### Step 4 — Create their EventBridge schedule

Each user gets one EventBridge rule that starts their EC2 pipeline run at
their assigned time slot (see [Section 2](#2-recommended-schedule-slots-8-users)).

**In the AWS Console → EventBridge → Schedules → Create schedule:**

| Setting | Value |
|---|---|
| Schedule name | `jobradar-<username>-daily` |
| Schedule pattern | Recurring schedule → Cron-based |
| Cron expression | See Section 2 for their assigned slot |
| Flexible time window | Off |
| Target | EC2 → Run command → `bash /home/ubuntu/jobradar/run_profile.sh profiles/<username>.yaml` |
| IAM role | Your existing EventBridge execution role |
| Timezone | UTC (all cron times in this guide are UTC) |

**Cron expression format for EventBridge:**

```
cron(MINUTE HOUR * * ? *)
```

Example for 02:30 UTC:

```
cron(30 2 * * ? *)
```

> [!NOTE]
> EventBridge uses a 6-field cron where the 6th field is year (not day-of-week).
> The `?` in position 5 means "any day of week" — required when day-of-month is `*`.

---

### Step 5 — Verify end to end

SSH into the EC2 instance and do a dry run manually to confirm everything loads:

```bash
ssh -i jobradar-key.pem ubuntu@<ec2-public-ip>
cd /home/ubuntu/jobradar
source venv/bin/activate
python main.py profiles/<username>.yaml --dry-run
```

Check the output for:
- `Candidate    : <their name>` ✓
- `Database     : data/<username>.db` ✓
- `Telegram ID  : <their chat_id>` (not `NOT SET`) ✓
- `Sources ON   : ats, instahyre, ...` ✓

Then **commit and push** the updated `profiles/template.yaml` if you changed it
(individual `profiles/<username>.yaml` files are gitignored — push only the code).

---

## 2. Recommended Schedule Slots (8 Users)

Slots are staggered at **20-minute intervals starting 08:00 IST (02:30 UTC)**.
Each pipeline run takes up to 25 minutes; the 20-minute stagger means runs
overlap slightly — confirm the EC2 instance can handle concurrent runs or
use a longer gap if needed.

| Slot | User | UTC time | IST time | EventBridge cron |
|---|---|---|---|---|
| 1 | rohit | 02:30 | 08:00 | `cron(30 2 * * ? *)` |
| 2 | _(friend 2)_ | 02:50 | 08:20 | `cron(50 2 * * ? *)` |
| 3 | _(friend 3)_ | 03:10 | 08:40 | `cron(10 3 * * ? *)` |
| 4 | _(friend 4)_ | 03:30 | 09:00 | `cron(30 3 * * ? *)` |
| 5 | _(friend 5)_ | 03:50 | 09:20 | `cron(50 3 * * ? *)` |
| 6 | _(friend 6)_ | 04:10 | 09:40 | `cron(10 4 * * ? *)` |
| 7 | _(friend 7)_ | 04:30 | 10:00 | `cron(30 4 * * ? *)` |
| 8 | _(friend 8)_ | 04:50 | 10:20 | `cron(50 4 * * ? *)` |

> [!TIP]
> Fill in usernames as you add friends. Assign slots in order — don't leave gaps,
> it makes it harder to reason about which runs might overlap.

---

## 3. Monitoring All Users

### Check the log for a specific user

Each user has their own rotating log file at `data/<username>.log`.

```bash
# Follow live (during a run)
tail -f data/rohit.log

# Last 50 lines (after a run)
tail -50 data/rohit.log

# See all users' last-run summary lines at once
grep "Pipeline complete\|No new eligible\|FAILED\|TIMED OUT" data/*.log
```

---

### What a healthy run looks like

```
[INFO] jobradar: JobRadar pipeline starting — profile: profiles/rohit.yaml
[INFO] jobradar: --- Fetching ATS endpoints ...
[INFO] storage.db: ...
[INFO] jobradar: Total raw jobs from all sources: 312
[INFO] pipeline.dedup: Dedup: 312 raw -> 87 new
[INFO] pipeline.prefilter: Pre-filter: 87 jobs -> 14 passed (sent to AI scorer)
[INFO] pipeline.scorer: Scoring 14 eligible jobs with Groq (meta-llama/...)
[INFO] pipeline.scorer: Scored: Backend Engineer @ Razorpay -> 9/10 [high]
...
[INFO] pipeline.scorer: Scoring complete: 2 urgent, 5 digest, 7 low, 0 expired (dropped)
[INFO] jobradar: Sending 2 urgent Telegram alerts
[INFO] jobradar: Pipeline complete.
[INFO] jobradar: Summary: 312 raw -> 87 new -> 14 eligible -> 2 urgent
```

Key signals:
- `Total raw jobs` → 100–500 is normal; 0 means all sources failed
- `Dedup: X -> Y new` → Y should be > 0 on most days (new listings appear daily)
- `Pre-filter: X -> Y passed` → Y should be < 30 (pre-filter working correctly)
- `Scoring complete` → should appear with counts (not a traceback)
- `Pipeline complete` → last line; if missing, the run died mid-way

---

### What a failed run looks like

```
[ERROR] pipeline.scorer: Groq scoring failed for Backend Engineer: ...
[ERROR] notify.telegram_bot: Telegram send failed: ...
```

Or the log simply ends abruptly with no `Pipeline complete.` — this means
the process was killed (timeout or OOM).

---

### Check if the EC2 shut down correctly (single-user run.sh)

```bash
# In AWS Console → EC2 → Instances:
# Instance state should be "stopped" ~30 minutes after the scheduled run.

# Or via CLI (requires aws-cli configured):
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=jobradar" \
  --query "Reservations[].Instances[].{State:State.Name,ID:InstanceId}" \
  --output table
```

> [!WARNING]
> If the instance is still `running` 45+ minutes after a scheduled start time,
> the pipeline hung. See [Troubleshooting → Instance stayed on](#instance-stayed-on-pipeline-hung).

---

### Quick multi-user health check

```bash
# Run this the morning after all slots have fired:
echo "=== JobRadar daily health ===" && \
for f in data/*.log; do
  username=$(basename "$f" .log)
  last=$(grep "Pipeline complete\|No new eligible\|FAILED\|TIMED OUT" "$f" | tail -1)
  echo "[$username] $last"
done
```

---

## 4. Changing a User's Preferences

Since each run is one-shot (EventBridge → EC2 → pipeline → shutdown), there is
**no running process to restart**. Changes take effect on the next scheduled run.

### Common edits

**Add a skill to their stack:**
```yaml
# profiles/<username>.yaml
candidate:
  skills:
    strong:
      - "Python"       # ← add here
```

**Add a company to their blacklist:**
```yaml
hard_reject:
  company_blacklist:
    - "TCS"
    - "Infosys"        # ← add here
```

**Tighten the job age filter:**
```yaml
hard_reject:
  max_job_age_days: 30   # was 45 — only show jobs from the last 30 days
```

**Disable a noisy source:**
```yaml
sources:
  freshers_blogs: false   # too many false positives for this user
```

**After editing, always dry-run to check the YAML is valid:**
```bash
python main.py profiles/<username>.yaml --dry-run
```

> [!NOTE]
> Changing `db_path` mid-flight means the user's seen-jobs history resets.
> Their pipeline will re-surface jobs it already sent. Only change `db_path`
> if you're intentionally resetting their history.

---

## 5. Removing a User

### Step 1 — Disable their EventBridge schedule

```
AWS Console → EventBridge → Schedules → jobradar-<username>-daily → Disable
```

Or delete it permanently:

```
AWS Console → EventBridge → Schedules → jobradar-<username>-daily → Delete
```

### Step 2 — Delete their profile

```bash
rm profiles/<username>.yaml
```

### Step 3 — Archive or delete their database

```bash
# Option A: archive (recommended — preserves history in case they return)
mkdir -p data/archive
mv data/<username>.db data/archive/<username>_$(date +%Y%m%d).db

# Option B: delete permanently
rm data/<username>.db
```

### Step 4 — Archive their log

```bash
mv data/<username>.log data/archive/<username>_$(date +%Y%m%d).log
```

---

## 6. Troubleshooting

### Instance stayed on (pipeline hung)

**Symptom:** EC2 instance is still `running` 45+ minutes after scheduled start.

**Diagnosis:**
```bash
# SSH in and check what's running
ssh -i jobradar-key.pem ubuntu@<ec2-public-ip>
ps aux | grep python
tail -f /home/ubuntu/jobradar/data/<username>.log
```

**Likely causes:**
- `StealthyFetcher` (Playwright) hung on a slow/unresponsive site — the 10s timeout per fetch should prevent this, but sometimes the browser process itself hangs
- `score_all()` stuck waiting on Groq — check if `GROQ_API_KEY` is valid

**Fix:**
```bash
# Kill the hung pipeline
sudo pkill -f "python main.py"

# Then manually shut down
sudo shutdown -h now
```

**Prevention:** The `timeout 1500` in `run_profile.sh` sends SIGKILL after 25 minutes.
If this is firing, the run is genuinely too slow — consider disabling the slowest
sources (usually `yc`, `serper`, `wellfound`) for that user.

---

### User not receiving notifications (chat_id issue)

**Symptom:** Log shows `Sending N urgent Telegram alerts` but user sees nothing.

**Step 1 — Verify chat_id is correct:**
```bash
python main.py profiles/<username>.yaml --dry-run
# Check: "Telegram ID  : <number>"  (not "NOT SET")
```

**Step 2 — Send a direct test message:**
```bash
source .env
CHAT_ID=$(python -c "import yaml; p=yaml.safe_load(open('profiles/<username>.yaml')); print(p['telegram_chat_id'])")
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d chat_id="${CHAT_ID}" \
  -d text="JobRadar test — can you see this?"
```

**Step 3 — Check the bot response:**
```bash
# A successful send returns: {"ok":true,"result":{...}}
# A failed send returns:    {"ok":false,"error_code":400,"description":"Bad Request: chat not found"}
```

**Common fixes:**

| Error | Fix |
|---|---|
| `chat not found` | User hasn't started the bot. Ask them to send `/start` to the bot and re-run. |
| `bot was blocked by the user` | User blocked the bot. Ask them to unblock it in Telegram settings. |
| `NOT SET` in dry-run | `telegram_chat_id` field is empty in their profile yaml. Fill it in. |
| Correct chat_id but no messages | Log may say `Telegram send failed` — check for MarkdownV2 formatting error in the job title/company name |

---

### 0 jobs after scoring (rate limit / AI provider issue)

**Symptom:** Log shows `Scoring N eligible jobs` then `Scoring complete: 0 urgent, 0 digest, N low`.

This is usually not a bug — it means the AI scored all jobs below 6. But if the numbers look wrong:

**Check for Groq rate limit errors:**
```bash
grep "Groq scoring failed\|429\|rate limit" data/<username>.log
```

**Check the Groq dashboard:**
- https://console.groq.com → Usage
- Free tier: 500K tokens/day, 30 req/min, 1K req/day
- If TPD (tokens/day) is exhausted, all scoring calls will fail silently and jobs get score -1

**If Groq is rate-limited:**
- The next day's run will work (limits reset at midnight UTC)
- Consider reducing `max_ai_jobs_per_run` in the profile to use fewer tokens:
  ```yaml
  hard_reject:
    max_ai_jobs_per_run: 40   # was 80
  ```
- Or stagger the users' run times further apart (30+ min gaps) to spread Groq load

**Check for scorer errors:**
```bash
grep "score.*-1\|Scoring error" data/<username>.log | head -20
```

---

### Profiles getting each other's jobs (db_path misconfiguration)

**Symptom:** User B stops receiving jobs that user A has already seen, or vice versa.

**This means two profiles share the same `db_path`.** Every job has a dedup hash —
if it's already in User A's DB and User B's `db_path` points to the same file,
User B's pipeline skips it.

**Diagnosis:**
```bash
# Check db_path for all profiles
grep "db_path" profiles/*.yaml
```

Expected output — every line must be unique:
```
profiles/rohit.yaml:db_path: "data/rohit.db"
profiles/alice.yaml:db_path: "data/alice.db"
profiles/bob.yaml:db_path: "data/bob.db"
```

**If two profiles share a db_path:**
1. Edit the incorrect profile to give it a unique path
2. Delete the incorrect db file so the next run starts fresh:
   ```bash
   rm data/<wronguser>.db
   ```
3. Dry-run to confirm:
   ```bash
   python main.py profiles/<username>.yaml --dry-run
   # Check: "Database     : data/<username>.db"
   ```

> [!CAUTION]
> Deleting a `.db` file resets that user's seen-jobs history. On the next run,
> they may receive alerts for jobs they've already applied to. Warn them.
