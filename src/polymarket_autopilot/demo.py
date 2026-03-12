"""Offline demo dataset loader for reproducible project walkthroughs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from polymarket_autopilot.db import Database, MarketSnapshot, PaperTrade

DEMO_DIR = Path(__file__).resolve().parents[2] / "demo"
SNAPSHOT_FILE = DEMO_DIR / "sample_snapshots.csv"
TRADES_FILE = DEMO_DIR / "sample_trades.json"


@dataclass(frozen=True)
class DemoLoadResult:
    snapshot_count: int
    trade_count: int
    closed_trade_count: int
    open_trade_count: int


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_demo_data(db: Database) -> DemoLoadResult:
    """Reset DB and load deterministic demo snapshots + trades."""
    db.init()
    db.reset()

    snapshots = _load_snapshots()
    for snapshot in snapshots:
        db.record_snapshot(snapshot)

    trades_raw = _load_trades()
    closed_count = 0
    open_count = 0
    for row in trades_raw:
        trade = PaperTrade(
            id=None,
            condition_id=str(row["condition_id"]),
            question=str(row["question"]),
            outcome=str(row["outcome"]),
            strategy=str(row["strategy"]),
            shares=float(row["shares"]),
            entry_price=float(row["entry_price"]),
            exit_price=None,
            take_profit=float(row["take_profit"]),
            stop_loss=float(row["stop_loss"]),
            status="open",
            pnl=None,
            opened_at=_parse_dt(str(row["opened_at"])),
            closed_at=None,
        )
        trade_id = db.open_trade(trade)

        status = str(row.get("status", "open"))
        if status != "open":
            exit_price = float(row["exit_price"])
            closed_at = _parse_dt(str(row["closed_at"]))
            db.close_trade(trade_id, exit_price, status, closed_at=closed_at)
            closed_count += 1
        else:
            open_count += 1

    return DemoLoadResult(
        snapshot_count=len(snapshots),
        trade_count=len(trades_raw),
        closed_trade_count=closed_count,
        open_trade_count=open_count,
    )


def _load_snapshots() -> list[MarketSnapshot]:
    with SNAPSHOT_FILE.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            MarketSnapshot(
                id=None,
                condition_id=str(row["condition_id"]),
                yes_price=float(row["yes_price"]),
                no_price=float(row["no_price"]),
                volume=float(row["volume"]),
                recorded_at=_parse_dt(str(row["recorded_at"])),
            )
            for row in reader
        ]


def _load_trades() -> list[dict[str, Any]]:
    with TRADES_FILE.open("r", encoding="utf-8") as f:
        parsed = json.load(f)
    if not isinstance(parsed, list):
        raise ValueError("Demo trades payload must be a list")
    return [row for row in parsed if isinstance(row, dict)]
