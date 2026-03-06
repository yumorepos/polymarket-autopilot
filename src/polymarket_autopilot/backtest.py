"""Backtesting engine for polymarket-autopilot.

Replays trading strategies against historical market_snapshots stored in the
SQLite database and reports performance metrics.

Usage
-----
From Python::

    from polymarket_autopilot.backtest import Backtester
    from polymarket_autopilot.db import Database

    db = Database()
    bt = Backtester(db, strategy_name="TAIL", days=7, starting_capital=10_000.0)
    result = bt.run()
    result.print_summary()

CLI::

    polymarket-autopilot backtest --strategy TAIL --days 7
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from polymarket_autopilot.api import Market, Outcome
from polymarket_autopilot.db import Database, MarketSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    """Performance metrics produced by a backtest run.

    Attributes:
        strategy_name: Strategy that was tested.
        period: Human-readable description of the date range.
        starting_capital: Capital at the start of the simulation.
        ending_capital: Simulated capital at end of the simulation.
        total_return_pct: (ending - starting) / starting * 100.
        win_rate: Fraction of closed trades that were profitable.
        max_drawdown: Maximum peak-to-trough capital decline (as a fraction).
        sharpe_ratio: Simplified ratio: mean_return / std_return (0 if no trades).
        trades_count: Total number of simulated trades opened.
        closed_trades: Number of trades that were closed during the period.
        snapshots_processed: Number of snapshot batches evaluated.
    """

    strategy_name: str
    period: str
    starting_capital: float
    ending_capital: float
    total_return_pct: float
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    trades_count: int
    closed_trades: int
    snapshots_processed: int
    trade_pnls: list[float] = field(default_factory=list)

    def print_summary(self) -> None:
        """Print a formatted summary of backtest results to stdout."""
        sign = "+" if self.total_return_pct >= 0 else ""
        print(f"\n{'='*56}")
        print(f"  BACKTEST RESULTS — {self.strategy_name}")
        print(f"{'='*56}")
        print(f"  Period             : {self.period}")
        print(f"  Starting capital   : ${self.starting_capital:>10,.2f}")
        print(f"  Ending capital     : ${self.ending_capital:>10,.2f}")
        print(f"  Total return       : {sign}{self.total_return_pct:>9.2f}%")
        print(f"  Win rate           : {self.win_rate * 100:>9.1f}%")
        print(f"  Max drawdown       : {self.max_drawdown * 100:>9.2f}%")
        print(f"  Sharpe ratio       : {self.sharpe_ratio:>10.3f}")
        print(f"  Trades opened      : {self.trades_count:>10}")
        print(f"  Trades closed      : {self.closed_trades:>10}")
        print(f"  Snapshot batches   : {self.snapshots_processed:>10}")
        print(f"{'='*56}\n")


# ---------------------------------------------------------------------------
# Internal simulation models
# ---------------------------------------------------------------------------


@dataclass
class _SimPosition:
    """An open simulated position tracked during backtesting."""

    condition_id: str
    question: str
    outcome: str
    entry_price: float
    shares: float
    take_profit: float
    stop_loss: float
    cost: float


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------


class Backtester:
    """Replay a trading strategy against historical snapshot data.

    The backtester works by:
    1. Loading all market snapshots within the requested date range from the DB.
    2. Grouping them by timestamp (each group = one "tick").
    3. For each tick, reconstructing synthetic Market objects from the snapshots.
    4. Running the strategy's ``evaluate()`` logic on each market.
    5. Checking open positions for take-profit / stop-loss exits.
    6. Tracking a paper portfolio (cash + open positions).

    Args:
        db: Database instance containing market_snapshots.
        strategy_name: Name of the strategy to test (e.g. ``"TAIL"``).
        days: Number of past days of snapshot history to replay.
        starting_capital: Initial cash for the simulation.
        start_dt: Explicit start datetime (overrides ``days`` if provided).
        end_dt: Explicit end datetime (defaults to now if not provided).
    """

    def __init__(
        self,
        db: Database,
        strategy_name: str,
        days: int = 7,
        starting_capital: float = 10_000.0,
        start_dt: datetime | None = None,
        end_dt: datetime | None = None,
    ) -> None:
        self.db = db
        self.strategy_name = strategy_name.upper()
        self.starting_capital = starting_capital

        now = datetime.utcnow()
        self.end_dt = end_dt or now
        self.start_dt = start_dt or (now - timedelta(days=days))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> BacktestResult:
        """Execute the backtest and return performance metrics.

        Returns:
            BacktestResult populated with all computed metrics.
        """
        from polymarket_autopilot.strategies import get_strategy

        # Load all snapshots in range, grouped by timestamp bucket
        ticks = self._load_ticks()
        logger.info(
            "Backtesting %s over %d tick(s) from %s to %s",
            self.strategy_name,
            len(ticks),
            self.start_dt.date(),
            self.end_dt.date(),
        )

        if not ticks:
            logger.warning("No snapshot data found for the requested period.")
            return self._empty_result()

        # Paper portfolio state
        cash = self.starting_capital
        positions: dict[str, _SimPosition] = {}
        capital_history: list[float] = [cash]
        trade_pnls: list[float] = []
        trades_opened = 0

        # We create a fake DB backed by the simulation state for strategy calls
        sim_db = _SimDatabase(self.db)

        strategy = get_strategy(self.strategy_name, sim_db)  # type: ignore[arg-type]

        for tick_snapshots in ticks:
            markets = _snapshots_to_markets(tick_snapshots)
            market_map = {m.condition_id: m for m in markets}

            # 1. Check exits for open positions
            for cid in list(positions.keys()):
                pos = positions[cid]
                market = market_map.get(cid)
                if market is None:
                    continue

                current_price = _price_for_outcome(market, pos.outcome)
                if current_price is None:
                    continue

                if current_price >= pos.take_profit:
                    pnl = (current_price - pos.entry_price) * pos.shares
                    proceeds = pos.shares * current_price
                    cash += proceeds
                    trade_pnls.append(pnl)
                    logger.debug(
                        "TP hit: %s  pnl=%.4f", cid[:8], pnl
                    )
                    del positions[cid]
                    sim_db.remove_open_position(cid)

                elif current_price <= pos.stop_loss:
                    pnl = (current_price - pos.entry_price) * pos.shares
                    proceeds = pos.shares * current_price
                    cash += proceeds
                    trade_pnls.append(pnl)
                    logger.debug(
                        "SL hit: %s  pnl=%.4f", cid[:8], pnl
                    )
                    del positions[cid]
                    sim_db.remove_open_position(cid)

            # 2. Evaluate entries
            portfolio_value = cash + sum(
                p.shares * p.entry_price for p in positions.values()
            )
            sim_db.set_state(cash=cash, portfolio_value=portfolio_value)

            for market in markets:
                if market.condition_id in positions:
                    continue
                signal = strategy.evaluate(market)
                if signal is None:
                    continue

                cost = signal.shares * signal.entry_price
                if cost > cash:
                    continue  # Not enough cash

                cash -= cost
                pos = _SimPosition(
                    condition_id=signal.condition_id,
                    question=signal.question,
                    outcome=signal.outcome,
                    entry_price=signal.entry_price,
                    shares=signal.shares,
                    take_profit=signal.take_profit,
                    stop_loss=signal.stop_loss,
                    cost=cost,
                )
                positions[signal.condition_id] = pos
                sim_db.add_open_position(signal.condition_id)
                trades_opened += 1
                logger.debug(
                    "Opened %s %s @ %.3f  cost=%.2f",
                    signal.outcome,
                    signal.condition_id[:8],
                    signal.entry_price,
                    cost,
                )

            # Track capital including open position cost basis
            total_value = cash + sum(p.cost for p in positions.values())
            capital_history.append(total_value)

        # Mark all remaining open positions as closed at last known price
        for cid, pos in positions.items():
            # Use entry price as exit (no final price available)
            cash += pos.cost  # Refund cost basis (neutral P&L)
            trade_pnls.append(0.0)

        return self._compute_result(
            ending_capital=cash + sum(p.cost for p in positions.values()),
            capital_history=capital_history,
            trade_pnls=trade_pnls,
            trades_opened=trades_opened,
            closed_trades=len([p for p in trade_pnls if p != 0.0]),
            ticks_processed=len(ticks),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_ticks(self) -> list[list[MarketSnapshot]]:
        """Load snapshots from the DB, grouped into per-timestamp buckets.

        Returns:
            List of snapshot groups ordered chronologically.
        """
        with self.db._connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT id, condition_id, yes_price, no_price, volume, recorded_at
                FROM market_snapshots
                WHERE recorded_at >= ? AND recorded_at <= ?
                ORDER BY recorded_at ASC
                """,
                (
                    self.start_dt.isoformat(),
                    self.end_dt.isoformat(),
                ),
            ).fetchall()

        if not rows:
            return []

        # Group by minute-level timestamp bucket for manageable tick size
        buckets: dict[str, list[MarketSnapshot]] = {}
        for row in rows:
            ts_raw: str = row["recorded_at"]
            # Round down to nearest minute
            bucket_key = ts_raw[:16]  # "YYYY-MM-DDTHH:MM"
            snap = MarketSnapshot(
                id=row["id"],
                condition_id=row["condition_id"],
                yes_price=float(row["yes_price"]),
                no_price=float(row["no_price"]),
                volume=float(row["volume"]),
                recorded_at=datetime.fromisoformat(ts_raw) if ts_raw else datetime.utcnow(),
            )
            buckets.setdefault(bucket_key, []).append(snap)

        # Use latest snapshot per market per bucket (deduplicate)
        ticks: list[list[MarketSnapshot]] = []
        for bucket_snaps in buckets.values():
            # Keep latest snapshot per condition_id in this bucket
            latest: dict[str, MarketSnapshot] = {}
            for s in bucket_snaps:
                existing = latest.get(s.condition_id)
                if existing is None or s.recorded_at > existing.recorded_at:
                    latest[s.condition_id] = s
            ticks.append(list(latest.values()))

        return ticks

    def _compute_result(
        self,
        ending_capital: float,
        capital_history: list[float],
        trade_pnls: list[float],
        trades_opened: int,
        closed_trades: int,
        ticks_processed: int,
    ) -> BacktestResult:
        """Compute final BacktestResult from simulation state.

        Args:
            ending_capital: Final simulated capital.
            capital_history: Capital value at each tick.
            trade_pnls: P&L for each closed trade.
            trades_opened: Total number of trades entered.
            closed_trades: Number of trades that hit TP or SL.
            ticks_processed: Number of snapshot ticks evaluated.

        Returns:
            Populated BacktestResult.
        """
        total_return_pct = (
            (ending_capital - self.starting_capital) / self.starting_capital * 100
        )

        wins = [p for p in trade_pnls if p > 0]
        win_rate = len(wins) / len(trade_pnls) if trade_pnls else 0.0

        max_drawdown = _max_drawdown(capital_history)
        sharpe = _sharpe_ratio(capital_history)

        period = (
            f"{self.start_dt.strftime('%Y-%m-%d')} → {self.end_dt.strftime('%Y-%m-%d')}"
        )

        return BacktestResult(
            strategy_name=self.strategy_name,
            period=period,
            starting_capital=self.starting_capital,
            ending_capital=ending_capital,
            total_return_pct=total_return_pct,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            trades_count=trades_opened,
            closed_trades=closed_trades,
            snapshots_processed=ticks_processed,
            trade_pnls=trade_pnls,
        )

    def _empty_result(self) -> BacktestResult:
        """Return a zeroed BacktestResult when no data is available."""
        period = (
            f"{self.start_dt.strftime('%Y-%m-%d')} → {self.end_dt.strftime('%Y-%m-%d')}"
        )
        return BacktestResult(
            strategy_name=self.strategy_name,
            period=period,
            starting_capital=self.starting_capital,
            ending_capital=self.starting_capital,
            total_return_pct=0.0,
            win_rate=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            trades_count=0,
            closed_trades=0,
            snapshots_processed=0,
        )


# ---------------------------------------------------------------------------
# Simulation DB shim
# ---------------------------------------------------------------------------


class _SimDatabase:
    """Thin wrapper around a real Database that overrides portfolio state.

    Strategies call ``db.get_cash()``, ``db.get_portfolio_value()``, and
    ``db.get_trade_by_condition()`` during ``evaluate()``.  This shim
    intercepts those calls and returns the simulated values so strategies
    behave consistently during replay.

    Args:
        real_db: The actual Database used for snapshot reads.
    """

    def __init__(self, real_db: Database) -> None:
        self._real_db = real_db
        self._cash: float = 10_000.0
        self._portfolio_value: float = 10_000.0
        self._open_condition_ids: set[str] = set()

    def set_state(self, cash: float, portfolio_value: float) -> None:
        """Update the simulated portfolio state.

        Args:
            cash: Current simulated cash balance.
            portfolio_value: Current simulated total portfolio value.
        """
        self._cash = cash
        self._portfolio_value = portfolio_value

    def add_open_position(self, condition_id: str) -> None:
        """Register a condition ID as having an open position.

        Args:
            condition_id: Market condition ID to mark as open.
        """
        self._open_condition_ids.add(condition_id)

    def remove_open_position(self, condition_id: str) -> None:
        """Deregister a condition ID from open positions.

        Args:
            condition_id: Market condition ID to mark as closed.
        """
        self._open_condition_ids.discard(condition_id)

    # --- Forwarded strategy calls ---

    def get_cash(self) -> float:
        """Return simulated cash balance."""
        return self._cash

    def get_portfolio_value(self) -> float:
        """Return simulated portfolio value."""
        return self._portfolio_value

    def get_trade_by_condition(self, condition_id: str) -> Any:
        """Return a truthy placeholder if the condition is already open.

        Args:
            condition_id: Market condition ID to check.

        Returns:
            A non-None sentinel if the position is open, else None.
        """
        if condition_id in self._open_condition_ids:
            return True  # Non-None sentinel — strategy will skip
        return None

    def get_recent_snapshots(self, condition_id: str, n: int = 10) -> list[MarketSnapshot]:
        """Delegate to the real database for snapshot reads.

        Args:
            condition_id: Market condition ID.
            n: Number of recent snapshots to return.

        Returns:
            List of MarketSnapshot objects.
        """
        return self._real_db.get_recent_snapshots(condition_id, n)

    def _connect(self) -> Any:
        """Delegate connection to the real database (not used in simulation)."""
        return self._real_db._connect()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _max_drawdown(capital_history: list[float]) -> float:
    """Compute the maximum peak-to-trough drawdown from a capital series.

    Args:
        capital_history: List of portfolio values over time.

    Returns:
        Maximum drawdown as a fraction (0.0–1.0). Returns 0.0 if empty.
    """
    if len(capital_history) < 2:
        return 0.0

    peak = capital_history[0]
    max_dd = 0.0
    for value in capital_history[1:]:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _sharpe_ratio(capital_history: list[float]) -> float:
    """Compute a simplified Sharpe ratio: mean_return / std_return.

    Args:
        capital_history: List of portfolio values over time.

    Returns:
        Sharpe ratio, or 0.0 if insufficient data or zero variance.
    """
    if len(capital_history) < 3:
        return 0.0

    returns = [
        (capital_history[i] - capital_history[i - 1]) / capital_history[i - 1]
        for i in range(1, len(capital_history))
        if capital_history[i - 1] > 0
    ]

    if not returns:
        return 0.0

    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / n
    std = math.sqrt(variance)

    if std == 0:
        return 0.0
    return mean / std


# ---------------------------------------------------------------------------
# Market reconstruction helpers
# ---------------------------------------------------------------------------


def _snapshots_to_markets(snapshots: list[MarketSnapshot]) -> list[Market]:
    """Convert a list of MarketSnapshot objects into synthetic Market objects.

    Args:
        snapshots: Snapshots from a single tick.

    Returns:
        List of Market instances usable by strategy ``evaluate()``.
    """
    markets: list[Market] = []
    for snap in snapshots:
        market = Market(
            condition_id=snap.condition_id,
            question=snap.condition_id,  # Question unavailable in snapshots
            outcomes=[
                Outcome(name="YES", price=snap.yes_price),
                Outcome(name="NO", price=snap.no_price),
            ],
            volume=snap.volume,
            end_date=None,
            active=True,
            closed=False,
        )
        markets.append(market)
    return markets


def _price_for_outcome(market: Market, outcome: str) -> float | None:
    """Return the current price for a given outcome in a market.

    Args:
        market: Market to query.
        outcome: ``"YES"`` or ``"NO"``.

    Returns:
        Price as a float, or None if not found.
    """
    for o in market.outcomes:
        if o.name.upper() == outcome.upper():
            return o.price
    return None
