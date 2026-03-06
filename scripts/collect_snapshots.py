#!/usr/bin/env python3
"""Standalone snapshot collector for building historical data.

Fetches all active markets from Polymarket and records price snapshots
to the SQLite database. Run periodically to build a dataset for
backtesting and pricing efficiency analysis.

Usage:
    python scripts/collect_snapshots.py [--max-pages 5] [--db data/autopilot.db]

No API keys or credentials required — uses public Polymarket data only.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarket_autopilot.api import PolymarketClient
from polymarket_autopilot.db import Database, MarketSnapshot, DEFAULT_DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def collect(max_pages: int = 5, db_path: str = str(DEFAULT_DB_PATH)) -> None:
    """Fetch active markets and record snapshots.

    Args:
        max_pages: Maximum API pages to fetch.
        db_path: Path to the SQLite database.
    """
    db = Database(Path(db_path))
    db.init()

    snapshot_count = 0

    async with PolymarketClient() as client:
        logger.info("Fetching active markets (max %d pages)…", max_pages)
        markets = await client.get_all_active_markets(max_pages=max_pages)
        logger.info("Found %d active markets.", len(markets))

        for market in markets:
            if market.yes_price is None:
                continue

            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id=market.condition_id,
                    yes_price=market.yes_price or 0.0,
                    no_price=market.no_price or 0.0,
                    volume=market.volume,
                    recorded_at=datetime.utcnow(),
                )
            )
            snapshot_count += 1

    logger.info(
        "Collected %d snapshots from %d markets → %s",
        snapshot_count,
        len(markets),
        db_path,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Collect Polymarket snapshots")
    parser.add_argument("--max-pages", type=int, default=5, help="Max API pages")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="DB path")
    args = parser.parse_args()

    asyncio.run(collect(max_pages=args.max_pages, db_path=args.db))
