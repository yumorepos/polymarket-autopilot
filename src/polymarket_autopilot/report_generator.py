"""Daily report generator for polymarket-autopilot.

Produces a formatted text summary of portfolio performance, trades,
and strategy breakdown. Designed for Telegram delivery.
"""

from __future__ import annotations

from datetime import datetime, timezone

from polymarket_autopilot.db import Database
from polymarket_autopilot.portfolio import PortfolioTracker


def generate_daily_report(db: Database) -> str:
    """Generate a daily P&L report as formatted text.

    Args:
        db: Database instance with trade and portfolio data.

    Returns:
        Markdown-formatted report string suitable for Telegram.
    """
    tracker = PortfolioTracker(db)
    report = tracker.get_report()

    # Get today's trades
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    all_trades = db.get_trade_history(limit=200, offset=0)

    today_opened = [
        t for t in all_trades if t.opened_at and t.opened_at >= today_start and t.status == "open"
    ]
    today_closed = [
        t for t in all_trades if t.closed_at and t.closed_at >= today_start and t.status != "open"
    ]

    today_pnl = sum(t.pnl or 0 for t in today_closed)

    # Strategy breakdown
    strategy_counts: dict[str, dict[str, int | float]] = {}
    for t in all_trades:
        if t.strategy not in strategy_counts:
            strategy_counts[t.strategy] = {"open": 0, "closed": 0, "pnl": 0.0}
        if t.status == "open":
            strategy_counts[t.strategy]["open"] += 1
        else:
            strategy_counts[t.strategy]["closed"] += 1
            strategy_counts[t.strategy]["pnl"] += t.pnl or 0

    # Top/worst positions (by unrealized movement from entry)
    open_trades = [t for t in all_trades if t.status == "open"]

    sign = "+" if report.total_return_pct >= 0 else ""
    today_sign = "+" if today_pnl >= 0 else ""

    lines = [
        "📊 **DAILY P&L REPORT**",
        f"_{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        f"💰 **Portfolio Value:** ${report.total_value:,.2f}",
        f"💵 **Cash:** ${report.cash:,.2f}",
        f"📈 **Total Return:** {sign}{report.total_return_pct:.2f}%",
        f"📉 **Realised P&L:** ${report.realised_pnl:+,.2f}",
        "",
        "**Today:**",
        f"  • Opened: {len(today_opened)} trades",
        f"  • Closed: {len(today_closed)} trades",
        f"  • Today P&L: {today_sign}${abs(today_pnl):,.2f}",
        "",
        f"**Positions:** {report.open_positions} open (${report.open_cost_basis:,.2f} deployed)",
        f"**Win Rate:** {report.win_rate * 100:.1f}% ({report.closed_trades} closed trades)",
    ]

    # Strategy breakdown
    if strategy_counts:
        lines.append("")
        lines.append("**Strategy Breakdown:**")
        for name, stats in sorted(strategy_counts.items()):
            lines.append(
                f"  • {name}: {stats['open']} open, "
                f"{stats['closed']} closed, "
                f"P&L: ${stats['pnl']:+,.2f}"
            )

    # Open positions summary
    if open_trades:
        lines.append("")
        lines.append(f"**Open Positions ({len(open_trades)}):**")
        for t in open_trades[:10]:  # Show max 10
            lines.append(f"  • {t.outcome} {t.question[:40]}… @ {t.entry_price:.3f}")
        if len(open_trades) > 10:
            lines.append(f"  _...and {len(open_trades) - 10} more_")

    return "\n".join(lines)
