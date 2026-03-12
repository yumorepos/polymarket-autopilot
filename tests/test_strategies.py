"""Tests for the strategy engine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from polymarket_autopilot.api import Market, Outcome
from polymarket_autopilot.db import Database, MarketSnapshot, STARTING_CAPITAL
from polymarket_autopilot.strategies import TailStrategy, get_strategy, strategy_catalog


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
    def test_no_signal_above_max_threshold(self, db: Database) -> None:
        """TAIL should reject high-probability events (not long-shots)."""
        strat = TailStrategy(db, max_yes_prob=0.20)
        market = _make_market(yes_price=0.70)  # Too high, not a long-shot
        assert strat.evaluate(market) is None

    def test_signal_in_range_no_history(self, db: Database) -> None:
        """With no snapshot history, volume/trend checks are skipped."""
        strat = TailStrategy(db, max_yes_prob=0.20, min_yes_prob=0.05)
        market = _make_market(yes_price=0.15)  # Long-shot in valid range
        signal = strat.evaluate(market)
        assert signal is not None
        assert signal.outcome == "YES"
        assert signal.entry_price == 0.15

    def test_take_profit_and_stop_loss(self, db: Database) -> None:
        """TAIL should use wide TP/SL for long-shot volatility.
        
        Note: For low-probability entries (<0.15), the _calc_tp/_calc_sl helpers
        use absolute spreads rather than percentages to ensure realistic targets.
        """
        strat = TailStrategy(db, max_yes_prob=0.20, tp_pct=1.00, sl_pct=0.50)
        market = _make_market(yes_price=0.15)
        signal = strat.evaluate(market)
        assert signal is not None
        # For entry at 0.15 (exactly at _LOW_CERTAINTY_THRESHOLD):
        # TP uses absolute spread: 0.15 + 0.04 = 0.19
        # SL uses absolute spread: 0.15 - 0.05 = 0.10
        assert signal.take_profit == 0.19
        assert signal.stop_loss == 0.10

    def test_position_sizing(self, db: Database) -> None:
        """TAIL should use smaller position sizing (2% default)."""
        strat = TailStrategy(db, max_yes_prob=0.20, max_position_pct=0.02)
        market = _make_market(yes_price=0.15)
        signal = strat.evaluate(market)
        assert signal is not None
        cost = signal.shares * signal.entry_price
        max_allowed = STARTING_CAPITAL * 0.02
        assert cost <= max_allowed + 0.01  # small float tolerance

    def test_no_signal_when_volume_below_avg(self, db: Database) -> None:
        """TAIL should skip markets with low volume even if price is in range."""
        strat = TailStrategy(db, max_yes_prob=0.20)
        # Seed snapshots with high volume and rising prices (within long-shot range)
        for i in range(5):
            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id="test-cond-001",
                    yes_price=0.10 + i * 0.01,
                    no_price=0.90 - i * 0.01,
                    volume=500_000.0,
                    recorded_at=datetime.utcnow(),
                )
            )
        # Current market has low volume
        market = _make_market(yes_price=0.15, volume=1_000.0)
        signal = strat.evaluate(market)
        assert signal is None

    def test_no_signal_when_price_trending_down(self, db: Database) -> None:
        """TAIL should skip markets where price is declining (no momentum)."""
        strat = TailStrategy(db, max_yes_prob=0.20)
        # Seed snapshots with prices higher than current (declining trend)
        for i in range(5):
            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id="test-cond-001",
                    yes_price=0.18,  # Higher than current
                    no_price=0.82,
                    volume=10_000.0,
                    recorded_at=datetime.utcnow(),
                )
            )
        # Current price lower than historical average (no upward momentum)
        market = _make_market(yes_price=0.10, volume=50_000.0)
        signal = strat.evaluate(market)
        assert signal is None

    def test_no_signal_when_position_already_open(self, db: Database) -> None:
        """TAIL should not open duplicate positions in same market."""
        from polymarket_autopilot.db import PaperTrade

        strat = TailStrategy(db, max_yes_prob=0.20)
        market = _make_market(yes_price=0.15)
        # Open a trade manually
        db.open_trade(
            PaperTrade(
                id=None,
                condition_id="test-cond-001",
                question="Will this test pass?",
                outcome="YES",
                strategy="TAIL",
                shares=10.0,
                entry_price=0.15,
                exit_price=None,
                take_profit=0.30,
                stop_loss=0.075,
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

    def test_strategy_catalog_contains_defaults(self) -> None:
        catalog = strategy_catalog()
        tail = next(item for item in catalog if item["name"] == "TAIL")
        assert "summary" in tail
        params = tail["parameters"]
        assert isinstance(params, dict)
        assert params["max_yes_prob"] == 0.2
