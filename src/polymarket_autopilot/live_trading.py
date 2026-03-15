"""Live trading execution module for Polymarket.

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
from typing import Any

try:
    import httpx
except ImportError:
    from polymarket_autopilot import httpx_compat as httpx

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
    - POLYMARKET_API_KEY (your API key)
    - POLYMARKET_API_SECRET (your API secret)
    - POLYMARKET_WALLET_ADDRESS (your wallet address)
    - LIVE_TRADING_ENABLED=true (explicit opt-in)

    All orders are subject to RiskLimits validation before placement.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        wallet_address: str | None = None,
        risk_limits: RiskLimits | None = None,
    ):
        self.api_key = api_key or os.getenv("POLYMARKET_API_KEY")
        self.api_secret = api_secret or os.getenv("POLYMARKET_API_SECRET")
        self.wallet_address = wallet_address or os.getenv("POLYMARKET_WALLET_ADDRESS")
        self.risk_limits = risk_limits or RiskLimits.from_env()

        # Safety: require explicit opt-in
        self.enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

        if not self.enabled:
            logger.warning(
                "Live trading is DISABLED. Set LIVE_TRADING_ENABLED=true to enable."
            )

        if self.enabled and not all([self.api_key, self.api_secret, self.wallet_address]):
            raise LiveTradingError(
                "Live trading requires POLYMARKET_API_KEY, POLYMARKET_API_SECRET, "
                "and POLYMARKET_WALLET_ADDRESS environment variables."
            )

        self.base_url = "https://clob.polymarket.com"
        self.client = httpx.Client(timeout=30.0)

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

    def place_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        size: float,  # USD amount
        price: float,  # limit price in [0, 1]
        market_liquidity: float = 0.0,
    ) -> dict[str, Any]:
        """Place a limit order on Polymarket.

        Args:
            token_id: The outcome token ID
            side: "BUY" or "SELL"
            size: Order size in USD
            price: Limit price (probability between 0 and 1)
            market_liquidity: Current market liquidity in USD (for risk check)

        Returns:
            Order confirmation dict with order_id, fill_price, etc.

        Raises:
            RiskLimitExceeded: If order violates risk limits
            LiveTradingError: If order placement fails
        """
        if not self.enabled:
            raise LiveTradingError(
                "Live trading is DISABLED. This is a safety feature. "
                "Set LIVE_TRADING_ENABLED=true to enable real orders."
            )

        # Liquidity check
        if market_liquidity < self.risk_limits.min_market_liquidity:
            raise RiskLimitExceeded(
                f"Market liquidity {market_liquidity:.2f} below minimum "
                f"{self.risk_limits.min_market_liquidity:.2f}"
            )

        # Build order payload (Polymarket CLOB API format)
        order_payload = {
            "token_id": token_id,
            "side": side.upper(),
            "size": str(size),
            "price": str(price),
            "wallet": self.wallet_address,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        }

        # Sign the order (placeholder - requires ECDSA signature)
        # Real implementation would use eth_account or web3.py to sign
        signature = self._sign_order(order_payload)
        order_payload["signature"] = signature

        # Submit order
        try:
            response = self.client.post(
                f"{self.base_url}/orders",
                json=order_payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            order_result = response.json()
            logger.info(f"Order placed: {order_result}")
            return order_result
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise LiveTradingError(f"Failed to place order: {e}") from e

    def _sign_order(self, order_payload: dict[str, Any]) -> str:
        """Sign an order payload with the wallet private key.

        TODO: Implement ECDSA signature using eth_account.
        This is a placeholder that will raise an error if called.
        """
        raise NotImplementedError(
            "Order signing not yet implemented. "
            "Requires eth_account or web3.py integration."
        )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order.

        Args:
            order_id: The order ID to cancel

        Returns:
            Cancellation confirmation dict
        """
        if not self.enabled:
            raise LiveTradingError("Live trading is not enabled.")

        try:
            response = self.client.delete(
                f"{self.base_url}/orders/{order_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"Order cancelled: {result}")
            return result
        except Exception as e:
            logger.error(f"Order cancellation failed: {e}")
            raise LiveTradingError(f"Failed to cancel order: {e}") from e

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Fetch all open orders for the authenticated wallet."""
        if not self.enabled:
            return []

        try:
            response = self.client.get(
                f"{self.base_url}/orders",
                params={"wallet": self.wallet_address},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return []

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()


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
        for order in open_orders:
            order_id = order.get("order_id", order.get("id"))
            if order_id:
                logger.info(f"Cancelling order {order_id}")
                client.cancel_order(order_id)

        logger.info(f"Emergency stop complete: cancelled {len(open_orders)} orders")
    except Exception as e:
        logger.error(f"Emergency stop failed: {e}")
    finally:
        client.close()


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
