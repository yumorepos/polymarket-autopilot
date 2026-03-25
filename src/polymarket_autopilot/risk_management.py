"""Risk management module for stop-loss and take-profit orders.

This module monitors open positions and automatically exits when
stop-loss or take-profit thresholds are breached.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_autopilot.api import Market, PolymarketClient
    from polymarket_autopilot.db import Database, PaperTrade

logger = logging.getLogger(__name__)


@dataclass
class RiskManagementConfig:
    """Configuration for stop-loss and take-profit thresholds.
    
    Attributes:
        stop_loss_pct: Percentage loss threshold (e.g., 0.10 for -10%)
        take_profit_pct: Percentage profit threshold (e.g., 0.20 for +20%)
    """
    stop_loss_pct: float = 0.10  # Default: -10%
    take_profit_pct: float = 0.20  # Default: +20%
    
    def calculate_stop_loss_price(self, entry_price: float, outcome: str) -> float:
        """Calculate stop-loss exit price.
        
        Args:
            entry_price: Original entry price (probability)
            outcome: "YES" or "NO"
            
        Returns:
            Stop-loss price threshold
        """
        if outcome.upper() == "YES":
            # For YES positions, stop-loss triggers when price falls
            return entry_price * (1 - self.stop_loss_pct)
        else:
            # For NO positions, stop-loss triggers when price rises
            return entry_price * (1 + self.stop_loss_pct)
    
    def calculate_take_profit_price(self, entry_price: float, outcome: str) -> float:
        """Calculate take-profit exit price.
        
        Args:
            entry_price: Original entry price (probability)
            outcome: "YES" or "NO"
            
        Returns:
            Take-profit price threshold
        """
        if outcome.upper() == "YES":
            # For YES positions, take-profit triggers when price rises
            return entry_price * (1 + self.take_profit_pct)
        else:
            # For NO positions, take-profit triggers when price falls
            return entry_price * (1 - self.take_profit_pct)


class PositionMonitor:
    """Monitors open positions and executes stop-loss/take-profit orders.
    
    Args:
        db: Database instance
        client: Polymarket API client
        config: Risk management configuration
    """
    
    def __init__(
        self,
        db: Database,
        client: PolymarketClient,
        config: RiskManagementConfig | None = None,
    ) -> None:
        self.db = db
        self.client = client
        self.config = config or RiskManagementConfig()
    
    def check_positions(self) -> dict[str, int]:
        """Check all open positions and execute stop-loss/take-profit if needed.
        
        Returns:
            Dictionary with counts: {"stop_loss": N, "take_profit": M, "unchanged": K}
        """
        open_trades = self.db.get_open_trades()
        
        if not open_trades:
            logger.info("No open positions to monitor")
            return {"stop_loss": 0, "take_profit": 0, "unchanged": 0}
        
        logger.info(f"Monitoring {len(open_trades)} open positions")
        
        stop_loss_count = 0
        take_profit_count = 0
        unchanged_count = 0
        
        for trade in open_trades:
            action = self._check_single_position(trade)
            
            if action == "stop_loss":
                stop_loss_count += 1
            elif action == "take_profit":
                take_profit_count += 1
            else:
                unchanged_count += 1
        
        logger.info(
            f"Position check complete: {stop_loss_count} stop-loss, "
            f"{take_profit_count} take-profit, {unchanged_count} unchanged"
        )
        
        return {
            "stop_loss": stop_loss_count,
            "take_profit": take_profit_count,
            "unchanged": unchanged_count,
        }
    
    def _check_single_position(self, trade: PaperTrade) -> str:
        """Check a single position and execute exit if threshold breached.
        
        Args:
            trade: Open paper trade to check
            
        Returns:
            Action taken: "stop_loss", "take_profit", or "unchanged"
        """
        try:
            # Fetch current market price (handles async/sync client)
            result = self.client.get_market(trade.condition_id)
            market: Market | None = asyncio.run(result) if inspect.iscoroutine(result) else result  # type: ignore[assignment]
            if market is None:
                logger.warning(f"Market not found for {trade.condition_id}")
                return "unchanged"
            current_price = (
                market.yes_price if trade.outcome.upper() == "YES"
                else market.no_price
            )
            if current_price is None:
                logger.warning(f"No price for outcome {trade.outcome} in {trade.condition_id}")
                return "unchanged"
            
            # Calculate P&L percentage
            if trade.outcome.upper() == "YES":
                pnl_pct = (current_price - trade.entry_price) / trade.entry_price
            else:
                pnl_pct = (trade.entry_price - current_price) / trade.entry_price
            
            # Check stop-loss threshold
            if pnl_pct <= -self.config.stop_loss_pct:
                logger.warning(
                    f"STOP-LOSS triggered for {trade.question[:50]}... "
                    f"(P&L: {pnl_pct*100:.2f}%, threshold: -{self.config.stop_loss_pct*100:.0f}%)"
                )
                self._execute_exit(trade, current_price, "closed_sl")
                return "stop_loss"
            
            # Check take-profit threshold
            if pnl_pct >= self.config.take_profit_pct:
                logger.info(
                    f"TAKE-PROFIT triggered for {trade.question[:50]}... "
                    f"(P&L: {pnl_pct*100:.2f}%, threshold: +{self.config.take_profit_pct*100:.0f}%)"
                )
                self._execute_exit(trade, current_price, "closed_tp")
                return "take_profit"
            
            logger.debug(
                f"Position within thresholds: {trade.question[:50]}... "
                f"(P&L: {pnl_pct*100:.2f}%)"
            )
            return "unchanged"
            
        except Exception as e:
            logger.error(
                f"Error checking position {trade.condition_id}: {e}",
                exc_info=True,
            )
            return "unchanged"
    
    def _execute_exit(
        self,
        trade: PaperTrade,
        exit_price: float,
        status: str,
    ) -> None:
        """Execute position exit and update database.
        
        Args:
            trade: Trade to exit
            exit_price: Current market price
            status: Exit status ("closed_sl" or "closed_tp")
        """
        # Calculate P&L for logging (db.close_trade will recalculate)
        pnl = (exit_price - trade.entry_price) * trade.shares
        
        # Update trade in database (close_trade handles cash update internally)
        self.db.close_trade(
            trade_id=trade.id,  # type: ignore
            exit_price=exit_price,
            status=status,
            closed_at=datetime.now(timezone.utc),
        )
        
        action_name = "STOP-LOSS" if status == "closed_sl" else "TAKE-PROFIT"
        logger.info(
            f"{action_name} executed: {trade.question[:50]}... | "
            f"Entry: ${trade.entry_price:.4f}, Exit: ${exit_price:.4f}, "
            f"P&L: ${pnl:+.2f}"
        )
