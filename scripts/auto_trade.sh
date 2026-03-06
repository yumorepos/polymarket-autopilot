#!/bin/bash
# Polymarket Autopilot — automated trade cycle
# Runs all strategies, logs output

set -euo pipefail

PROJ_DIR="$HOME/Projects/polymarket-autopilot"
LOG_DIR="$PROJ_DIR/logs"
VENV="$PROJ_DIR/.venv/bin/activate"

mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/trade_${TIMESTAMP}.log"

cd "$PROJ_DIR"
source "$VENV"

echo "=== Auto-trade cycle: $(date) ===" >> "$LOG_FILE"
polymarket-autopilot trade --strategy all --max-pages 5 >> "$LOG_FILE" 2>&1
echo "=== Completed: $(date) ===" >> "$LOG_FILE"

# Keep only last 7 days of logs
find "$LOG_DIR" -name "trade_*.log" -mtime +7 -delete 2>/dev/null || true
