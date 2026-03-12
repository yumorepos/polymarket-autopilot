"""CLI entry point for polymarket-autopilot.

Commands
--------
init      — Initialise the database with starting capital.
scan      — Fetch active markets, record snapshots, print opportunities.
trade     — Run a full scan-and-trade cycle using the selected strategy.
report    — Print a portfolio performance summary.
history   — Show trade history (open + closed).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import click

from dotenv import load_dotenv
from polymarket_autopilot.api import PolymarketAPIError, PolymarketClient
from polymarket_autopilot.backtest import (
    Backtester,
    compare_strategies,
    format_backtest_result,
    format_strategy_comparison,
)
from polymarket_autopilot.db import DEFAULT_DB_PATH, Database, MarketSnapshot
from polymarket_autopilot.demo import load_demo_data
from polymarket_autopilot.portfolio import PortfolioTracker
from polymarket_autopilot.report_generator import generate_daily_report
from polymarket_autopilot.risk import RiskConfig, check_entry_risk
from polymarket_autopilot.strategies import (
    STRATEGIES,
    Strategy,
    get_strategy,
    list_strategies,
    signal_to_trade,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Shared context
# ---------------------------------------------------------------------------


def _get_db(db_path: str) -> Database:
    return Database(Path(db_path))


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


def _resolve_strategies(strategy: str, db: Database) -> tuple[list[str], list[Strategy]]:
    strategy_names = list(STRATEGIES.keys()) if strategy.upper() == "ALL" else [strategy.upper()]
    try:
        strategies = [get_strategy(name, db) for name in strategy_names]
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return strategy_names, strategies


@click.group()
@click.option(
    "--db",
    default=str(DEFAULT_DB_PATH),
    show_default=True,
    envvar="AUTOPILOT_DB",
    help="Path to the SQLite database file.",
)
@click.option(
    "--log-level",
    default="WARNING",
    show_default=True,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Logging verbosity.",
)
@click.pass_context
def cli(ctx: click.Context, db: str, log_level: str) -> None:
    """Polymarket Autopilot — paper trading bot for prediction markets."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db


@cli.command(name="strategies")
def strategies_cmd() -> None:
    """List available strategies and their metadata."""
    click.echo("\nAvailable strategies:")
    for meta in list_strategies():
        click.echo(
            f"- {meta.name:<18} risk={meta.risk_profile:<8} "
            f"holding={meta.holding_style:<12} signal={meta.signal_family}"
        )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialise the database with $10,000 starting capital."""
    db = _get_db(ctx.obj["db_path"])
    db.init()
    cash = db.get_cash()
    click.echo(f"Database initialised at: {ctx.obj['db_path']}")
    click.echo(f"Starting cash balance:   ${cash:,.2f}")


@cli.command(name="demo-setup")
@click.pass_context
def demo_setup(ctx: click.Context) -> None:
    """Load deterministic offline demo data into the configured database."""
    db = _get_db(ctx.obj["db_path"])
    result = load_demo_data(db)
    click.echo(f"Demo data loaded into: {ctx.obj['db_path']}")
    click.echo(
        f"Snapshots: {result.snapshot_count} | Trades: {result.trade_count} "
        f"(closed={result.closed_trade_count}, open={result.open_trade_count})"
    )


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--strategy",
    default="TAIL",
    show_default=True,
    help="Strategy to evaluate (or 'all' for all strategies).",
)
@click.option("--max-pages", default=3, show_default=True, help="Max API pages to fetch.")
@click.option(
    "--record/--no-record",
    default=True,
    show_default=True,
    help="Record market snapshots to the database.",
)
@click.pass_context
def scan(ctx: click.Context, strategy: str, max_pages: int, record: bool) -> None:
    """Scan active markets and display trade signals (no trades executed)."""
    db = _get_db(ctx.obj["db_path"])

    strategy_names, strategies = _resolve_strategies(strategy, db)

    async def _run() -> None:
        async with PolymarketClient() as client:
            click.echo(f"Fetching markets (up to {max_pages} pages)…")
            try:
                markets = await client.get_all_active_markets(max_pages=max_pages)
            except PolymarketAPIError as exc:
                raise click.ClickException(f"Market fetch failed: {exc}") from exc
            click.echo(f"Found {len(markets)} active markets.")
            click.echo(f"Running {len(strategies)} strategy(ies): {', '.join(strategy_names)}")

            # Record snapshots first
            for market in markets:
                if record and market.yes_price is not None:
                    db.record_snapshot(
                        MarketSnapshot(
                            id=None,
                            condition_id=market.condition_id,
                            yes_price=market.yes_price or 0.0,
                            no_price=market.no_price or 0.0,
                            volume=market.volume,
                            recorded_at=datetime.now(timezone.utc),
                        )
                    )

            # Evaluate all strategies
            signals = []
            for strat in strategies:
                for market in markets:
                    signal = strat.evaluate(market)
                    if signal:
                        signals.append(signal)

            if not signals:
                click.echo("No signals generated.")
                return

            click.echo(f"\n{'=' * 70}")
            click.echo(f"  {len(signals)} signal(s) from {len(strategies)} strategy(ies)")
            click.echo(f"{'=' * 70}")
            for s in signals:
                click.echo(
                    f"\n  [{s.strategy}]"
                    f"\n  Market : {s.question[:65]}"
                    f"\n  Outcome: {s.outcome}  @ {s.entry_price:.3f}"
                    f"\n  Shares : {s.shares:.4f}  (cost ${s.shares * s.entry_price:.2f})"
                    f"\n  TP     : {s.take_profit:.3f}  SL: {s.stop_loss:.3f}"
                    f"\n  Reason : {s.reason}"
                )
            click.echo(f"\n{'=' * 70}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# trade
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--strategy",
    default="TAIL",
    show_default=True,
    help="Strategy to run (or 'all' for all strategies).",
)
@click.option("--max-pages", default=3, show_default=True, help="Max API pages to fetch.")
@click.option("--dry-run", is_flag=True, help="Show what would be traded without executing.")
@click.option(
    "--max-positions",
    default=12,
    show_default=True,
    type=int,
    help="Max concurrent open positions.",
)
@click.option(
    "--max-exposure-market",
    default=800.0,
    show_default=True,
    type=float,
    help="Max USD exposure per market.",
)
@click.option(
    "--max-exposure-strategy",
    default=2500.0,
    show_default=True,
    type=float,
    help="Max USD exposure per strategy.",
)
@click.option(
    "--max-trade-cost",
    default=400.0,
    show_default=True,
    type=float,
    help="Max USD allocated per trade.",
)
@click.option(
    "--cash-buffer",
    default=1000.0,
    show_default=True,
    type=float,
    help="Minimum cash that must remain after a trade.",
)
@click.pass_context
def trade(
    ctx: click.Context,
    strategy: str,
    max_pages: int,
    dry_run: bool,
    max_positions: int,
    max_exposure_market: float,
    max_exposure_strategy: float,
    max_trade_cost: float,
    cash_buffer: float,
) -> None:
    """Run a full scan-and-trade cycle: record snapshots, open/close positions."""
    db = _get_db(ctx.obj["db_path"])

    strategy_names, strategies = _resolve_strategies(strategy, db)
    risk = RiskConfig(
        max_positions=max_positions,
        max_exposure_per_market=max_exposure_market,
        max_exposure_per_strategy=max_exposure_strategy,
        max_trade_cost=max_trade_cost,
        min_cash_buffer=cash_buffer,
    )

    async def _run() -> None:
        async with PolymarketClient() as client:
            click.echo("Fetching markets…")
            try:
                markets = await client.get_all_active_markets(max_pages=max_pages)
            except PolymarketAPIError as exc:
                raise click.ClickException(f"Market fetch failed: {exc}") from exc
            click.echo(f"{len(markets)} active markets fetched.")
            click.echo(f"Running {len(strategies)} strategy(ies): {', '.join(strategy_names)}")

            market_map = {m.condition_id: m for m in markets}

            # --- snapshot all markets ---
            for market in markets:
                if market.yes_price is not None:
                    db.record_snapshot(
                        MarketSnapshot(
                            id=None,
                            condition_id=market.condition_id,
                            yes_price=market.yes_price or 0.0,
                            no_price=market.no_price or 0.0,
                            volume=market.volume,
                            recorded_at=datetime.now(timezone.utc),
                        )
                    )

            # --- process exit signals for all strategies ---
            for strat in strategies:
                exit_signals = strat.check_exits(market_map)
                for sig in exit_signals:
                    if dry_run:
                        click.echo(
                            f"[DRY-RUN] [{strat.name}] Would close trade #{sig.trade_id} "
                            f"@ {sig.exit_price:.3f} ({sig.reason})"
                        )
                    else:
                        closed = db.close_trade(sig.trade_id, sig.exit_price, sig.status)
                        if closed:
                            pnl_str = (
                                f"+${closed.pnl:.2f}"
                                if (closed.pnl or 0) >= 0
                                else f"-${abs(closed.pnl or 0):.2f}"
                            )
                            click.echo(
                                "[CLOSED] "
                                f"[{strat.name}] #{sig.trade_id} {sig.reason} "
                                f" PnL: {pnl_str}"
                            )

            # --- process entry signals for all strategies ---
            entry_count = 0
            for strat in strategies:
                for market in markets:
                    signal = strat.evaluate(market)
                    if signal is None:
                        continue
                    trade_obj = signal_to_trade(signal)
                    cost = trade_obj.shares * trade_obj.entry_price

                    decision = check_entry_risk(db, signal, risk)
                    if not decision.allowed:
                        click.echo(
                            f"[SKIP] [{strat.name}] {signal.question[:50]} -> {decision.reason}"
                        )
                        continue

                    if dry_run:
                        click.echo(
                            f"[DRY-RUN] [{strat.name}] Would open {signal.outcome} on "
                            f"'{signal.question[:50]}' @ {signal.entry_price:.3f} "
                            f"(${cost:.2f})"
                        )
                    else:
                        trade_id = db.open_trade(trade_obj)
                        click.echo(
                            f"[OPENED] [{strat.name}] #{trade_id} {signal.outcome} "
                            f"'{signal.question[:50]}' @ {signal.entry_price:.3f} "
                            f"(${cost:.2f})  TP:{signal.take_profit:.3f}  SL:{signal.stop_loss:.3f}"
                        )
                    entry_count += 1

            if entry_count == 0:
                click.echo("No trades executed this cycle.")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def report(ctx: click.Context) -> None:
    """Print a full portfolio performance report."""
    db = _get_db(ctx.obj["db_path"])
    tracker = PortfolioTracker(db)
    r = tracker.get_report()

    sign = "+" if r.total_return_pct >= 0 else ""
    click.echo(f"\n{'=' * 50}")
    click.echo("  PORTFOLIO REPORT")
    click.echo(f"{'=' * 50}")
    click.echo(f"  Starting capital : ${r.starting_capital:>10,.2f}")
    click.echo(f"  Cash             : ${r.cash:>10,.2f}")
    click.echo(f"  Open positions   : {r.open_positions:>10}  (cost: ${r.open_cost_basis:.2f})")
    click.echo(f"  Deployed capital : {r.deployed_pct:>9.2f}%")
    click.echo(f"  Total value      : ${r.total_value:>10,.2f}")
    click.echo(f"  Total return     : {sign}{r.total_return_pct:>9.2f}%")
    click.echo(f"  Realised P&L     : ${r.realised_pnl:>+10.2f}")
    click.echo(f"  Closed trades    : {r.closed_trades:>10}")
    click.echo(f"  Win rate         : {r.win_rate * 100:>9.1f}%")

    if r.strategy_stats:
        click.echo(f"\n  {'Strategy':<12}  {'Trades':>6}  {'Wins':>5}  {'Win%':>6}  {'PnL':>10}")
        click.echo(f"  {'-' * 50}")
        for s in r.strategy_stats.values():
            click.echo(
                f"  {s.name:<12}  {s.total_trades:>6}  {s.wins:>5}  "
                f"{s.win_rate * 100:>5.1f}%  ${s.total_pnl:>+9.2f}"
            )

    if r.open_trades:
        click.echo(f"\n  Open Positions ({r.open_positions}):")
        click.echo(f"  {'#':>4}  {'Outcome':>7}  {'Entry':>6}  {'TP':>6}  {'SL':>6}  Question")
        click.echo(f"  {'-' * 70}")
        for t in r.open_trades:
            click.echo(
                f"  {t.id:>4}  {t.outcome:>7}  {t.entry_price:>6.3f}  "
                f"{t.take_profit:>6.3f}  {t.stop_loss:>6.3f}  "
                f"{t.question[:40]}"
            )

    click.echo(f"{'=' * 50}\n")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--limit", default=20, show_default=True, help="Number of trades to show.")
@click.option("--offset", default=0, show_default=True, help="Pagination offset.")
@click.pass_context
def history(ctx: click.Context, limit: int, offset: int) -> None:
    """Display trade history (open and closed), newest first."""
    db = _get_db(ctx.obj["db_path"])
    trades = db.get_trade_history(limit=limit, offset=offset)

    if not trades:
        click.echo("No trades found.")
        return

    click.echo(f"\n{'=' * 80}")
    click.echo(
        f"  {'#':>4}  {'Status':<12}  {'Out':>4}  {'Entry':>6}  {'Exit':>6}  {'PnL':>8}  Question"
    )
    click.echo(f"  {'-' * 78}")
    for t in trades:
        exit_str = f"{t.exit_price:.3f}" if t.exit_price is not None else "  —  "
        pnl_str = f"${t.pnl:>+.2f}" if t.pnl is not None else "      —"
        click.echo(
            f"  {t.id:>4}  {t.status:<12}  {t.outcome:>4}  {t.entry_price:>6.3f}  "
            f"{exit_str:>6}  {pnl_str:>8}  {t.question[:35]}"
        )
    click.echo(f"{'=' * 80}\n")


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--strategy",
    default="TAIL",
    show_default=True,
    help="Strategy name to backtest (e.g. TAIL, MOMENTUM).",
)
@click.option(
    "--days",
    default=7,
    show_default=True,
    type=int,
    help="Number of past days of snapshot history to replay.",
)
@click.option(
    "--capital",
    default=10_000.0,
    show_default=True,
    type=float,
    help="Starting capital for the simulation.",
)
@click.pass_context
def backtest(ctx: click.Context, strategy: str, days: int, capital: float) -> None:
    """Replay a strategy against historical snapshots and report metrics."""
    db = _get_db(ctx.obj["db_path"])
    bt = Backtester(db, strategy_name=strategy, starting_capital=capital)
    result = bt.run(days=days)
    click.echo(format_backtest_result(result))


@cli.command(name="compare")
@click.option(
    "--days", default=7, show_default=True, type=int, help="Days of snapshots to compare."
)
@click.option(
    "--capital", default=10_000.0, show_default=True, type=float, help="Starting capital."
)
@click.option(
    "--top",
    default=10,
    show_default=True,
    type=int,
    help="Show top N ranked strategies (use 0 for all).",
)
@click.pass_context
def compare(ctx: click.Context, days: int, capital: float, top: int) -> None:
    """Run multi-strategy backtest comparison and print a ranked leaderboard."""
    db = _get_db(ctx.obj["db_path"])
    rows = compare_strategies(db=db, days=days, capital=capital)
    if top > 0:
        rows = rows[:top]
    click.echo(format_strategy_comparison(rows))


# ---------------------------------------------------------------------------
# daily-report
# ---------------------------------------------------------------------------


@cli.command(name="daily-report")
@click.pass_context
def daily_report(ctx: click.Context) -> None:
    """Print a Markdown daily report (portfolio, trades, strategy breakdown)."""
    db = _get_db(ctx.obj["db_path"])
    report = generate_daily_report(db)
    click.echo(report)
