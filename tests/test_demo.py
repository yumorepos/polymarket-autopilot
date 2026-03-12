"""Tests for deterministic offline demo dataset loading and analytics compare."""

from __future__ import annotations

from pathlib import Path

from polymarket_autopilot.backtest import compare_strategies
from polymarket_autopilot.db import Database
from polymarket_autopilot.demo import load_demo_data


def test_demo_loader_populates_snapshots_and_trades(tmp_path: Path) -> None:
    db = Database(tmp_path / "demo.db")
    result = load_demo_data(db)

    assert result.snapshot_count > 0
    assert result.trade_count == result.closed_trade_count + result.open_trade_count
    assert len(db.get_open_trades()) == result.open_trade_count
    assert (
        len(db.get_trade_history(limit=100, statuses=["closed_tp", "closed_sl"]))
        == result.closed_trade_count
    )


def test_strategy_comparison_returns_ranked_rows(tmp_path: Path) -> None:
    db = Database(tmp_path / "demo.db")
    load_demo_data(db)
    rows = compare_strategies(db, days=30, capital=10_000)

    assert rows
    assert rows[0].total_return_pct >= rows[-1].total_return_pct
    assert all(row.strategy for row in rows)
