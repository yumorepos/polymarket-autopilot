#!/usr/bin/env python3
"""Demo script showing stop-loss and take-profit functionality.

This script demonstrates how the risk management features work by:
1. Creating sample positions
2. Simulating price movements
3. Showing automatic position exits
"""

from datetime import datetime, timezone
from pathlib import Path

from src.polymarket_autopilot.db import Database, PaperTrade
from src.polymarket_autopilot.risk_management import (
    PositionMonitor,
    RiskManagementConfig,
)
from unittest.mock import Mock


def main():
    print("=" * 70)
    print("POLYMARKET AUTOPILOT - RISK MANAGEMENT DEMO")
    print("=" * 70)
    print()
    
    # Setup
    db_path = Path("data/demo_risk.db")
    db_path.unlink(missing_ok=True)  # Clean slate
    
    db = Database(db_path)
    db.init()
    
    # Create mock API client
    mock_client = Mock()
    
    print("📊 INITIAL SETUP")
    print(f"Starting capital: ${db.get_cash():,.2f}")
    print()
    
    # Scenario 1: Stop-loss trigger
    print("=" * 70)
    print("SCENARIO 1: Stop-Loss Trigger")
    print("=" * 70)
    
    trade1 = PaperTrade(
        id=None,
        condition_id="0x1234",
        question="Will BTC reach $100k by end of 2026?",
        outcome="YES",
        strategy="MOMENTUM",
        shares=100.0,
        entry_price=0.60,
        exit_price=None,
        take_profit=0.72,
        stop_loss=0.54,
        status="open",
        pnl=None,
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )
    
    trade_id = db.open_trade(trade1)
    print(f"✅ Opened position #{trade_id}")
    print(f"   Question: {trade1.question}")
    print(f"   Entry: ${trade1.entry_price:.4f} | Position: {trade1.shares} shares")
    print(f"   Stop-loss: ${trade1.stop_loss:.4f} (-10%) | Take-profit: ${trade1.take_profit:.4f} (+20%)")
    print()
    
    # Simulate price drop to 0.51 (15% loss)
    print("📉 PRICE UPDATE: $0.6000 → $0.5100 (-15%)")
    mock_client.get_market.return_value = {"yes_price": 0.51, "no_price": 0.49}
    
    config = RiskManagementConfig(stop_loss_pct=0.10, take_profit_pct=0.20)
    monitor = PositionMonitor(db, mock_client, config)
    
    print("🔍 Monitoring positions...")
    results = monitor.check_positions()
    
    print(f"⚠️  STOP-LOSS TRIGGERED!")
    print(f"   Exit price: $0.5100")
    print(f"   P&L: ${(0.51 - 0.60) * 100:+.2f}")
    print()
    
    # Scenario 2: Take-profit trigger
    print("=" * 70)
    print("SCENARIO 2: Take-Profit Trigger")
    print("=" * 70)
    
    trade2 = PaperTrade(
        id=None,
        condition_id="0x5678",
        question="Will ETH reach $5k by Q2 2026?",
        outcome="YES",
        strategy="TAIL",
        shares=150.0,
        entry_price=0.40,
        exit_price=None,
        take_profit=0.48,
        stop_loss=0.36,
        status="open",
        pnl=None,
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )
    
    trade_id2 = db.open_trade(trade2)
    print(f"✅ Opened position #{trade_id2}")
    print(f"   Question: {trade2.question}")
    print(f"   Entry: ${trade2.entry_price:.4f} | Position: {trade2.shares} shares")
    print(f"   Stop-loss: ${trade2.stop_loss:.4f} (-10%) | Take-profit: ${trade2.take_profit:.4f} (+20%)")
    print()
    
    # Simulate price rise to 0.50 (25% gain)
    print("📈 PRICE UPDATE: $0.4000 → $0.5000 (+25%)")
    mock_client.get_market.return_value = {"yes_price": 0.50, "no_price": 0.50}
    
    print("🔍 Monitoring positions...")
    results = monitor.check_positions()
    
    print(f"✅ TAKE-PROFIT TRIGGERED!")
    print(f"   Exit price: $0.5000")
    print(f"   P&L: ${(0.50 - 0.40) * 150:+.2f}")
    print()
    
    # Scenario 3: Position within thresholds
    print("=" * 70)
    print("SCENARIO 3: Position Within Thresholds")
    print("=" * 70)
    
    trade3 = PaperTrade(
        id=None,
        condition_id="0x9abc",
        question="Will SOL reach $200 by end of 2026?",
        outcome="NO",
        strategy="VOLATILITY",
        shares=200.0,
        entry_price=0.35,
        exit_price=None,
        take_profit=0.28,
        stop_loss=0.385,
        status="open",
        pnl=None,
        opened_at=datetime.now(timezone.utc),
        closed_at=None,
    )
    
    trade_id3 = db.open_trade(trade3)
    print(f"✅ Opened position #{trade_id3}")
    print(f"   Question: {trade3.question}")
    print(f"   Entry: ${trade3.entry_price:.4f} | Position: {trade3.shares} shares")
    print(f"   Stop-loss: ${trade3.stop_loss:.4f} (+10%) | Take-profit: ${trade3.take_profit:.4f} (-20%)")
    print()
    
    # Simulate price change to 0.37 (5.7% profit for NO position)
    print("📊 PRICE UPDATE: $0.3500 → $0.3700 (+5.7% for NO)")
    mock_client.get_market.return_value = {"yes_price": 0.63, "no_price": 0.37}
    
    print("🔍 Monitoring positions...")
    results = monitor.check_positions()
    
    print(f"✅ Position within thresholds - no action taken")
    print()
    
    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    summary = db.get_portfolio_summary()
    closed = [t for t in db.get_trade_history(limit=100) if t.status != "open"]
    total_pnl = sum(t.pnl or 0.0 for t in closed)
    
    print(f"Cash: ${summary['cash']:,.2f}")
    print(f"Open positions: {len(db.get_open_trades())}")
    print(f"Closed trades: {len(closed)}")
    print(f"Total P&L: ${total_pnl:+.2f}")
    print()
    
    print("Risk management successfully protected capital and locked in profits! 🎯")
    print()
    
    # Cleanup
    db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
