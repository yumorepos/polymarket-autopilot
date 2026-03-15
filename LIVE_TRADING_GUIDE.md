# Live Trading Implementation Guide

**Goal:** Turn polymarket-autopilot from paper trading into a real money-making system.

---

## What Just Got Built

### 1. **Live Trading Module** (`src/polymarket_autopilot/live_trading.py`)
- Authenticated order placement for Polymarket CLOB API
- `RiskLimits` class with hard safety constraints:
  - Max daily loss: $20 (configurable)
  - Max position size: $50 per trade
  - Max open positions: 10 concurrent
  - Min market liquidity: $10K
  - Max slippage: 2%
- `emergency_stop()` function to cancel all orders immediately
- Environment variable opt-in (`LIVE_TRADING_ENABLED=true`)

### 2. **Monitoring Script** (`scripts/monitor_live_trading.py`)
- Real-time checks for:
  - Daily P&L approaching/exceeding loss limit
  - Win rate dropping below 40%
  - API health/connectivity
- Telegram alerts for critical events
- Run as: `python scripts/monitor_live_trading.py --interval 300`

### 3. **Dashboard Upgrade** (`dashboard.py`)
- Live/paper mode indicator (red banner when live trading enabled)
- Real-time P&L tracking
- Strategy attribution with win rate per strategy

### 4. **Readiness Checklist** (`READINESS.md`)
- 4-phase roadmap: Paper validation → System hardening → Small capital → Scale
- Pre-flight checklist for live deployment
- Risk controls documentation

### 5. **Extended Paper Trading Runner** (`scripts/run_extended_paper_trading.sh`)
- Automated 14-day continuous paper trading
- Hourly trade cycles + daily performance reports
- Usage: `nohup ./scripts/run_extended_paper_trading.sh > logs/paper_trading.log 2>&1 &`

---

## Current Status: Phase 1 (Paper Validation)

**Your bot has:**
- ✅ 48 open positions, 67 closed trades
- ✅ +13.18% return ($11,317 from $10K starting capital)
- ⚠️ 37.3% win rate (needs improvement — target: >50%)
- ✅ Top strategies identified: MEAN_REVERSION (+$1017), TAIL (+$177), MOMENTUM (+$123)

**Problem:** Win rate is too low for confident live deployment. Need more data to validate edge.

---

## Action Plan: Paper → Profit

### **Immediate (Today): Start Extended Paper Run**

```bash
cd /Users/yumo/Projects/polymarket-autopilot

# Create logs directory
mkdir -p logs

# Start 14-day paper trading in background
nohup ./scripts/run_extended_paper_trading.sh > logs/paper_trading.log 2>&1 &

# Check progress
tail -f logs/paper_trading.log

# Or run a single cycle manually first
source .venv/bin/activate
polymarket-autopilot trade --dry-run
polymarket-autopilot report
```

This will:
- Run 336 trade cycles over 14 days (hourly)
- Generate daily performance reports
- Collect enough data to validate strategy edge
- Identify which strategies are actually profitable

**Expected outcome:** 200+ trades, clearer win rate per strategy, confidence in top 2-3 strategies.

---

### **Week 2: Analyze & Harden (After 14-Day Run)**

1. **Review final report:**
   ```bash
   polymarket-autopilot report
   polymarket-autopilot compare --days 14 --top 5
   polymarket-autopilot history --limit 100 > logs/final_history.txt
   ```

2. **Identify winners:**
   - Which strategies have win rate >50%?
   - Which have Sharpe ratio >1.0?
   - Which have consistent returns vs lucky streaks?

3. **Kill losers:**
   - Disable strategies with <40% win rate
   - Remove strategies with negative Sharpe
   - Focus capital on proven winners only

4. **Set up Polymarket account:**
   - Create account at https://polymarket.com
   - Add $100-500 (risk capital only)
   - Generate API keys (Settings → API)
   - **Do NOT enable live trading yet**

---

### **Week 3: Pre-Live System Test**

1. **Configure credentials:**
   ```bash
   cp .env.live.example .env.live
   # Edit .env.live with your API keys
   # Keep LIVE_TRADING_ENABLED=false for now
   ```

2. **Test authenticated API (read-only):**
   ```python
   from polymarket_autopilot.live_trading import LiveTradingClient
   
   client = LiveTradingClient()
   orders = client.get_open_orders()  # Should return empty list
   print(f"API connection: OK, {len(orders)} orders")
   ```

3. **Set up monitoring:**
   ```bash
   # Configure Telegram bot (optional but recommended)
   # Get bot token from @BotFather
   # Get chat ID from @userinfobot
   
   # Edit .env.live:
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   
   # Test monitoring (one-shot)
   python scripts/monitor_live_trading.py --once
   ```

4. **Run $10 test trade:**
   - Manually place ONE small order via Polymarket web UI
   - Verify fill quality (slippage, latency)
   - Monitor execution for 24 hours
   - Close position manually

---

### **Week 4: Go Live (Small Capital)**

**Pre-flight checklist:**
- [ ] Extended paper run shows consistent profit (14+ days)
- [ ] Win rate >50% on top 2-3 strategies
- [ ] Sharpe ratio >1.0
- [ ] Max drawdown <20%
- [ ] API credentials tested (read-only)
- [ ] Telegram alerts working
- [ ] Monitoring script tested
- [ ] $100-500 deposited in Polymarket account
- [ ] Backup of paper trading database made

**Enable live trading:**
```bash
# Edit .env.live
LIVE_TRADING_ENABLED=true
LIVE_MAX_DAILY_LOSS=10.0  # Start conservative
LIVE_MAX_POSITION_SIZE=25.0  # 5% of $500 bankroll

# Start monitoring (background)
nohup python scripts/monitor_live_trading.py --interval 300 > logs/monitor.log 2>&1 &

# Start live trading (manual first cycle)
source .venv/bin/activate
polymarket-autopilot trade  # No --dry-run flag = REAL ORDERS

# Check status
polymarket-autopilot report
tail -f logs/monitor.log
```

**First week goals:**
- Execute 10-20 trades
- Stay within risk limits (no alerts)
- Win rate >40%
- No manual intervention required
- Slippage <2% on average

---

### **Month 2+: Scale**

**If Week 1 live is profitable:**
- Increase capital: $500 → $1000 → $2500
- Adjust position sizes proportionally
- Add more strategies (after paper validation)
- Optimize Kelly sizing
- Build automated rebalancing

**If Week 1 live is unprofitable:**
- **STOP IMMEDIATELY**
- Review trade logs
- Analyze what paper trading missed (slippage? timing? liquidity?)
- Return to paper mode
- Fix issues before next live attempt

---

## Key Implementation TODOs

### **Critical (Required for Live Trading)**
- [ ] Implement order signing (`_sign_order()` in `live_trading.py`)
  - Use `eth_account` library to sign orders with private key
  - Add `POLYMARKET_PRIVATE_KEY` to `.env.live`
  - Never commit private key to git
  
- [ ] Test authenticated order placement on testnet (if available)
  - Verify order format matches Polymarket API spec
  - Test cancellation flow
  - Validate fill callbacks

- [ ] Add slippage tracking
  - Log expected vs actual fill prices
  - Alert if slippage >2%
  - Adjust limit orders based on historical slippage

### **High Priority (Week 2-3)**
- [ ] Build backtesting pipeline with walk-forward analysis
- [ ] Add database backup automation (hourly snapshots)
- [ ] Create strategy A/B testing framework
- [ ] Implement dynamic strategy allocation based on recent performance
- [ ] Add portfolio rebalancing logic

### **Nice to Have (Month 2+)**
- [ ] VPS deployment for 24/7 uptime
- [ ] Hot wallet rotation for security
- [ ] Multi-leg strategies (hedges, spreads)
- [ ] Order book depth analysis
- [ ] Correlation matrix (avoid redundant exposure)

---

## Safety Red Lines (Never Cross)

1. **No revenge trading** — Never increase bet size after losses
2. **No manual overrides** — Trust the system or shut it down
3. **No unvalidated strategies** — Minimum 2 weeks paper trading first
4. **No gambling on low-liquidity markets** — Require >$10K volume
5. **No ignoring drawdown limits** — Hard stop at -20% monthly loss
6. **No sharing private keys** — Ever. Period.

---

## Emergency Procedures

### **If Daily Loss Limit Hit:**
```bash
# Automated: monitoring script will alert you
# Manual override:
python -c "from polymarket_autopilot.live_trading import emergency_stop; emergency_stop()"
```

### **If System Malfunction:**
1. Run emergency stop (above)
2. Set `LIVE_TRADING_ENABLED=false` in `.env.live`
3. Manually close positions via Polymarket web UI
4. Review logs: `logs/paper_trading.log`, `logs/monitor.log`
5. Fix issue in paper mode before re-enabling live

### **If API Connection Lost:**
- Monitoring script will alert within 5 minutes
- Positions remain open (no forced liquidation)
- Manual intervention via Polymarket web UI if needed
- Bot will resume when connection restored

---

## Cost Estimates

**Paper trading (ongoing):** $0 (free)

**Live trading costs:**
- Polymarket trading fees: ~0.5% per trade
- Expected slippage: ~1-2% per trade
- VPS hosting (optional): ~$10-20/month
- Telegram bot: Free

**Example:** 100 trades/month at $50 avg size = $5K volume = ~$25-50 in fees + slippage.

---

## Questions?

- Review `READINESS.md` for detailed checklist
- Check `src/polymarket_autopilot/live_trading.py` for implementation details
- Run `python scripts/monitor_live_trading.py --help` for monitoring options
- Open GitHub issue for bugs/questions: https://github.com/yumorepos/polymarket-autopilot/issues

---

**Next Action:** Start extended paper trading run (14 days) to validate edge.

```bash
cd /Users/yumo/Projects/polymarket-autopilot
nohup ./scripts/run_extended_paper_trading.sh > logs/paper_trading.log 2>&1 &
```
