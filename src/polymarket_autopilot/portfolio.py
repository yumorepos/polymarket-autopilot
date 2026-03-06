"""Portfolio tracker for polymarket-autopilot.

Provides high-level analytics on top of the raw trade data stored in the
database: P&L, win rate, strategy-level breakdowns, and open position summaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from polymarket_autopilot.db import Database, PaperTrade, STARTING_CAPITAL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Report data models
# ---------------------------------------------------------------------------


@dataclass
class StrategyStats:
    """Performance statistics for a single strategy.

    Attributes:
        name: Strategy identifier.
        total_trades: Total closed trades.
        wins: Trades that closed at take-profit.
        losses: Trades that closed at stop-loss.
        total_pnl: Sum of P&L across all closed trades.
        avg_pnl: Average P&L per closed trade.
        win_rate: Fraction of trades that were winners.
    """

    name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0


@dataclass
class PortfolioReport:
    """A full portfolio snapshot report.

    Attributes:
        cash: Current cash balance.
        open_positions: Number of open positions.
        open_cost_basis: Total cost of open positions.
        total_value: cash + open_cost_basis.
        starting_capital: Initial capital (always $10,000).
        total_return_pct: (total_value - starting_capital) / starting_capital * 100.
        realised_pnl: Sum of P&L from all closed trades.
        unrealised_pnl: Estimated P&L on open trades at current entry price (always 0 here,
            as we don't have live prices in the report).
        closed_trades: Total number of closed trades.
        win_rate: Overall win rate across all closed trades.
        strategy_stats: Per-strategy breakdown.
        open_trades: List of current open PaperTrade objects.
    """

    cash: float
    open_positions: int
    open_cost_basis: float
    total_value: float
    starting_capital: float
    total_return_pct: float
    realised_pnl: float
    unrealised_pnl: float
    closed_trades: int
    win_rate: float
    strategy_stats: dict[str, StrategyStats] = field(default_factory=dict)
    open_trades: list[PaperTrade] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Portfolio tracker
# ---------------------------------------------------------------------------


class PortfolioTracker:
    """Computes portfolio analytics from the database.

    Args:
        db: Database instance to query.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_report(self) -> PortfolioReport:
        """Generate a full portfolio report.

        Returns:
            PortfolioReport with current state and statistics.
        """
        cash = self.db.get_cash()
        open_trades = self.db.get_open_trades()
        all_trades = self.db.get_trade_history(limit=10_000)

        open_cost = sum(t.shares * t.entry_price for t in open_trades)
        total_value = cash + open_cost

        closed = [t for t in all_trades if t.status != "open"]
        realised_pnl = sum(t.pnl or 0.0 for t in closed)
        wins = [t for t in closed if (t.pnl or 0.0) > 0]
        win_rate = len(wins) / len(closed) if closed else 0.0

        total_return_pct = (
            (total_value - STARTING_CAPITAL) / STARTING_CAPITAL * 100
        )

        strategy_stats = _compute_strategy_stats(closed)

        return PortfolioReport(
            cash=cash,
            open_positions=len(open_trades),
            open_cost_basis=open_cost,
            total_value=total_value,
            starting_capital=STARTING_CAPITAL,
            total_return_pct=total_return_pct,
            realised_pnl=realised_pnl,
            unrealised_pnl=0.0,
            closed_trades=len(closed),
            win_rate=win_rate,
            strategy_stats=strategy_stats,
            open_trades=open_trades,
        )

    def get_open_positions_summary(self) -> list[dict[str, object]]:
        """Return a list of dicts summarising each open position.

        Returns:
            List of position summary dicts with keys:
            id, condition_id, question, outcome, strategy, shares,
            entry_price, take_profit, stop_loss, cost, opened_at.
        """
        return [
            {
                "id": t.id,
                "condition_id": t.condition_id,
                "question": t.question[:60],
                "outcome": t.outcome,
                "strategy": t.strategy,
                "shares": t.shares,
                "entry_price": t.entry_price,
                "take_profit": t.take_profit,
                "stop_loss": t.stop_loss,
                "cost": round(t.shares * t.entry_price, 4),
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            }
            for t in self.db.get_open_trades()
        ]

    def net_pnl(self) -> float:
        """Return total realised P&L across all closed trades.

        Returns:
            Summed P&L in USD.
        """
        trades = self.db.get_trade_history(limit=10_000)
        return sum(t.pnl or 0.0 for t in trades if t.status != "open")

    def win_rate(self) -> float:
        """Return win rate as a fraction (0–1) across all closed trades.

        Returns:
            Win rate, or 0.0 if no closed trades.
        """
        trades = self.db.get_trade_history(limit=10_000)
        closed = [t for t in trades if t.status != "open"]
        if not closed:
            return 0.0
        wins = [t for t in closed if (t.pnl or 0.0) > 0]
        return len(wins) / len(closed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_strategy_stats(closed_trades: list[PaperTrade]) -> dict[str, StrategyStats]:
    """Aggregate per-strategy statistics from closed trades.

    Args:
        closed_trades: All non-open trades.

    Returns:
        Mapping of strategy name -> StrategyStats.
    """
    stats: dict[str, StrategyStats] = {}
    for trade in closed_trades:
        s = stats.setdefault(trade.strategy, StrategyStats(name=trade.strategy))
        s.total_trades += 1
        pnl = trade.pnl or 0.0
        s.total_pnl += pnl
        if pnl > 0:
            s.wins += 1
        else:
            s.losses += 1

    for s in stats.values():
        s.avg_pnl = s.total_pnl / s.total_trades if s.total_trades else 0.0
        s.win_rate = s.wins / s.total_trades if s.total_trades else 0.0

    return stats
