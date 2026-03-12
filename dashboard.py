"""
Polymarket Trading Performance Dashboard
Built with Streamlit + Plotly
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone
import sqlite3
from pathlib import Path

# Page config
st.set_page_config(
    page_title="Polymarket Autopilot Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Database connection
DB_PATH = Path(__file__).parent / "data" / "autopilot.db"


def _side_is_yes(value: str) -> bool:
    return str(value).strip().upper() == "YES"

@st.cache_data(ttl=60)
def load_portfolio():
    """Load current portfolio state"""
    if not DB_PATH.exists():
        return pd.Series({"cash": 10000.0})
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM portfolio", conn)
    conn.close()
    return df.iloc[0] if not df.empty else pd.Series({"cash": 10000.0})

@st.cache_data(ttl=60)
def load_trades():
    """Load all paper trades"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT * FROM paper_trades
        ORDER BY opened_at DESC
    """, conn)
    conn.close()
    df['opened_at'] = pd.to_datetime(df['opened_at'], format='mixed', utc=True)
    df['closed_at'] = pd.to_datetime(df['closed_at'], format='mixed', utc=True)
    return df

@st.cache_data(ttl=60)
def load_market_snapshots():
    """Load market snapshots for portfolio value calculation"""
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT * FROM market_snapshots
        ORDER BY recorded_at
    """, conn)
    conn.close()
    df['recorded_at'] = pd.to_datetime(df['recorded_at'], format='mixed', utc=True)
    return df


def build_data_health(trades_df: pd.DataFrame, snapshots_df: pd.DataFrame) -> dict[str, object]:
    """Return lightweight data provenance metrics for dashboard trust signals."""
    latest_snapshot_at = None
    if not snapshots_df.empty:
        latest_snapshot_at = snapshots_df['recorded_at'].max().to_pydatetime()
    open_positions = int((trades_df['status'] == 'open').sum()) if not trades_df.empty else 0
    unique_markets = int(snapshots_df['condition_id'].nunique()) if not snapshots_df.empty else 0
    snapshot_rows = len(snapshots_df)
    stale_minutes = None
    if latest_snapshot_at is not None:
        stale_minutes = (datetime.now(timezone.utc) - latest_snapshot_at).total_seconds() / 60

    return {
        'latest_snapshot_at': latest_snapshot_at,
        'snapshot_rows': snapshot_rows,
        'unique_markets': unique_markets,
        'open_positions': open_positions,
        'stale_minutes': stale_minutes,
    }

def calculate_portfolio_value_over_time(trades_df, snapshots_df, initial_cash=10000.0):
    """Calculate portfolio value over time from snapshots and trades"""
    
    # Get open trades
    open_trades = trades_df[trades_df['status'] == 'open'].copy()
    
    if open_trades.empty or snapshots_df.empty:
        # Return simple equity curve based on closed trades
        closed_trades = trades_df[trades_df['status'].isin(['closed_tp', 'closed_sl'])].copy()
        if closed_trades.empty:
            return pd.DataFrame({
                'timestamp': [datetime.now()],
                'portfolio_value': [initial_cash]
            })
        
        closed_trades = closed_trades.sort_values('closed_at')
        closed_trades['cumulative_pnl'] = closed_trades['pnl'].cumsum()
        equity_curve = pd.DataFrame({
            'timestamp': closed_trades['closed_at'],
            'portfolio_value': initial_cash + closed_trades['cumulative_pnl']
        })
        return equity_curve
    
    # Create equity curve from snapshots
    equity_data = []
    
    # Group snapshots by timestamp
    for timestamp, group in snapshots_df.groupby('recorded_at'):
        # Calculate closed P&L up to this point
        closed_trades = trades_df[
            (trades_df['status'].isin(['closed_tp', 'closed_sl'])) &
            (trades_df['closed_at'] <= timestamp)
        ]
        realized_pnl = closed_trades['pnl'].sum() if not closed_trades.empty else 0.0
        
        # Calculate unrealized P&L for open positions
        unrealized_pnl = 0.0
        for _, trade in open_trades.iterrows():
            if trade['opened_at'] > timestamp:
                continue
            
            # Find current price for this position
            snapshot = group[group['condition_id'] == trade['condition_id']]
            if not snapshot.empty:
                current_price = snapshot.iloc[0]['yes_price'] if _side_is_yes(trade['outcome']) else snapshot.iloc[0]['no_price']
                position_pnl = (current_price - trade['entry_price']) * trade['shares']
                unrealized_pnl += position_pnl
        
        portfolio_value = initial_cash + realized_pnl + unrealized_pnl
        equity_data.append({
            'timestamp': timestamp,
            'portfolio_value': portfolio_value,
            'realized_pnl': realized_pnl,
            'unrealized_pnl': unrealized_pnl
        })
    
    equity_df = pd.DataFrame(equity_data)
    
    # Resample to reduce data points for better performance
    if len(equity_df) > 1000:
        equity_df = equity_df.set_index('timestamp').resample('15min').last().reset_index()
    
    return equity_df

def main():
    st.title("📊 Polymarket Autopilot Dashboard")
    st.markdown("Portfolio, strategy, and data-freshness monitoring for paper trading operations")
    
    # Load data
    portfolio = load_portfolio()
    trades_df = load_trades()
    snapshots_df = load_market_snapshots()

    if trades_df.empty:
        st.warning("No trade data found yet. Run `polymarket-autopilot init` and then `polymarket-autopilot trade --dry-run` to generate first-cycle signals.")
        return

    health = build_data_health(trades_df, snapshots_df)
    if health['stale_minutes'] is not None and health['stale_minutes'] > 120:
        st.warning(
            f"Market snapshot data appears stale ({health['stale_minutes']:.0f} minutes old). "
            "Run a fresh scan/trade cycle before interpreting current unrealized P&L."
        )
    elif health['latest_snapshot_at'] is not None:
        st.caption(
            f"Data provenance — snapshots: {health['snapshot_rows']:,} rows across "
            f"{health['unique_markets']} markets, latest at {health['latest_snapshot_at'].strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

    strategy_filter = st.multiselect(
        "Strategy Filter",
        options=sorted(trades_df['strategy'].dropna().unique().tolist()),
        default=sorted(trades_df['strategy'].dropna().unique().tolist()),
    )
    if strategy_filter:
        trades_df = trades_df[trades_df['strategy'].isin(strategy_filter)]
    
    # Calculate metrics
    open_trades = trades_df[trades_df['status'] == 'open']
    closed_trades = trades_df[trades_df['status'].isin(['closed_tp', 'closed_sl'])]
    
    total_trades = len(closed_trades)
    winning_trades = len(closed_trades[closed_trades['pnl'] > 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    realized_pnl = closed_trades['pnl'].sum() if not closed_trades.empty else 0.0
    
    # Calculate current portfolio value
    current_cash = portfolio['cash']
    unrealized_pnl = 0.0
    
    # Get latest snapshot for each open position
    if not open_trades.empty and not snapshots_df.empty:
        latest_snapshots = snapshots_df.sort_values('recorded_at').groupby('condition_id').last()
        
        for _, trade in open_trades.iterrows():
            if trade['condition_id'] in latest_snapshots.index:
                snapshot = latest_snapshots.loc[trade['condition_id']]
                current_price = snapshot['yes_price'] if _side_is_yes(trade['outcome']) else snapshot['no_price']
                position_pnl = (current_price - trade['entry_price']) * trade['shares']
                unrealized_pnl += position_pnl
    
    total_value = current_cash + unrealized_pnl
    
    # KPI Cards
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric(
            label="💰 Portfolio Value",
            value=f"${total_value:,.2f}",
            delta=f"${total_value - 10000:,.2f}"
        )
    
    with col2:
        st.metric(
            label="✅ Realized P&L",
            value=f"${realized_pnl:,.2f}",
            delta=f"{(realized_pnl / 10000 * 100):.2f}%"
        )
    
    with col3:
        st.metric(
            label="🎯 Win Rate",
            value=f"{win_rate:.1f}%",
            delta=f"{winning_trades}/{total_trades} wins"
        )
    
    with col4:
        st.metric(
            label="📈 Open Positions",
            value=len(open_trades),
            delta=f"${unrealized_pnl:,.2f} unrealized"
        )

    with col5:
        deployed = (open_trades['entry_price'] * open_trades['shares']).sum() if not open_trades.empty else 0.0
        st.metric(
            label="🛡️ Deployed Capital",
            value=f"${deployed:,.2f}",
            delta=f"{len(open_trades)} open"
        )

    st.markdown("---")
    
    # Equity Curve
    st.subheader("📈 Equity Curve")
    
    equity_df = calculate_portfolio_value_over_time(trades_df, snapshots_df)
    
    if not equity_df.empty:
        fig_equity = go.Figure()
        
        fig_equity.add_trace(go.Scatter(
            x=equity_df['timestamp'],
            y=equity_df['portfolio_value'],
            mode='lines',
            name='Portfolio Value',
            line=dict(color='#00D9FF', width=2),
            fill='tozeroy',
            fillcolor='rgba(0, 217, 255, 0.1)'
        ))
        
        fig_equity.add_hline(
            y=10000,
            line_dash="dash",
            line_color="gray",
            annotation_text="Initial Capital",
            annotation_position="right"
        )
        
        fig_equity.update_layout(
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            hovermode='x unified',
            template='plotly_dark',
            height=400,
            showlegend=False
        )
        
        st.plotly_chart(fig_equity, use_container_width=True)
    else:
        st.info("No equity data available yet")
    
    # Strategy Performance
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🎲 Strategy Breakdown")
        
        strategy_stats = closed_trades.groupby('strategy').agg({
            'pnl': ['sum', 'count', 'mean']
        }).reset_index()
        strategy_stats.columns = ['Strategy', 'Total P&L', 'Trades', 'Avg P&L']
        
        if not strategy_stats.empty:
            fig_strategy = go.Figure()
            
            colors = {
                'MOMENTUM': '#00D9FF',
                'MEAN_REVERSION': '#FF6B6B',
                'TAIL': '#4ECDC4',
                'AI_PROBABILITY': '#FFE66D'
            }
            
            fig_strategy.add_trace(go.Bar(
                x=strategy_stats['Strategy'],
                y=strategy_stats['Total P&L'],
                marker_color=[colors.get(s, '#888888') for s in strategy_stats['Strategy']],
                text=strategy_stats['Total P&L'].apply(lambda x: f"${x:.2f}"),
                textposition='outside'
            ))
            
            fig_strategy.update_layout(
                xaxis_title="Strategy",
                yaxis_title="Total P&L ($)",
                template='plotly_dark',
                height=350,
                showlegend=False
            )
            
            st.plotly_chart(fig_strategy, use_container_width=True)
            st.dataframe(strategy_stats.sort_values('Total P&L', ascending=False), use_container_width=True)
        else:
            st.info("No closed trades yet; strategy-level realized performance will appear once exits occur.")
    
    with col2:
        st.subheader("🎯 Win Rate by Strategy")
        
        if not closed_trades.empty:
            win_rate_by_strategy = closed_trades.groupby('strategy').apply(
                lambda x: pd.Series({
                    'Win Rate': (x['pnl'] > 0).sum() / len(x) * 100,
                    'Wins': (x['pnl'] > 0).sum(),
                    'Total': len(x)
                })
            ).reset_index()
            
            fig_winrate = go.Figure()
            
            fig_winrate.add_trace(go.Bar(
                x=win_rate_by_strategy['strategy'],
                y=win_rate_by_strategy['Win Rate'],
                marker_color=[colors.get(s, '#888888') for s in win_rate_by_strategy['strategy']],
                text=win_rate_by_strategy.apply(
                    lambda x: f"{x['Win Rate']:.1f}%<br>({int(x['Wins'])}/{int(x['Total'])})",
                    axis=1
                ),
                textposition='outside'
            ))
            
            fig_winrate.update_layout(
                xaxis_title="Strategy",
                yaxis_title="Win Rate (%)",
                yaxis_range=[0, 100],
                template='plotly_dark',
                height=350,
                showlegend=False
            )
            
            st.plotly_chart(fig_winrate, use_container_width=True)
        else:
            st.info("No closed trades yet; win rates require completed positions.")
    
    st.markdown("---")
    
    # Open Positions
    st.subheader("📊 Open Positions")
    
    if not open_trades.empty:
        # Enhance open trades with current prices
        open_display = open_trades.copy()
        
        if not snapshots_df.empty:
            latest_snapshots = snapshots_df.sort_values('recorded_at').groupby('condition_id').last()
            
            open_display['current_price'] = open_display.apply(
                lambda row: (
                    latest_snapshots.loc[row['condition_id'], 'yes_price']
                    if row['condition_id'] in latest_snapshots.index and _side_is_yes(row['outcome'])
                    else (
                        latest_snapshots.loc[row['condition_id'], 'no_price']
                        if row['condition_id'] in latest_snapshots.index
                        else row['entry_price']
                    )
                ),
                axis=1
            )
            
            open_display['unrealized_pnl'] = (open_display['current_price'] - open_display['entry_price']) * open_display['shares']
            open_display['unrealized_pnl_pct'] = (open_display['current_price'] - open_display['entry_price']) / open_display['entry_price'] * 100
        
        # Display columns
        display_cols = ['question', 'outcome', 'strategy', 'entry_price', 'current_price', 
                       'shares', 'unrealized_pnl', 'unrealized_pnl_pct', 'take_profit', 'stop_loss', 'opened_at']
        
        display_df = open_display[display_cols].copy()
        display_df.columns = ['Market', 'Side', 'Strategy', 'Entry', 'Current', 
                             'Shares', 'Unrealized P&L', 'Unrealized %', 'TP', 'SL', 'Opened']
        
        # Format
        display_df['Entry'] = display_df['Entry'].apply(lambda x: f"${x:.3f}")
        display_df['Current'] = display_df['Current'].apply(lambda x: f"${x:.3f}")
        display_df['Shares'] = display_df['Shares'].apply(lambda x: f"{x:.0f}")
        display_df['Unrealized P&L'] = display_df['Unrealized P&L'].apply(lambda x: f"${x:.2f}")
        display_df['Unrealized %'] = display_df['Unrealized %'].apply(lambda x: f"{x:+.2f}%")
        display_df['TP'] = display_df['TP'].apply(lambda x: f"${x:.3f}")
        display_df['SL'] = display_df['SL'].apply(lambda x: f"${x:.3f}")
        display_df['Opened'] = pd.to_datetime(display_df['Opened']).dt.strftime('%Y-%m-%d %H:%M')
        
        st.dataframe(display_df, use_container_width=True, height=400)
    else:
        st.info("No open positions currently. When trades are opened, live mark-to-market columns appear here.")
    
    st.markdown("---")
    
    # Closed Trades
    st.subheader("📜 Closed Trades")
    
    if not closed_trades.empty:
        closed_display = closed_trades.copy()
        
        display_cols = ['question', 'outcome', 'strategy', 'entry_price', 'exit_price',
                       'shares', 'pnl', 'status', 'opened_at', 'closed_at']
        
        display_df = closed_display[display_cols].copy()
        display_df.columns = ['Market', 'Side', 'Strategy', 'Entry', 'Exit',
                             'Shares', 'P&L', 'Exit Type', 'Opened', 'Closed']
        
        # Format
        display_df['Entry'] = display_df['Entry'].apply(lambda x: f"${x:.3f}")
        display_df['Exit'] = display_df['Exit'].apply(lambda x: f"${x:.3f}")
        display_df['Shares'] = display_df['Shares'].apply(lambda x: f"{x:.0f}")
        display_df['P&L'] = display_df['P&L'].apply(lambda x: f"${x:.2f}")
        display_df['Exit Type'] = display_df['Exit Type'].str.replace('closed_', '').str.upper()
        display_df['Opened'] = pd.to_datetime(display_df['Opened']).dt.strftime('%Y-%m-%d %H:%M')
        display_df['Closed'] = pd.to_datetime(display_df['Closed']).dt.strftime('%Y-%m-%d %H:%M')
        
        st.dataframe(display_df, use_container_width=True, height=400)
    else:
        st.info("No closed trades yet; this table will populate after TP/SL/manual exits.")
    
    # Footer
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Data refresh: 60s")

if __name__ == "__main__":
    main()
