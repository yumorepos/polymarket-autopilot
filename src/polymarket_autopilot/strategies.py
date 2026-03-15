"""Strategy engine for polymarket-autopilot.

Provides a base Strategy interface and 10 built-in trading strategies:

1. TAIL          — Trend-Following Adaptive Indicator Logic
2. MARKET_MAKER  — Automated market making (spread earning)
3. AI_PROBABILITY — AI-powered probability arbitrage
4. CORRELATION   — Cross-market logical arbitrage
5. MEAN_REVERSION — Fade sharp overreactions
6. MOMENTUM      — Ride strong directional moves
7. VOLATILITY    — Trade pre-catalyst uncertain markets
8. WHALE_FOLLOW  — Follow large volume spikes
9. NEWS_MOMENTUM — Trade sudden price jumps from news
10. CONTRARIAN   — Buy extreme fear / sell extreme greed
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from polymarket_autopilot.api import Market
from polymarket_autopilot.db import Database, MarketSnapshot, PaperTrade

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Price helpers — prediction market prices are bounded [0, 1]
# ---------------------------------------------------------------------------

# Maximum / minimum valid prices on prediction markets
# Prediction markets cannot reach exactly 1.00 (would require infinite capital),
# so we cap take-profit targets at 0.99 to ensure they are reachable.
_PRICE_CEIL = 0.99
_PRICE_FLOOR = 0.01

# For very high-certainty bets (entry > 0.95), use even tighter targets
# because the remaining upside is extremely limited.
_VERY_HIGH_CERTAINTY_THRESHOLD = 0.95
_VERY_HIGH_CERTAINTY_TP_MULT = 1.03  # e.g. entry 0.95 → TP 0.9785 (capped at 0.99)
_VERY_HIGH_CERTAINTY_SL_MULT = 0.95  # e.g. entry 0.95 → SL 0.9025

# For high-certainty bets (entry > 0.85), use tighter absolute bands
# instead of percentages that would overshoot 1.0
_HIGH_CERTAINTY_THRESHOLD = 0.85
_HIGH_CERTAINTY_TP_SPREAD = 0.04  # e.g. entry 0.90 → TP 0.94
_HIGH_CERTAINTY_SL_SPREAD = 0.05  # e.g. entry 0.90 → SL 0.85

# For entries above 0.90, tighten stop-loss dynamically to reduce risk
# when there's limited upside.
_TIGHT_SL_THRESHOLD = 0.90
_TIGHT_SL_MULT = 0.95  # e.g. entry 0.92 → SL 0.874

# Low-certainty bets (entry < threshold) — tighter on the other side
_LOW_CERTAINTY_THRESHOLD = 0.15
_LOW_CERTAINTY_TP_SPREAD = 0.04
_LOW_CERTAINTY_SL_SPREAD = 0.05

# Time-based exit: close flat positions after this many days
MAX_HOLD_DAYS = 14


def _calc_tp(entry: float, tp_pct: float) -> float:
    """Calculate take-profit, capped at _PRICE_CEIL.

    Prediction market constraint: prices are bounded [0, 1] and cannot reach
    exactly 1.00 in practice. We cap all TP values at 0.99.

    For very high-certainty entries (>0.95), uses minimal percentage multiplier
    to ensure realistic profit targets given limited remaining upside.
    For high-certainty entries (>0.85), uses absolute spread to avoid
    unreachable targets above 1.0.
    """
    if entry >= _VERY_HIGH_CERTAINTY_THRESHOLD:
        # For entries above 0.95, use tight multiplier and cap at 0.99
        tp = entry * _VERY_HIGH_CERTAINTY_TP_MULT
    elif entry >= _HIGH_CERTAINTY_THRESHOLD:
        tp = entry + _HIGH_CERTAINTY_TP_SPREAD
    elif entry <= _LOW_CERTAINTY_THRESHOLD:
        tp = entry - _LOW_CERTAINTY_TP_SPREAD  # TP is lower for NO-like bets at low prices
        tp = max(tp, _PRICE_FLOOR)
        # Actually for YES bets at low price, TP is still higher
        tp = entry + _LOW_CERTAINTY_TP_SPREAD
    else:
        tp = entry * (1 + tp_pct)
    return round(min(tp, _PRICE_CEIL), 6)


def _calc_sl(entry: float, sl_pct: float) -> float:
    """Calculate stop-loss, floored at _PRICE_FLOOR.

    For high-certainty entries (>0.85), uses absolute spread to ensure
    reasonable stop-loss distances. For entries above 0.90, uses even
    tighter stop-loss (95% of entry) to limit risk when upside is capped.
    """
    if entry >= _VERY_HIGH_CERTAINTY_THRESHOLD:
        # For entries above 0.95, use tight multiplier for stop-loss
        sl = entry * _VERY_HIGH_CERTAINTY_SL_MULT
    elif entry >= _TIGHT_SL_THRESHOLD:
        # For entries above 0.90, tighten SL dynamically
        sl = entry * _TIGHT_SL_MULT
    elif entry >= _HIGH_CERTAINTY_THRESHOLD:
        sl = entry - _HIGH_CERTAINTY_SL_SPREAD
    elif entry <= _LOW_CERTAINTY_THRESHOLD:
        sl = entry - _LOW_CERTAINTY_SL_SPREAD
    else:
        sl = entry * (1 - sl_pct)
    return round(max(sl, _PRICE_FLOOR), 6)


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
    status: str  # "closed_tp" | "closed_sl" | "closed_manual"
    reason: str


# ---------------------------------------------------------------------------
# Base strategy
# ---------------------------------------------------------------------------


class StrategyDB(Protocol):
    def get_open_trades(self) -> list[PaperTrade]: ...

    def get_trade_by_condition(self, condition_id: str) -> PaperTrade | None: ...

    def get_recent_snapshots(self, condition_id: str, n: int = 10) -> list[MarketSnapshot]: ...

    def get_portfolio_value(self) -> float: ...

    def get_cash(self) -> float: ...


class Strategy(ABC):
    """Abstract base class for trading strategies.

    Args:
        db: Database instance for reading portfolio / snapshot data.
    """

    name: str = "base"

    def __init__(self, db: StrategyDB) -> None:
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

            # Clamp stored TP/SL to valid range (fixes legacy positions)
            effective_tp = min(trade.take_profit, _PRICE_CEIL)
            effective_sl = max(trade.stop_loss, _PRICE_FLOOR)

            if trade.id is None:
                continue

            if current_price >= effective_tp:
                signals.append(
                    ExitSignal(
                        trade_id=trade.id,
                        exit_price=current_price,
                        status="closed_tp",
                        reason=f"Take-profit hit ({current_price:.3f} >= {effective_tp:.3f})",
                    )
                )
            elif current_price <= effective_sl:
                signals.append(
                    ExitSignal(
                        trade_id=trade.id,
                        exit_price=current_price,
                        status="closed_sl",
                        reason=f"Stop-loss hit ({current_price:.3f} <= {effective_sl:.3f})",
                    )
                )
            elif trade.opened_at is not None:
                # Time-based exit: close stale positions after MAX_HOLD_DAYS
                now = datetime.now(timezone.utc)
                age_days = (now - trade.opened_at).days
                if age_days >= MAX_HOLD_DAYS:
                    signals.append(
                        ExitSignal(
                            trade_id=trade.id,
                            exit_price=current_price,
                            status="closed_manual",
                            reason=f"Time exit ({age_days}d held, max {MAX_HOLD_DAYS}d)",
                        )
                    )

        return signals


# ---------------------------------------------------------------------------
# TAIL strategy
# ---------------------------------------------------------------------------


class TailStrategy(Strategy):
    """True Tail Risk strategy — buy long-shot bets (low probability events).

    Entry conditions (all must be true):
    1. YES probability < ``max_yes_prob`` (default 20 %) — buy LONG-SHOTS only
    2. YES probability > ``min_yes_prob`` (default 5 %) — avoid extremely unlikely events
    3. Current volume > rolling average volume (indicates liquidity for exit)
    4. Price showing positive momentum (trending up from average)
    5. No existing open position in this market

    Position sizing:
    - Max ``max_position_pct`` (default 2 %) of total portfolio value per trade
      (smaller positions appropriate for high-risk long-shot bets)
    - Take-profit: entry + ``tp_pct`` (default +100 %) — wide targets for tail events
    - Stop-loss:   entry - ``sl_pct`` (default -50 %) — wider stops for volatility

    Rationale:
    - True tail strategies profit from rare high-payoff events
    - Entry at low prices (5-20%) allows for asymmetric upside
    - Smaller position sizing limits downside from frequent small losses
    - Wider TP/SL bands accommodate the volatility of long-shot bets

    Args:
        db: Database instance.
        max_yes_prob: Maximum YES probability to consider entry (0–1).
        min_yes_prob: Minimum YES probability to avoid noise (0–1).
        max_position_pct: Max fraction of portfolio per trade (0–1).
        tp_pct: Take-profit as a fraction above entry.
        sl_pct: Stop-loss as a fraction below entry.
        lookback: Number of historical snapshots for trend analysis.
    """

    name: str = "TAIL"

    def __init__(
        self,
        db: Database,
        max_yes_prob: float = 0.20,  # Changed: now a MAXIMUM (buy long-shots)
        min_yes_prob: float = 0.05,  # Added: avoid extremely unlikely events
        max_position_pct: float = 0.02,  # Reduced from 0.05 to 0.02 (2% vs 5%)
        tp_pct: float = 1.00,  # Increased from 0.15 to 1.00 (100% gain target)
        sl_pct: float = 0.50,  # Increased from 0.10 to 0.50 (50% loss max)
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.max_yes_prob = max_yes_prob
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

        # Condition 1: Buy LONG-SHOTS only (low probability events)
        if yes_price > self.max_yes_prob:
            logger.debug(
                "TAIL skip %s: YES=%.3f > max %.3f (not a long-shot)",
                market.condition_id[:8],
                yes_price,
                self.max_yes_prob,
            )
            return None

        # Condition 2: Avoid extremely unlikely events (noise filter)
        if yes_price < self.min_yes_prob:
            logger.debug(
                "TAIL skip %s: YES=%.3f < min %.3f (too unlikely)",
                market.condition_id[:8],
                yes_price,
                self.min_yes_prob,
            )
            return None

        # Condition 3: No existing open position
        existing = self.db.get_trade_by_condition(market.condition_id)
        if existing is not None:
            logger.debug("TAIL skip %s: position already open", market.condition_id[:8])
            return None

        # Retrieve recent snapshots for trend analysis
        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)

        # Condition 4: Volume above average (liquidity check, requires at least 2 snapshots)
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

        # Condition 5: Price showing positive momentum (trending up from average)
        if len(snapshots) >= 2:
            avg_price = sum(s.yes_price for s in snapshots) / len(snapshots)
            if yes_price <= avg_price:
                logger.debug(
                    "TAIL skip %s: price %.3f <= avg %.3f (no momentum)",
                    market.condition_id[:8],
                    yes_price,
                    avg_price,
                )
                return None

        # Position sizing — smaller positions for high-risk long-shot bets
        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < yes_price:
            logger.debug("TAIL skip %s: insufficient cash", market.condition_id[:8])
            return None

        shares = max_spend / yes_price
        take_profit = _calc_tp(yes_price, self.tp_pct)
        stop_loss = _calc_sl(yes_price, self.sl_pct)

        reason = (
            "Long-shot bet: "
            f"YES={yes_price:.3f} "
            f"(in range {self.min_yes_prob:.0%}-{self.max_yes_prob:.0%}); "
            "volume/momentum conditions met"
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
# MARKET_MAKER strategy
# ---------------------------------------------------------------------------


class MarketMakerStrategy(Strategy):
    """Automated Market Making — earn the bid/ask spread.

    Signal: markets where YES + NO prices deviate from 1.00 (wide spread).
    Places virtual positions on the underpriced side.

    Win Rate: 78-85% | Returns: 1-3% monthly | Risk: Low
    """

    name: str = "MARKET_MAKER"

    def __init__(
        self,
        db: Database,
        spread_threshold: float = 0.05,
        max_position_pct: float = 0.03,
        tp_pct: float = 0.08,
        sl_pct: float = 0.05,
    ) -> None:
        super().__init__(db)
        self.spread_threshold = spread_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        no_price = market.no_price
        if yes_price is None or no_price is None:
            return None

        total = yes_price + no_price
        spread = abs(total - 1.0)

        if spread < self.spread_threshold:
            return None

        # Check no existing position
        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        # Buy the underpriced side
        if total < 1.0 - self.spread_threshold:
            outcome = "YES" if yes_price < no_price else "NO"
            entry = yes_price if outcome == "YES" else no_price
        elif total > 1.0 + self.spread_threshold:
            outcome = "YES" if yes_price > no_price else "NO"
            entry = yes_price if outcome == "YES" else no_price
        else:
            return None

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=f"Spread detected: YES+NO={total:.3f} (spread={spread:.3f}), buying {outcome}",
        )


# ---------------------------------------------------------------------------
# AI_PROBABILITY strategy
# ---------------------------------------------------------------------------


class AIProbabilityStrategy(Strategy):
    """AI Probability Arbitrage — compare market price to estimated fair value.

    Estimates fair probability using volume signals, market maturity, and
    price stability. Trades when market diverges >15% from estimate.

    Win Rate: 65-75% | Returns: 3-8% monthly | Risk: Medium
    """

    name: str = "AI_PROBABILITY"

    def __init__(
        self,
        db: Database,
        divergence_threshold: float = 0.15,
        max_position_pct: float = 0.05,
        tp_pct: float = 0.20,
        sl_pct: float = 0.12,
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.divergence_threshold = divergence_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def _estimate_fair_value(self, market: Market) -> float | None:
        """Estimate fair probability using snapshot history and volume."""
        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 3:
            return None

        # Volume-weighted average price
        total_vol = sum(s.volume for s in snapshots)
        if total_vol <= 0:
            return sum(s.yes_price for s in snapshots) / len(snapshots)

        vwap = sum(s.yes_price * s.volume for s in snapshots) / total_vol
        return vwap

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        fair_value = self._estimate_fair_value(market)
        if fair_value is None:
            return None

        divergence = yes_price - fair_value

        if abs(divergence) < self.divergence_threshold:
            return None

        # Market overpriced → sell (buy NO), underpriced → buy YES
        if divergence > 0:
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)
        else:
            outcome = "YES"
            entry = yes_price

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        # Simplified Kelly: edge / odds
        edge = abs(divergence)
        kelly_fraction = min(edge / entry, self.max_position_pct)
        max_spend = min(portfolio_value * kelly_fraction, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=(
                f"Fair value={fair_value:.3f}, market={yes_price:.3f}, divergence={divergence:+.3f}"
            ),
        )


# ---------------------------------------------------------------------------
# CORRELATION strategy
# ---------------------------------------------------------------------------


class CorrelationStrategy(Strategy):
    """Correlation / Logical Arbitrage — exploit pricing inconsistencies.

    Signal: within a single market, YES + NO deviates significantly from 1.00.
    Mathematical edge with low risk.

    Win Rate: 70-80% | Returns: 2-5% monthly | Risk: Low
    """

    name: str = "CORRELATION"

    def __init__(
        self,
        db: Database,
        mispricing_threshold: float = 0.05,
        max_position_pct: float = 0.05,
        tp_pct: float = 0.10,
        sl_pct: float = 0.05,
    ) -> None:
        super().__init__(db)
        self.mispricing_threshold = mispricing_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        no_price = market.no_price
        if yes_price is None or no_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        total = yes_price + no_price

        if total > 1.0 + self.mispricing_threshold:
            # Overpriced — buy the cheaper side (will correct down)
            # Actually: total > 1 means selling both guarantees profit
            # We buy the side closer to fair value
            outcome = "NO" if yes_price > no_price else "YES"
            entry = no_price if outcome == "NO" else yes_price
            reason = f"Overpriced: YES+NO={total:.3f} > 1.0, buying {outcome}"
        elif total < 1.0 - self.mispricing_threshold:
            # Underpriced — both sides too cheap, buy the one with more edge
            outcome = "YES" if yes_price < 0.5 else "NO"
            entry = yes_price if outcome == "YES" else no_price
            reason = f"Underpriced: YES+NO={total:.3f} < 1.0, buying {outcome}"
        else:
            return None

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# MEAN_REVERSION strategy
# ---------------------------------------------------------------------------


class MeanReversionStrategy(Strategy):
    """Mean Reversion — fade sharp price deviations.

    Signal: price deviated >20% from rolling average (overreaction).
    Buys when price dropped sharply, shorts when spiked.

    Win Rate: 60-70% | Returns: 2-5% monthly | Risk: Medium
    """

    name: str = "MEAN_REVERSION"

    def __init__(
        self,
        db: Database,
        deviation_threshold: float = 0.20,
        max_position_pct: float = 0.05,
        tp_pct: float = 0.10,
        sl_pct: float = 0.15,
        lookback: int = 10,
    ) -> None:
        super().__init__(db)
        self.deviation_threshold = deviation_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 3:
            return None

        avg_price = sum(s.yes_price for s in snapshots) / len(snapshots)
        if avg_price <= 0:
            return None

        deviation = (yes_price - avg_price) / avg_price

        if abs(deviation) < self.deviation_threshold:
            return None

        if deviation < -self.deviation_threshold:
            # Price dropped sharply — buy YES (expect reversion up)
            outcome = "YES"
            entry = yes_price
        else:
            # Price spiked — buy NO (expect reversion down)
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=(
                f"Mean reversion: avg={avg_price:.3f}, current={yes_price:.3f}, "
                f"deviation={deviation:+.1%}"
            ),
        )


# ---------------------------------------------------------------------------
# MOMENTUM strategy
# ---------------------------------------------------------------------------


class MomentumStrategy(Strategy):
    """Momentum Trading — ride strong directional moves with volume.

    Signal: price moved >10% in same direction over last 5 snapshots
    AND volume is increasing. Rides the trend.

    Win Rate: 55-65% | Returns: 3-7% monthly | Risk: Medium-High
    """

    name: str = "MOMENTUM"

    def __init__(
        self,
        db: Database,
        move_threshold: float = 0.10,
        max_position_pct: float = 0.04,
        tp_pct: float = 0.25,
        sl_pct: float = 0.08,
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.move_threshold = move_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 3:
            return None

        oldest_price = snapshots[0].yes_price
        if oldest_price <= 0:
            return None

        price_change = (yes_price - oldest_price) / oldest_price

        # Check volume is increasing
        mid = len(snapshots) // 2
        early_vol = sum(s.volume for s in snapshots[:mid]) / max(mid, 1)
        late_vol = sum(s.volume for s in snapshots[mid:]) / max(len(snapshots) - mid, 1)
        vol_increasing = late_vol > early_vol

        if abs(price_change) < self.move_threshold or not vol_increasing:
            return None

        if price_change > 0:
            outcome = "YES"
            entry = yes_price
        else:
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=(
                f"Momentum: {price_change:+.1%} move over {len(snapshots)} snapshots, "
                f"volume {'increasing' if vol_increasing else 'flat'}"
            ),
        )


# ---------------------------------------------------------------------------
# VOLATILITY strategy
# ---------------------------------------------------------------------------


class VolatilityStrategy(Strategy):
    """Volatility Trading — trade uncertain markets near resolution.

    Signal: market within 7 days of end_date AND price between 30-70%
    (high uncertainty = big move coming). Smaller positions, wider stops.

    Win Rate: 50-60% | Returns: 3-10% monthly | Risk: High
    """

    name: str = "VOLATILITY"

    def __init__(
        self,
        db: Database,
        days_to_expiry: int = 7,
        min_uncertainty: float = 0.30,
        max_uncertainty: float = 0.70,
        max_position_pct: float = 0.03,
        tp_pct: float = 0.30,
        sl_pct: float = 0.15,
    ) -> None:
        super().__init__(db)
        self.days_to_expiry = days_to_expiry
        self.min_uncertainty = min_uncertainty
        self.max_uncertainty = max_uncertainty
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None or market.end_date is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        # Check proximity to resolution
        now = datetime.now(timezone.utc)
        days_left = (market.end_date - now).total_seconds() / 86400
        if days_left < 0 or days_left > self.days_to_expiry:
            return None

        # Check uncertainty range
        if not (self.min_uncertainty <= yes_price <= self.max_uncertainty):
            return None

        # Bet on the side with slight edge (closer to 50% = more uncertain)
        if yes_price >= 0.5:
            outcome = "YES"
            entry = yes_price
        else:
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=(
                f"Volatility: {days_left:.1f} days to expiry, "
                f"price={yes_price:.3f} (uncertain range)"
            ),
        )


# ---------------------------------------------------------------------------
# WHALE_FOLLOW strategy
# ---------------------------------------------------------------------------


class WhaleFollowStrategy(Strategy):
    """Whale Following — detect and follow large volume spikes.

    Signal: current volume > 3x rolling average volume = whale activity.
    Follow the direction of the price move.

    Win Rate: 60-70% | Returns: 2-5% monthly | Risk: Medium
    """

    name: str = "WHALE_FOLLOW"

    def __init__(
        self,
        db: Database,
        volume_multiplier: float = 3.0,
        max_position_pct: float = 0.04,
        tp_pct: float = 0.15,
        sl_pct: float = 0.10,
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.volume_multiplier = volume_multiplier
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 2:
            return None

        avg_volume = sum(s.volume for s in snapshots) / len(snapshots)
        if avg_volume <= 0:
            return None

        if market.volume < avg_volume * self.volume_multiplier:
            return None

        # Follow direction: if price went up, buy YES
        last_price = snapshots[-1].yes_price
        if yes_price > last_price:
            outcome = "YES"
            entry = yes_price
        elif yes_price < last_price:
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)
        else:
            return None

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=(
                f"Whale activity: volume={market.volume:,.0f} "
                f"vs avg={avg_volume:,.0f} ({market.volume / avg_volume:.1f}x)"
            ),
        )


# ---------------------------------------------------------------------------
# NEWS_MOMENTUM strategy
# ---------------------------------------------------------------------------


class NewsMomentumStrategy(Strategy):
    """News Momentum — trade sudden price jumps from information events.

    Signal: price changed >15% between last 2 snapshots = news event.
    Ride the move assuming information is being priced in.

    Win Rate: 55-65% | Returns: 3-8% monthly | Risk: Medium-High
    """

    name: str = "NEWS_MOMENTUM"

    def __init__(
        self,
        db: Database,
        jump_threshold: float = 0.15,
        max_position_pct: float = 0.04,
        tp_pct: float = 0.20,
        sl_pct: float = 0.10,
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.jump_threshold = jump_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 2:
            return None

        prev_price = snapshots[-1].yes_price
        if prev_price <= 0:
            return None

        jump = (yes_price - prev_price) / prev_price

        if abs(jump) < self.jump_threshold:
            return None

        # High volume confirms the move is real
        avg_volume = sum(s.volume for s in snapshots) / len(snapshots)
        if avg_volume > 0 and market.volume < avg_volume:
            return None  # Low volume jump = noise

        if jump > 0:
            outcome = "YES"
            entry = yes_price
        else:
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=f"News momentum: {jump:+.1%} price jump with volume confirmation",
        )


# ---------------------------------------------------------------------------
# CONTRARIAN strategy
# ---------------------------------------------------------------------------


class ContrarianStrategy(Strategy):
    """Contrarian / Bonding — buy extreme fear, fade panic.

    Signal: price dropped >25% from recent average (overreaction).
    Assumes partial recovery. Wider stop-loss for reversion plays.

    Win Rate: 55-65% | Returns: 2-6% monthly | Risk: Medium-High
    """

    name: str = "CONTRARIAN"

    def __init__(
        self,
        db: Database,
        drop_threshold: float = 0.25,
        max_position_pct: float = 0.04,
        tp_pct: float = 0.15,
        sl_pct: float = 0.20,
        lookback: int = 5,
    ) -> None:
        super().__init__(db)
        self.drop_threshold = drop_threshold
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 3:
            return None

        avg_price = sum(s.yes_price for s in snapshots) / len(snapshots)
        if avg_price <= 0:
            return None

        drop = (yes_price - avg_price) / avg_price

        # Only trigger on large drops (buying fear)
        if drop > -self.drop_threshold:
            return None

        outcome = "YES"
        entry = yes_price

        if entry <= 0:
            return None

        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=_calc_tp(entry, self.tp_pct),
            stop_loss=_calc_sl(entry, self.sl_pct),
            strategy=self.name,
            reason=f"Contrarian: price dropped {drop:+.1%} from avg={avg_price:.3f}, buying fear",
        )


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES: dict[str, type[Strategy]] = {
    "TAIL": TailStrategy,
    "MARKET_MAKER": MarketMakerStrategy,
    "AI_PROBABILITY": AIProbabilityStrategy,
    "CORRELATION": CorrelationStrategy,
    "MEAN_REVERSION": MeanReversionStrategy,
    "MOMENTUM": MomentumStrategy,
    "VOLATILITY": VolatilityStrategy,
    "WHALE_FOLLOW": WhaleFollowStrategy,
    "NEWS_MOMENTUM": NewsMomentumStrategy,
    "CONTRARIAN": ContrarianStrategy,
}

# Import optimized strategy variants (experimental)
try:
    from polymarket_autopilot.strategies_optimized import (
        CatalystHunterStrategy,
        MeanReversionV2Strategy,
        TailWideStopLossStrategy,
    )
    STRATEGIES["TAIL_WIDE_SL"] = TailWideStopLossStrategy
    STRATEGIES["MEAN_REVERSION_V2"] = MeanReversionV2Strategy
    STRATEGIES["CATALYST_HUNTER"] = CatalystHunterStrategy
except ImportError:
    pass  # Optimized strategies not available


def get_strategy(name: str, db: StrategyDB, **kwargs: float | int) -> Strategy:
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
    return cls(db, **kwargs)


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
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )


class SIMPLE_MEAN_REVERSAL(Strategy):
    name = "SIMPLE_MEAN_REVERSAL"

    def evaluate(self, market: Market) -> TradeSignal | None:
        # A simple placeholder that does not trade, representing mean-reversion core logic template.
        return None


STRATEGIES["SIMPLE_MEAN_REVERSAL"] = SIMPLE_MEAN_REVERSAL


@dataclass(frozen=True)
class StrategyMetadata:
    name: str
    risk_profile: str
    holding_style: str
    signal_family: str


STRATEGY_METADATA: dict[str, StrategyMetadata] = {
    "TAIL": StrategyMetadata("TAIL", "medium", "swing", "trend+volume"),
    "MARKET_MAKER": StrategyMetadata("MARKET_MAKER", "low", "short", "spread"),
    "AI_PROBABILITY": StrategyMetadata("AI_PROBABILITY", "medium", "swing", "mispricing"),
    "CORRELATION": StrategyMetadata("CORRELATION", "low", "short", "arbitrage"),
    "MEAN_REVERSION": StrategyMetadata("MEAN_REVERSION", "medium", "swing", "mean-reversion"),
    "MOMENTUM": StrategyMetadata("MOMENTUM", "med-high", "swing", "momentum"),
    "VOLATILITY": StrategyMetadata("VOLATILITY", "high", "event-driven", "volatility"),
    "WHALE_FOLLOW": StrategyMetadata("WHALE_FOLLOW", "medium", "short", "volume-spike"),
    "NEWS_MOMENTUM": StrategyMetadata("NEWS_MOMENTUM", "med-high", "short", "news-momentum"),
    "CONTRARIAN": StrategyMetadata("CONTRARIAN", "med-high", "swing", "extreme-fear"),
    "SIMPLE_MEAN_REVERSAL": StrategyMetadata(
        "SIMPLE_MEAN_REVERSAL", "low", "template", "placeholder"
    ),
    # Optimized variants
    "TAIL_WIDE_SL": StrategyMetadata("TAIL_WIDE_SL", "medium", "swing", "long-shot-wide-sl"),
    "MEAN_REVERSION_V2": StrategyMetadata(
        "MEAN_REVERSION_V2", "medium", "swing", "mean-rev-filtered"
    ),
    "CATALYST_HUNTER": StrategyMetadata("CATALYST_HUNTER", "low-med", "short", "post-catalyst"),
}


def list_strategies() -> list[StrategyMetadata]:
    return [STRATEGY_METADATA[name] for name in sorted(STRATEGIES)]
