# Polymarket Autopilot

A **paper trading bot** for [Polymarket](https://polymarket.com) prediction markets.
Scan active markets, evaluate them with pluggable strategies, simulate trades against a virtual $10,000 portfolio, and track performance — all from a single CLI.

---

## Features

- **Live market data** — fetches active markets from the Polymarket CLOB REST API with pagination and retry logic
- **TAIL strategy** — Trend-Following Adaptive Indicator Logic: buys YES when probability > 60 %, volume is above rolling average, and price is trending up
- **Paper portfolio** — $10,000 starting capital, per-trade take-profit (+15 %) and stop-loss (-10 %), position sizing capped at 5 % of portfolio
- **SQLite persistence** — trades, portfolio state, and historical snapshots stored locally; no external services needed
- **Rich CLI** — `init`, `scan`, `trade`, `report`, and `history` commands via Click
- **Extensible** — add new strategies by subclassing `Strategy` and registering in `STRATEGIES`

---

## Architecture

```
polymarket-autopilot/
├── src/polymarket_autopilot/
│   ├── __init__.py        # package metadata
│   ├── __main__.py        # python -m polymarket_autopilot entry point
│   ├── api.py             # async Polymarket CLOB client (httpx)
│   ├── db.py              # SQLite layer — trades, portfolio, snapshots
│   ├── strategies.py      # Strategy base class + TAIL strategy
│   ├── portfolio.py       # Portfolio analytics & reporting
│   └── cli.py             # Click CLI — init / scan / trade / report / history
├── tests/
│   ├── test_api.py        # parse_market unit tests
│   ├── test_db.py         # database CRUD tests
│   ├── test_strategies.py # TAIL strategy signal tests
│   └── test_portfolio.py  # portfolio tracker tests
├── pyproject.toml
├── .env.example
└── README.md
```

### Data flow

```
Polymarket CLOB API
      │
      ▼
  api.py  ──── Market objects ────►  strategies.py
      │                                    │
      │  (snapshots)                  TradeSignal
      ▼                                    │
   db.py  ◄───────────────────────────────┘
      │
      ▼
 portfolio.py  ──►  CLI report
```

---

## Setup

### Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip`

### Install

```bash
# Clone
git clone https://github.com/youruser/polymarket-autopilot.git
cd polymarket-autopilot

# Create venv and install (uv)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env as needed (all settings have defaults)
```

---

## Usage

### Initialise the database

```bash
polymarket-autopilot init
# Database initialised at: data/autopilot.db
# Starting cash balance:   $10,000.00
```

### Scan markets for signals (no trades executed)

```bash
polymarket-autopilot scan
polymarket-autopilot scan --strategy TAIL --max-pages 5
```

### Run a full trade cycle

```bash
# Execute signals (open/close positions)
polymarket-autopilot trade

# Preview without committing
polymarket-autopilot trade --dry-run
```

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
polymarket-autopilot --db /path/to/custom.db --log-level DEBUG report
```

---

## Running Tests

```bash
pytest
pytest -v                    # verbose
pytest tests/test_db.py      # single file
```

---

## TAIL Strategy

The **TAIL** (Trend-Following Adaptive Indicator Logic) strategy evaluates each market against three conditions before opening a YES position:

| Condition | Threshold |
|-----------|-----------|
| YES probability | > 60 % |
| Current volume | > rolling average of last 5 snapshots |
| Current YES price | > rolling average of last 5 snapshots |

**Position sizing:**
- Max spend per trade: 5 % of total portfolio value
- Take-profit: entry price × 1.15 (+15 %)
- Stop-loss: entry price × 0.90 (−10 %)

### Adding a new strategy

```python
# src/polymarket_autopilot/strategies.py

class MyStrategy(Strategy):
    name = "MYSIG"

    def evaluate(self, market: Market) -> TradeSignal | None:
        # your logic here
        ...

# Register it
STRATEGIES["MYSIG"] = MyStrategy
```

Then use it:

```bash
polymarket-autopilot scan --strategy MYSIG
polymarket-autopilot trade --strategy MYSIG
```

---

## Configuration Reference

| Env var | Default | Description |
|---------|---------|-------------|
| `AUTOPILOT_DB` | `data/autopilot.db` | SQLite database path |
| `LOG_LEVEL` | `WARNING` | Logging verbosity |

---

## License

MIT
