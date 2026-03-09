# Polymarket Autopilot Dashboard

Professional trading performance dashboard built with Streamlit and Plotly.

## Features

- **📈 Equity Curve**: Real-time portfolio value over time (realized + unrealized P&L)
- **🎲 Strategy Breakdown**: P&L by strategy (MOMENTUM, MEAN_REVERSION, TAIL, AI_PROBABILITY)
- **🎯 Win Rate Stats**: Overall and per-strategy win rates
- **📊 Open Positions**: Live positions with current prices and unrealized P&L
- **📜 Closed Trades**: Full history with entry/exit prices and P&L
- **💰 KPI Cards**: Portfolio value, realized P&L, win rate, open positions count

## Local Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Run dashboard
streamlit run dashboard.py
```

Dashboard will open at http://localhost:8501

## Deployment to Streamlit Cloud

1. Push to GitHub (already done ✓)
2. Go to https://share.streamlit.io/
3. Click "New app"
4. Select repository: `yumorepos/polymarket-autopilot`
5. Branch: `main`
6. Main file path: `dashboard.py`
7. Click "Deploy"

**Note:** Database file (`data/autopilot.db`) must be accessible. For cloud deployment, consider:
- Uploading to cloud storage (S3, GCS) and fetching on load
- Using a hosted database (PostgreSQL, MySQL)
- For demo: commit a snapshot of the DB (not recommended for production)

## Tech Stack

- **Streamlit**: Web framework
- **Plotly**: Interactive charts
- **Pandas**: Data processing
- **SQLite**: Local database

## Data Sources

- `portfolio` table: Current cash balance
- `paper_trades` table: All trades (open/closed)
- `market_snapshots` table: Historical price data for equity curve calculation
