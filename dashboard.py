"""Streamlit dashboard for paper-trading performance analysis."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

CLOSED_STATUSES = ["closed_tp", "closed_sl", "closed_manual"]
DB_PATH = Path(__file__).parent / "data" / "autopilot.db"
DEMO_TRADES_PATH = Path(__file__).parent / "demo" / "sample_trades.json"
DEMO_SNAPSHOTS_PATH = Path(__file__).parent / "demo" / "sample_snapshots.csv"
INITIAL_CAPITAL = 10_000.0
USE_DEMO_MODE = not DB_PATH.exists()

# Live trading toggle (for future use)
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"


@dataclass(frozen=True)
class DashboardMetrics:
    total_value: float
    realized_pnl: float
    unrealized_pnl: float
    win_rate_pct: float
    winning_trades: int
    closed_trades: int
    open_positions: int
    deployed_cost: float


def _side_is_yes(value: str) -> bool:
    return str(value).strip().upper() == "YES"


def _empty_portfolio() -> pd.Series:
    return pd.Series({"cash": INITIAL_CAPITAL})


@st.cache_data(ttl=60)
def load_portfolio(path: Path = DB_PATH) -> pd.Series:
    """Load current portfolio state."""
    if not path.exists():
        return _empty_portfolio()

    conn = sqlite3.connect(path)
    df = pd.read_sql_query("SELECT * FROM portfolio", conn)
    conn.close()
    return df.iloc[0] if not df.empty else _empty_portfolio()


@st.cache_data(ttl=60)
def load_trades(path: Path = DB_PATH) -> pd.DataFrame:
    """Load all paper trades."""
    if USE_DEMO_MODE and DEMO_TRADES_PATH.exists():
        # Load from demo JSON
        with open(DEMO_TRADES_PATH) as f:
            trades_data = json.load(f)
        
        df = pd.DataFrame(trades_data)
        if df.empty:
            return df
        
        # Calculate P&L for demo trades
        df["pnl"] = 0.0
        for idx, row in df.iterrows():
            if row["status"] in CLOSED_STATUSES:
                df.at[idx, "pnl"] = (row["exit_price"] - row["entry_price"]) * row["shares"]
        
        df["opened_at"] = pd.to_datetime(df["opened_at"], format="mixed", utc=True)
        df["closed_at"] = pd.to_datetime(df["closed_at"], format="mixed", utc=True, errors='coerce')
        return df
    
    if not path.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(path)
    df = pd.read_sql_query("SELECT * FROM paper_trades ORDER BY opened_at DESC", conn)
    conn.close()

    if df.empty:
        return df

    df["opened_at"] = pd.to_datetime(df["opened_at"], format="mixed", utc=True)
    df["closed_at"] = pd.to_datetime(df["closed_at"], format="mixed", utc=True)
    return df


@st.cache_data(ttl=60)
def load_market_snapshots(path: Path = DB_PATH) -> pd.DataFrame:
    """Load market snapshots used for mark-to-market calculations."""
    if USE_DEMO_MODE and DEMO_SNAPSHOTS_PATH.exists():
        # Load from demo CSV
        df = pd.read_csv(DEMO_SNAPSHOTS_PATH)
        if df.empty:
            return df
        df["recorded_at"] = pd.to_datetime(df["recorded_at"], format="mixed", utc=True)
        return df
    
    if not path.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(path)
    df = pd.read_sql_query("SELECT * FROM market_snapshots ORDER BY recorded_at", conn)
    conn.close()

    if df.empty:
        return df

    df["recorded_at"] = pd.to_datetime(df["recorded_at"], format="mixed", utc=True)
    return df


def _latest_snapshots(snapshots_df: pd.DataFrame) -> pd.DataFrame:
    if snapshots_df.empty:
        return pd.DataFrame()
    return snapshots_df.sort_values("recorded_at").groupby("condition_id").last()


def _current_price_from_snapshot(trade: pd.Series, latest: pd.DataFrame) -> float:
    condition_id = str(trade["condition_id"])
    if condition_id not in latest.index:
        return float(trade["entry_price"])

    snapshot = latest.loc[condition_id]
    return float(
        snapshot["yes_price"] if _side_is_yes(str(trade["outcome"])) else snapshot["no_price"]
    )


def compute_metrics(
    portfolio: pd.Series,
    trades_df: pd.DataFrame,
    snapshots_df: pd.DataFrame,
) -> DashboardMetrics:
    """Compute headline metrics shown in KPI cards."""
    open_trades = trades_df[trades_df["status"] == "open"]
    closed_trades = trades_df[trades_df["status"].isin(CLOSED_STATUSES)]

    realized_pnl = float(closed_trades["pnl"].sum()) if not closed_trades.empty else 0.0
    winning_trades = int((closed_trades["pnl"] > 0).sum()) if not closed_trades.empty else 0
    total_closed = len(closed_trades)
    win_rate_pct = (winning_trades / total_closed * 100) if total_closed else 0.0

    latest = _latest_snapshots(snapshots_df)
    unrealized_pnl = 0.0
    for _, trade in open_trades.iterrows():
        current_price = _current_price_from_snapshot(trade, latest)
        unrealized_pnl += (current_price - float(trade["entry_price"])) * float(trade["shares"])

    deployed_cost = (
        float((open_trades["entry_price"] * open_trades["shares"]).sum())
        if not open_trades.empty
        else 0.0
    )
    current_cash = float(portfolio.get("cash", INITIAL_CAPITAL))
    total_value = current_cash + deployed_cost + unrealized_pnl

    return DashboardMetrics(
        total_value=total_value,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        win_rate_pct=win_rate_pct,
        winning_trades=winning_trades,
        closed_trades=total_closed,
        open_positions=len(open_trades),
        deployed_cost=deployed_cost,
    )


def calculate_portfolio_value_over_time(
    trades_df: pd.DataFrame,
    snapshots_df: pd.DataFrame,
    initial_cash: float = INITIAL_CAPITAL,
) -> pd.DataFrame:
    """Calculate equity curve from trades and snapshots."""
    open_trades = trades_df[trades_df["status"] == "open"].copy()

    if open_trades.empty or snapshots_df.empty:
        closed_trades = trades_df[trades_df["status"].isin(CLOSED_STATUSES)].copy()
        if closed_trades.empty:
            return pd.DataFrame(
                {"timestamp": [datetime.now(timezone.utc)], "portfolio_value": [initial_cash]}
            )

        closed_trades = closed_trades.sort_values("closed_at")
        closed_trades["cumulative_pnl"] = closed_trades["pnl"].cumsum()
        return pd.DataFrame(
            {
                "timestamp": closed_trades["closed_at"],
                "portfolio_value": initial_cash + closed_trades["cumulative_pnl"],
            }
        )

    equity_data: list[dict[str, float | pd.Timestamp]] = []
    for timestamp, group in snapshots_df.groupby("recorded_at"):
        closed_until = trades_df[
            trades_df["status"].isin(CLOSED_STATUSES) & (trades_df["closed_at"] <= timestamp)
        ]
        realized_pnl = float(closed_until["pnl"].sum()) if not closed_until.empty else 0.0

        unrealized_pnl = 0.0
        for _, trade in open_trades.iterrows():
            if pd.Timestamp(trade["opened_at"]) > timestamp:
                continue

            snapshot = group[group["condition_id"] == trade["condition_id"]]
            if snapshot.empty:
                continue

            current_price = float(
                snapshot.iloc[0]["yes_price"]
                if _side_is_yes(str(trade["outcome"]))
                else snapshot.iloc[0]["no_price"]
            )
            unrealized_pnl += (current_price - float(trade["entry_price"])) * float(trade["shares"])

        equity_data.append(
            {
                "timestamp": timestamp,
                "portfolio_value": initial_cash + realized_pnl + unrealized_pnl,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
            }
        )

    equity_df = pd.DataFrame(equity_data)
    if len(equity_df) > 1000:
        equity_df = equity_df.set_index("timestamp").resample("15min").last().reset_index()
    return equity_df


def _build_open_positions_table(
    trades_df: pd.DataFrame, snapshots_df: pd.DataFrame
) -> pd.DataFrame:
    open_trades = trades_df[trades_df["status"] == "open"].copy()
    if open_trades.empty:
        return pd.DataFrame()

    latest = _latest_snapshots(snapshots_df)
    open_trades["current_price"] = open_trades.apply(
        lambda row: _current_price_from_snapshot(row, latest),
        axis=1,
    )
    open_trades["unrealized_pnl"] = (
        open_trades["current_price"] - open_trades["entry_price"]
    ) * open_trades["shares"]
    open_trades["unrealized_pnl_pct"] = (
        (open_trades["current_price"] - open_trades["entry_price"])
        / open_trades["entry_price"]
        * 100
    )

    table = open_trades[
        [
            "question",
            "outcome",
            "strategy",
            "entry_price",
            "current_price",
            "shares",
            "unrealized_pnl",
            "unrealized_pnl_pct",
            "take_profit",
            "stop_loss",
            "opened_at",
        ]
    ].copy()
    table.columns = [
        "Market",
        "Side",
        "Strategy",
        "Entry",
        "Current",
        "Shares",
        "Unrealized P&L",
        "Unrealized %",
        "TP",
        "SL",
        "Opened",
    ]
    return table


def _build_closed_trades_table(trades_df: pd.DataFrame) -> pd.DataFrame:
    closed = trades_df[trades_df["status"].isin(CLOSED_STATUSES)].copy()
    if closed.empty:
        return pd.DataFrame()

    table = closed[
        [
            "question",
            "outcome",
            "strategy",
            "entry_price",
            "exit_price",
            "shares",
            "pnl",
            "status",
            "opened_at",
            "closed_at",
        ]
    ].copy()
    table.columns = [
        "Market",
        "Side",
        "Strategy",
        "Entry",
        "Exit",
        "Shares",
        "P&L",
        "Exit Type",
        "Opened",
        "Closed",
    ]
    return table


def _strategy_attribution(trades_df: pd.DataFrame) -> pd.DataFrame:
    closed = trades_df[trades_df["status"].isin(CLOSED_STATUSES)].copy()
    if closed.empty:
        return pd.DataFrame()

    grouped = (
        closed.groupby("strategy")
        .agg(
            total_pnl=("pnl", "sum"),
            trades=("pnl", "count"),
            win_rate=("pnl", lambda x: (x > 0).mean()),
        )
        .reset_index()
    )
    total_abs = grouped["total_pnl"].abs().sum()
    grouped["pnl_contribution_pct"] = (
        grouped["total_pnl"] / total_abs * 100 if total_abs > 0 else 0.0
    )
    grouped["win_rate"] = grouped["win_rate"] * 100
    grouped["explainability_note"] = grouped.apply(
        lambda row: (
            f"{row['strategy']} contributed ${row['total_pnl']:+.2f} over "
            f"{int(row['trades'])} trades with {row['win_rate']:.1f}% wins."
        ),
        axis=1,
    )
    return grouped.sort_values("total_pnl", ascending=False)


def _snapshot_benchmark_return_pct(snapshots_df: pd.DataFrame) -> float:
    """Baseline context: equal-weight YES buy-and-hold across tracked markets."""
    if snapshots_df.empty:
        return 0.0

    returns: list[float] = []
    for _, group in snapshots_df.groupby("condition_id"):
        ordered = group.sort_values("recorded_at")
        first = float(ordered.iloc[0]["yes_price"])
        last = float(ordered.iloc[-1]["yes_price"])
        if first <= 0:
            continue
        returns.append((last - first) / first * 100)
    return sum(returns) / len(returns) if returns else 0.0


def _render_strategy_attribution(trades_df: pd.DataFrame) -> None:
    st.subheader("🧠 Strategy Attribution")
    attribution = _strategy_attribution(trades_df)
    if attribution.empty:
        st.info("No closed trades available for strategy attribution yet")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=attribution["strategy"],
            y=attribution["total_pnl"],
            text=attribution["total_pnl"].map(lambda x: f"${x:,.2f}"),
            textposition="outside",
            marker_color=["#00D9FF" if x >= 0 else "#FF6B6B" for x in attribution["total_pnl"]],
            name="Total P&L",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        xaxis_title="Strategy",
        yaxis_title="Total P&L ($)",
        height=320,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    table = attribution.rename(
        columns={
            "strategy": "Strategy",
            "total_pnl": "Total P&L",
            "trades": "Trades",
            "win_rate": "Win Rate %",
            "pnl_contribution_pct": "PnL Contribution %",
            "explainability_note": "Why this strategy ranks here",
        }
    )
    st.dataframe(table, use_container_width=True, height=220)


def _render_kpis(metrics: DashboardMetrics) -> None:
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric(
            label="💰 Portfolio Value",
            value=f"${metrics.total_value:,.2f}",
            delta=f"${metrics.total_value - INITIAL_CAPITAL:,.2f}",
        )
    with col2:
        st.metric(
            label="✅ Realized P&L",
            value=f"${metrics.realized_pnl:,.2f}",
            delta=f"{(metrics.realized_pnl / INITIAL_CAPITAL * 100):.2f}%",
        )
    with col3:
        st.metric(
            label="🎯 Win Rate",
            value=f"{metrics.win_rate_pct:.1f}%",
            delta=f"{metrics.winning_trades}/{metrics.closed_trades} wins",
        )
    with col4:
        st.metric(
            label="📈 Open Positions",
            value=metrics.open_positions,
            delta=f"${metrics.unrealized_pnl:,.2f} unrealized",
        )
    with col5:
        st.metric(
            label="🛡️ Deployed Capital",
            value=f"${metrics.deployed_cost:,.2f}",
            delta=f"{metrics.open_positions} open",
        )


def main() -> None:
    st.set_page_config(
        page_title="Polymarket Autopilot Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.title("📊 Polymarket Autopilot Dashboard")
    
    # Trading mode indicator
    if LIVE_TRADING_ENABLED:
        st.warning("🔴 **LIVE TRADING ENABLED** — Real money at risk")
    elif USE_DEMO_MODE:
        st.info("🎬 **Demo Mode** — Displaying sample trading data for portfolio demonstration")
    else:
        st.info("📄 **Paper Trading Mode** — No real money at risk")
    
    st.markdown(
        "Paper-trading performance tracking for strategy research and portfolio risk review"
    )

    portfolio = load_portfolio()
    trades_df = load_trades()
    snapshots_df = load_market_snapshots()

    if trades_df.empty:
        st.warning(
            "No trade data yet. Run `polymarket-autopilot init` and "
            "`polymarket-autopilot trade --dry-run` first."
        )
        return

    strategy_options = sorted(trades_df["strategy"].dropna().unique().tolist())
    strategy_filter = st.multiselect(
        "Strategy Filter", options=strategy_options, default=strategy_options
    )
    if strategy_filter:
        trades_df = trades_df[trades_df["strategy"].isin(strategy_filter)]

    metrics = compute_metrics(portfolio, trades_df, snapshots_df)
    _render_kpis(metrics)

    st.markdown("---")
    st.subheader("📈 Equity Curve")
    benchmark_return = _snapshot_benchmark_return_pct(snapshots_df)
    st.caption(
        "Benchmark context: equal-weight YES buy-and-hold across tracked markets = "
        f"{benchmark_return:.2f}% over the current snapshot window."
    )
    equity_df = calculate_portfolio_value_over_time(trades_df, snapshots_df)
    if not equity_df.empty:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=equity_df["timestamp"],
                y=equity_df["portfolio_value"],
                mode="lines",
                line={"color": "#00D9FF", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(0, 217, 255, 0.1)",
                name="Portfolio Value",
            )
        )
        fig.add_hline(
            y=INITIAL_CAPITAL,
            line_dash="dash",
            line_color="gray",
            annotation_text="Initial Capital",
            annotation_position="right",
        )
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            hovermode="x unified",
            template="plotly_dark",
            showlegend=False,
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    _render_strategy_attribution(trades_df)

    st.markdown("---")
    st.subheader("📊 Open Positions")
    open_table = _build_open_positions_table(trades_df, snapshots_df)
    if open_table.empty:
        st.info("No open positions")
    else:
        st.dataframe(open_table, use_container_width=True, height=320)

    st.subheader("📜 Closed Trades")
    closed_table = _build_closed_trades_table(trades_df)
    if closed_table.empty:
        st.info("No closed trades yet")
    else:
        st.dataframe(closed_table, use_container_width=True, height=320)

    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Data refresh: 60s")


if __name__ == "__main__":
    main()
