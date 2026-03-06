"""Tests for the backtesting engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polymarket_autopilot.backtest import (
    Backtester,
    BacktestResult,
    _max_drawdown,
    _sharpe_ratio,
    _snapshots_to_markets,
    _SimDatabase,
)
from polymarket_autopilot.db import Database, MarketSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
) -> list[datetime]:
    """Insert a series of snapshots spaced 1 hour apart.

    Args:
        db: Database to write to.
        condition_id: Market condition ID for all snapshots.
        yes_prices: YES prices to record; defaults to a rising series.
        volume: Volume for all snapshots.
        start: First snapshot timestamp (defaults to 8 days ago).

    Returns:
        List of timestamps used.
    """
    if yes_prices is None:
        yes_prices = [0.55, 0.58, 0.61, 0.65, 0.68, 0.70, 0.72]
    if start is None:
        start = datetime.utcnow() - timedelta(days=8)

    timestamps: list[datetime] = []
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
        timestamps.append(ts)
    return timestamps


# ---------------------------------------------------------------------------
# Math helper tests
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_flat_series_returns_zero(self) -> None:
        assert _max_drawdown([1000.0, 1000.0, 1000.0]) == 0.0

    def test_always_increasing_returns_zero(self) -> None:
        assert _max_drawdown([1000.0, 1100.0, 1200.0]) == 0.0

    def test_single_dip(self) -> None:
        # Peak 1200, dips to 900: drawdown = (1200-900)/1200 = 0.25
        result = _max_drawdown([1000.0, 1200.0, 900.0, 1100.0])
        assert abs(result - 0.25) < 1e-9

    def test_empty_returns_zero(self) -> None:
        assert _max_drawdown([]) == 0.0

    def test_single_value_returns_zero(self) -> None:
        assert _max_drawdown([1000.0]) == 0.0


class TestSharpeRatio:
    def test_no_returns_returns_zero(self) -> None:
        assert _sharpe_ratio([]) == 0.0

    def test_too_few_points_returns_zero(self) -> None:
        assert _sharpe_ratio([1000.0]) == 0.0
        assert _sharpe_ratio([1000.0, 1100.0]) == 0.0

    def test_zero_variance_returns_zero(self) -> None:
        # All returns are identical → std=0 → sharpe=0
        assert _sharpe_ratio([1000.0, 1000.0, 1000.0, 1000.0]) == 0.0

    def test_positive_trend_returns_positive(self) -> None:
        series = [1000.0 + i * 50 for i in range(20)]
        sharpe = _sharpe_ratio(series)
        assert sharpe > 0


# ---------------------------------------------------------------------------
# Market reconstruction tests
# ---------------------------------------------------------------------------


class TestSnapshotsToMarkets:
    def test_converts_snapshot_to_market(self) -> None:
        snap = MarketSnapshot(
            id=1,
            condition_id="test-001",
            yes_price=0.70,
            no_price=0.30,
            volume=50_000.0,
            recorded_at=datetime.utcnow(),
        )
        markets = _snapshots_to_markets([snap])
        assert len(markets) == 1
        m = markets[0]
        assert m.condition_id == "test-001"
        assert m.yes_price == pytest.approx(0.70)
        assert m.no_price == pytest.approx(0.30)
        assert m.volume == 50_000.0

    def test_empty_list_returns_empty(self) -> None:
        assert _snapshots_to_markets([]) == []


# ---------------------------------------------------------------------------
# SimDatabase tests
# ---------------------------------------------------------------------------


class TestSimDatabase:
    def test_get_cash_and_portfolio_value(self, db: Database) -> None:
        sim = _SimDatabase(db)
        sim.set_state(cash=5_000.0, portfolio_value=7_500.0)
        assert sim.get_cash() == pytest.approx(5_000.0)
        assert sim.get_portfolio_value() == pytest.approx(7_500.0)

    def test_open_position_tracking(self, db: Database) -> None:
        sim = _SimDatabase(db)
        assert sim.get_trade_by_condition("cond-001") is None
        sim.add_open_position("cond-001")
        assert sim.get_trade_by_condition("cond-001") is not None
        sim.remove_open_position("cond-001")
        assert sim.get_trade_by_condition("cond-001") is None

    def test_get_recent_snapshots_delegates(self, db: Database) -> None:
        _seed_snapshots(db, condition_id="cond-001")
        sim = _SimDatabase(db)
        snaps = sim.get_recent_snapshots("cond-001", n=5)
        assert len(snaps) == 5


# ---------------------------------------------------------------------------
# Backtester simulation tests
# ---------------------------------------------------------------------------


class TestBacktester:
    def test_no_data_returns_empty_result(self, db: Database) -> None:
        bt = Backtester(db, strategy_name="TAIL", days=7, starting_capital=10_000.0)
        result = bt.run()
        assert result.trades_count == 0
        assert result.total_return_pct == pytest.approx(0.0)
        assert result.snapshots_processed == 0

    def test_result_has_correct_strategy_name(self, db: Database) -> None:
        bt = Backtester(db, strategy_name="MOMENTUM", days=7)
        result = bt.run()
        assert result.strategy_name == "MOMENTUM"

    def test_result_period_format(self, db: Database) -> None:
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 7)
        bt = Backtester(db, strategy_name="TAIL", start_dt=start, end_dt=end)
        result = bt.run()
        assert "2024-01-01" in result.period
        assert "2024-01-07" in result.period

    def test_simulation_with_snapshot_data(self, db: Database) -> None:
        """Full simulation smoke test: seed rising prices, expect some signals."""
        start = datetime.utcnow() - timedelta(days=3)
        _seed_snapshots(
            db,
            condition_id="market-A",
            yes_prices=[0.62, 0.65, 0.68, 0.71, 0.74, 0.77],
            volume=500_000.0,
            start=start,
        )

        bt = Backtester(
            db,
            strategy_name="TAIL",
            start_dt=start - timedelta(hours=1),
            end_dt=datetime.utcnow(),
            starting_capital=10_000.0,
        )
        result = bt.run()

        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "TAIL"
        assert result.snapshots_processed > 0
        # Capital should be non-negative
        assert result.ending_capital >= 0
        # Return should be finite
        assert abs(result.total_return_pct) < 1_000  # sanity bound

    def test_multiple_markets(self, db: Database) -> None:
        """Backtester handles multiple condition IDs in the same tick."""
        start = datetime.utcnow() - timedelta(hours=12)
        for cid in ["market-A", "market-B", "market-C"]:
            _seed_snapshots(
                db,
                condition_id=cid,
                yes_prices=[0.65, 0.68, 0.72, 0.76],
                volume=200_000.0,
                start=start,
            )

        bt = Backtester(
            db,
            strategy_name="TAIL",
            start_dt=start - timedelta(minutes=30),
            end_dt=datetime.utcnow(),
            starting_capital=10_000.0,
        )
        result = bt.run()
        assert result.snapshots_processed > 0

    def test_win_rate_bounds(self, db: Database) -> None:
        """Win rate must always be in [0, 1]."""
        start = datetime.utcnow() - timedelta(hours=6)
        _seed_snapshots(db, start=start)

        bt = Backtester(
            db,
            strategy_name="TAIL",
            start_dt=start - timedelta(minutes=30),
            end_dt=datetime.utcnow(),
        )
        result = bt.run()
        assert 0.0 <= result.win_rate <= 1.0

    def test_max_drawdown_bounds(self, db: Database) -> None:
        """Max drawdown must be >= 0."""
        start = datetime.utcnow() - timedelta(hours=6)
        _seed_snapshots(db, start=start)

        bt = Backtester(
            db,
            strategy_name="TAIL",
            start_dt=start - timedelta(minutes=30),
            end_dt=datetime.utcnow(),
        )
        result = bt.run()
        assert result.max_drawdown >= 0.0

    def test_print_summary_runs_without_error(
        self, db: Database, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bt = Backtester(db, strategy_name="TAIL", days=1)
        result = bt.run()
        result.print_summary()
        captured = capsys.readouterr()
        assert "BACKTEST RESULTS" in captured.out
        assert "TAIL" in captured.out
