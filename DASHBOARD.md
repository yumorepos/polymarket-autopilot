# Dashboard Guide

The dashboard is a first-class operations surface for this project.

## Run

```bash
streamlit run dashboard.py
```

## What it shows

- Portfolio value and equity curve.
- Realized and unrealized P&L.
- Open positions and deployed capital.
- Strategy breakdown (P&L and win rate).
- Open and closed trade tables.
- Data provenance (snapshot row count, market coverage, freshness timestamp).
- Stale-data warning when snapshots are old enough to make mark-to-market views unreliable.

## Notes

- Uses `data/autopilot.db` by default.
- Refresh caching uses 60-second TTL to keep UI responsive.
- Best used after running `polymarket-autopilot trade` or `scan` to ensure snapshots are fresh.
