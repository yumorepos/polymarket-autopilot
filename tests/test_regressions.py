"""High-signal regression tests for recent hardening changes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import polymarket_autopilot.backtest as backtest_module
from polymarket_autopilot.api import PolymarketClient
from polymarket_autopilot.backtest import Backtester, _BacktestPortfolio
from polymarket_autopilot.db import Database, MarketSnapshot
from polymarket_autopilot.strategies import ExitSignal, TradeSignal, list_strategies


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "regression.db")
    db.init()
    return db


def test_get_markets_skips_malformed_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PolymarketClient()

    async def fake_get(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "condition_id": "valid",
                "question": "Valid market",
                "tokens": [{"outcome": "YES", "price": 0.55}],
                "volume": 10,
            },
            {
                "condition_id": "bad",
                "question": "Malformed market",
                "tokens": [],
            },
        ]

    monkeypatch.setattr(client, "_get", fake_get)
    markets, cursor = asyncio.run(client.get_markets(limit=2))

    assert cursor == "2"
    assert [m.condition_id for m in markets] == ["valid"]


def test_backtest_portfolio_ids_and_tp_sl_are_deterministic() -> None:
    portfolio = _BacktestPortfolio(starting_capital=1_000)
    signal = TradeSignal(
        condition_id="cond-1",
        question="Question",
        outcome="YES",
        entry_price=0.5,
        shares=100,
        take_profit=0.62,
        stop_loss=0.41,
        strategy="TEST",
        reason="unit",
    )

    opened = portfolio.open_position(signal, opened_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    open_trade = portfolio.get_trade_by_condition("cond-1")

    assert opened.trade_id == 1
    assert open_trade is not None
    assert open_trade.id == 1
    assert open_trade.take_profit == pytest.approx(0.62)
    assert open_trade.stop_loss == pytest.approx(0.41)


class _DeterministicStrategy:
    name = "TEST"

    def __init__(self, db: object) -> None:
        self.db = db
        self.has_opened = False

    def evaluate(self, market: object) -> TradeSignal | None:
        trade = self.db.get_trade_by_condition("cond-1")
        if trade is not None or self.has_opened:
            return None
        self.has_opened = True

        return TradeSignal(
            condition_id="cond-1",
            question="Q",
            outcome="YES",
            entry_price=0.5,
            shares=20,
            take_profit=0.7,
            stop_loss=0.4,
            strategy=self.name,
            reason="entry",
        )

    def check_exits(self, _markets: object) -> list[ExitSignal]:
        open_trades = self.db.get_open_trades()
        if not open_trades:
            return []
        return [
            ExitSignal(
                trade_id=open_trades[0].id,
                exit_price=0.7,
                status="closed_tp",
                reason="tp",
            )
        ]


def test_backtest_exit_matching_and_timestamp_propagation(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    for ts, price in [(start, 0.5), (start + timedelta(minutes=5), 0.7)]:
        db.record_snapshot(
            MarketSnapshot(
                id=None,
                condition_id="cond-1",
                yes_price=price,
                no_price=1 - price,
                volume=100_000,
                recorded_at=ts,
            )
        )

    monkeypatch.setattr(
        backtest_module, "get_strategy", lambda _name, portfolio: _DeterministicStrategy(portfolio)
    )

    result = Backtester(db, strategy_name="TAIL", starting_capital=1_000).run(days=365)

    assert result.total_trades == 1
    assert result.winning_trades == 1
    assert result.open_trades == 0
    assert result.trades[0].opened_at == start
    assert result.trades[0].closed_at == start + timedelta(minutes=5)


def test_strategy_metadata_registry_is_complete() -> None:
    names = [meta.name for meta in list_strategies()]
    assert "TAIL" in names
    assert "MOMENTUM" in names
    assert len(names) >= 10


def test_portfolio_summary_calculation(db: Database) -> None:
    summary = db.get_portfolio_summary()
    assert summary["cash"] == pytest.approx(10_000.0)
    assert summary["open_cost"] == pytest.approx(0.0)
    assert summary["total_value"] == pytest.approx(10_000.0)
