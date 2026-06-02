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

<<<<<<< HEAD
# timeout 1500 = kill Python after 25 minutes if it hangs
timeout 1500 python main.py profiles/rohit.yaml >> "$LOG_FILE" 2>&1
=======
# Pull latest code before running
echo "Pulling latest code: $(date)" >> "$LOG_FILE"
git pull >> "$LOG_FILE" 2>&1
if [ $? -ne 0 ]; then
    echo "WARNING: git pull failed, continuing with existing code" >> "$LOG_FILE"
fi

# timeout 2400 = kill Python after 40 minutes if it hangs
timeout 2400 python main.py >> "$LOG_FILE" 2>&1
>>>>>>> 36df35d (add new source:hirist, not tested yet. increase service timeout from 25 to 40 mins)
EXIT_CODE=$?

if [ $EXIT_CODE -eq 124 ]; then
    echo "PIPELINE TIMED OUT after 25 minutes: $(date)" >> "$LOG_FILE"
    CHAT_ID=$(python -c "import yaml; p=yaml.safe_load(open('profiles/rohit.yaml')); print(p.get('telegram_chat_id',''))" 2>/dev/null)
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        -d text="⏰ JobRadar timed out after 25 min — instance shutting down. Check logs." \
        > /dev/null 2>&1
elif [ $EXIT_CODE -ne 0 ]; then
    echo "Pipeline FAILED with exit code $EXIT_CODE: $(date)" >> "$LOG_FILE"
    CHAT_ID=$(python -c "import yaml; p=yaml.safe_load(open('profiles/rohit.yaml')); print(p.get('telegram_chat_id',''))" 2>/dev/null)
    TAIL=$(tail -20 "$LOG_FILE")
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${CHAT_ID}" \
        -d text="❌ JobRadar failed (exit $EXIT_CODE):%0A$(echo "$TAIL" | head -c 3000)" \
        > /dev/null 2>&1
fi

# trap handles shutdown