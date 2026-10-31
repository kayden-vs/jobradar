#!/usr/bin/env bash
# Portable scheduled entry point. The host owns scheduling and power management.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
mkdir -p data
exec 9>data/pipeline.lock
flock -n 9 || exit 0
PYTHON_BIN="${JOBRADAR_PYTHON:-$PROJECT_DIR/venv/bin/python}"
# Deploy/install separately; a scheduled run never changes its own code or dependencies.
timeout 3600 "$PYTHON_BIN" main.py "${1:-profile.yaml}"
