"""Tests for the backtesting engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polymarket_autopilot.backtest import (
    Backtester,
    BacktestResult,
    format_backtest_result,
)
from polymarket_autopilot.db import Database, MarketSnapshot


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    """Return a freshly initialised Database in a temp directory."""
    d = Database(tmp_path / "backtest_test.db")
    d.init()
    return d


def _seed_snapshots(
    db: Database,
    condition_id: str = "cond-001",
    yes_prices: list[float] | None = None,
    volume: float = 100_000.0,
    start: datetime | None = None,
) -> None:
    if yes_prices is None:
        yes_prices = [0.55, 0.58, 0.61, 0.65, 0.68, 0.70, 0.72]
    if start is None:
        start = datetime.utcnow() - timedelta(days=3)

    for i, price in enumerate(yes_prices):
        ts = start + timedelta(hours=i)
        db.record_snapshot(
            MarketSnapshot(
                id=None,
                condition_id=condition_id,
                yes_price=price,
                no_price=round(1.0 - price, 4),
                volume=volume,
                recorded_at=ts,
            )
        )


class TestBacktester:
    def test_no_data_returns_empty_result(self, db: Database) -> None:
        bt = Backtester(db, strategy_name="TAIL", starting_capital=10_000.0)
        result = bt.run(days=7)
        assert result.total_trades == 0
        assert result.total_return_pct == pytest.approx(0.0)

    def test_result_has_correct_strategy_name(self, db: Database) -> None:
        bt = Backtester(db, strategy_name="MOMENTUM")
        result = bt.run(days=7)
        assert result.strategy_name == "MOMENTUM"

    def test_simulation_with_snapshot_data(self, db: Database) -> None:
        _seed_snapshots(
            db,
            condition_id="market-A",
            yes_prices=[0.62, 0.65, 0.68, 0.71, 0.74, 0.77],
            volume=500_000.0,
        )
        bt = Backtester(db, strategy_name="TAIL", starting_capital=10_000.0)
        result = bt.run(days=7)
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "TAIL"
        assert result.ending_capital >= 0

    def test_win_rate_bounds(self, db: Database) -> None:
        _seed_snapshots(db)
        bt = Backtester(db, strategy_name="TAIL")
        result = bt.run(days=7)
        assert 0.0 <= result.win_rate <= 1.0

    def test_max_drawdown_non_negative(self, db: Database) -> None:
        _seed_snapshots(db)
        bt = Backtester(db, strategy_name="TAIL")
        result = bt.run(days=7)
        assert result.max_drawdown_pct >= 0.0

    def test_format_backtest_result(self, db: Database) -> None:
        bt = Backtester(db, strategy_name="TAIL")
        result = bt.run(days=1)
        output = format_backtest_result(result)
        assert "BACKTEST REPORT" in output
        assert "TAIL" in output


    def test_run_with_overrides_applies_parameters(self, db: Database) -> None:
        _seed_snapshots(db, yes_prices=[0.12, 0.13, 0.14, 0.15, 0.16], volume=200_000.0)
        bt = Backtester(db, strategy_name="TAIL")
        result = bt.run_with_overrides(days=7, overrides={"max_yes_prob": 0.18})
        assert isinstance(result, BacktestResult)

    def test_multiple_markets(self, db: Database) -> None:
        for cid in ["market-A", "market-B", "market-C"]:
            _seed_snapshots(
                db,
                condition_id=cid,
                yes_prices=[0.65, 0.68, 0.72, 0.76],
                volume=200_000.0,
            )
        bt = Backtester(db, strategy_name="TAIL", starting_capital=10_000.0)
        result = bt.run(days=7)
        assert isinstance(result, BacktestResult)
