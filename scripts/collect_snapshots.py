#!/usr/bin/env python3
"""Standalone script to collect and store market snapshots.

Fetches all active Polymarket markets and records a price/volume snapshot
for each one into the SQLite database.  Designed to be run on a schedule
(e.g. via cron or a systemd timer) to build up historical data for
backtesting and pricing efficiency analysis.

Usage
-----
::

    python scripts/collect_snapshots.py

Environment variables (loaded from ``.env`` automatically):
    AUTOPILOT_DB  — Path to the SQLite DB (default: ``data/autopilot.db``)
    LOG_LEVEL     — Logging level (default: ``INFO``)

Exit codes:
    0 — Success
    1 — Fatal error (network failure, DB unavailable, etc.)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Allow running from the repo root without installing the package
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root / "src") not in sys.path:
    sys.path.insert(0, str(_repo_root / "src"))

from dotenv import load_dotenv

load_dotenv()

from polymarket_autopilot.api import PolymarketClient
from polymarket_autopilot.db import Database, MarketSnapshot, DEFAULT_DB_PATH

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _log_level_name, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("collect_snapshots")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = Path(os.getenv("AUTOPILOT_DB", str(DEFAULT_DB_PATH)))

# Maximum number of API pages to fetch per run (100 markets per page)
MAX_PAGES = int(os.getenv("COLLECT_MAX_PAGES", "10"))


# ---------------------------------------------------------------------------
# Main coroutine
# ---------------------------------------------------------------------------


async def collect() -> None:
    """Fetch active markets and record snapshots for all of them.

    Raises:
        Exception: Propagated on unrecoverable API or DB errors.
    """
    db = Database(DB_PATH)
    db.init()

    logger.info("Starting snapshot collection (db=%s, max_pages=%d)", DB_PATH, MAX_PAGES)

    async with PolymarketClient() as client:
        logger.info("Fetching active markets from Polymarket API…")
        markets = await client.get_all_active_markets(max_pages=MAX_PAGES)
        logger.info("Fetched %d active markets.", len(markets))

        if not markets:
            logger.warning("No active markets returned by the API. Exiting.")
            return

        recorded = 0
        skipped = 0
        now = datetime.utcnow()

        for market in markets:
            yes_price = market.yes_price
            no_price = market.no_price

            # Skip markets without price data
            if yes_price is None:
                skipped += 1
                logger.debug("Skipping %s — no YES price.", market.condition_id[:12])
                continue

            snapshot = MarketSnapshot(
                id=None,
                condition_id=market.condition_id,
                yes_price=yes_price,
                no_price=no_price if no_price is not None else round(1.0 - yes_price, 6),
                volume=market.volume,
                recorded_at=now,
            )
            db.record_snapshot(snapshot)
            recorded += 1

        logger.info(
            "Snapshot collection complete: %d recorded, %d skipped (no price data).",
            recorded,
            skipped,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the snapshot collector.

    Returns:
        Exit code (0 on success, 1 on error).
    """
    try:
        asyncio.run(collect())
        return 0
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        return 0
    except Exception:
        logger.exception("Fatal error during snapshot collection.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
