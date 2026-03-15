#!/usr/bin/env python3
"""Live trading monitoring script.

Continuously monitors live trading performance and sends alerts when:
- Daily loss limit is approached or breached
- Unusual P&L swings occur
- API connection fails
- Win rate drops below threshold

Run as a background process or cron job:
    python scripts/monitor_live_trading.py --interval 300

Or as a one-shot check:
    python scripts/monitor_live_trading.py --once
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from polymarket_autopilot.live_trading import RiskLimits, get_daily_pnl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def send_telegram_alert(message: str) -> None:
    """Send an alert via Telegram bot.

    Requires:
        TELEGRAM_BOT_TOKEN
        TELEGRAM_CHAT_ID
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured, alert not sent")
        return

    try:
        import httpx

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        httpx.post(url, json={"chat_id": chat_id, "text": message}, timeout=10.0)
        logger.info("Telegram alert sent")
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")


def check_daily_loss(db_path: str, risk_limits: RiskLimits) -> None:
    """Check if daily loss is approaching or exceeding the limit."""
    daily_pnl = get_daily_pnl(db_path)

    # Alert thresholds
    warning_threshold = -risk_limits.max_daily_loss * 0.75  # 75% of limit
    critical_threshold = -risk_limits.max_daily_loss

    if daily_pnl <= critical_threshold:
        message = (
            f"🚨 CRITICAL: Daily loss limit EXCEEDED\n"
            f"Current P&L: ${daily_pnl:.2f}\n"
            f"Limit: ${-risk_limits.max_daily_loss:.2f}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"ACTION: Stop trading immediately"
        )
        logger.critical(message)
        send_telegram_alert(message)

    elif daily_pnl <= warning_threshold:
        message = (
            f"⚠️ WARNING: Daily loss approaching limit\n"
            f"Current P&L: ${daily_pnl:.2f}\n"
            f"Threshold: ${warning_threshold:.2f}\n"
            f"Limit: ${-risk_limits.max_daily_loss:.2f}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.warning(message)
        send_telegram_alert(message)

    else:
        logger.info(f"Daily P&L: ${daily_pnl:.2f} (within limits)")


def check_win_rate(db_path: str, min_win_rate: float = 0.40) -> None:
    """Check if win rate has dropped below threshold.

    Args:
        db_path: Path to SQLite database
        min_win_rate: Minimum acceptable win rate (default: 40%)
    """
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get last 20 closed trades
        cursor.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades
            WHERE exit_timestamp IS NOT NULL
            ORDER BY exit_timestamp DESC
            LIMIT 20
            """
        )
        result = cursor.fetchone()
        conn.close()

        if result and result[0] >= 10:  # Need at least 10 trades
            total, wins = result
            win_rate = wins / total

            if win_rate < min_win_rate:
                message = (
                    f"📉 Win rate dropped below {min_win_rate*100:.0f}%\n"
                    f"Current: {win_rate*100:.1f}% ({wins}/{total} wins)\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"ACTION: Review strategy performance"
                )
                logger.warning(message)
                send_telegram_alert(message)
            else:
                logger.info(f"Win rate: {win_rate*100:.1f}% ({wins}/{total})")

    except Exception as e:
        logger.error(f"Failed to check win rate: {e}")


def check_api_health() -> None:
    """Verify that Polymarket API is reachable."""
    try:
        import httpx

        response = httpx.get("https://clob.polymarket.com/ping", timeout=10.0)
        if response.status_code == 200:
            logger.info("API health check: OK")
        else:
            message = (
                f"⚠️ API health check failed\n"
                f"Status: {response.status_code}\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            logger.warning(message)
            send_telegram_alert(message)

    except Exception as e:
        message = (
            f"🚨 API connection FAILED\n"
            f"Error: {e}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"ACTION: Check network/API status"
        )
        logger.error(message)
        send_telegram_alert(message)


def monitor_loop(db_path: str, interval: int = 300) -> None:
    """Run monitoring checks in a loop.

    Args:
        db_path: Path to SQLite database
        interval: Seconds between checks (default: 300 = 5 minutes)
    """
    risk_limits = RiskLimits.from_env()

    logger.info(f"Starting live trading monitor (interval: {interval}s)")
    logger.info(f"Risk limits: {risk_limits}")

    while True:
        try:
            logger.info("Running monitoring checks...")

            check_daily_loss(db_path, risk_limits)
            check_win_rate(db_path)
            check_api_health()

            logger.info(f"Next check in {interval} seconds\n")
            time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
            break
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
            time.sleep(interval)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Live trading monitor")
    parser.add_argument(
        "--db",
        type=str,
        default="data/autopilot.db",
        help="Path to SQLite database (default: data/autopilot.db)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Check interval in seconds (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run checks once and exit (no loop)",
    )

    args = parser.parse_args()

    risk_limits = RiskLimits.from_env()

    if args.once:
        logger.info("Running one-shot monitoring check...")
        check_daily_loss(args.db, risk_limits)
        check_win_rate(args.db)
        check_api_health()
        logger.info("Check complete")
    else:
        monitor_loop(args.db, args.interval)


if __name__ == "__main__":
    main()
