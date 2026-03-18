#!/usr/bin/env python3
"""
Intelligence Pipeline V2 - Fixed version with Tavily, pagination, and caching
Addresses blockers from 2026-03-18 intelligence report
"""

import os
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
import diskcache

# Configuration
CACHE_DIR = Path.home() / ".openclaw/workspace/.cache/intel"
CACHE_TTL = 3600  # 1 hour
DATA_DIR = Path.home() / ".openclaw/workspace/data"
INTEL_DIR = Path.home() / ".openclaw/workspace/intel"

# API Configuration
POLYMARKET_API = "https://data-api.polymarket.com"
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Initialize cache
cache = diskcache.Cache(str(CACHE_DIR))

class TavilySearch:
    """Tavily search client (replaces Gemini)"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or TAVILY_API_KEY
        if not self.api_key:
            print("⚠️  TAVILY_API_KEY not set - web search disabled")
        self.base_url = "https://api.tavily.com"
    
    @cache.memoize(expire=CACHE_TTL, tag="tavily")
    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        """Search with Tavily API"""
        if not self.api_key:
            return []
        
        try:
            response = requests.post(
                f"{self.base_url}/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic"
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            print(f"⚠️  Tavily search failed: {e}")
            return []


class PolymarketScanner:
    """Polymarket data scanner with pagination"""
    
    def __init__(self):
        self.base_url = POLYMARKET_API
        self.batch_size = 100
    
    @cache.memoize(expire=300, tag="polymarket")  # 5-min cache
    def fetch_recent_trades(self, limit: int = 1000) -> List[Dict]:
        """Fetch recent trades with pagination"""
        all_trades = []
        offset = 0
        cutoff = int(time.time() - 3600)  # Last hour
        
        print(f"[{self._timestamp()}] Fetching up to {limit} trades...")
        
        while offset < limit:
            try:
                response = requests.get(
                    f"{self.base_url}/trades",
                    params={
                        "min_timestamp": cutoff,
                        "limit": self.batch_size,
                        "offset": offset
                    },
                    timeout=10
                )
                response.raise_for_status()
                batch = response.json()
                
                if not batch:
                    break
                
                all_trades.extend(batch)
                offset += self.batch_size
                
                print(f"[{self._timestamp()}]   → {len(all_trades)} trades fetched")
                
                if len(batch) < self.batch_size:
                    break  # No more data
                
                time.sleep(0.2)  # Rate limiting
                
            except Exception as e:
                print(f"[{self._timestamp()}] ⚠️  Batch failed at offset {offset}: {e}")
                break
        
        print(f"[{self._timestamp()}] ✓ Total trades: {len(all_trades)}")
        return all_trades
    
    def find_arbitrage(self, trades: List[Dict]) -> List[Dict]:
        """Find arbitrage opportunities from trade data"""
        opportunities = []
        
        # Group by market
        markets = {}
        for trade in trades:
            mid = trade.get("market")
            if mid not in markets:
                markets[mid] = {
                    "title": trade.get("title", "Unknown"),
                    "trades": [],
                    "volume": 0
                }
            markets[mid]["trades"].append(trade)
            markets[mid]["volume"] += abs(trade.get("size", 0) * trade.get("price", 0))
        
        # Calculate edges
        for mid, data in markets.items():
            if len(data["trades"]) < 10 or data["volume"] < 1000:
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
                    "ev": edge * data["volume"],
                    "type": "arbitrage",
                    "confidence": min(10, int(edge * 2))  # Score 0-10
                })
        
        return sorted(opportunities, key=lambda x: x["ev"], reverse=True)
    
    def _weighted_price(self, trades: List[Dict], outcome: str) -> float:
        """Time-weighted average price"""
        filtered = [t for t in trades if t.get("outcome") == outcome]
        if not filtered:
            return 0.0
        
        now = time.time()
        weighted_sum = weight_total = 0
        
        for trade in filtered:
            minutes_ago = (now - trade.get("timestamp", now)) / 60
            weight = 0.9 ** minutes_ago  # Exponential decay
            weighted_sum += trade.get("price", 0) * weight
            weight_total += weight
        
        return weighted_sum / weight_total if weight_total > 0 else 0.0
    
    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")


class CompetitiveIntel:
    """Competitive analysis with Tavily"""
    
    def __init__(self):
        self.tavily = TavilySearch()
    
    def analyze_competitors(self) -> List[Dict]:
        """Find and analyze competitors"""
        queries = [
            "polymarket trading bot saas",
            "prediction market automation",
            "polymarket api competitors"
        ]
        
        competitors = []
        
        for query in queries:
            print(f"[{self._timestamp()}] Searching: {query}")
            results = self.tavily.search(query, max_results=5)
            
            for result in results:
                competitors.append({
                    "title": result.get("title", "Unknown"),
                    "url": result.get("url"),
                    "snippet": result.get("content", "")[:200],
                    "score": result.get("score", 0),
                    "source": "tavily"
                })
            
            time.sleep(1)  # Rate limiting
        
        # Deduplicate by URL
        seen = set()
        unique = []
        for comp in competitors:
            url = comp["url"]
            if url not in seen:
                seen.add(url)
                unique.append(comp)
        
        print(f"[{self._timestamp()}] ✓ Found {len(unique)} unique competitors")
        return sorted(unique, key=lambda x: x["score"], reverse=True)
    
    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")


def main():
    """Run full intelligence pipeline"""
    print(f"\n{'='*60}")
    print(f"🔍 INTELLIGENCE PIPELINE V2")
    print(f"{'='*60}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Cache: {CACHE_DIR}")
    print(f"{'='*60}\n")
    
    # Ensure directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "alpha_signals": [],
        "competitors": [],
        "metadata": {
            "cache_hits": 0,
            "api_calls": 0,
            "errors": []
        }
    }
    
    # Step 1: Polymarket Alpha Discovery
    print("[1/2] 🔍 Scanning Polymarket for arbitrage opportunities...")
    scanner = PolymarketScanner()
    
    try:
        trades = scanner.fetch_recent_trades(limit=1000)
        opportunities = scanner.find_arbitrage(trades)
        
        results["alpha_signals"] = opportunities[:10]
        print(f"✓ Found {len(opportunities)} arbitrage opportunities")
        
        if opportunities:
            print("\nTop 3 opportunities:")
            for i, opp in enumerate(opportunities[:3], 1):
                print(f"  {i}. {opp['market'][:60]}")
                print(f"     Edge: {opp['edge']:.2f}% | Volume: ${opp['volume']:,.0f} | EV: ${opp['ev']:,.0f}")
    
    except Exception as e:
        print(f"❌ Polymarket scan failed: {e}")
        results["metadata"]["errors"].append(f"Polymarket: {e}")
    
    print()
    
    # Step 2: Competitive Analysis
    print("[2/2] 🔍 Analyzing competitors...")
    intel = CompetitiveIntel()
    
    try:
        competitors = intel.analyze_competitors()
        results["competitors"] = competitors[:10]
        
        print(f"✓ Found {len(competitors)} competitors")
        
        if competitors:
            print("\nTop 3 competitors:")
            for i, comp in enumerate(competitors[:3], 1):
                print(f"  {i}. {comp['title']}")
                print(f"     URL: {comp['url']}")
                print(f"     Snippet: {comp['snippet']}")
    
    except Exception as e:
        print(f"❌ Competitive analysis failed: {e}")
        results["metadata"]["errors"].append(f"Competitors: {e}")
    
    # Save results
    output_file = INTEL_DIR / f"intelligence-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"✅ Intelligence pipeline complete")
    print(f"{'='*60}")
    print(f"Alpha signals: {len(results['alpha_signals'])}")
    print(f"Competitors: {len(results['competitors'])}")
    print(f"Errors: {len(results['metadata']['errors'])}")
    print(f"Output: {output_file}")
    print(f"{'='*60}\n")
    
    return results


if __name__ == "__main__":
    main()
