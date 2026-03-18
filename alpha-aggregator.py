#!/usr/bin/env python3
"""
Multi-Source Alpha Aggregator
Discovers trading opportunities across:
- Polymarket (prediction markets)
- Crypto markets (volatility, momentum)
- X Intelligence (sentiment, signals)
Zero-cost data sources, high-signal extraction
"""

import json
import requests
from datetime import datetime

OUTPUT_FILE = "/Users/yumo/.openclaw/workspace/trading-data/alpha_opportunities.json"

def scan_polymarket():
    """Scan Polymarket for mispriced markets"""
    try:
        url = "https://gamma-api.polymarket.com/markets?limit=50&closed=false&order=liquidity&ascending=false"
        response = requests.get(url, timeout=10)
        markets = response.json()
        
        opportunities = []
        for m in markets:
            try:
                prices = json.loads(m['outcomePrices'])
                yes_price = float(prices[0])
                volume = float(m.get('volume24hr', 0))
                
                # High-confidence, high-liquidity opportunities
                if 0.70 < yes_price < 0.85 and volume > 50000:
                    opportunities.append({
                        "source": "Polymarket",
                        "type": "high_confidence",
                        "market": m['question'],
                        "price": yes_price,
                        "volume_24h": volume,
                        "edge": f"Strong confidence at fair price",
                        "url": f"https://polymarket.com/event/{m['slug']}"
                    })
                
                # Extreme mispricing (volatility plays)
                if yes_price < 0.10 and volume > 20000:
                    opportunities.append({
                        "source": "Polymarket",
                        "type": "volatility_play",
                        "market": m['question'],
                        "price": yes_price,
                        "volume_24h": volume,
                        "edge": f"Lottery ticket with volume",
                        "url": f"https://polymarket.com/event/{m['slug']}"
                    })
            except:
                continue
        
        return opportunities[:10]  # Top 10
    except:
        return []

def scan_crypto_momentum():
    """Scan top crypto for momentum signals"""
    try:
        symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
        opportunities = []
        
        for symbol in symbols:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
            response = requests.get(url, timeout=5)
            data = response.json()
            
            change = float(data['priceChangePercent'])
            price = float(data['lastPrice'])
            volume = float(data['quoteVolume'])
            
            # Strong momentum signals
            if abs(change) > 5:
                opportunities.append({
                    "source": "Crypto",
                    "type": "momentum",
                    "asset": symbol.replace('USDT', ''),
                    "price": price,
                    "change_24h": change,
                    "volume_24h": volume,
                    "edge": f"Strong {'upward' if change > 0 else 'downward'} momentum",
                    "action": "BUY" if change > 0 else "SELL"
                })
        
        return opportunities
    except:
        return []

def scan_x_intelligence():
    """Check latest X Intelligence report for signals"""
    try:
        report_file = "/Users/yumo/.openclaw/workspace/x-intelligence/daily_report.md"
        with open(report_file, 'r') as f:
            content = f.read()
        
        # Extract key signals (simplified)
        opportunities = []
        
        if 'BULLISH' in content.upper():
            opportunities.append({
                "source": "X Intelligence",
                "type": "sentiment",
                "signal": "Bullish market sentiment",
                "edge": "Social momentum indicator",
                "action": "Monitor for entry"
            })
        
        return opportunities
    except:
        return []

def rank_opportunities(opportunities):
    """Rank by expected value and feasibility"""
    scored = []
    
    for opp in opportunities:
        score = 0
        
        # Volume score (liquidity)
        if 'volume_24h' in opp:
            if opp['volume_24h'] > 100000:
                score += 3
            elif opp['volume_24h'] > 50000:
                score += 2
            else:
                score += 1
        
        # Type score
        if opp['type'] == 'high_confidence':
            score += 3
        elif opp['type'] == 'momentum':
            score += 2
        else:
            score += 1
        
        # Source reliability
        if opp['source'] == 'Polymarket':
            score += 2
        elif opp['source'] == 'Crypto':
            score += 1
        
        opp['score'] = score
        scored.append(opp)
    
    return sorted(scored, key=lambda x: x['score'], reverse=True)

def main():
    print(f"🔍 ALPHA AGGREGATOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # Scan all sources
    print("Scanning Polymarket...")
    poly_ops = scan_polymarket()
    
    print("Scanning crypto markets...")
    crypto_ops = scan_crypto_momentum()
    
    print("Checking X Intelligence...")
    x_ops = scan_x_intelligence()
    
    # Combine and rank
    all_opportunities = poly_ops + crypto_ops + x_ops
    ranked = rank_opportunities(all_opportunities)
    
    print(f"\n📊 Found {len(ranked)} opportunities\n")
    
    # Display top 10
    for i, opp in enumerate(ranked[:10], 1):
        print(f"{i}. [{opp['source']}] {opp.get('market', opp.get('asset', opp.get('signal', 'Unknown')))}")
        print(f"   Type: {opp['type']} | Score: {opp['score']}")
        print(f"   Edge: {opp['edge']}")
        if 'price' in opp:
            print(f"   Price: ${opp['price']:.2f}")
        if 'action' in opp:
            print(f"   Action: {opp['action']}")
        print()
    
    # Save
    output = {
        "timestamp": datetime.now().isoformat(),
        "total_opportunities": len(ranked),
        "opportunities": ranked
    }
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"✓ Saved to: {OUTPUT_FILE}")
    
    return 0

if __name__ == "__main__":
    exit(main())
