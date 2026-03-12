# 📈 Polymarket Autopilot

A Python paper-trading system for Polymarket markets with:
- a production-style CLI,
- a Streamlit monitoring dashboard,
- pluggable strategies,
- risk guardrails,
- and historical backtesting.

The project is intentionally **paper-trading only**. It is built to demonstrate disciplined strategy engineering, data handling, and operator safety in a prediction-market context.

## What this project does

Polymarket Autopilot runs an end-to-end loop:
1. fetch active markets from Polymarket,
2. normalize and store snapshots,
3. evaluate markets across strategy modules,
4. apply portfolio/risk constraints,
5. open/close paper trades,
6. report performance from both CLI and dashboard.

## Core capabilities

- **CLI trading workflow**: `init`, `scan`, `trade`, `report`, `history`, `daily-report`.
- **Strategy tooling**: `strategies` catalog, multi-strategy `compare`, and parameter `sweep` backtests.
- **Risk controls**: max positions, per-market exposure, per-strategy exposure, max trade cost, and cash buffer.
- **Backtesting metrics**: return, drawdown, Sharpe, win rate, profit factor, expectancy, average trade return, and duration.
- **Data provenance signals**: scan/trade output now includes fetch timestamp, pages fetched, raw markets seen, parsed markets, and filtered inactive/closed records.
- **Dashboard visibility**: portfolio value, realized/unrealized P&L, open positions, strategy breakdown, win-rate views, and snapshot freshness warnings.

## Quick start

```bash
git clone https://github.com/yumorepos/polymarket-autopilot.git
cd polymarket-autopilot
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Initialize DB:
```bash
polymarket-autopilot init
```

## Main CLI flows

Scan only (no trades):
```bash
polymarket-autopilot scan --strategy TAIL --max-pages 3
```

Trade cycle (entries + exits, with risk controls):
```bash
polymarket-autopilot trade \
  --strategy all \
  --max-pages 5 \
  --max-positions 12 \
  --max-exposure-market 800 \
  --max-exposure-strategy 2500 \
  --max-trade-cost 400 \
  --cash-buffer 1000
```

Dry-run:
```bash
polymarket-autopilot trade --dry-run --strategy MOMENTUM
```

Performance and history:
```bash
polymarket-autopilot report
polymarket-autopilot history --limit 50
polymarket-autopilot daily-report
```

## Strategy analysis workflows

List registered strategy metadata and defaults:
```bash
polymarket-autopilot strategies
```

Compare multiple strategies on the same history window:
```bash
polymarket-autopilot compare --strategies TAIL,MOMENTUM,MEAN_REVERSION --days 14
```

Run a lightweight parameter sweep:
```bash
polymarket-autopilot sweep --strategy TAIL --param max_yes_prob --values 0.15,0.20,0.25 --days 14
```

Backtest one strategy:
```bash
polymarket-autopilot backtest --strategy TAIL --days 7 --capital 10000
```

## Dashboard

Run locally:
```bash
streamlit run dashboard.py
```

Dashboard focus areas:
- portfolio value and equity curve,
- deployed capital and open-position exposure,
- strategy-level realized performance,
- open/closed trade tables,
- **data freshness and snapshot provenance** (including stale-data warnings).

## Automation (safer cron mode)

Use the provided script:
```bash
scripts/auto_trade.sh
```

It now includes:
- project-root auto-detection,
- duplicate-run prevention via lockfile,
- explicit run IDs and structured logs,
- conservative early-exit behavior when environment is not ready,
- log retention cleanup.

Example cron:
```bash
*/15 * * * * /path/to/polymarket-autopilot/scripts/auto_trade.sh
```

## Testing and checks

```bash
pytest
ruff check .
mypy src
```

## Known limitations / current constraints

- **Paper trading only**: no live order placement.
- **No slippage/fee model** in backtests yet.
- **Historical fidelity depends on snapshot cadence**: sparse snapshots reduce backtest realism.
- **Strategy logic is heuristic** and should be treated as research baselines, not production alpha.
- **Single-node SQLite design**: good for local/demo workflows, not multi-process production scale.

## Tech stack

- Python 3.12+
- Click (CLI)
- httpx (async API client)
- SQLite
- Streamlit + Plotly
- pytest, Ruff, mypy

## License

MIT
