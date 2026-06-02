#!/bin/bash
# run_profile.sh — run the JobRadar pipeline for a specific user profile
#
# Usage:
#   ./run_profile.sh                          # defaults to profiles/rohit.yaml
#   ./run_profile.sh profiles/alice.yaml
#
# Log is written to data/<username>.log (e.g. data/alice.log)

PROFILE=${1:-profiles/rohit.yaml}
USERNAME=$(basename "$PROFILE" .yaml)
LOG="data/${USERNAME}.log"

# Ensure data/ exists (init_db also does this, but log needs it first)
mkdir -p data

set -a
source .env
set +a

source venv/bin/activate

echo "========================================"    >> "$LOG"
echo "Run started: $(date)  [${PROFILE}]"         >> "$LOG"

# timeout 1500 = kill Python after 25 minutes if it hangs
timeout 1500 python main.py "$PROFILE" >> "$LOG" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    echo "PIPELINE TIMED OUT after 25 minutes: $(date)" >> "$LOG"
    # Read chat_id from the profile yaml for the timeout alert
    CHAT_ID=$(python -c "import yaml,sys; p=yaml.safe_load(open('$PROFILE')); print(p.get('telegram_chat_id',''))" 2>/dev/null)
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        -d text="⏰ JobRadar timed out after 25 min [${USERNAME}] — instance shutting down." \
        > /dev/null 2>&1
elif [ $EXIT_CODE -ne 0 ]; then
    echo "Pipeline FAILED with exit code $EXIT_CODE: $(date)" >> "$LOG"
    CHAT_ID=$(python -c "import yaml,sys; p=yaml.safe_load(open('$PROFILE')); print(p.get('telegram_chat_id',''))" 2>/dev/null)
    TAIL=$(tail -20 "$LOG")
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        -d text="❌ JobRadar failed (exit $EXIT_CODE) [${USERNAME}]:%0A$(echo "$TAIL" | head -c 3000)" \
        > /dev/null 2>&1
fi
