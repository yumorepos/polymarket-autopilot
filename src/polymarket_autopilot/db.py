"""SQLite database layer for polymarket-autopilot.

Manages three tables:
- paper_trades   — individual simulated trade records
- portfolio      — current cash balance and open positions
- market_snapshots — historical price snapshots for trend analysis
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path("data/autopilot.db")
STARTING_CAPITAL = 10_000.0

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class PaperTrade:
    """A single simulated trade entry."""

    id: int | None
    condition_id: str
    question: str
    outcome: str  # "YES" or "NO"
    strategy: str
    shares: float
    entry_price: float
    exit_price: float | None
    take_profit: float  # target exit price
    stop_loss: float  # stop-loss exit price
    status: str  # "open" | "closed_tp" | "closed_sl" | "closed_manual"
    pnl: float | None
    opened_at: datetime
    closed_at: datetime | None


@dataclass
class Position:
    """An open position in the portfolio."""

    condition_id: str
    question: str
    outcome: str
    strategy: str
    shares: float
    entry_price: float
    take_profit: float
    stop_loss: float
    opened_at: datetime


@dataclass
class MarketSnapshot:
    """A price/volume snapshot for a market at a point in time."""

    id: int | None
    condition_id: str
    yes_price: float
    no_price: float
    volume: float
    recorded_at: datetime


# ---------------------------------------------------------------------------
# Database manager
# ---------------------------------------------------------------------------


class Database:
    """SQLite database wrapper providing CRUD operations.

    Args:
        path: Path to the SQLite database file.
    """

    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path

    def init(self) -> None:
        """Create database file and tables if they don't exist."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            _create_tables(conn)
            _ensure_portfolio_row(conn)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a SQLite connection with row_factory set."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def get_cash(self) -> float:
        """Return the current cash balance.

        Returns:
            Cash balance in USD.
        """
        with self._connect() as conn:
            row = conn.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
            return float(row["cash"]) if row else STARTING_CAPITAL

    def update_cash(self, delta: float) -> float:
        """Adjust cash balance by delta (positive = add, negative = spend).

        Args:
            delta: Amount to add or subtract.

        Returns:
            New cash balance.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE portfolio SET cash = cash + ?, updated_at = ? WHERE id = 1",
                (delta, _now()),
            )
            row = conn.execute("SELECT cash FROM portfolio WHERE id = 1").fetchone()
            return float(row["cash"])

    def get_portfolio_value(self) -> float:
        """Return total portfolio value (cash + open position cost basis).

        Returns:
            Total portfolio value in USD.
        """
        cash = self.get_cash()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT SUM(shares * entry_price) as cost FROM paper_trades WHERE status = 'open'"
            ).fetchone()
            cost = float(row["cost"] or 0.0)
        return cash + cost

    def get_portfolio_summary(self) -> dict[str, float]:
        """Return cash, open cost, and exposure metrics in one query."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.cash AS cash,
                    COALESCE(
                        SUM(CASE WHEN t.status = 'open' THEN t.shares * t.entry_price END),
                        0.0
                    ) AS open_cost,
                    COALESCE(SUM(CASE WHEN t.status = 'open' THEN t.shares END), 0.0) AS open_shares
                FROM portfolio p
                LEFT JOIN paper_trades t ON 1=1
                WHERE p.id = 1
                """
            ).fetchone()

        cash = float(row["cash"]) if row else STARTING_CAPITAL
        open_cost = float(row["open_cost"]) if row else 0.0
        return {
            "cash": cash,
            "open_cost": open_cost,
            "total_value": cash + open_cost,
            "deployed_pct": (open_cost / (cash + open_cost) * 100.0)
            if (cash + open_cost) > 0
            else 0.0,
        }


    def reset(self) -> None:
        """Reset portfolio, trades, and snapshots for reproducible demo runs."""
        with self._connect() as conn:
            conn.execute("DELETE FROM paper_trades")
            conn.execute("DELETE FROM market_snapshots")
            conn.execute(
                "UPDATE portfolio SET cash = ?, updated_at = ? WHERE id = 1",
                (STARTING_CAPITAL, _now()),
            )

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def open_trade(self, trade: PaperTrade) -> int:
        """Insert a new open trade and deduct cost from cash.

        Args:
            trade: Trade to record.

        Returns:
            Row ID of the new trade.
        """
        cost = trade.shares * trade.entry_price
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO paper_trades
                  (condition_id, question, outcome, strategy, shares, entry_price,
                   exit_price, take_profit, stop_loss, status, pnl, opened_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 'open', NULL, ?, NULL)
                """,
                (
                    trade.condition_id,
                    trade.question,
                    trade.outcome,
                    trade.strategy,
                    trade.shares,
                    trade.entry_price,
                    trade.take_profit,
                    trade.stop_loss,
                    _fmt(trade.opened_at),
                ),
            )
            trade_id = cursor.lastrowid
            if trade_id is None:
                raise RuntimeError("Failed to persist trade row")
            conn.execute(
                "UPDATE portfolio SET cash = cash - ?, updated_at = ? WHERE id = 1",
                (cost, _now()),
            )
        return trade_id

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        status: str,
        closed_at: datetime | None = None,
    ) -> PaperTrade | None:
        """Close an open trade and credit proceeds to cash.

        Args:
            trade_id: ID of the trade to close.
            exit_price: Price at which the position is closed.
            status: Reason for closure (e.g. 'closed_tp', 'closed_sl').

        Returns:
            Updated PaperTrade, or None if trade_id not found.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_trades WHERE id = ? AND status = 'open'",
                (trade_id,),
            ).fetchone()
            if row is None:
                return None

            shares = float(row["shares"])
            entry_price = float(row["entry_price"])
            pnl = (exit_price - entry_price) * shares
            proceeds = shares * exit_price

            conn.execute(
                """
                UPDATE paper_trades
                SET exit_price = ?, status = ?, pnl = ?, closed_at = ?
                WHERE id = ?
                """,
                (exit_price, status, pnl, _fmt(closed_at) if closed_at else _now(), trade_id),
            )
            conn.execute(
                "UPDATE portfolio SET cash = cash + ?, updated_at = ? WHERE id = 1",
                (proceeds, _now()),
            )
            updated = conn.execute(
                "SELECT * FROM paper_trades WHERE id = ?", (trade_id,)
            ).fetchone()
        return _row_to_trade(updated)

    def get_open_trades(self) -> list[PaperTrade]:
        """Return all currently open trades.

        Returns:
            List of open PaperTrade objects.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY opened_at"
            ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def get_trade_history(
        self, limit: int = 50, offset: int = 0, statuses: Sequence[str] | None = None
    ) -> list[PaperTrade]:
        """Return all trades (open and closed), newest first.

        Args:
            limit: Maximum number of records to return.
            offset: Pagination offset.

        Returns:
            List of PaperTrade objects.
        """
        with self._connect() as conn:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    (
                        "SELECT * FROM paper_trades "
                        f"WHERE status IN ({placeholders}) "
                        "ORDER BY opened_at DESC LIMIT ? OFFSET ?"
                    ),
                    (*statuses, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM paper_trades ORDER BY opened_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def get_trade_by_condition(self, condition_id: str) -> PaperTrade | None:
        """Return the open trade for a given market, if any.

        Args:
            condition_id: Market condition ID.

        Returns:
            Open PaperTrade or None.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_trades WHERE condition_id = ? AND status = 'open'",
                (condition_id,),
            ).fetchone()
        return _row_to_trade(row) if row else None

    # ------------------------------------------------------------------
    # Market snapshots
    # ------------------------------------------------------------------

    def record_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Insert a market price/volume snapshot.

        Args:
            snapshot: Snapshot to persist.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_snapshots
                  (condition_id, yes_price, no_price, volume, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.condition_id,
                    snapshot.yes_price,
                    snapshot.no_price,
                    snapshot.volume,
                    _fmt(snapshot.recorded_at),
                ),
            )

    def get_recent_snapshots(self, condition_id: str, n: int = 10) -> list[MarketSnapshot]:
        """Return the N most recent snapshots for a market.

        Args:
            condition_id: Market condition ID.
            n: Number of snapshots to return.

        Returns:
            List of MarketSnapshot objects, oldest first.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM market_snapshots
                WHERE condition_id = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (condition_id, n),
            ).fetchall()
        snapshots = [_row_to_snapshot(r) for r in rows]
        return list(reversed(snapshots))  # chronological order


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolio (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            cash        REAL    NOT NULL DEFAULT 10000.0,
            updated_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id TEXT    NOT NULL,
            question     TEXT    NOT NULL,
            outcome      TEXT    NOT NULL,
            strategy     TEXT    NOT NULL,
            shares       REAL    NOT NULL,
            entry_price  REAL    NOT NULL,
            exit_price   REAL,
            take_profit  REAL    NOT NULL,
            stop_loss    REAL    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'open',
            pnl          REAL,
            opened_at    TEXT    NOT NULL,
            closed_at    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trades_condition
            ON paper_trades (condition_id);
        CREATE INDEX IF NOT EXISTS idx_trades_status
            ON paper_trades (status);

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_id TEXT    NOT NULL,
            yes_price    REAL    NOT NULL,
            no_price     REAL    NOT NULL,
            volume       REAL    NOT NULL,
            recorded_at  TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_condition
            ON market_snapshots (condition_id, recorded_at);
        """
    )


def _ensure_portfolio_row(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT id FROM portfolio WHERE id = 1").fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO portfolio (id, cash, updated_at) VALUES (1, ?, ?)",
            (STARTING_CAPITAL, _now()),
        )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
        # If naive, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _row_to_trade(row: sqlite3.Row) -> PaperTrade:
    return PaperTrade(
        id=row["id"],
        condition_id=row["condition_id"],
        question=row["question"],
        outcome=row["outcome"],
        strategy=row["strategy"],
        shares=float(row["shares"]),
        entry_price=float(row["entry_price"]),
        exit_price=float(row["exit_price"]) if row["exit_price"] is not None else None,
        take_profit=float(row["take_profit"]),
        stop_loss=float(row["stop_loss"]),
        status=row["status"],
        pnl=float(row["pnl"]) if row["pnl"] is not None else None,
        opened_at=_parse_dt(row["opened_at"]) or datetime.now(timezone.utc),
        closed_at=_parse_dt(row["closed_at"]),
    )


def _row_to_snapshot(row: sqlite3.Row) -> MarketSnapshot:
    return MarketSnapshot(
        id=row["id"],
        condition_id=row["condition_id"],
        yes_price=float(row["yes_price"]),
        no_price=float(row["no_price"]),
        volume=float(row["volume"]),
        recorded_at=_parse_dt(row["recorded_at"]) or datetime.now(timezone.utc),
    )
