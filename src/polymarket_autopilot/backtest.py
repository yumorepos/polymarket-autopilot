"""Backtesting engine for polymarket-autopilot.

Replays trading strategies against historical market snapshots stored in
the SQLite database. Produces performance metrics including return,
win rate, max drawdown, and Sharpe ratio.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from polymarket_autopilot.api import Market, Outcome
from polymarket_autopilot.db import Database, MarketSnapshot
from polymarket_autopilot.strategies import (
    STRATEGIES,
    Strategy,
    TradeSignal,
    get_strategy,
    _price_for_outcome,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class BacktestTrade:
    """A single trade in the backtest simulation."""

    condition_id: str
    question: str
    outcome: str
    strategy: str
    entry_price: float
    exit_price: float | None = None
    shares: float = 0.0
    pnl: float = 0.0
    status: str = "open"  # open | closed_tp | closed_sl


@dataclass
class BacktestResult:
    """Summary of a backtest run."""

    strategy_name: str
    period_start: datetime
    period_end: datetime
    starting_capital: float
    ending_capital: float
    total_return_pct: float
    win_rate: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    open_trades: int
    trades: list[BacktestTrade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------


class Backtester:
    """Replay a strategy against historical snapshot data.

    Args:
        db: Database with historical snapshots.
        strategy_name: Name of the strategy to test.
        starting_capital: Initial paper capital.
    """

    def __init__(
        self,
        db: Database,
        strategy_name: str,
        starting_capital: float = 10_000.0,
    ) -> None:
        self.db = db
        self.strategy_name = strategy_name
        self.starting_capital = starting_capital

    def run(self, days: int = 7) -> BacktestResult:
        """Run the backtest over the specified period.

        Args:
            days: Number of days of history to replay.

        Returns:
            BacktestResult with performance metrics.
        """
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        # Get all snapshots in the period, grouped by timestamp
        snapshots = self._get_snapshots_in_range(start_date, end_date)
        if not snapshots:
            return self._empty_result(start_date, end_date)

        # Group snapshots by approximate timestamp (within 60 seconds)
        batches = self._group_by_timestamp(snapshots)

        cash = self.starting_capital
        positions: dict[str, BacktestTrade] = {}  # condition_id -> trade
        closed_trades: list[BacktestTrade] = []
        portfolio_values: list[float] = [self.starting_capital]

        for batch in batches:
            # Build market objects from snapshots
            markets = self._snapshots_to_markets(batch)

            # Check exits on existing positions
            for cid, trade in list(positions.items()):
                if cid not in markets:
                    continue
                market = markets[cid]
                current_price = self._get_price(market, trade.outcome)
                if current_price is None:
                    continue

                if current_price >= trade.entry_price * 1.15:  # TP
                    trade.exit_price = current_price
                    trade.pnl = (current_price - trade.entry_price) * trade.shares
                    trade.status = "closed_tp"
                    cash += trade.shares * current_price
                    closed_trades.append(trade)
                    del positions[cid]
                elif current_price <= trade.entry_price * 0.90:  # SL
                    trade.exit_price = current_price
                    trade.pnl = (current_price - trade.entry_price) * trade.shares
                    trade.status = "closed_sl"
                    cash += trade.shares * current_price
                    closed_trades.append(trade)
                    del positions[cid]

            # Evaluate new signals (simplified — use price threshold only)
            for cid, market in markets.items():
                if cid in positions:
                    continue
                yes_price = market.yes_price
                if yes_price is None or yes_price < 0.6:
                    continue

                # Simple position sizing: max 5% of portfolio
                portfolio_val = cash + sum(
                    p.shares * p.entry_price for p in positions.values()
                )
                max_spend = min(portfolio_val * 0.05, cash)
                if max_spend < yes_price:
                    continue

                shares = max_spend / yes_price
                trade = BacktestTrade(
                    condition_id=cid,
                    question=market.question,
                    outcome="YES",
                    strategy=self.strategy_name,
                    entry_price=yes_price,
                    shares=shares,
                )
                positions[cid] = trade
                cash -= max_spend

            # Track portfolio value
            open_value = sum(
                t.shares * t.entry_price for t in positions.values()
            )
            portfolio_values.append(cash + open_value)

        # Calculate metrics
        all_trades = closed_trades + list(positions.values())
        winning = [t for t in closed_trades if t.pnl > 0]
        losing = [t for t in closed_trades if t.pnl <= 0]

        ending_capital = portfolio_values[-1] if portfolio_values else self.starting_capital
        total_return = ((ending_capital - self.starting_capital) / self.starting_capital) * 100

        win_rate = len(winning) / len(closed_trades) if closed_trades else 0.0
        max_dd = self._max_drawdown(portfolio_values)
        sharpe = self._sharpe_ratio(portfolio_values)

        return BacktestResult(
            strategy_name=self.strategy_name,
            period_start=start_date,
            period_end=end_date,
            starting_capital=self.starting_capital,
            ending_capital=ending_capital,
            total_return_pct=total_return,
            win_rate=win_rate,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            total_trades=len(all_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            open_trades=len(positions),
            trades=all_trades,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_snapshots_in_range(
        self, start: datetime, end: datetime
    ) -> list[MarketSnapshot]:
        """Fetch snapshots within a date range from the DB."""
        with self.db._connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, condition_id, yes_price, no_price, volume, recorded_at
                FROM market_snapshots
                WHERE recorded_at >= ? AND recorded_at <= ?
                ORDER BY recorded_at ASC
                """,
                (start.isoformat(), end.isoformat()),
            )
            rows = cursor.fetchall()

        snapshots = []
        for row in rows:
            snapshots.append(
                MarketSnapshot(
                    id=row[0],
                    condition_id=row[1],
                    yes_price=row[2],
                    no_price=row[3],
                    volume=row[4],
                    recorded_at=datetime.fromisoformat(row[5]) if row[5] else datetime.utcnow(),
                )
            )
        return snapshots

    def _group_by_timestamp(
        self, snapshots: list[MarketSnapshot], window_secs: int = 120
    ) -> list[list[MarketSnapshot]]:
        """Group snapshots into batches by approximate timestamp."""
        if not snapshots:
            return []

        batches: list[list[MarketSnapshot]] = []
        current_batch: list[MarketSnapshot] = [snapshots[0]]

        for snap in snapshots[1:]:
            prev_time = current_batch[0].recorded_at
            if (snap.recorded_at - prev_time).total_seconds() <= window_secs:
                current_batch.append(snap)
            else:
                batches.append(current_batch)
                current_batch = [snap]

        if current_batch:
            batches.append(current_batch)

        return batches

    def _snapshots_to_markets(
        self, snapshots: list[MarketSnapshot]
    ) -> dict[str, Market]:
        """Convert snapshots to Market objects for strategy evaluation."""
        markets: dict[str, Market] = {}
        for snap in snapshots:
            markets[snap.condition_id] = Market(
                condition_id=snap.condition_id,
                question=f"Market {snap.condition_id[:12]}",
                outcomes=[
                    Outcome(name="Yes", price=snap.yes_price, token_id=""),
                    Outcome(name="No", price=snap.no_price, token_id=""),
                ],
                volume=snap.volume,
                end_date=None,
                active=True,
                closed=False,
            )
        return markets

    def _get_price(self, market: Market, outcome: str) -> float | None:
        """Get price for an outcome."""
        for o in market.outcomes:
            if o.name.upper() == outcome.upper():
                return o.price
        return None

    def _max_drawdown(self, values: list[float]) -> float:
        """Calculate maximum drawdown percentage."""
        if len(values) < 2:
            return 0.0
        peak = values[0]
        max_dd = 0.0
        for val in values[1:]:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd

    def _sharpe_ratio(self, values: list[float]) -> float:
        """Simplified Sharpe ratio: mean return / std of returns."""
        if len(values) < 3:
            return 0.0
        returns = [
            (values[i] - values[i - 1]) / values[i - 1]
            for i in range(1, len(values))
            if values[i - 1] > 0
        ]
        if not returns:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_ret = math.sqrt(variance) if variance > 0 else 0.0
        return mean_ret / std_ret if std_ret > 0 else 0.0

    def _empty_result(self, start: datetime, end: datetime) -> BacktestResult:
        """Return an empty result when no data is available."""
        return BacktestResult(
            strategy_name=self.strategy_name,
            period_start=start,
            period_end=end,
            starting_capital=self.starting_capital,
            ending_capital=self.starting_capital,
            total_return_pct=0.0,
            win_rate=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            open_trades=0,
        )


def format_backtest_result(result: BacktestResult) -> str:
    """Format a BacktestResult as a human-readable string.

    Args:
        result: The backtest result to format.

    Returns:
        Formatted multi-line string.
    """
    sign = "+" if result.total_return_pct >= 0 else ""
    lines = [
        f"{'='*55}",
        f"  BACKTEST REPORT: {result.strategy_name}",
        f"{'='*55}",
        f"  Period         : {result.period_start:%Y-%m-%d} → {result.period_end:%Y-%m-%d}",
        f"  Starting capital: ${result.starting_capital:>10,.2f}",
        f"  Ending capital  : ${result.ending_capital:>10,.2f}",
        f"  Total return    : {sign}{result.total_return_pct:>9.2f}%",
        f"  Max drawdown    : -{result.max_drawdown_pct:>8.2f}%",
        f"  Sharpe ratio    : {result.sharpe_ratio:>9.3f}",
        f"  Total trades    : {result.total_trades:>10}",
        f"  Winning         : {result.winning_trades:>10}",
        f"  Losing          : {result.losing_trades:>10}",
        f"  Still open      : {result.open_trades:>10}",
        f"  Win rate        : {result.win_rate*100:>9.1f}%",
        f"{'='*55}",
    ]
    return "\n".join(lines)
