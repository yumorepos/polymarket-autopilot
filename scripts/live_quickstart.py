#!/usr/bin/env python3
"""Quick-start script for live trading on Polymarket.

This script helps you set up and validate your live trading configuration
before deploying automated strategies.

Usage:
    python scripts/live_quickstart.py --step 1  # Test API connection
    python scripts/live_quickstart.py --step 2  # Check balance & positions
    python scripts/live_quickstart.py --step 3  # Place $10 test order
    python scripts/live_quickstart.py --step 4  # Start automated trading
"""

import argparse
import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from polymarket_autopilot.live_trading import LiveTradingClient, get_daily_pnl


def step1_test_connection():
    """Step 1: Test API connection (read-only)."""
    print("\n" + "=" * 70)
    print("STEP 1: Testing API Connection")
    print("=" * 70)

    # Check environment variables
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"

    if not private_key:
        print("❌ POLYMARKET_PRIVATE_KEY not set")
        print("\nTo fix this:")
        print("1. Copy .env.live.example to .env")
        print("2. Add your private key: POLYMARKET_PRIVATE_KEY=0x...")
        print("3. Keep LIVE_TRADING_ENABLED=false for now")
        return False

    if enabled:
        print("⚠️  LIVE_TRADING_ENABLED is set to 'true'")
        print("   For testing, keep it 'false' until Step 3")

    print("✅ Private key configured")

    # Initialize client (read-only mode)
    try:
        client = LiveTradingClient()
        print(f"✅ Client initialized (host: {client.host})")

        # Test read-only endpoint (get simplified markets)
        markets = client.client.get_simplified_markets()
        market_count = len(markets.get("data", []))
        print(f"✅ API connection successful ({market_count} markets found)")

        return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False


def step2_check_balance():
    """Step 2: Check wallet balance and open positions."""
    print("\n" + "=" * 70)
    print("STEP 2: Checking Wallet Balance & Positions")
    print("=" * 70)

    # Temporarily enable live mode to access authenticated endpoints
    os.environ["LIVE_TRADING_ENABLED"] = "true"

    try:
        client = LiveTradingClient()

        # Get open orders
        open_orders = client.get_open_orders()
        print(f"Open orders: {len(open_orders)}")

        if open_orders:
            print("\nCurrent open orders:")
            for order in open_orders[:5]:  # Show first 5
                print(f"  - {order.get('id')}: {order.get('side')} @ {order.get('price')}")

        # Calculate daily P&L
        daily_pnl = get_daily_pnl()
        print(f"\nToday's P&L: ${daily_pnl:+.2f}")

        print("\n✅ Balance check complete")
        return True

    except Exception as e:
        print(f"❌ Balance check failed: {e}")
        return False
    finally:
        # Reset to safe default
        os.environ["LIVE_TRADING_ENABLED"] = "false"


def step3_test_order():
    """Step 3: Place a small $10 test order."""
    print("\n" + "=" * 70)
    print("STEP 3: Placing $10 Test Order")
    print("=" * 70)

    print("⚠️  THIS WILL PLACE A REAL ORDER WITH REAL MONEY")
    print("\nTest order parameters:")
    print("  - Token: Will use first available market")
    print("  - Side: YES")
    print("  - Amount: $10")
    print("  - Type: Limit order at current midpoint")

    confirm = input("\nType 'YES' to proceed: ")
    if confirm.strip().upper() != "YES":
        print("❌ Test order cancelled")
        return False

    # Enable live mode
    os.environ["LIVE_TRADING_ENABLED"] = "true"

    try:
        client = LiveTradingClient()

        # Get a market to trade
        markets = client.client.get_simplified_markets()
        market_data = markets.get("data", [])[0] if markets.get("data") else None

        if not market_data:
            print("❌ No markets found")
            return False

        token_id = market_data["tokens"][0]["token_id"]
        question = market_data["question"]
        print(f"\nSelected market: {question[:60]}...")
        print(f"Token ID: {token_id}")

        # Get current price
        midpoint = client.get_midpoint(token_id)
        if not midpoint:
            print("❌ Failed to get market price")
            return False

        print(f"Current price: {midpoint:.3f}")

        # Place limit order at midpoint
        print(f"\nPlacing limit order: $10 @ {midpoint:.3f}...")
        response = client.place_limit_order(
            token_id=token_id,
            side="YES",
            size=10.0,
            price=midpoint,
        )

        print(f"✅ Order placed successfully!")
        print(f"Order ID: {response.get('orderID', 'N/A')}")
        print(f"Status: {response.get('status', 'N/A')}")

        print("\n⚠️  IMPORTANT: Monitor this order and manually close it after 24 hours")
        print("   to validate execution quality before starting automated trading.")

        return True

    except Exception as e:
        print(f"❌ Test order failed: {e}")
        return False
    finally:
        os.environ["LIVE_TRADING_ENABLED"] = "false"


def step4_start_live():
    """Step 4: Start automated live trading."""
    print("\n" + "=" * 70)
    print("STEP 4: Starting Automated Live Trading")
    print("=" * 70)

    print("⚠️  THIS WILL START REAL AUTOMATED TRADING")
    print("\nPre-flight checklist:")
    print("  [ ] Test order from Step 3 executed successfully")
    print("  [ ] Slippage was <2%")
    print("  [ ] No API errors or timeouts")
    print("  [ ] Risk limits configured in .env")
    print("  [ ] Monitoring alerts set up (Telegram)")
    print("  [ ] Emergency stop procedure documented")

    confirm = input("\nType 'START LIVE TRADING' to proceed: ")
    if confirm.strip() != "START LIVE TRADING":
        print("❌ Live trading start cancelled")
        return False

    print("\n✅ Starting live trading...")
    print("\nRun these commands in separate terminals:")
    print("\n1. Start monitoring:")
    print("   python scripts/monitor_live_trading.py --interval 300")
    print("\n2. Start trading bot:")
    print("   LIVE_TRADING_ENABLED=true polymarket-autopilot trade --strategy ALL")

    print("\n⚠️  CRITICAL: Keep monitoring terminal open at all times")
    print("   If daily loss limit is hit, trading will auto-stop")

    return True


def main():
    parser = argparse.ArgumentParser(description="Polymarket Live Trading Quick-Start")
    parser.add_argument(
        "--step",
        type=int,
        choices=[1, 2, 3, 4],
        required=True,
        help="Which setup step to run (1-4)",
    )
    args = parser.parse_args()

    steps = {
        1: step1_test_connection,
        2: step2_check_balance,
        3: step3_test_order,
        4: step4_start_live,
    }

    success = steps[args.step]()

    if success and args.step < 4:
        print(f"\n✅ Step {args.step} complete. Next: python scripts/live_quickstart.py --step {args.step + 1}")
    elif success:
        print("\n✅ All steps complete. Live trading is now active.")
        print("   Monitor closely for the first 24 hours.")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
