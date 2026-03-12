<div align="center">

# Polymarket Autopilot

**Paper-trading research engine for prediction-market strategies**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Tests](https://img.shields.io/badge/Tests-57_passing-success?logo=pytest&logoColor=white)](#validation)
[![Lint](https://img.shields.io/badge/Lint-Ruff-D7FF64?logo=ruff&logoColor=black)](https://github.com/astral-sh/ruff)
[![Types](https://img.shields.io/badge/Types-mypy_strict-blue?logo=python&logoColor=white)](https://mypy-lang.org)

</div>

Polymarket Autopilot is an end-to-end **paper-trading** system for prediction markets: market ingestion, strategy evaluation, risk-constrained execution simulation, backtesting, reporting, and dashboard observability.

> This repository is intentionally paper-trading only. It does not execute real-money orders.

---

## Why this project matters

Prediction markets combine sparse signals, noisy sentiment, and event-driven microstructure. A useful strategy engine therefore needs more than one-off scripts; it needs reproducible data flows, explicit risk controls, and transparent analytics.

This project demonstrates:
- **Quant mindset**: strategy testing under constraints, benchmark context, attribution, and realistic caveats.
- **Engineering discipline**: strict typing, linted code, CI checks, deterministic demo mode, and regression tests.
- **Product quality**: clean CLI, dashboard UX, and onboarding flow optimized for demos/interviews.

## What makes this different from a toy bot

- Deterministic **offline demo mode** (`demo-setup`, `demo-run`) for recruiter-safe walkthroughs.
- Strategy leaderboard with **benchmark-relative performance** and deterministic ranking explanations.
- Dashboard with **strategy attribution** and portfolio context, not just raw P&L.
- Explicit failure-mode handling for network-restricted environments.
- Clean quality gates (Ruff + mypy + tests) and GitHub Actions CI.

---

## Quick demo in 60 seconds

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# one-command deterministic demo
polymarket-autopilot demo-run --days 30 --top 5

# optional: interactive dashboard
streamlit run dashboard.py
```

Recommended demo story:
1. `demo-run` (shows setup + ranked comparison + portfolio snapshot)
2. `report` (portfolio details)
3. dashboard (attribution + positions + equity)

---

## Reproducible demo mode

Use demo mode when live API access is unavailable or when you want a repeatable interview walkthrough.

```bash
# seed deterministic snapshots + trades
polymarket-autopilot demo-setup

# compare strategy performance with benchmark context
polymarket-autopilot compare --days 30 --top 5

# portfolio + history views
polymarket-autopilot report
polymarket-autopilot history --limit 20
```

Demo assets live in:
- `demo/sample_snapshots.csv`
- `demo/sample_trades.json`

---

## Architecture (concise)

```text
Polymarket / Demo Data
        │
        ▼
api.py + demo.py  ──► strategies.py (signals, rationale)
        │                    │
        ▼                    ▼
      db.py  ◄────── cli.py (scan/trade/report/backtest/compare)
        │
        ├──► backtest.py (metrics, benchmark, ranking explanations)
        ├──► portfolio.py / report_generator.py
        └──► dashboard.py (equity, attribution, open/closed views)
```

---

## Command reference

| Command | Purpose | Typical use |
|---|---|---|
| `polymarket-autopilot init` | Initialize SQLite portfolio state | first run |
| `polymarket-autopilot demo-setup` | Seed deterministic offline demo data | reproducible walkthrough |
| `polymarket-autopilot demo-run` | One-command demo (`setup → compare → snapshot`) | fastest interview path |
| `polymarket-autopilot strategies` | List strategies + metadata | strategy discovery |
| `polymarket-autopilot scan` | Fetch live markets and print entry opportunities | live paper scan |
| `polymarket-autopilot trade --dry-run` | Evaluate live cycle without DB writes | risk/logic validation |
| `polymarket-autopilot report` | Portfolio summary + strategy stats | status review |
| `polymarket-autopilot backtest` | Single-strategy replay | focused evaluation |
| `polymarket-autopilot compare` | Multi-strategy ranking + benchmark context | analytics/demo |
| `polymarket-autopilot daily-report` | Markdown report output | automation/cron |

---

## Strategy/analytics highlights

- **Explainability layer**: ranked strategy output now includes deterministic rationale per strategy (return vs baseline, Sharpe, drawdown, trade count).
- **Benchmark context**: strategy comparison reports excess return vs a simple equal-weight YES buy-and-hold baseline over the same snapshot window.
- **Dashboard attribution**: per-strategy P&L, win rate, contribution %, and concise attribution notes.

---

## Dashboard

Launch locally:

```bash
streamlit run dashboard.py
```

Key sections:
- KPI cards (value, realized/unrealized, win rate, deployed capital)
- Equity curve + benchmark context caption
- Strategy attribution chart + table
- Open and closed position tables

---

## Validation

```bash
PYTHONPATH=src ruff check src tests dashboard.py
PYTHONPATH=src mypy src
PYTHONPATH=src pytest -q
```

Current suite: **57 tests**.

---

## Limitations / caveats

- Live `scan`/`trade` depend on outbound connectivity to Polymarket APIs.
- Backtests are bounded by snapshot quality/coverage; they do not model slippage/fees/order-book depth.
- Included local compatibility shims (`src/httpx.py`, `src/dotenv/__init__.py`) are for restricted environments; normal development should use declared dependencies.

---

## What I would build next

1. **Walk-forward evaluation** and rolling-window stability diagnostics.
2. **Execution realism**: configurable spread/slippage/latency models in backtests.
3. **Richer attribution**: reason-code decomposition at trade lifecycle level (entry/exit contributors).
4. **Dataset tooling**: snapshot quality checks and scenario packs for stress testing.

---

## License

MIT
