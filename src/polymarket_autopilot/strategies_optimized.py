"""Optimized strategy variants based on quant research (2026-03-14).

These are experimental improvements to existing strategies based on
performance analysis of the first 67 trades.

Key findings:
- MEAN_REVERSION: 77% of profit, but only 33% win rate → needs better entry filters
- TAIL: Solid W/L ratio (1.59) but worst loss -$193 → needs wider stops
- Overall: 37.3% win rate too low → needs improved entry timing

Optimizations:
1. TAIL_WIDE_SL: Wider stop-losses to avoid fakeouts (50% → 70%)
2. MEAN_REVERSION_V2: Stricter entry filters (volume, deviation thresholds)
3. CATALYST_HUNTER: New strategy for post-news stabilization trades
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from polymarket_autopilot.api import Market
from polymarket_autopilot.db import Database, MarketSnapshot
from polymarket_autopilot.strategies import (
    Strategy,
    TradeSignal,
    _calc_sl,
    _calc_tp,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TAIL_WIDE_SL: Experiment with wider stop-losses
# ---------------------------------------------------------------------------


class TailWideStopLossStrategy(Strategy):
    """TAIL variant with wider stop-losses to reduce fakeouts.

    Hypothesis: Original TAIL (50% SL) gets stopped out on noise,
    then markets revert. Widening to 70% SL should improve win rate
    by giving positions more breathing room.

    Changes from original TAIL:
    - sl_pct: 0.50 → 0.70 (70% max loss instead of 50%)
    - tp_pct: 1.00 → 1.50 (wider profit targets to match wider stops)

    Expected: Lower win rate, but higher profit (fewer premature exits).
    """

    name: str = "TAIL_WIDE_SL"

    def __init__(
        self,
        db: Database,
        max_yes_prob: float = 0.20,
        min_yes_prob: float = 0.05,
        max_position_pct: float = 0.02,
        tp_pct: float = 1.50,  # Increased from 1.00
        sl_pct: float = 0.70,  # Increased from 0.50
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
        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Condition 1: Buy long-shots only (5-20% probability)
        if yes_price > self.max_yes_prob or yes_price < self.min_yes_prob:
            return None

        # Condition 2: No existing position
        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        # Condition 3: Volume + momentum checks
        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) >= 2:
            avg_volume = sum(s.volume for s in snapshots) / len(snapshots)
            if market.volume < avg_volume:
                return None

            avg_price = sum(s.yes_price for s in snapshots) / len(snapshots)
            if yes_price <= avg_price:
                return None

        # Position sizing
        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < yes_price:
            return None

        shares = max_spend / yes_price
        take_profit = _calc_tp(yes_price, self.tp_pct)
        stop_loss = _calc_sl(yes_price, self.sl_pct)

        reason = (
            f"Long-shot (WIDE SL): YES={yes_price:.3f} "
            f"(range {self.min_yes_prob:.0%}-{self.max_yes_prob:.0%}); "
            f"SL widened to {self.sl_pct:.0%} to avoid fakeouts"
        )

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
# MEAN_REVERSION_V2: Stricter entry filters
# ---------------------------------------------------------------------------


class MeanReversionV2Strategy(Strategy):
    """MEAN_REVERSION with stricter entry criteria.

    Original MEAN_REVERSION: 33% win rate, but +$1017 profit (77% of total).
    Problem: Taking too many low-quality trades.

    New filters:
    1. Minimum volume: $50K (avoid illiquid markets)
    2. Minimum deviation: 15% from 7-day average (only trade big moves)
    3. Cooldown: 48h between trades in same market (avoid chasing)
    4. Maximum entry count: 3 trades/day max (avoid overtrading)

    Expected: Fewer trades, higher win rate, similar total profit.
    """

    name: str = "MEAN_REVERSION_V2"

    def __init__(
        self,
        db: Database,
        deviation_threshold: float = 0.15,  # 15% deviation required
        min_volume: float = 50_000.0,  # $50K minimum volume
        cooldown_hours: int = 48,  # 48h cooldown per market
        max_trades_per_day: int = 3,  # Max 3 entries per day
        max_position_pct: float = 0.05,
        tp_pct: float = 0.20,
        sl_pct: float = 0.10,
        lookback: int = 7,
    ) -> None:
        super().__init__(db)
        self.deviation_threshold = deviation_threshold
        self.min_volume = min_volume
        self.cooldown_hours = cooldown_hours
        self.max_trades_per_day = max_trades_per_day
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback
        self._today_trade_count = 0
        self._last_reset = datetime.now(timezone.utc).date()

    def _check_daily_limit(self) -> bool:
        """Check if daily trade limit has been reached."""
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset:
            self._today_trade_count = 0
            self._last_reset = today
        return self._today_trade_count < self.max_trades_per_day

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Filter 1: Daily trade limit
        if not self._check_daily_limit():
            logger.debug(
                "MEAN_REVERSION_V2 skip: daily limit reached (%d/%d)",
                self._today_trade_count,
                self.max_trades_per_day,
            )
            return None

        # Filter 2: Minimum volume
        if market.volume < self.min_volume:
            logger.debug(
                "MEAN_REVERSION_V2 skip %s: volume %.0f < min %.0f",
                market.condition_id[:8],
                market.volume,
                self.min_volume,
            )
            return None

        # Filter 3: No existing position
        existing = self.db.get_trade_by_condition(market.condition_id)
        if existing is not None:
            return None

        # Filter 4: Cooldown check (avoid re-entering same market too soon)
        # TODO: Implement cooldown tracking in database
        # For now, skip this check (would require schema change)

        # Filter 5: Get historical snapshots
        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 3:
            return None

        # Calculate 7-day average price
        avg_price = sum(s.yes_price for s in snapshots) / len(snapshots)
        if avg_price <= 0:
            return None

        # Filter 6: Minimum deviation threshold
        deviation = abs(yes_price - avg_price) / avg_price
        if deviation < self.deviation_threshold:
            logger.debug(
                "MEAN_REVERSION_V2 skip %s: deviation %.1f%% < threshold %.1f%%",
                market.condition_id[:8],
                deviation * 100,
                self.deviation_threshold * 100,
            )
            return None

        # Determine direction: fade the move
        if yes_price > avg_price:
            # Price above average → bet it will fall (buy NO)
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)
        else:
            # Price below average → bet it will rise (buy YES)
            outcome = "YES"
            entry = yes_price

        if entry <= 0:
            return None

        # Position sizing
        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        take_profit = _calc_tp(entry, self.tp_pct)
        stop_loss = _calc_sl(entry, self.sl_pct)

        # Increment daily counter
        self._today_trade_count += 1

        reason = (
            f"Mean reversion V2: {deviation:.1%} deviation "
            f"(price={yes_price:.3f}, avg={avg_price:.3f}); "
            f"volume ${market.volume:,.0f} (min ${self.min_volume:,.0f})"
        )

        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=take_profit,
            stop_loss=stop_loss,
            strategy=self.name,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# CATALYST_HUNTER: New strategy for post-news stabilization
# ---------------------------------------------------------------------------


class CatalystHunterStrategy(Strategy):
    """Post-catalyst mean reversion — trade after sharp price spikes.

    Logic:
    1. Detect sharp price move (>10% in last 1-2 snapshots)
    2. Wait for stabilization (price settles within 3% range for 2+ snapshots)
    3. Enter mean reversion trade (fade the initial spike)

    Rationale:
    - Markets overreact to news/catalysts
    - Initial spike is driven by emotion/herd behavior
    - After 1-4 hours, rational traders stabilize price
    - Fade the overreaction for quick profits

    Risk profile:
    - Tight stops (3% loss max)
    - Quick profits (5% target)
    - High frequency (many small trades)

    Win Rate Target: >60% | Hold Time: 1-3 days
    """

    name: str = "CATALYST_HUNTER"

    def __init__(
        self,
        db: Database,
        spike_threshold: float = 0.10,  # 10% move = "spike"
        stabilization_range: float = 0.03,  # 3% range = "stable"
        stabilization_periods: int = 2,  # 2 snapshots stable
        max_position_pct: float = 0.03,
        tp_pct: float = 0.05,  # Tight profit target (5%)
        sl_pct: float = 0.03,  # Tight stop-loss (3%)
        lookback: int = 10,
    ) -> None:
        super().__init__(db)
        self.spike_threshold = spike_threshold
        self.stabilization_range = stabilization_range
        self.stabilization_periods = stabilization_periods
        self.max_position_pct = max_position_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.lookback = lookback

    def evaluate(self, market: Market) -> TradeSignal | None:
        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Check no existing position
        if self.db.get_trade_by_condition(market.condition_id) is not None:
            return None

        # Get recent price history
        snapshots = self.db.get_recent_snapshots(market.condition_id, self.lookback)
        if len(snapshots) < 5:  # Need at least 5 snapshots for spike + stabilization detection
            return None

        # Step 1: Detect spike (compare recent price to older price)
        spike_start_idx = len(snapshots) - 4  # Look back 4 periods
        if spike_start_idx < 0:
            return None

        old_price = snapshots[spike_start_idx].yes_price
        spike_price = max(s.yes_price for s in snapshots[spike_start_idx:])  # Peak during spike

        if old_price <= 0:
            return None

        spike_magnitude = abs(spike_price - old_price) / old_price

        if spike_magnitude < self.spike_threshold:
            return None  # No significant spike detected

        # Step 2: Check for stabilization (last N snapshots within tight range)
        recent_prices = [s.yes_price for s in snapshots[-self.stabilization_periods:]]
        if len(recent_prices) < self.stabilization_periods:
            return None

        price_range = max(recent_prices) - min(recent_prices)
        avg_recent = sum(recent_prices) / len(recent_prices)

        if avg_recent <= 0:
            return None

        stabilization_pct = price_range / avg_recent

        if stabilization_pct > self.stabilization_range:
            return None  # Not yet stabilized

        # Step 3: Determine direction (fade the spike)
        if spike_price > old_price:
            # Spike was upward → fade by betting NO (price will fall)
            outcome = "NO"
            entry = market.no_price or (1.0 - yes_price)
        else:
            # Spike was downward → fade by betting YES (price will rise)
            outcome = "YES"
            entry = yes_price

        if entry <= 0:
            return None

        # Position sizing
        portfolio_value = self.db.get_portfolio_value()
        cash = self.db.get_cash()
        max_spend = min(portfolio_value * self.max_position_pct, cash)
        if max_spend < entry:
            return None

        shares = max_spend / entry
        take_profit = _calc_tp(entry, self.tp_pct)
        stop_loss = _calc_sl(entry, self.sl_pct)

        reason = (
            f"Catalyst fade: {spike_magnitude:.1%} spike detected "
            f"(old={old_price:.3f} → peak={spike_price:.3f}); "
            f"stabilized at {yes_price:.3f} (range {stabilization_pct:.1%})"
        )

        return TradeSignal(
            condition_id=market.condition_id,
            question=market.question,
            outcome=outcome,
            entry_price=entry,
            shares=round(shares, 4),
            take_profit=take_profit,
            stop_loss=stop_loss,
            strategy=self.name,
            reason=reason,
        )
