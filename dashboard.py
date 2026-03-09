"""
Polymarket Trading Performance Dashboard
Built with Streamlit + Plotly
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
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

@st.cache_data(ttl=60)
def load_portfolio():
    """Load current portfolio state"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM portfolio", conn)
    conn.close()
    return df.iloc[0]

@st.cache_data(ttl=60)
def load_trades():
    """Load all paper trades"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT * FROM paper_trades
        ORDER BY opened_at DESC
    """, conn)
    conn.close()
    df['opened_at'] = pd.to_datetime(df['opened_at'])
    df['closed_at'] = pd.to_datetime(df['closed_at'])
    return df

@st.cache_data(ttl=60)
def load_market_snapshots():
    """Load market snapshots for portfolio value calculation"""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT * FROM market_snapshots
        ORDER BY recorded_at
    """, conn)
    conn.close()
    df['recorded_at'] = pd.to_datetime(df['recorded_at'])
    return df

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
                current_price = snapshot.iloc[0]['yes_price'] if trade['outcome'] == 'Yes' else snapshot.iloc[0]['no_price']
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
    st.markdown("Real-time performance tracking for automated prediction market trading")
    
    # Load data
    portfolio = load_portfolio()
    trades_df = load_trades()
    snapshots_df = load_market_snapshots()
    
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
                current_price = snapshot['yes_price'] if trade['outcome'] == 'Yes' else snapshot['no_price']
                position_pnl = (current_price - trade['entry_price']) * trade['shares']
                unrealized_pnl += position_pnl
    
    total_value = current_cash + unrealized_pnl
    
    # KPI Cards
    col1, col2, col3, col4 = st.columns(4)
    
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
        else:
            st.info("No closed trades yet")
    
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
            st.info("No closed trades yet")
    
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
                    if row['condition_id'] in latest_snapshots.index and row['outcome'] == 'Yes'
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
        st.info("No open positions")
    
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
        st.info("No closed trades yet")
    
    # Footer
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Data refresh: 60s")

if __name__ == "__main__":
    main()
