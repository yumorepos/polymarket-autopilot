#!/bin/bash
# Extended paper trading validation run (14 days)
#
# This script runs continuous paper trading with periodic reports
# to validate strategy edge before live deployment.
#
# Usage:
#   ./scripts/run_extended_paper_trading.sh
#
# Or as a background process:
#   nohup ./scripts/run_extended_paper_trading.sh > logs/paper_trading.log 2>&1 &

set -e

# Configuration
TRADE_INTERVAL=3600  # 1 hour between trade cycles
REPORT_INTERVAL=86400  # Daily reports (24 hours)
DURATION_DAYS=14

echo "Starting extended paper trading validation..."
echo "Duration: $DURATION_DAYS days"
echo "Trade interval: $TRADE_INTERVAL seconds ($(($TRADE_INTERVAL / 60)) minutes)"
echo "Report interval: $REPORT_INTERVAL seconds ($(($REPORT_INTERVAL / 3600)) hours)"
echo ""

# Activate virtual environment
source .venv/bin/activate

# Initialize database if needed
if [ ! -f "data/autopilot.db" ]; then
    echo "Initializing database..."
    polymarket-autopilot init
fi

# Calculate end time
END_TIME=$(($(date +%s) + $DURATION_DAYS * 86400))

# Counters
CYCLE_COUNT=0
REPORT_COUNT=0
LAST_REPORT_TIME=$(date +%s)

echo "Starting trading cycles..."
echo ""

while [ $(date +%s) -lt $END_TIME ]; do
    CYCLE_COUNT=$((CYCLE_COUNT + 1))
    CURRENT_TIME=$(date +%s)
    
    echo "=== Cycle $CYCLE_COUNT at $(date '+%Y-%m-%d %H:%M:%S') ==="
    
    # Run trading cycle
    echo "Scanning markets and executing trades..."
    polymarket-autopilot trade --dry-run || echo "Trade cycle completed with warnings"
    
    # Check if it's time for a report
    TIME_SINCE_REPORT=$((CURRENT_TIME - LAST_REPORT_TIME))
    if [ $TIME_SINCE_REPORT -ge $REPORT_INTERVAL ]; then
        REPORT_COUNT=$((REPORT_COUNT + 1))
        echo ""
        echo "=== Daily Report #$REPORT_COUNT at $(date '+%Y-%m-%d %H:%M:%S') ==="
        polymarket-autopilot report
        polymarket-autopilot compare --days 7 --top 3
        echo ""
        
        LAST_REPORT_TIME=$CURRENT_TIME
    fi
    
    # Calculate remaining time
    REMAINING_SECONDS=$((END_TIME - CURRENT_TIME))
    REMAINING_DAYS=$((REMAINING_SECONDS / 86400))
    REMAINING_HOURS=$(((REMAINING_SECONDS % 86400) / 3600))
    
    echo "Remaining: ${REMAINING_DAYS}d ${REMAINING_HOURS}h"
    echo "Next cycle in $TRADE_INTERVAL seconds..."
    echo ""
    
    sleep $TRADE_INTERVAL
done

echo ""
echo "=== Extended Paper Trading Complete ==="
echo "Total cycles: $CYCLE_COUNT"
echo "Duration: $DURATION_DAYS days"
echo ""
echo "Generating final report..."
polymarket-autopilot report
polymarket-autopilot compare --days $DURATION_DAYS --top 5
polymarket-autopilot history --limit 50

echo ""
echo "Results saved to data/autopilot.db"
echo "Review READINESS.md for next steps toward live trading"
