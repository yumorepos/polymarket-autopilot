"""Daily report generator for polymarket-autopilot.

Generates human-readable Markdown summaries of portfolio performance,
trade activity, and strategy breakdowns suitable for display in a
terminal or forwarding to a messaging service like Telegram.

Usage
-----
From Python::

    from polymarket_autopilot.report_generator import generate_daily_report
    from polymarket_autopilot.db import Database

    db = Database()
    print(generate_daily_report(db))

CLI::

    polymarket-autopilot daily-report
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from polymarket_autopilot.db import Database, PaperTrade, STARTING_CAPITAL
from polymarket_autopilot.portfolio import PortfolioTracker, StrategyStats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DailyReport:
    """Structured data for a daily portfolio report.

    Attributes:
        generated_at: Timestamp when the report was created.
        cash: Current cash balance.
        portfolio_value: Total portfolio value (cash + open positions).
        open_positions_count: Number of currently open positions.
        starting_capital: Initial capital (typically $10,000).
        overall_pnl: Total realised P&L across all history.
        overall_return_pct: Return percentage since inception.
        today_opened: Trades opened since midnight UTC today.
        today_closed: Trades closed since midnight UTC today.
        today_pnl: Sum of P&L from today's closed trades.
        strategy_stats: Per-strategy performance breakdown.
        top_positions: Up to 3 best-performing open positions (by cost basis).
        worst_positions: Up to 3 worst-performing open positions (by cost basis).
        win_rate: Overall win rate across all closed trades.
        closed_trades_total: Total number of closed trades ever.
    """

    generated_at: datetime
    cash: float
    portfolio_value: float
    open_positions_count: int
    starting_capital: float
    overall_pnl: float
    overall_return_pct: float
    today_opened: list[PaperTrade]
    today_closed: list[PaperTrade]
    today_pnl: float
    strategy_stats: dict[str, StrategyStats]
    top_positions: list[PaperTrade]
    worst_positions: list[PaperTrade]
    win_rate: float
    closed_trades_total: int


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate_daily_report(db: Database) -> str:
    """Generate a Markdown-formatted daily report string.

    Queries the database for current portfolio state and recent trade
    activity, then formats everything into clean Markdown suitable for
    display in Telegram, a terminal, or a log file.

    Args:
        db: Database instance to query.

    Returns:
        Multi-line Markdown string with the full report.
    """
    report = _build_report(db)
    return _format_report(report)


def _build_report(db: Database) -> DailyReport:
    """Collect all data needed for the report.

    Args:
        db: Database instance.

    Returns:
        Populated DailyReport dataclass.
    """
    tracker = PortfolioTracker(db)
    portfolio = tracker.get_report()

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Split trades into today vs historical
    today_opened: list[PaperTrade] = []
    today_closed: list[PaperTrade] = []
    today_pnl = 0.0

    all_trades = db.get_trade_history(limit=10_000)
    for trade in all_trades:
        if trade.opened_at and trade.opened_at >= today_start:
            today_opened.append(trade)
        if trade.closed_at and trade.closed_at >= today_start and trade.status != "open":
            today_closed.append(trade)
            today_pnl += trade.pnl or 0.0

    # Sort open positions by cost (shares * entry_price) for top/worst
    open_trades = portfolio.open_trades
    open_trades_sorted = sorted(
        open_trades,
        key=lambda t: t.shares * t.entry_price,
        reverse=True,
    )
    top_positions = open_trades_sorted[:3]
    worst_positions = list(reversed(open_trades_sorted))[:3]

    return DailyReport(
        generated_at=now,
        cash=portfolio.cash,
        portfolio_value=portfolio.total_value,
        open_positions_count=portfolio.open_positions,
        starting_capital=portfolio.starting_capital,
        overall_pnl=portfolio.realised_pnl,
        overall_return_pct=portfolio.total_return_pct,
        today_opened=today_opened,
        today_closed=today_closed,
        today_pnl=today_pnl,
        strategy_stats=portfolio.strategy_stats,
        top_positions=top_positions,
        worst_positions=worst_positions,
        win_rate=portfolio.win_rate,
        closed_trades_total=portfolio.closed_trades,
    )


def _format_report(r: DailyReport) -> str:
    """Format a DailyReport into a Markdown string.

    Args:
        r: The populated DailyReport.

    Returns:
        Markdown-formatted text report.
    """
    lines: list[str] = []
    ts = r.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    # Header
    lines.append(f"# 📊 Daily Report — {r.generated_at.strftime('%Y-%m-%d')}")
    lines.append(f"*Generated at {ts}*")
    lines.append("")

    # Portfolio summary
    ret_sign = "+" if r.overall_return_pct >= 0 else ""
    lines.append("## Portfolio")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| 💵 Cash | ${r.cash:,.2f} |")
    lines.append(f"| 📈 Portfolio value | ${r.portfolio_value:,.2f} |")
    lines.append(f"| 📂 Open positions | {r.open_positions_count} |")
    lines.append(f"| 💰 Overall P&L | ${r.overall_pnl:+,.2f} |")
    lines.append(f"| 📉 Return since inception | {ret_sign}{r.overall_return_pct:.2f}% |")
    lines.append(f"| 🏆 Win rate | {r.win_rate * 100:.1f}% ({r.closed_trades_total} closed) |")
    lines.append("")

    # Today's activity
    today_sign = "+" if r.today_pnl >= 0 else ""
    lines.append("## Today's Activity")
    lines.append(f"- Trades opened: **{len(r.today_opened)}**")
    lines.append(f"- Trades closed: **{len(r.today_closed)}**")
    lines.append(f"- Today P&L: **${r.today_pnl:+,.2f}**")

    if r.today_closed:
        lines.append("")
        lines.append("**Closed today:**")
        for t in r.today_closed:
            pnl_str = f"${t.pnl:+,.2f}" if t.pnl is not None else "—"
            outcome_emoji = "✅" if (t.pnl or 0) > 0 else "❌"
            q = (t.question[:55] + "…") if len(t.question) > 55 else t.question
            lines.append(
                f"  {outcome_emoji} [{t.strategy}] {t.outcome} *{q}* — {pnl_str}"
            )

    if r.today_opened:
        lines.append("")
        lines.append("**Opened today:**")
        for t in r.today_opened:
            q = (t.question[:55] + "…") if len(t.question) > 55 else t.question
            cost = t.shares * t.entry_price
            lines.append(
                f"  🔵 [{t.strategy}] {t.outcome} *{q}* @ {t.entry_price:.3f} (${cost:.2f})"
            )

    lines.append("")

    # Strategy breakdown
    if r.strategy_stats:
        # Sort by total P&L descending
        sorted_strategies = sorted(
            r.strategy_stats.values(),
            key=lambda s: s.total_pnl,
            reverse=True,
        )
        best = sorted_strategies[0]
        lines.append("## Strategy Breakdown")
        lines.append(f"| Strategy | Trades | Win% | P&L |")
        lines.append(f"|----------|--------|------|-----|")
        for s in sorted_strategies:
            medal = " 🥇" if s.name == best.name else ""
            lines.append(
                f"| {s.name}{medal} | {s.total_trades} | {s.win_rate*100:.0f}% | ${s.total_pnl:+,.2f} |"
            )
        lines.append("")
        lines.append(f"*Best performing strategy: **{best.name}** (${best.total_pnl:+,.2f})*")
        lines.append("")

    # Top performing positions
    if r.top_positions:
        lines.append("## Top 3 Open Positions (by cost)")
        for i, t in enumerate(r.top_positions, 1):
            cost = t.shares * t.entry_price
            q = (t.question[:50] + "…") if len(t.question) > 50 else t.question
            lines.append(
                f"{i}. **[{t.strategy}]** {t.outcome} *{q}*  \n"
                f"   Entry: {t.entry_price:.3f} | Shares: {t.shares:.4f} | Cost: ${cost:.2f}  \n"
                f"   TP: {t.take_profit:.3f} | SL: {t.stop_loss:.3f}"
            )
        lines.append("")

    # Worst performing positions (by unrealised loss potential)
    if r.worst_positions and r.worst_positions != r.top_positions:
        lines.append("## Bottom 3 Open Positions (by cost)")
        for i, t in enumerate(r.worst_positions, 1):
            cost = t.shares * t.entry_price
            q = (t.question[:50] + "…") if len(t.question) > 50 else t.question
            lines.append(
                f"{i}. **[{t.strategy}]** {t.outcome} *{q}*  \n"
                f"   Entry: {t.entry_price:.3f} | Shares: {t.shares:.4f} | Cost: ${cost:.2f}  \n"
                f"   TP: {t.take_profit:.3f} | SL: {t.stop_loss:.3f}"
            )
        lines.append("")

    lines.append("---")
    lines.append("*polymarket-autopilot paper trading bot*")

    return "\n".join(lines)
