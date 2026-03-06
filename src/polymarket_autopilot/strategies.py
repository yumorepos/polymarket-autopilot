"""Strategy engine for polymarket-autopilot.

Provides a base Strategy interface and the built-in TAIL (Trend-following
Adaptive Indicator Logic) strategy.

TAIL Strategy rules:
- Buy YES when YES probability > 60 %
- Market volume must be above the rolling average of recent snapshots
- Price must be trending upward (current price > average of last N snapshots)
- Position size capped at 5 % of total portfolio value
- Take-profit at +15 % above entry, stop-loss at -10 % below entry
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from polymarket_autopilot.api import Market
from polymarket_autopilot.db import Database, MarketSnapshot, PaperTrade

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal types
# ---------------------------------------------------------------------------


@dataclass
class TradeSignal:
    """A trade signal produced by a strategy.

    Attributes:
        condition_id: Market to trade.
        question: Human-readable question text.
        outcome: "YES" or "NO".
        entry_price: Suggested entry price.
        shares: Number of shares to buy.
        take_profit: Target exit price (TP).
        stop_loss: Stop-loss exit price (SL).
        strategy: Strategy name that generated the signal.
        reason: Human-readable rationale.
    """

    condition_id: str
    question: str
    outcome: str
    entry_price: float
    shares: float
    take_profit: float
    stop_loss: float
    strategy: str
    reason: str


@dataclass
class ExitSignal:
    """An exit signal for an open position."""

    trade_id: int
    exit_price: float
    status: str   # "closed_tp" | "closed_sl" | "closed_manual"
    reason: str


# ---------------------------------------------------------------------------
# Base strategy
# ---------------------------------------------------------------------------


class Strategy(ABC):
    """Abstract base class for trading strategies.

    Args:
        db: Database instance for reading portfolio / snapshot data.
    """

    name: str = "base"

    def __init__(self, db: Database) -> None:
        self.db = db

    @abstractmethod
    def evaluate(self, market: Market) -> TradeSignal | None:
        """Evaluate a market and return a trade signal, or None to skip.

        Args:
            market: The market to evaluate.

        Returns:
            A TradeSignal if the strategy wants to open a position,
            otherwise None.
        """

    def check_exits(self, markets: dict[str, Market]) -> list[ExitSignal]:
        """Check open positions for take-profit or stop-loss conditions.

        Args:
            markets: Mapping of condition_id -> Market with current prices.

        Returns:
            List of ExitSignals for positions that should be closed.
        """
        signals: list[ExitSignal] = []
        open_trades = self.db.get_open_trades()

        for trade in open_trades:
            if trade.strategy != self.name:
                continue
            market = markets.get(trade.condition_id)
            if market is None:
                continue

            current_price = _price_for_outcome(market, trade.outcome)
            if current_price is None:
                continue

            if current_price >= trade.take_profit:
                signals.append(
                    ExitSignal(
                        trade_id=trade.id,  # type: ignore[arg-type]
                        exit_price=current_price,
                        status="closed_tp",
                        reason=f"Take-profit hit ({current_price:.3f} >= {trade.take_profit:.3f})",
                    )
                )
            elif current_price <= trade.stop_loss:
                signals.append(
                    ExitSignal(
                        trade_id=trade.id,  # type: ignore[arg-type]
                        exit_price=current_price,
                        status="closed_sl",
                        reason=f"Stop-loss hit ({current_price:.3f} <= {trade.stop_loss:.3f})",
                    )
                )

        return signals


# ---------------------------------------------------------------------------
# TAIL strategy
# ---------------------------------------------------------------------------


class TailStrategy(Strategy):
    """Trend-Following Adaptive Indicator Logic (TAIL) strategy.

    Entry conditions (all must be true):
    1. YES probability > ``min_yes_prob`` (default 60 %)
    2. Current volume > rolling average volume of last ``lookback`` snapshots
    3. Current YES price > average YES price of last ``lookback`` snapshots
    4. No existing open position in this market

    Position sizing:
    - Max ``max_position_pct`` (default 5 %) of total portfolio value per trade
    - Take-profit: entry + ``tp_pct`` (default +15 %)
    - Stop-loss:   entry - ``sl_pct`` (default -10 %)

    Args:
        db: Database instance.
        min_yes_prob: Minimum YES probability to consider entry (0–1).
        max_position_pct: Max fraction of portfolio per trade (0–1).
        tp_pct: Take-profit as a fraction above entry.
        sl_pct: Stop-loss as a fraction below entry.
        lookback: Number of historical snapshots for trend analysis.
    """

    name: str = "TAIL"

    def __init__(
        self,
        db: Database,
        min_yes_prob: float = 0.60,
        max_position_pct: float = 0.05,
        tp_pct: float = 0.15,
        sl_pct: float = 0.10,
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.min_yes_prob = min_yes_prob
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        """Evaluate a market for a TAIL entry signal.

        Args:
            market: The current market state.

        Returns:
            A TradeSignal if all entry conditions pass, else None.
        """
        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Condition 1: probability threshold
        if yes_price < self.min_yes_prob:
            logger.debug(
                "TAIL skip %s: YES=%.3f < min %.3f",
                market.condition_id[:8],
                yes_price,
                self.min_yes_prob,
            )
            return None

        # Condition 4: no existing open position
        existing = self.db.get_trade_by_condition(market.condition_id)
        if existing is not None:
            logger.debug("TAIL skip %s: position already open", market.condition_id[:8])
            return None

        # Retrieve recent snapshots for trend analysis
        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)

        # Condition 2: volume above average (requires at least 2 snapshots)
        if len(snapshots) >= 2:
            avg_volume = sum(s.volume for s in snapshots) / len(snapshots)
            if market.volume < avg_volume:
                logger.debug(
                    "TAIL skip %s: volume %.0f < avg %.0f",
                    market.condition_id[:8],
                    market.volume,
                    avg_volume,
                )
                return None

        # Condition 3: price trending up (requires at least 2 snapshots)
        if len(snapshots) >= 2:
            avg_price = sum(s.yes_price for s in snapshots) / len(snapshots)
            if yes_price <= avg_price:
                logger.debug(
                    "TAIL skip %s: price %.3f <= avg %.3f",
                    market.condition_id[:8],
                    yes_price,
                    avg_price,
                )
                return None

        # Position sizing
        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < yes_price:
            logger.debug("TAIL skip %s: insufficient cash", market.condition_id[:8])
            return None

        shares = max_spend / yes_price
        take_profit = round(yes_price * (1 + self.tp_pct), 6)
        stop_loss = round(yes_price * (1 - self.sl_pct), 6)

        reason = (
            f"YES={yes_price:.3f} > {self.min_yes_prob:.0%} threshold; "
            f"volume/trend conditions met"
        )
        if len(snapshots) < 2:
            reason += " (volume/trend skipped — insufficient history)"

        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome="YES",
            entry_price=yes_price,
            shares=round(shares, 4),
            take_profit=take_profit,
            stop_loss=stop_loss,
            strategy=self.name,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES: dict[str, type[Strategy]] = {
    "TAIL": TailStrategy,
}


def get_strategy(name: str, db: Database, **kwargs: float | int) -> Strategy:
    """Instantiate a strategy by name.

    Args:
        name: Strategy identifier (e.g. 'TAIL').
        db: Database instance.
        **kwargs: Strategy-specific keyword arguments.

    Returns:
        Instantiated Strategy.

    Raises:
        ValueError: If strategy name is not registered.
    """
    cls = STRATEGIES.get(name.upper())
    if cls is None:
        available = ", ".join(STRATEGIES)
        raise ValueError(f"Unknown strategy '{name}'. Available: {available}")
    return cls(db, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _price_for_outcome(market: Market, outcome: str) -> float | None:
    """Return the current price for a specific outcome in a market.

    Args:
        market: Market to query.
        outcome: "YES" or "NO".

    Returns:
        Price float, or None if not found.
    """
    for o in market.outcomes:
        if o.name.upper() == outcome.upper():
            return o.price
    return None


def signal_to_trade(signal: TradeSignal) -> PaperTrade:
    """Convert a TradeSignal into a PaperTrade ready for DB insertion.

    Args:
        signal: The trade signal to convert.

    Returns:
        Unsaved PaperTrade (id=None).
    """
    return PaperTrade(
        id=None,
        condition_id=signal.condition_id,
        question=signal.question,
        outcome=signal.outcome,
        strategy=signal.strategy,
        shares=signal.shares,
        entry_price=signal.entry_price,
        exit_price=None,
        take_profit=signal.take_profit,
        stop_loss=signal.stop_loss,
        status="open",
        pnl=None,
        opened_at=datetime.utcnow(),
        closed_at=None,
    )
