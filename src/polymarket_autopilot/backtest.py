"""Backtesting engine for polymarket-autopilot.

Replays trading strategies against historical market snapshots stored in
the SQLite database. Uses the actual strategy classes (not simplified logic)
so backtest results reflect real strategy behaviour.

Produces performance metrics including return, win rate, max drawdown,
and Sharpe ratio.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from polymarket_autopilot.api import Market, Outcome
from polymarket_autopilot.db import (
    Database,
    MarketSnapshot,
    PaperTrade,
    STARTING_CAPITAL,
)
from polymarket_autopilot.strategies import (
    STRATEGIES,
    Strategy,
    TradeSignal,
    ExitSignal,
    get_strategy,
    signal_to_trade,
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
    opened_at: datetime | None = None
    closed_at: datetime | None = None


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
    profit_factor: float
    expectancy: float
    avg_return_per_trade_pct: float
    best_trade_pnl: float
    worst_trade_pnl: float
    avg_trade_duration_hours: float
    trades: list[BacktestTrade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# In-memory portfolio for backtest isolation
# ---------------------------------------------------------------------------


class _BacktestPortfolio:
    """Lightweight in-memory portfolio that mimics the Database interface
    required by Strategy classes, without touching the real database.

    This ensures backtests are fully isolated from live paper trading.
    """

    def __init__(self, starting_capital: float = 10_000.0) -> None:
        self.cash = starting_capital
        self.starting_capital = starting_capital
        self._open_trades: dict[str, BacktestTrade] = {}  # condition_id -> trade
        self._snapshots: dict[str, list[MarketSnapshot]] = {}  # condition_id -> snapshots

    # --- Methods that Strategy classes call via self.db ---

    def get_cash(self) -> float:
        return self.cash

    def get_portfolio_value(self) -> float:
        open_cost = sum(t.shares * t.entry_price for t in self._open_trades.values())
        return self.cash + open_cost

    def get_trade_by_condition(self, condition_id: str) -> PaperTrade | None:
        """Return a fake PaperTrade if we have an open position."""
        bt = self._open_trades.get(condition_id)
        if bt is None:
            return None
        # Return a minimal PaperTrade so strategies see an open position
        return PaperTrade(
            id=0,
            condition_id=bt.condition_id,
            question=bt.question,
            outcome=bt.outcome,
            strategy=bt.strategy,
            shares=bt.shares,
            entry_price=bt.entry_price,
            exit_price=None,
            take_profit=bt.entry_price * 1.15,
            stop_loss=bt.entry_price * 0.90,
            status="open",
            pnl=None,
            opened_at=datetime.now(timezone.utc),
            closed_at=None,
        )

    def get_open_trades(self) -> list[PaperTrade]:
        """Return all open positions as PaperTrade objects."""
        result: list[PaperTrade] = []
        for bt in self._open_trades.values():
            pt = self.get_trade_by_condition(bt.condition_id)
            if pt is not None:
                result.append(pt)
        return result

    def get_recent_snapshots(
        self, condition_id: str, n: int = 10
    ) -> list[MarketSnapshot]:
        """Return the N most recent snapshots for a market (chronological)."""
        snaps = self._snapshots.get(condition_id, [])
        return snaps[-n:]

    # --- Backtest-specific methods ---

    def record_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Append a snapshot to the in-memory history."""
        self._snapshots.setdefault(snapshot.condition_id, []).append(snapshot)

    def open_position(self, signal: TradeSignal) -> BacktestTrade:
        """Open a position from a trade signal."""
        snapshots = self._snapshots.get(signal.condition_id, [])
        opened_at = snapshots[-1].recorded_at if snapshots else datetime.now(timezone.utc)
        cost = signal.shares * signal.entry_price
        self.cash -= cost
        trade = BacktestTrade(
            condition_id=signal.condition_id,
            question=signal.question,
            outcome=signal.outcome,
            strategy=signal.strategy,
            entry_price=signal.entry_price,
            shares=signal.shares,
            opened_at=opened_at,
        )
        self._open_trades[signal.condition_id] = trade
        return trade

    def close_position(
        self, condition_id: str, exit_price: float, status: str
    ) -> BacktestTrade | None:
        """Close a position and return it."""
        trade = self._open_trades.pop(condition_id, None)
        if trade is None:
            return None
        trade.exit_price = exit_price
        trade.pnl = (exit_price - trade.entry_price) * trade.shares
        trade.status = status
        snapshots = self._snapshots.get(condition_id, [])
        trade.closed_at = snapshots[-1].recorded_at if snapshots else datetime.now(timezone.utc)
        self.cash += trade.shares * exit_price
        return trade

    @property
    def open_positions(self) -> dict[str, BacktestTrade]:
        return self._open_trades


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------


class Backtester:
    """Replay a strategy against historical snapshot data.

    Uses the actual strategy classes with an in-memory portfolio so
    backtest results reflect real strategy behaviour.

    Args:
        db: Database with historical snapshots.
        strategy_name: Name of the strategy to test (or 'all').
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
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        # Get all snapshots in the period, grouped by timestamp
        snapshots = self._get_snapshots_in_range(start_date, end_date)
        if not snapshots:
            return self._empty_result(start_date, end_date)

        # Group snapshots by approximate timestamp (within 120 seconds)
        batches = self._group_by_timestamp(snapshots)

        # Create isolated in-memory portfolio
        portfolio = _BacktestPortfolio(self.starting_capital)

        # Instantiate real strategy classes with the backtest portfolio as "db"
        strategy_names = (
            list(STRATEGIES.keys())
            if self.strategy_name.upper() == "ALL"
            else [self.strategy_name]
        )
        strategies: list[Strategy] = []
        for name in strategy_names:
            strategies.append(get_strategy(name, portfolio))  # type: ignore[arg-type]

        closed_trades: list[BacktestTrade] = []
        portfolio_values: list[float] = [self.starting_capital]

        for batch in batches:
            # Record snapshots to in-memory history
            for snap in batch:
                portfolio.record_snapshot(snap)

            # Build market objects from this batch
            markets = self._snapshots_to_markets(batch)
            market_map = {m.condition_id: m for m in markets.values()}

            # --- Check exits using real strategy logic ---
            for strat in strategies:
                exit_signals = strat.check_exits(market_map)
                for sig in exit_signals:
                    # Find the condition_id for this trade
                    for cid, bt in list(portfolio.open_positions.items()):
                        pt = portfolio.get_trade_by_condition(cid)
                        if pt is not None and pt.id == sig.trade_id:
                            closed = portfolio.close_position(
                                cid, sig.exit_price, sig.status
                            )
                            if closed:
                                closed_trades.append(closed)
                            break

            # --- Check entries using real strategy logic ---
            for strat in strategies:
                for market in markets.values():
                    signal = strat.evaluate(market)
                    if signal is None:
                        continue
                    # Verify we have enough cash
                    cost = signal.shares * signal.entry_price
                    if cost > portfolio.cash or cost <= 0:
                        continue
                    portfolio.open_position(signal)

            # Track portfolio value
            portfolio_values.append(portfolio.get_portfolio_value())

        # --- Calculate metrics ---
        all_closed = closed_trades
        still_open = list(portfolio.open_positions.values())
        all_trades = all_closed + still_open

        winning = [t for t in all_closed if t.pnl > 0]
        losing = [t for t in all_closed if t.pnl <= 0]

        ending_capital = portfolio_values[-1] if portfolio_values else self.starting_capital
        total_return = (
            (ending_capital - self.starting_capital) / self.starting_capital * 100
        )

        win_rate = len(winning) / len(all_closed) if all_closed else 0.0
        max_dd = self._max_drawdown(portfolio_values)
        sharpe = self._sharpe_ratio(portfolio_values)
        profit_factor = self._profit_factor(all_closed)
        expectancy = self._expectancy(all_closed)
        avg_return_per_trade_pct = self._avg_return_per_trade_pct(all_closed)
        best_trade_pnl = max((t.pnl for t in all_closed), default=0.0)
        worst_trade_pnl = min((t.pnl for t in all_closed), default=0.0)
        avg_trade_duration_hours = self._avg_trade_duration_hours(all_closed)

        display_name = (
            self.strategy_name
            if self.strategy_name.upper() != "ALL"
            else "ALL STRATEGIES"
        )

        return BacktestResult(
            strategy_name=display_name,
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
            open_trades=len(still_open),
            profit_factor=profit_factor,
            expectancy=expectancy,
            avg_return_per_trade_pct=avg_return_per_trade_pct,
            best_trade_pnl=best_trade_pnl,
            worst_trade_pnl=worst_trade_pnl,
            avg_trade_duration_hours=avg_trade_duration_hours,
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
                    recorded_at=(
                        datetime.fromisoformat(row[5])
                        if row[5]
                        else datetime.now(timezone.utc)
                    ),
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
            profit_factor=0.0,
            expectancy=0.0,
            avg_return_per_trade_pct=0.0,
            best_trade_pnl=0.0,
            worst_trade_pnl=0.0,
            avg_trade_duration_hours=0.0,
        )

    def _profit_factor(self, trades: list[BacktestTrade]) -> float:
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        if gross_loss == 0:
            return gross_profit if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def _expectancy(self, trades: list[BacktestTrade]) -> float:
        if not trades:
            return 0.0
        return sum(t.pnl for t in trades) / len(trades)

    def _avg_return_per_trade_pct(self, trades: list[BacktestTrade]) -> float:
        returns = [
            (t.pnl / (t.entry_price * t.shares) * 100)
            for t in trades
            if (t.entry_price * t.shares) > 0
        ]
        return sum(returns) / len(returns) if returns else 0.0

    def _avg_trade_duration_hours(self, trades: list[BacktestTrade]) -> float:
        durations = [
            (t.closed_at - t.opened_at).total_seconds() / 3600
            for t in trades
            if t.opened_at is not None and t.closed_at is not None
        ]
        return sum(durations) / len(durations) if durations else 0.0


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
        f"  Profit factor   : {result.profit_factor:>9.3f}",
        f"  Expectancy      : ${result.expectancy:>8.2f}",
        f"  Avg/trade       : {result.avg_return_per_trade_pct:>8.2f}%",
        f"  Best trade      : ${result.best_trade_pnl:>8.2f}",
        f"  Worst trade     : ${result.worst_trade_pnl:>8.2f}",
        f"  Avg duration    : {result.avg_trade_duration_hours:>8.2f}h",
        f"{'='*55}",
    ]
    return "\n".join(lines)
