"""Tests for the backtesting engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polymarket_autopilot.backtest import (
    Backtester,
    BacktestResult,
    _BacktestPortfolio,
    format_backtest_result,
)
from polymarket_autopilot.db import Database, MarketSnapshot
from polymarket_autopilot.strategies import TradeSignal


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



    def test_trade_duration_uses_snapshot_timestamps(self, db: Database) -> None:
        portfolio = _BacktestPortfolio(10_000.0)
        t0 = datetime.utcnow() - timedelta(hours=3)
        t1 = t0 + timedelta(hours=2)

        portfolio.record_snapshot(
            MarketSnapshot(
                id=None,
                condition_id="market-duration",
                yes_price=0.65,
                no_price=0.35,
                volume=500_000.0,
                recorded_at=t0,
            )
        )
        trade = portfolio.open_position(
            TradeSignal(
                condition_id="market-duration",
                question="Q",
                outcome="Yes",
                entry_price=0.65,
                shares=10.0,
                take_profit=0.75,
                stop_loss=0.55,
                strategy="TAIL",
                reason="test",
            )
        )

        portfolio.record_snapshot(
            MarketSnapshot(
                id=None,
                condition_id="market-duration",
                yes_price=0.70,
                no_price=0.30,
                volume=500_000.0,
                recorded_at=t1,
            )
        )
        closed = portfolio.close_position("market-duration", exit_price=0.70, status="closed_tp")

        assert trade.opened_at == t0
        assert closed is not None
        assert closed.closed_at == t1

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
