"""Tests for dashboard accounting helper functions."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from dashboard import calculate_portfolio_value_over_time, compute_metrics


def test_compute_metrics_includes_deployed_cost_basis() -> None:
    portfolio = pd.Series({"cash": 9000.0})
    trades = pd.DataFrame(
        [
            {
                "condition_id": "m1",
                "status": "open",
                "outcome": "YES",
                "entry_price": 0.5,
                "shares": 100,
                "strategy": "TAIL",
                "opened_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            },
            {
                "condition_id": "m2",
                "status": "closed_manual",
                "outcome": "NO",
                "entry_price": 0.4,
                "shares": 50,
                "strategy": "TAIL",
                "pnl": 25.0,
                "opened_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
                "closed_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
            },
        ]
    )
    snapshots = pd.DataFrame(
        [
            {
                "condition_id": "m1",
                "yes_price": 0.6,
                "no_price": 0.4,
                "recorded_at": datetime(2025, 1, 3, tzinfo=timezone.utc),
            }
        ]
    )

    metrics = compute_metrics(portfolio, trades, snapshots)

    # cash + deployed_cost + unrealized_pnl = 9000 + 50 + 10
    assert metrics.total_value == 9060.0
    assert metrics.realized_pnl == 25.0


def test_equity_curve_counts_closed_manual_trades() -> None:
    trades = pd.DataFrame(
        [
            {
                "status": "closed_manual",
                "pnl": 15.0,
                "closed_at": datetime(2025, 1, 2, tzinfo=timezone.utc),
                "opened_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            }
        ]
    )

    result = calculate_portfolio_value_over_time(trades, pd.DataFrame(), initial_cash=1000.0)
    assert result["portfolio_value"].iloc[-1] == 1015.0
