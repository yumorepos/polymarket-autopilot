<div align="center">

# 📈 Polymarket Autopilot

**An algorithmic paper trading engine for prediction markets**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Tests](https://img.shields.io/badge/Tests-45_passing-success?logo=pytest&logoColor=white)](#testing)
[![Code Style](https://img.shields.io/badge/Code_Style-Ruff-D7FF64?logo=ruff&logoColor=black)](https://github.com/astral-sh/ruff)
[![Type Checked](https://img.shields.io/badge/Type_Checked-mypy_strict-blue?logo=python&logoColor=white)](https://mypy-lang.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Scan live [Polymarket](https://polymarket.com) prediction markets, evaluate them with **10 pluggable trading strategies**, simulate trades against a virtual portfolio, backtest against historical data, and receive automated daily P&L reports — all from a single CLI.

[Features](#features) · [Strategies](#strategies) · [Architecture](#architecture) · [Getting Started](#getting-started) · [Usage](#usage) · [Backtesting](#backtesting) · [Contributing](#contributing)

</div>

---

## Why This Exists

Prediction markets are one of the most efficient mechanisms for aggregating information — but trading them profitably requires systematic analysis, not gut feeling. **Polymarket Autopilot** is a quantitative paper trading system that lets you develop, test, and deploy trading strategies against real Polymarket data with zero financial risk.

Built as a portfolio project to demonstrate:
- **Quantitative finance concepts** — Kelly criterion sizing, Sharpe ratio, drawdown analysis
- **Software engineering** — clean architecture, type safety (`mypy --strict`), async I/O, 45+ tests
- **Systems thinking** — end-to-end pipeline from data ingestion to automated execution and reporting

## Features

| | Feature | Description |
|-|---------|-------------|
| 🔌 | **Live Market Data** | Async Polymarket CLOB API client with pagination, retry logic, and rate limiting |
| 🧠 | **10 Trading Strategies** | From trend-following to contrarian — each with configurable risk parameters |
| 💰 | **Paper Portfolio** | $10K virtual capital with configurable risk guardrails (max positions/exposure/cash buffer), TP/SL lifecycle, and P&L tracking |
| 📊 | **Backtesting Engine** | Replay strategies against historical snapshots with Sharpe ratio, max drawdown, and win rate |
| 📋 | **Daily Reports** | Automated P&L summaries with strategy breakdowns, ready for Telegram delivery |
| ⏰ | **Cron Automation** | Hands-free trading via included shell scripts — set it and forget it |
| 🗄️ | **SQLite Persistence** | Trades, portfolio state, and market snapshots stored locally — no external services |
| 🧪 | **Fully Tested** | 45 tests covering API parsing, database CRUD, strategy signals, portfolio tracking, and backtesting |

## Strategies

All strategies inherit from a common `Strategy` base class with standardized entry/exit signal interfaces. Each uses configurable take-profit, stop-loss, and position sizing parameters.

| # | Strategy | Signal | Risk | Description |
|---|----------|--------|------|-------------|
| 1 | **TAIL** | Trend + Volume + Price | Medium | Trend-Following Adaptive Indicator Logic — buys when probability, volume, and price all trend up |
| 2 | **MARKET_MAKER** | Spread | Low | Earns the bid/ask spread on markets where YES+NO deviates from 1.00 |
| 3 | **AI_PROBABILITY** | VWAP Divergence | Medium | Compares market price to volume-weighted fair value; trades when divergence exceeds 15% |
| 4 | **CORRELATION** | Arbitrage | Low | Exploits logical pricing inconsistencies (YES + NO ≠ 1.00) within a single market |
| 5 | **MEAN_REVERSION** | Deviation from Mean | Medium | Fades sharp overreactions — buys when price drops >20% from rolling average |
| 6 | **MOMENTUM** | Directional Move + Volume | Med-High | Rides strong directional moves (>10%) confirmed by increasing volume |
| 7 | **VOLATILITY** | Pre-Catalyst Uncertainty | High | Targets uncertain markets (30–70% range) approaching resolution date |
| 8 | **WHALE_FOLLOW** | Volume Spike | Medium | Detects 3x+ volume spikes (whale activity) and follows the price direction |
| 9 | **NEWS_MOMENTUM** | Price Jump | Med-High | Rides sudden price jumps (>15% between snapshots) confirmed by volume |
| 10 | **CONTRARIAN** | Extreme Fear | Med-High | Buys when price drops >25% from average — buying fear, selling greed |

## Architecture

```
polymarket-autopilot/
├── src/polymarket_autopilot/
│   ├── api.py               # Async Polymarket CLOB client (httpx)
│   ├── strategies.py        # Strategy base class + 10 implementations
│   ├── db.py                # SQLite layer — trades, portfolio, snapshots
│   ├── portfolio.py         # Portfolio analytics & performance metrics
│   ├── backtest.py          # Backtesting engine with Sharpe/drawdown
│   ├── report_generator.py  # Daily P&L report builder
│   └── cli.py               # Click CLI — init/scan/trade/report/history/backtest
├── tests/                   # 45 tests (pytest + pytest-asyncio)
├── scripts/
│   ├── auto_trade.sh        # Cron-ready automated trade cycle
│   └── collect_snapshots.py # Historical data collector
├── pyproject.toml           # Project config, dependencies, tool settings
└── .env.example             # Environment configuration template
```

### Data Flow

```
Polymarket CLOB API
        │
        ▼
    api.py ──── Market objects ────► strategies.py
        │                                  │
        │  (snapshots)               TradeSignal / ExitSignal
        ▼                                  │
     db.py ◄──────────────────────────────┘
        │                                  │
        ▼                                  ▼
  portfolio.py ──► CLI report     backtest.py ──► performance metrics
```

## Getting Started

### Prerequisites

- **Python 3.12+**
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip`

### Installation

```bash
# Clone the repository
git clone https://github.com/yumorepos/polymarket-autopilot.git
cd polymarket-autopilot

# Create venv and install (with uv — recommended)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Or with pip
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Configuration

```bash
cp .env.example .env
# Edit .env — all settings have sensible defaults
```

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTOPILOT_DB` | `data/autopilot.db` | SQLite database path |
| `LOG_LEVEL` | `WARNING` | Logging verbosity (`DEBUG` \| `INFO` \| `WARNING` \| `ERROR`) |
| `AUTOPILOT_RATE_LIMIT_SLEEP` | `1.0` | API rate-limit pause (seconds) |

## Usage

### Initialize the database

```bash
polymarket-autopilot init
# Database initialised at: data/autopilot.db
# Starting cash balance:   $10,000.00
```

### Scan markets for signals

```bash
# Scan with default strategy (TAIL)
polymarket-autopilot scan

# Scan with a specific strategy
polymarket-autopilot scan --strategy MOMENTUM --max-pages 5

# Scan with all strategies
polymarket-autopilot scan --strategy all
```

### Execute a trade cycle

```bash
# Run a full trade cycle (open + close positions)
polymarket-autopilot trade

# Preview signals without committing trades
polymarket-autopilot trade --dry-run

# Trade with a specific strategy
polymarket-autopilot trade --strategy WHALE_FOLLOW
```



### Risk controls in live paper trading

The `trade` command now supports portfolio guardrails:

```bash
polymarket-autopilot trade \
  --max-positions 12 \
  --max-exposure-market 800 \
  --max-exposure-strategy 2500 \
  --max-trade-cost 400 \
  --cash-buffer 1000
```

Signals that violate limits are skipped with explicit reason codes (`max_positions_reached`, `market_exposure_limit`, `cash_buffer_breach`, etc.).

### View portfolio report

```bash
polymarket-autopilot report
```

```
==================================================
  PORTFOLIO REPORT
==================================================
  Starting capital :  $10,000.00
  Cash             :   $9,412.50
  Open positions   :          3  (cost: $587.50)
  Total value      :  $10,000.00
  Total return     :      +0.00%
  Realised P&L     :      +$0.00
  Closed trades    :          0
  Win rate         :       0.0%
==================================================
```

### View trade history

```bash
polymarket-autopilot history
polymarket-autopilot history --limit 50 --offset 0
```

### Global options

```bash
# Custom database path + debug logging
polymarket-autopilot --db /path/to/custom.db --log-level DEBUG report
```

## Backtesting

Replay any strategy against historical market snapshots to evaluate performance before deploying:

```bash
polymarket-autopilot backtest --strategy TAIL --days 7
```

```
=======================================================
  BACKTEST REPORT: TAIL
=======================================================
  Period         : 2026-02-27 → 2026-03-06
  Starting capital: $ 10,000.00
  Ending capital  : $ 10,245.00
  Total return    :     +2.45%
  Max drawdown    :     -1.20%
  Sharpe ratio    :      1.340
  Total trades    :         12
  Winning         :          8
  Losing          :          3
  Still open      :          1
  Win rate        :      72.7%
=======================================================
```

Metrics include: total return, max drawdown, Sharpe ratio, win rate, profit factor, expectancy, average return per trade, best/worst trade, average trade duration, and per-trade detail.

## Automation

Set up hands-free trading with the included cron script:

```bash
# Run every 15 minutes during market hours
*/15 * * * * /path/to/polymarket-autopilot/scripts/auto_trade.sh

# Logs rotate automatically (7-day retention)
```

## Adding a Custom Strategy

Extend the system by subclassing `Strategy`:

```python
# src/polymarket_autopilot/strategies.py

class MyStrategy(Strategy):
    name = "MY_STRATEGY"

    def evaluate(self, market: Market) -> TradeSignal | None:
        # Your entry logic here
        ...

# Register it
STRATEGIES["MY_STRATEGY"] = MyStrategy
```

Then use it immediately:

```bash
polymarket-autopilot scan --strategy MY_STRATEGY
polymarket-autopilot trade --strategy MY_STRATEGY
```

## Testing

```bash
# Run full test suite
pytest

# Verbose output
pytest -v

# Run specific test module
pytest tests/test_strategies.py

# With coverage (if installed)
pytest --cov=polymarket_autopilot
```

**45 tests** covering:
- API response parsing and market object construction
- Database CRUD operations and schema migrations
- All strategy signal generation and edge cases
- Portfolio tracking and performance calculations
- Backtesting engine accuracy

## Tech Stack

- **Python 3.12** — modern syntax, type hints, `match` statements
- **httpx** — async HTTP client for Polymarket CLOB API
- **Click** — CLI framework with subcommands and options
- **SQLite** — zero-config local persistence
- **pytest** + **pytest-asyncio** — async-aware test suite
- **Ruff** — fast linting and formatting
- **mypy (strict)** — full static type checking

## License

[MIT](LICENSE) — use it, learn from it, build on it.

---

<div align="center">

Built by [Yumo](https://github.com/yumorepos) · Feedback and contributions welcome

</div>
