#!/usr/bin/env python3
"""
Trading Agency Orchestrator
Coordinates Market Scanner → Risk Manager → Executor → Performance Monitor
"""

import sys
import time
from datetime import datetime
from typing import Dict, List, Optional
import requests

# Configuration
BANKROLL = 600  # Starting capital
KELLY_FRACTION = 0.5
MAX_POSITION_PCT = 0.05
MAX_CONCURRENT = 3
MIN_EDGE = 0.005  # 0.5% minimum edge

class MarketScanner:
    """Finds arbitrage opportunities across 200+ markets"""
    
    def __init__(self):
        self.data_api = "https://data-api.polymarket.com"
        self.min_edge = MIN_EDGE
        self.min_volume = 1000
    
    def scan(self) -> List[Dict]:
        """Scan all active markets for opportunities"""
        print(f"[{self._timestamp()}] 🔍 MARKET SCANNER: Scanning markets...")
        
        try:
            # Fetch recent trades (last 5 min)
            cutoff = int(time.time() - 300)
            response = requests.get(f"{self.data_api}/trades", 
                                   params={"min_timestamp": cutoff}, 
                                   timeout=10)
            trades = response.json()
            
            # Group by market
            markets = {}
            for trade in trades:
                mid = trade.get("market")
                if mid not in markets:
                    markets[mid] = {"title": trade.get("title", "Unknown"), "trades": [], "volume": 0}
                markets[mid]["trades"].append(trade)
                markets[mid]["volume"] += abs(trade.get("size", 0) * trade.get("price", 0))
            
            # Calculate edges
            opportunities = []
            for mid, data in markets.items():
                if len(data["trades"]) < 10:
                    continue
                
                p_yes = self._weighted_price(data["trades"], "Yes")
                p_no = self._weighted_price(data["trades"], "No")
                
                sum_prob = p_yes + p_no
                if sum_prob < 0.98:  # 2% gap after fees
                    edge = (1.0 - sum_prob) * 100
                    opportunities.append({
                        "market": data["title"],
                        "market_id": mid,
                        "p_yes": p_yes,
                        "p_no": p_no,
                        "edge": edge,
                        "volume": data["volume"],
                        "ev": edge * data["volume"]
                    })
            
            opportunities.sort(key=lambda x: x["ev"], reverse=True)
            
            print(f"[{self._timestamp()}] ✅ Found {len(opportunities)} opportunities")
            return opportunities[:10]
            
        except Exception as e:
            print(f"[{self._timestamp()}] ❌ Scanner error: {e}")
            return []
    
    def _weighted_price(self, trades: List[Dict], outcome: str) -> float:
        """Calculate time-weighted average price"""
        filtered = [t for t in trades if t.get("outcome") == outcome]
        if not filtered:
            return 0.0
        
        now = time.time()
        weighted_sum = weight_total = 0
        
        for trade in filtered:
            minutes_ago = (now - trade.get("timestamp", now)) / 60
            weight = 0.9 ** minutes_ago
            weighted_sum += trade.get("price", 0) * weight
            weight_total += weight
        
        return weighted_sum / weight_total if weight_total > 0 else 0.0
    
    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")


class RiskManager:
    """Calculates position sizes using Kelly Criterion"""
    
    def __init__(self, bankroll: float):
        self.bankroll = bankroll
        self.kelly_fraction = KELLY_FRACTION
        self.max_position = bankroll * MAX_POSITION_PCT
        self.min_bet = 10
        self.active_positions = 0
        self.daily_pnl = 0
    
    def approve_trade(self, opportunity: Dict) -> Optional[Dict]:
        """Validate and size trade"""
        print(f"[{self._timestamp()}] 🛡️ RISK MANAGER: Reviewing {opportunity['market'][:50]}...")
        
        # Circuit breaker
        if self.daily_pnl <= -0.05 * self.bankroll:
            print(f"[{self._timestamp()}] ❌ REJECTED: Circuit breaker (-5% daily)")
            return None
        
        # Position limit
        if self.active_positions >= MAX_CONCURRENT:
            print(f"[{self._timestamp()}] ❌ REJECTED: Max positions ({MAX_CONCURRENT})")
            return None
        
        # Edge check
        edge = opportunity["edge"] / 100
        if edge <= 0:
            print(f"[{self._timestamp()}] ❌ REJECTED: Negative edge")
            return None
        
        # Calculate Kelly size
        odds = 2.0  # Even money approximation
        kelly_size = (edge / odds) * self.kelly_fraction * self.bankroll
        position_size = max(self.min_bet, min(kelly_size, self.max_position))
        
        print(f"[{self._timestamp()}] ✅ APPROVED: ${position_size:.2f} position")
        print(f"[{self._timestamp()}]    Edge: {edge*100:.2f}% | Risk: {position_size/self.bankroll*100:.1f}%")
        
        return {
            "opportunity": opportunity,
            "position_size": position_size,
            "edge": edge,
            "approved": True
        }
    
    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")


class Executor:
    """Simulates trade execution (paper trading)"""
    
    def execute(self, approved_trade: Dict) -> Dict:
        """Execute trade (paper mode)"""
        opp = approved_trade["opportunity"]
        size = approved_trade["position_size"]
        
        print(f"[{self._timestamp()}] ⚡ EXECUTOR: Placing order...")
        print(f"[{self._timestamp()}]    Market: {opp['market'][:50]}")
        print(f"[{self._timestamp()}]    Size: ${size:.2f} | Edge: {opp['edge']:.2f}%")
        
        # Simulate execution
        entry_price = opp["p_yes"]
        slippage = 0.002  # 0.2% typical slippage
        fill_price = entry_price * (1 + slippage)
        
        result = {
            "status": "FILLED",
            "market": opp["market"],
            "position_size": size,
            "entry_price": entry_price,
            "fill_price": fill_price,
            "slippage": slippage * 100,
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"[{self._timestamp()}] ✅ Order filled @ ${fill_price:.4f} (slippage: {slippage*100:.2f}%)")
        return result
    
    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")


class PerformanceMonitor:
    """Tracks P&L and system health"""
    
    def __init__(self, starting_bankroll: float):
        self.starting_bankroll = starting_bankroll
        self.current_bankroll = starting_bankroll
        self.trades = []
    
    def record_trade(self, execution: Dict, approved_trade: Dict):
        """Log trade and update metrics"""
        # Simulate outcome (paper trading)
        edge = approved_trade["edge"]
        win_prob = 0.5 + edge  # Edge translates to win rate
        won = time.time() % 1 < win_prob  # Pseudo-random
        
        pnl = approved_trade["position_size"] * (0.9 if won else -1.0)
        
        self.trades.append({
            "timestamp": execution["timestamp"],
            "market": execution["market"],
            "size": approved_trade["position_size"],
            "pnl": pnl,
            "status": "WIN" if won else "LOSS"
        })
        
        self.current_bankroll += pnl
        
        print(f"[{self._timestamp()}] 📊 PERFORMANCE: Trade {'WON' if won else 'LOST'}")
        print(f"[{self._timestamp()}]    P&L: ${pnl:+.2f} | Bankroll: ${self.current_bankroll:.2f}")
    
    def report(self):
        """Generate performance summary"""
        if not self.trades:
            print(f"[{self._timestamp()}] 📊 No trades yet")
            return
        
        wins = [t for t in self.trades if t["status"] == "WIN"]
        total_pnl = sum(t["pnl"] for t in self.trades)
        win_rate = len(wins) / len(self.trades) * 100
        
        print(f"\n{'='*60}")
        print(f"📊 PERFORMANCE SUMMARY")
        print(f"{'='*60}")
        print(f"Trades: {len(self.trades)} | Wins: {len(wins)} | Win Rate: {win_rate:.1f}%")
        print(f"Total P&L: ${total_pnl:+.2f} ({total_pnl/self.starting_bankroll*100:+.1f}%)")
        print(f"Bankroll: ${self.starting_bankroll:.2f} → ${self.current_bankroll:.2f}")
        print(f"{'='*60}\n")
    
    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")



class CorrelationScanner:
    """Detects mispricing in correlated markets (e.g., BTC 8:00-8:05 vs 8:05-8:10)"""
    
    def scan(self) -> List[Dict]:
        """Find correlation arbitrage opportunities"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 CORRELATION SCANNER: Analyzing market groups...")
        
        try:
            # Fetch all recent trades
            cutoff = int(time.time() - 300)
            response = requests.get("https://data-api.polymarket.com/trades", 
                                   params={"min_timestamp": cutoff}, 
                                   timeout=10)
            trades = response.json()
            
            # Group by asset (BTC, ETH, SOL, XRP)
            groups = {"BTC": [], "ETH": [], "SOL": [], "XRP": []}
            
            for trade in trades:
                title = trade.get("title", "")
                for asset in groups.keys():
                    if asset.lower() in title.lower():
                        market_id = trade.get("market")
                        if market_id not in [m["id"] for m in groups[asset]]:
                            groups[asset].append({
                                "id": market_id,
                                "title": title,
                                "trades": []
                            })
            
            # Calculate prices for each market
            for asset, markets in groups.items():
                for market in markets:
                    market_trades = [t for t in trades if t.get("market") == market["id"]]
                    if len(market_trades) >= 5:
                        # Weighted average
                        prices = [t.get("price", 0) for t in market_trades[-10:]]
                        market["price"] = sum(prices) / len(prices)
                        market["volume"] = sum(abs(t.get("size", 0) * t.get("price", 0)) for t in market_trades)
            
            # Find outliers (z-score > 1.5)
            opportunities = []
            for asset, markets in groups.items():
                valid = [m for m in markets if "price" in m and m.get("volume", 0) > 500]
                if len(valid) < 3:
                    continue
                
                prices = [m["price"] for m in valid]
                mean = sum(prices) / len(prices)
                std = (sum((p - mean)**2 for p in prices) / len(prices)) ** 0.5
                
                for market in valid:
                    if std > 0:
                        z_score = (market["price"] - mean) / std
                        if abs(z_score) > 1.5:
                            edge = abs(market["price"] - mean) * 100
                            opportunities.append({
                                "market": market["title"],
                                "market_id": market["id"],
                                "p_yes": market["price"],
                                "p_no": 1 - market["price"],
                                "edge": edge,
                                "volume": market["volume"],
                                "ev": edge * market["volume"],
                                "type": "correlation"
                            })
            
            opportunities.sort(key=lambda x: x["ev"], reverse=True)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Found {len(opportunities)} correlation opportunities")
            return opportunities[:10]
            
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ❌ Correlation scanner error: {e}")
            return []

def main():
    """Main orchestration loop"""
    print(f"\n{'='*60}")
    print(f"🎭 TRADING AGENCY ORCHESTRATOR")
    print(f"{'='*60}")
    print(f"Mode: Paper Trading (Simulation)")
    print(f"Bankroll: ${BANKROLL}")
    print(f"Kelly Fraction: {KELLY_FRACTION}x")
    print(f"Max Position: {MAX_POSITION_PCT*100}% (${BANKROLL*MAX_POSITION_PCT})")
    print(f"{'='*60}\n")
    
    # Initialize agents
    scanner = MarketScanner()
    correlation_scanner = CorrelationScanner()
    risk_mgr = RiskManager(BANKROLL)
    executor = Executor()
    monitor = PerformanceMonitor(BANKROLL)
    
    try:
        # Step 1: Scan markets (both arbitrage and correlation)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 Scanning arbitrage opportunities...")
        arb_opportunities = scanner.scan()
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 Scanning correlation opportunities...")
        corr_opportunities = correlation_scanner.scan()
        
        # Combine and sort by expected value
        opportunities = arb_opportunities + corr_opportunities
        opportunities.sort(key=lambda x: x.get("ev", 0), reverse=True)
        
        if not opportunities:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️  No opportunities found (market efficient)")
            return
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Total opportunities: {len(opportunities)} (arbitrage: {len(arb_opportunities)}, correlation: {len(corr_opportunities)})")
        
        # Step 2-4: Process top opportunities
        executed_count = 0
        for opp in opportunities[:5]:  # Limit to 5 trades
            # Step 2: Risk approval
            approved = risk_mgr.approve_trade(opp)
            if not approved:
                continue
            
            # Step 3: Execute
            execution = executor.execute(approved)
            
            # Step 4: Monitor
            monitor.record_trade(execution, approved)
            
            executed_count += 1
            risk_mgr.active_positions += 1
            
            # Rate limiting
            time.sleep(1)
        
        # Step 5: Report
        monitor.report()
        
        print(f"✅ Session complete: {executed_count} trades executed")
        
    except KeyboardInterrupt:
        print(f"\n\n⚠️  Interrupted by user")
        monitor.report()
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        monitor.report()


if __name__ == "__main__":
    main()

