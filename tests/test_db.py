"""Tests for the database layer."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from polymarket_autopilot.db import (
    Database,
    MarketSnapshot,
    PaperTrade,
    STARTING_CAPITAL,
)


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    """Return an initialised in-memory-style temp database."""
    d = Database(tmp_path / "test.db")
    d.init()
    return d


class TestPortfolio:
    def test_starting_cash(self, db: Database) -> None:
        assert db.get_cash() == STARTING_CAPITAL

    def test_update_cash_add(self, db: Database) -> None:
        new = db.update_cash(500.0)
        assert new == STARTING_CAPITAL + 500.0

    def test_update_cash_subtract(self, db: Database) -> None:
        new = db.update_cash(-200.0)
        assert new == STARTING_CAPITAL - 200.0

    def test_portfolio_value_matches_cash_when_no_trades(self, db: Database) -> None:
        assert db.get_portfolio_value() == db.get_cash()


class TestTrades:
    def _make_trade(self, condition_id: str = "cond-1") -> PaperTrade:
        return PaperTrade(
            id=None,
            condition_id=condition_id,
            question="Will X happen?",
            outcome="YES",
            strategy="TAIL",
            shares=100.0,
            entry_price=0.65,
            exit_price=None,
            take_profit=0.7475,
            stop_loss=0.585,
            status="open",
            pnl=None,
            opened_at=datetime.utcnow(),
            closed_at=None,
        )

    def test_open_trade_deducts_cash(self, db: Database) -> None:
        trade = self._make_trade()
        db.open_trade(trade)
        expected_cash = STARTING_CAPITAL - (trade.shares * trade.entry_price)
        assert abs(db.get_cash() - expected_cash) < 0.01

    def test_open_trade_returns_id(self, db: Database) -> None:
        trade_id = db.open_trade(self._make_trade())
        assert trade_id == 1

    def test_get_open_trades(self, db: Database) -> None:
        db.open_trade(self._make_trade("a"))
        db.open_trade(self._make_trade("b"))
        open_trades = db.get_open_trades()
        assert len(open_trades) == 2

    def test_close_trade_credits_cash(self, db: Database) -> None:
        trade = self._make_trade()
        trade_id = db.open_trade(trade)
        cash_after_open = db.get_cash()

        exit_price = 0.75
        closed = db.close_trade(trade_id, exit_price, "closed_tp")
        assert closed is not None
        assert closed.exit_price == exit_price
        assert closed.status == "closed_tp"

        expected_proceeds = trade.shares * exit_price
        assert abs(db.get_cash() - (cash_after_open + expected_proceeds)) < 0.01

    def test_close_trade_pnl(self, db: Database) -> None:
        trade = self._make_trade()
        trade_id = db.open_trade(trade)
        closed = db.close_trade(trade_id, 0.75, "closed_tp")
        assert closed is not None
        expected_pnl = (0.75 - 0.65) * 100.0
        assert abs((closed.pnl or 0.0) - expected_pnl) < 0.01

    def test_close_nonexistent_trade_returns_none(self, db: Database) -> None:
        result = db.close_trade(999, 0.5, "closed_tp")
        assert result is None

    def test_get_trade_by_condition(self, db: Database) -> None:
        db.open_trade(self._make_trade("cond-abc"))
        trade = db.get_trade_by_condition("cond-abc")
        assert trade is not None
        assert trade.condition_id == "cond-abc"

    def test_no_duplicate_open_for_same_condition(self, db: Database) -> None:
        """Only one open trade per condition is returned."""
        db.open_trade(self._make_trade("cond-dup"))
        db.open_trade(self._make_trade("cond-dup"))
        # get_trade_by_condition returns at most one
        trade = db.get_trade_by_condition("cond-dup")
        assert trade is not None


class TestMarketSnapshots:
    def test_record_and_retrieve_snapshot(self, db: Database) -> None:
        snap = MarketSnapshot(
            id=None,
            condition_id="cond-snap",
            yes_price=0.62,
            no_price=0.38,
            volume=50000.0,
            recorded_at=datetime.utcnow(),
        )
        db.record_snapshot(snap)
        snapshots = db.get_recent_snapshots("cond-snap", n=5)
        assert len(snapshots) == 1
        assert abs(snapshots[0].yes_price - 0.62) < 0.0001

    def test_returns_n_most_recent(self, db: Database) -> None:
        for i in range(10):
            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id="cond-many",
                    yes_price=0.5 + i * 0.01,
                    no_price=0.5 - i * 0.01,
                    volume=1000.0 * i,
                    recorded_at=datetime.utcnow(),
                )
            )
        snapshots = db.get_recent_snapshots("cond-many", n=5)
        assert len(snapshots) == 5

    def test_snapshots_returned_chronologically(self, db: Database) -> None:
        prices = [0.60, 0.62, 0.65]
        for p in prices:
            db.record_snapshot(
                MarketSnapshot(
                    id=None,
                    condition_id="cond-order",
                    yes_price=p,
                    no_price=1 - p,
                    volume=1000.0,
                    recorded_at=datetime.utcnow(),
                )
            )
        snaps = db.get_recent_snapshots("cond-order", n=10)
        retrieved_prices = [s.yes_price for s in snaps]
        assert retrieved_prices == prices
