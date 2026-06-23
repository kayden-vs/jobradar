#!/bin/bash

LOG_FILE="/home/ubuntu/jobradar/data/boot_run.log"
ENV_FILE="/home/ubuntu/jobradar/.env"

# ALWAYS shut down when this script exits, no matter what
trap 'echo "Script exiting — shutting down instance: $(date)" >> "$LOG_FILE"; sudo shutdown -h now' EXIT

echo "========================================" >> "$LOG_FILE"
echo "Boot run started: $(date)" >> "$LOG_FILE"

set -a
source "$ENV_FILE"
set +a

sleep 10  # wait for network

cd /home/ubuntu/jobradar
source venv/bin/activate

# Force-sync with remote — never fails due to local modifications.
# git reset --hard discards any local changes on EC2 and snaps to remote.
# Untracked files (data/, .env, venv/) are untouched — no git clean.
echo "Syncing code with remote: $(date)" >> "$LOG_FILE"
git fetch origin >> "$LOG_FILE" 2>&1
git reset --hard "origin/main" >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    echo "WARNING: git reset --hard failed, continuing with existing code" >> "$LOG_FILE"
fi

# update packages
echo "updating packages with pip" >> "$LOG_FILE"
pip install -r requirements.txt >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    echo "WARNING: pip install failed, continuing with existing code" >> "$LOG_FILE"
fi

# ── Start the application tracker bot in the background ──────────────────────
# This makes /applied, /status etc. available for the entire duration of the run.
echo "Starting tracker bot: $(date)" >> "$LOG_FILE"
python -m notify.tracker_bot >> "$LOG_FILE" 2>&1 &
TRACKER_BOT_PID=$!
echo "Tracker bot PID: $TRACKER_BOT_PID" >> "$LOG_FILE"

# kill Python after 60 minutes if it hangs
timeout 3600 python main.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    echo "PIPELINE TIMED OUT after 60 minutes: $(date)" >> "$LOG_FILE"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="⏰ JobRadar timed out after 60 min — instance shutting down. Check logs." \
        > /dev/null 2>&1
elif [ $EXIT_CODE -ne 0 ]; then
    echo "Pipeline FAILED with exit code $EXIT_CODE: $(date)" >> "$LOG_FILE"
    TAIL=$(tail -20 "$LOG_FILE")
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="❌ JobRadar failed (exit $EXIT_CODE):%0A$(echo "$TAIL" | head -c 3000)" \
        > /dev/null 2>&1
fi

# ── Run follow-up check (7-day drafts + 14-day dead marking) ─────────────────
echo "Running follow-up check: $(date)" >> "$LOG_FILE"
timeout 60 python -m notify.followup_check >> "$LOG_FILE" 2>&1

# ── Stop the tracker bot now that the instance is about to shut down ──────────
echo "Stopping tracker bot (PID $TRACKER_BOT_PID): $(date)" >> "$LOG_FILE"
kill "$TRACKER_BOT_PID" 2>/dev/null || true

# trap handles shutdown
