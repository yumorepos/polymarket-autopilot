"""Tests for the portfolio tracker."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from polymarket_autopilot.db import Database, PaperTrade, STARTING_CAPITAL
from polymarket_autopilot.portfolio import PortfolioTracker


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "port_test.db")
    d.init()
    return d


def _open_trade(db: Database, condition_id: str = "cond-1", price: float = 0.65) -> int:
    return db.open_trade(
        PaperTrade(
            id=None,
            condition_id=condition_id,
            question="Test question?",
            outcome="YES",
            strategy="TAIL",
            shares=100.0,
            entry_price=price,
            exit_price=None,
            take_profit=price * 1.15,
            stop_loss=price * 0.90,
            status="open",
            pnl=None,
            opened_at=datetime.utcnow(),
            closed_at=None,
        )
    )


class TestPortfolioTracker:
    def test_empty_report(self, db: Database) -> None:
        tracker = PortfolioTracker(db)
        r = tracker.get_report()
        assert r.cash == STARTING_CAPITAL
        assert r.open_positions == 0
        assert r.closed_trades == 0
        assert r.win_rate == 0.0
        assert r.total_value == STARTING_CAPITAL

    def test_report_with_open_trade(self, db: Database) -> None:
        _open_trade(db, "cond-A", price=0.65)
        tracker = PortfolioTracker(db)
        r = tracker.get_report()
        assert r.open_positions == 1
        assert r.open_cost_basis == 100.0 * 0.65
        assert abs(r.total_value - STARTING_CAPITAL) < 0.01  # unchanged

    def test_win_rate_after_closed_trades(self, db: Database) -> None:
        # One winning trade
        tid1 = _open_trade(db, "cond-W", price=0.60)
        db.close_trade(tid1, 0.69, "closed_tp")  # profit

        # One losing trade
        tid2 = _open_trade(db, "cond-L", price=0.60)
        db.close_trade(tid2, 0.54, "closed_sl")  # loss

        tracker = PortfolioTracker(db)
        r = tracker.get_report()
        assert r.closed_trades == 2
        assert abs(r.win_rate - 0.5) < 0.01

    def test_net_pnl(self, db: Database) -> None:
        tid = _open_trade(db, "cond-pnl", price=0.60)
        db.close_trade(tid, 0.69, "closed_tp")
        tracker = PortfolioTracker(db)
        expected_pnl = (0.69 - 0.60) * 100.0
        assert abs(tracker.net_pnl() - expected_pnl) < 0.01

    def test_strategy_stats(self, db: Database) -> None:
        tid = _open_trade(db, "cond-s1", price=0.65)
        db.close_trade(tid, 0.75, "closed_tp")

        tracker = PortfolioTracker(db)
        r = tracker.get_report()
        assert "TAIL" in r.strategy_stats
        s = r.strategy_stats["TAIL"]
        assert s.total_trades == 1
        assert s.wins == 1
        assert s.win_rate == 1.0

    def test_total_return_pct(self, db: Database) -> None:
        """After a winning trade, total return % should be positive."""
        tid = _open_trade(db, "cond-ret", price=0.60)
        db.close_trade(tid, 0.69, "closed_tp")

        tracker = PortfolioTracker(db)
        r = tracker.get_report()
        assert r.total_return_pct > 0

    def test_open_positions_summary(self, db: Database) -> None:
        _open_trade(db, "cond-sum", price=0.65)
        tracker = PortfolioTracker(db)
        summary = tracker.get_open_positions_summary()
        assert len(summary) == 1
        pos = summary[0]
        assert pos["outcome"] == "YES"
        assert pos["strategy"] == "TAIL"
