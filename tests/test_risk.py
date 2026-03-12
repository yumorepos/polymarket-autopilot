from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from polymarket_autopilot.db import Database, PaperTrade
from polymarket_autopilot.risk import RiskConfig, check_entry_risk
from polymarket_autopilot.strategies import TradeSignal


def _signal(cost: float = 200.0, strategy: str = "TAIL", condition: str = "m1") -> TradeSignal:
    entry = 0.5
    shares = cost / entry
    return TradeSignal(
        condition_id=condition,
        question="Q",
        outcome="YES",
        entry_price=entry,
        shares=shares,
        take_profit=0.6,
        stop_loss=0.45,
        strategy=strategy,
        reason="test",
    )


def _trade(condition: str, strategy: str, cost: float = 200.0) -> PaperTrade:
    entry = 0.5
    shares = cost / entry
    return PaperTrade(
        id=None,
        condition_id=condition,
        question="Q",
        outcome="YES",
        strategy=strategy,
        shares=shares,
        entry_price=entry,
        exit_price=None,
        take_profit=0.6,
        stop_loss=0.45,
        status="open",
        pnl=None,
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )


def test_risk_blocks_when_market_exposure_exceeded(tmp_path: Path) -> None:
    db = Database(tmp_path / "risk.db")
    db.init()
    db.open_trade(_trade("m1", "TAIL", 700.0))

    decision = check_entry_risk(
        db,
        _signal(cost=200.0, condition="m1"),
        RiskConfig(max_exposure_per_market=800.0),
    )
    assert not decision.allowed
    assert decision.reason == "market_exposure_limit"


def test_risk_allows_reasonable_trade(tmp_path: Path) -> None:
    db = Database(tmp_path / "risk2.db")
    db.init()

    decision = check_entry_risk(db, _signal(cost=120.0), RiskConfig())
    assert decision.allowed
    assert decision.reason == "ok"
