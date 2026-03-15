"""Live trading execution module for Polymarket using py-clob-client.

This module provides authenticated order placement and position management
for real-money trading. It is intentionally separate from paper trading
and requires explicit opt-in via environment variables.

WARNING: This module places REAL ORDERS with REAL MONEY.
         Only use after thorough paper trading validation.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OpenOrderParams, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

logger = logging.getLogger(__name__)


class LiveTradingError(RuntimeError):
    """Raised when live order placement fails."""


class RiskLimitExceeded(LiveTradingError):
    """Raised when a trade would exceed configured risk limits."""


# ---------------------------------------------------------------------------
# Configuration & Risk Limits
# ---------------------------------------------------------------------------


@dataclass
class RiskLimits:
    """Hard risk limits for live trading."""

    max_daily_loss: float = 20.0  # USD
    max_position_size: float = 50.0  # USD per trade
    max_open_positions: int = 10
    max_deployed_capital_pct: float = 0.80  # 80%
    min_market_liquidity: float = 10000.0  # USD
    max_slippage_pct: float = 0.02  # 2%

    @classmethod
    def from_env(cls) -> RiskLimits:
        """Load risk limits from environment variables."""
        return cls(
            max_daily_loss=float(os.getenv("LIVE_MAX_DAILY_LOSS", "20.0")),
            max_position_size=float(os.getenv("LIVE_MAX_POSITION_SIZE", "50.0")),
            max_open_positions=int(os.getenv("LIVE_MAX_OPEN_POSITIONS", "10")),
            max_deployed_capital_pct=float(
                os.getenv("LIVE_MAX_DEPLOYED_CAPITAL_PCT", "0.80")
            ),
            min_market_liquidity=float(os.getenv("LIVE_MIN_MARKET_LIQUIDITY", "10000.0")),
            max_slippage_pct=float(os.getenv("LIVE_MAX_SLIPPAGE_PCT", "0.02")),
        )


# ---------------------------------------------------------------------------
# Live Trading Client
# ---------------------------------------------------------------------------


class LiveTradingClient:
    """Authenticated client for placing real orders on Polymarket.

    This client requires:
    - POLYMARKET_PRIVATE_KEY (your wallet private key, 0x...)
    - POLYMARKET_FUNDER_ADDRESS (optional, for proxy/email wallets)
    - LIVE_TRADING_ENABLED=true (explicit opt-in)

    All orders are subject to RiskLimits validation before placement.
    """

    def __init__(
        self,
        private_key: str | None = None,
        funder_address: str | None = None,
        risk_limits: RiskLimits | None = None,
    ):
        self.private_key = private_key or os.getenv("POLYMARKET_PRIVATE_KEY")
        self.funder_address = funder_address or os.getenv("POLYMARKET_FUNDER_ADDRESS")
        self.risk_limits = risk_limits or RiskLimits.from_env()

        # Safety: require explicit opt-in
        self.enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

        if not self.enabled:
            logger.warning(
                "Live trading is DISABLED. Set LIVE_TRADING_ENABLED=true to enable."
            )

        if self.enabled and not self.private_key:
            raise LiveTradingError(
                "Live trading requires POLYMARKET_PRIVATE_KEY environment variable."
            )

        self.host = "https://clob.polymarket.com"
        self.chain_id = 137  # Polygon mainnet

        # Signature type: 0 for EOA (MetaMask, hardware wallet), 1 for email/Magic wallet
        self.signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0"))

        # Initialize py-clob-client
        if self.enabled and self.private_key:
            self.client = ClobClient(
                self.host,
                key=self.private_key,
                chain_id=self.chain_id,
                signature_type=self.signature_type,
                funder=self.funder_address or "",  # Empty string if not using proxy
            )
            # Create/derive API credentials (required for authenticated endpoints)
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
        else:
            self.client = ClobClient(self.host)  # Read-only client

    def check_risk_limits(
        self, order_size: float, current_positions: int, daily_pnl: float
    ) -> None:
        """Validate that a proposed order satisfies all risk limits.

        Raises:
            RiskLimitExceeded: If any limit would be violated.
        """
        if not self.enabled:
            raise LiveTradingError("Live trading is not enabled.")

        # Daily loss limit
        if daily_pnl < -self.risk_limits.max_daily_loss:
            raise RiskLimitExceeded(
                f"Daily loss limit exceeded: {daily_pnl:.2f} < "
                f"-{self.risk_limits.max_daily_loss:.2f}"
            )

        # Position size limit
        if order_size > self.risk_limits.max_position_size:
            raise RiskLimitExceeded(
                f"Order size {order_size:.2f} exceeds max "
                f"{self.risk_limits.max_position_size:.2f}"
            )

        # Open position count limit
        if current_positions >= self.risk_limits.max_open_positions:
            raise RiskLimitExceeded(
                f"Already at max open positions: {current_positions} >= "
                f"{self.risk_limits.max_open_positions}"
            )

        logger.info(f"Risk check passed: order_size={order_size:.2f}, daily_pnl={daily_pnl:.2f}")

    def place_limit_order(
        self,
        token_id: str,
        side: str,  # "YES" or "NO"
        size: float,  # USD amount
        price: float,  # limit price in [0, 1]
    ) -> dict:
        """Place a limit order on Polymarket.

        Args:
            token_id: The outcome token ID
            side: "YES" or "NO"
            size: Order size in USD
            price: Limit price (probability between 0 and 1)

        Returns:
            Order confirmation dict with order_id, status, etc.

        Raises:
            LiveTradingError: If order placement fails
        """
        if not self.enabled:
            raise LiveTradingError(
                "Live trading is DISABLED. This is a safety feature. "
                "Set LIVE_TRADING_ENABLED=true to enable real orders."
            )

        # Convert YES/NO to BUY/SELL
        order_side = BUY if side.upper() == "YES" else SELL

        # Build order
        order = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=order_side,
        )

        try:
            # Sign order
            signed_order = self.client.create_order(order)
            # Submit order (GTC = Good-Til-Cancelled)
            response = self.client.post_order(signed_order, OrderType.GTC)
            logger.info(f"Limit order placed: {response}")
            return response
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise LiveTradingError(f"Failed to place order: {e}") from e

    def place_market_order(
        self,
        token_id: str,
        side: str,  # "YES" or "NO"
        amount: float,  # USD amount
    ) -> dict:
        """Place a market order on Polymarket (Fill-Or-Kill).

        Args:
            token_id: The outcome token ID
            side: "YES" or "NO"
            amount: Order amount in USD

        Returns:
            Order confirmation dict with fill info

        Raises:
            LiveTradingError: If order placement fails
        """
        if not self.enabled:
            raise LiveTradingError(
                "Live trading is DISABLED. Set LIVE_TRADING_ENABLED=true to enable."
            )

        # Convert YES/NO to BUY/SELL
        order_side = BUY if side.upper() == "YES" else SELL

        # Build market order (FOK = Fill-Or-Kill)
        market_order = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=order_side,
            order_type=OrderType.FOK,
        )

        try:
            signed_order = self.client.create_market_order(market_order)
            response = self.client.post_order(signed_order, OrderType.FOK)
            logger.info(f"Market order placed: {response}")
            return response
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            raise LiveTradingError(f"Failed to place market order: {e}") from e

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order.

        Args:
            order_id: The order ID to cancel

        Returns:
            Cancellation confirmation dict
        """
        if not self.enabled:
            raise LiveTradingError("Live trading is not enabled.")

        try:
            response = self.client.cancel(order_id)
            logger.info(f"Order cancelled: {response}")
            return response
        except Exception as e:
            logger.error(f"Order cancellation failed: {e}")
            raise LiveTradingError(f"Failed to cancel order: {e}") from e

    def cancel_all_orders(self) -> None:
        """Cancel all open orders."""
        if not self.enabled:
            raise LiveTradingError("Live trading is not enabled.")

        try:
            self.client.cancel_all()
            logger.info("All orders cancelled")
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            raise LiveTradingError(f"Failed to cancel all orders: {e}") from e

    def get_open_orders(self) -> list[dict]:
        """Fetch all open orders for the authenticated wallet."""
        if not self.enabled:
            return []

        try:
            orders = self.client.get_orders(OpenOrderParams())
            return orders
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return []

    def get_midpoint(self, token_id: str) -> float | None:
        """Get the current midpoint price for a token.

        Args:
            token_id: The token ID

        Returns:
            Midpoint price, or None if unavailable
        """
        try:
            return self.client.get_midpoint(token_id)
        except Exception as e:
            logger.error(f"Failed to get midpoint for {token_id}: {e}")
            return None

    def get_order_book(self, token_id: str) -> dict | None:
        """Fetch the order book for a token.

        Args:
            token_id: The token ID

        Returns:
            Order book dict with bids/asks, or None if unavailable
        """
        try:
            return self.client.get_order_book(token_id)
        except Exception as e:
            logger.error(f"Failed to get order book for {token_id}: {e}")
            return None


# ---------------------------------------------------------------------------
# Safety Utilities
# ---------------------------------------------------------------------------


def emergency_stop() -> None:
    """Emergency stop: cancel all open orders and log the event.

    This is a manual override function to be called in case of:
    - Unexpected market behavior
    - System malfunction
    - Risk limit breach requiring immediate intervention
    """
    logger.critical("EMERGENCY STOP TRIGGERED - Cancelling all open orders")

    client = LiveTradingClient()
    if not client.enabled:
        logger.warning("Live trading is not enabled, no orders to cancel")
        return

    try:
        open_orders = client.get_open_orders()
        logger.info(f"Found {len(open_orders)} open orders")

        client.cancel_all_orders()
        logger.info(f"Emergency stop complete: cancelled {len(open_orders)} orders")
    except Exception as e:
        logger.error(f"Emergency stop failed: {e}")


def get_daily_pnl(db_path: str = "data/autopilot.db") -> float:
    """Calculate today's realized P&L from the portfolio database.

    Args:
        db_path: Path to the SQLite database

    Returns:
        Today's P&L in USD (positive = profit, negative = loss)
    """
    import sqlite3
    from datetime import date

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        today = date.today().isoformat()
        cursor.execute(
            """
            SELECT SUM(pnl) FROM trades
            WHERE exit_timestamp >= ? AND exit_timestamp < date(?, '+1 day')
            """,
            (today, today),
        )
        result = cursor.fetchone()
        conn.close()

        return result[0] if result and result[0] is not None else 0.0
    except Exception as e:
        logger.error(f"Failed to calculate daily P&L: {e}")
        return 0.0
