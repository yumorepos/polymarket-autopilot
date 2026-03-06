"""Tests for the strategy engine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from polymarket_autopilot.api import Market, Outcome
from polymarket_autopilot.db import Database, MarketSnapshot, STARTING_CAPITAL
from polymarket_autopilot.strategies import TailStrategy, get_strategy


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "strat_test.db")
    d.init()
    return d


def _make_market(yes_price: float, volume: float = 100_000.0) -> Market:
    return Market(
        condition_id="test-cond-001",
        question="Will this test pass?",
        outcomes=[
            Outcome(name="YES", price=yes_price),
            Outcome(name="NO", price=round(1 - yes_price, 4)),
        ],
        volume=volume,
        end_date=None,
        active=True,
        closed=False,
    )


class TestTailStrategy:
    def test_no_signal_below_threshold(self, db: Database) -> None:
        strat = TailStrategy(db, min_yes_prob=0.60)
        market = _make_market(yes_price=0.55)
        assert strat.evaluate(market) is None

    def test_signal_above_threshold_no_history(self, db: Database) -> None:
        """With no snapshot history, volume/trend checks are skipped."""
        strat = TailStrategy(db, min_yes_prob=0.60)
        market = _make_market(yes_price=0.70)
        signal = strat.evaluate(market)
        assert signal is not None
        assert signal.outcome == "YES"
        assert signal.entry_price == 0.70

    def test_take_profit_and_stop_loss(self, db: Database) -> None:
        strat = TailStrategy(db, min_yes_prob=0.60, tp_pct=0.15, sl_pct=0.10)
        market = _make_market(yes_price=0.70)
        signal = strat.evaluate(market)
        assert signal is not None
        assert abs(signal.take_profit - 0.70 * 1.15) < 1e-6
        assert abs(signal.stop_loss - 0.70 * 0.90) < 1e-6

    def test_position_sizing(self, db: Database) -> None:
        strat = TailStrategy(db, min_yes_prob=0.60, max_position_pct=0.05)
        market = _make_market(yes_price=0.70)
        signal = strat.evaluate(market)
        assert signal is not None
        cost = signal.shares * signal.entry_price
        max_allowed = STARTING_CAPITAL * 0.05
        assert cost <= max_allowed + 0.01  # small float tolerance

    def test_no_signal_when_volume_below_avg(self, db: Database) -> None:
        strat = TailStrategy(db, min_yes_prob=0.60)
        # Seed snapshots with high volume and rising prices
        for i in range(5):
            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id="test-cond-001",
                    yes_price=0.75 + i * 0.01,
                    no_price=0.25 - i * 0.01,
                    volume=500_000.0,
                    recorded_at=datetime.utcnow(),
                )
            )
        # Current market has low volume
        market = _make_market(yes_price=0.80, volume=1_000.0)
        signal = strat.evaluate(market)
        assert signal is None

    def test_no_signal_when_price_trending_down(self, db: Database) -> None:
        strat = TailStrategy(db, min_yes_prob=0.60)
        # Seed snapshots with prices higher than current
        for i in range(5):
            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id="test-cond-001",
                    yes_price=0.90,
                    no_price=0.10,
                    volume=10_000.0,
                    recorded_at=datetime.utcnow(),
                )
            )
        # Current price lower than historical average
        market = _make_market(yes_price=0.65, volume=50_000.0)
        signal = strat.evaluate(market)
        assert signal is None

    def test_no_signal_when_position_already_open(self, db: Database) -> None:
        from polymarket_autopilot.db import PaperTrade

        strat = TailStrategy(db, min_yes_prob=0.60)
        market = _make_market(yes_price=0.70)
        # Open a trade manually
        db.open_trade(
            PaperTrade(
                id=None,
                condition_id="test-cond-001",
                question="Will this test pass?",
                outcome="YES",
                strategy="TAIL",
                shares=10.0,
                entry_price=0.70,
                exit_price=None,
                take_profit=0.805,
                stop_loss=0.63,
                status="open",
                pnl=None,
                opened_at=datetime.utcnow(),
                closed_at=None,
            )
        )
        signal = strat.evaluate(market)
        assert signal is None

    def test_get_strategy_registry(self, db: Database) -> None:
        strat = get_strategy("TAIL", db)
        assert isinstance(strat, TailStrategy)

    def test_get_strategy_invalid_raises(self, db: Database) -> None:
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_strategy("UNKNOWN", db)
