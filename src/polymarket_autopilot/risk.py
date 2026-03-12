"""Risk controls for entry validation and portfolio guardrails."""

from __future__ import annotations

from dataclasses import dataclass

from polymarket_autopilot.db import Database
from polymarket_autopilot.strategies import TradeSignal


@dataclass(frozen=True)
class RiskConfig:
    """Portfolio-level risk controls."""

    max_positions: int = 12
    max_exposure_per_market: float = 800.0
    max_exposure_per_strategy: float = 2500.0
    max_trade_cost: float = 400.0
    min_cash_buffer: float = 1000.0


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


def check_entry_risk(db: Database, signal: TradeSignal, config: RiskConfig) -> RiskDecision:
    """Return whether a trade can be opened under configured constraints."""
    open_trades = db.get_open_trades()
    cash = db.get_cash()
    trade_cost = signal.shares * signal.entry_price

    if trade_cost <= 0:
        return RiskDecision(False, "invalid_trade_cost")

    if len(open_trades) >= config.max_positions:
        return RiskDecision(False, "max_positions_reached")

    if trade_cost > config.max_trade_cost:
        return RiskDecision(False, "trade_cost_above_limit")

    if trade_cost > cash:
        return RiskDecision(False, "insufficient_cash")

    if cash - trade_cost < config.min_cash_buffer:
        return RiskDecision(False, "cash_buffer_breach")

    market_exposure = sum(
        t.shares * t.entry_price
        for t in open_trades
        if t.condition_id == signal.condition_id
    )
    if market_exposure + trade_cost > config.max_exposure_per_market:
        return RiskDecision(False, "market_exposure_limit")

    strategy_exposure = sum(
        t.shares * t.entry_price
        for t in open_trades
        if t.strategy == signal.strategy
    )
    if strategy_exposure + trade_cost > config.max_exposure_per_strategy:
        return RiskDecision(False, "strategy_exposure_limit")

    return RiskDecision(True, "ok")
