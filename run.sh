#!/bin/bash

# JobRadar daily runner — called by systemd on boot
# Always shuts down the instance after, success or failure

LOG_FILE="/home/ubuntu/jobradar/data/boot_run.log"
ENV_FILE="/home/ubuntu/jobradar/.env"

echo "========================================" >> "$LOG_FILE"
echo "Boot run started: $(date)" >> "$LOG_FILE"

# Load env vars for Telegram token (for error notifications)
set -a
source "$ENV_FILE"
set +a

# Wait for network to be fully ready (EC2 boot can be slow)
sleep 10

# Activate venv and run pipeline
cd /home/ubuntu/jobradar
source venv/bin/activate

if python main.py >> "$LOG_FILE" 2>&1; then
    echo "Pipeline completed successfully: $(date)" >> "$LOG_FILE"
else
    EXIT_CODE=$?
    echo "Pipeline FAILED with exit code $EXIT_CODE: $(date)" >> "$LOG_FILE"
    
    # Send failure alert to Telegram
    TAIL=$(tail -20 "$LOG_FILE")
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="${TELEGRAM_CHAT_ID}" \
        -d text="❌ JobRadar failed (exit $EXIT_CODE). Last 20 log lines:%0A$(echo "$TAIL" | head -c 3000)" \
        > /dev/null 2>&1
fi

echo "Shutting down instance: $(date)" >> "$LOG_FILE"
sudo shutdown -h now
