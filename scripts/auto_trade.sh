#!/usr/bin/env bash
# Polymarket Autopilot — automated trade cycle runner with lock + structured logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJ_DIR}/logs"
LOCK_DIR="${PROJ_DIR}/.locks"
LOCK_FILE="${LOCK_DIR}/auto_trade.lock"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/trade_${RUN_ID}.log"
MAX_PAGES="${AUTOPILOT_MAX_PAGES:-5}"
STRATEGY="${AUTOPILOT_STRATEGY:-all}"
LOCK_TTL_SECONDS="${AUTOPILOT_LOCK_TTL_SECONDS:-3600}"

mkdir -p "${LOG_DIR}" "${LOCK_DIR}"

if [[ ! -x "${PROJ_DIR}/.venv/bin/polymarket-autopilot" ]]; then
  echo "[ERROR] ${PROJ_DIR}/.venv/bin/polymarket-autopilot not found. Activate/install project first." >&2
  exit 1
fi

if [[ -e "${LOCK_FILE}" ]]; then
  lock_age=$(( $(date +%s) - $(stat -c %Y "${LOCK_FILE}") ))
  if (( lock_age >= LOCK_TTL_SECONDS )); then
    echo "[WARN] Existing lock detected (${lock_age}s old), exceeding TTL (${LOCK_TTL_SECONDS}s). Removing stale lock." >> "${LOG_FILE}"
    rm -f "${LOCK_FILE}"
  else
    echo "[WARN] Existing lock detected (${lock_age}s old). Skipping duplicate run." >> "${LOG_FILE}"
    exit 0
  fi
fi

cleanup() {
  rm -f "${LOCK_FILE}" || true
}
trap cleanup EXIT

touch "${LOCK_FILE}"

{
  echo "=== Auto-trade cycle start (UTC): $(date -u '+%Y-%m-%d %H:%M:%S') ==="
  echo "Run ID: ${RUN_ID}"
  echo "Project: ${PROJ_DIR}"
  echo "Command: polymarket-autopilot trade --strategy ${STRATEGY} --max-pages ${MAX_PAGES}"

  "${PROJ_DIR}/.venv/bin/polymarket-autopilot" trade --strategy "${STRATEGY}" --max-pages "${MAX_PAGES}"

  echo "=== Auto-trade cycle completed (UTC): $(date -u '+%Y-%m-%d %H:%M:%S') ==="
} >> "${LOG_FILE}" 2>&1

# Keep only last 14 days of logs
find "${LOG_DIR}" -name "trade_*.log" -mtime +14 -delete 2>/dev/null || true
