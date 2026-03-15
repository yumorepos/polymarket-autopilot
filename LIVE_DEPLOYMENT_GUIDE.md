# Live Deployment Guide - Polymarket Autopilot

**MISSION: Get to profitable live trading ASAP while protecting capital.**

---

## Quick-Start Path (FASTEST)

### Prerequisites (5 minutes)
1. Polymarket account with $100-500 deposited
2. Private key exported from your wallet
3. `.env` file configured (see below)

### Setup `.env` File
```bash
# Copy template
cp .env.live.example .env

# Edit with your credentials
nano .env  # or vim, code, etc.
```

Required variables:
```bash
# Your wallet private key (starts with 0x)
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE

# If using email/Magic wallet, add funder address
# (Skip this if using MetaMask/hardware wallet)
POLYMARKET_FUNDER_ADDRESS=0xYOUR_FUNDER_ADDRESS_HERE

# Safety toggle (keep false until Step 3)
LIVE_TRADING_ENABLED=false

# Risk limits (start conservative)
LIVE_MAX_DAILY_LOSS=10.0           # $10 max loss per day
LIVE_MAX_POSITION_SIZE=25.0        # $25 max per trade
LIVE_MAX_OPEN_POSITIONS=5          # Max 5 concurrent positions
```

### 4-Step Deployment (30 minutes total)

#### Step 1: Test API Connection (2 min)
```bash
cd /Users/yumo/Projects/polymarket-autopilot
source .venv/bin/activate
python scripts/live_quickstart.py --step 1
```

**Expected output:**
- ✅ Private key configured
- ✅ Client initialized
- ✅ API connection successful

**If errors:**
- Check `.env` file exists and has correct format
- Verify private key starts with `0x`
- Ensure internet connection is active

---

#### Step 2: Check Balance & Positions (2 min)
```bash
python scripts/live_quickstart.py --step 2
```

**Expected output:**
- Open orders count
- Today's P&L (should be $0 if no trades yet)

**If errors:**
- "Authentication failed" → Check private key is correct
- "Invalid signature" → Add `POLYMARKET_SIGNATURE_TYPE=1` to `.env` (for email wallets)

---

#### Step 3: Place $10 Test Order (5 min)
```bash
python scripts/live_quickstart.py --step 3
```

**This will:**
1. Show you the market and price
2. Ask for confirmation ("YES")
3. Place a real $10 limit order
4. Return order ID and status

**After placing:**
1. Open Polymarket web app: https://polymarket.com/portfolio
2. Verify order appears in your open positions
3. Monitor for 10-15 minutes
4. Manually close the order via web UI
5. Check final P&L and slippage

**Success criteria:**
- Order filled within 10 minutes
- Slippage <2%
- No API errors or timeouts

**If test fails:**
- Check wallet has sufficient balance (>$50)
- Ensure wallet has USDC on Polygon (not Ethereum mainnet)
- Try a different market with higher liquidity

---

#### Step 4: Start Automated Trading (15 min)
```bash
# First: Update .env
nano .env
# Change: LIVE_TRADING_ENABLED=true
# Save and exit

# Start monitoring (Terminal 1)
python scripts/monitor_live_trading.py --interval 300

# Start trading bot (Terminal 2)
LIVE_TRADING_ENABLED=true polymarket-autopilot trade --strategy ALL --max-pages 5
```

**What happens:**
- Bot scans markets every cycle
- Places orders when signals trigger
- Monitors open positions for exits (TP/SL)
- Auto-stops if daily loss limit hit

**First hour checklist:**
- [ ] Monitor terminal shows no errors
- [ ] At least 1-2 trades placed
- [ ] Orders are filling (not stuck pending)
- [ ] Risk limits are being enforced
- [ ] Daily P&L tracking is accurate

---

## Risk Management (CRITICAL)

### Hard Limits (Enforced by Code)
```python
MAX_DAILY_LOSS = $10          # Auto-stop trading if hit
MAX_POSITION_SIZE = $25       # Max per trade
MAX_OPEN_POSITIONS = 5        # Max concurrent trades
MIN_MARKET_LIQUIDITY = $10K   # Only trade liquid markets
MAX_SLIPPAGE = 2%             # Skip if slippage too high
```

### Manual Controls

**Emergency Stop (kills all orders immediately):**
```bash
python -c "from polymarket_autopilot.live_trading import emergency_stop; emergency_stop()"
```

**Disable Trading (safe shutdown):**
```bash
# Set in .env:
LIVE_TRADING_ENABLED=false

# Then restart bot
```

**Check Current Status:**
```bash
polymarket-autopilot report
```

---

## Monitoring & Alerts

### Real-Time Monitoring
```bash
# Terminal 1: Monitoring script
python scripts/monitor_live_trading.py --interval 300

# Terminal 2: Watch logs
tail -f logs/trading.log
```

### Telegram Alerts (Optional but Recommended)
1. Create bot: Talk to @BotFather on Telegram
2. Get your chat ID: Talk to @userinfobot
3. Add to `.env`:
```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
```

Alerts trigger on:
- Daily loss approaching limit (75% warning, 100% critical)
- Win rate drops below 40%
- API connection lost
- Unusual slippage detected

---

## Performance Tracking

### Daily Check-In (Every 24 Hours)
```bash
# Full performance report
polymarket-autopilot report

# Trade history
polymarket-autopilot history --limit 50

# Strategy comparison
polymarket-autopilot compare --days 1 --top 5
```

### Week 1 Goals
- **Volume:** 20-50 trades
- **Win rate:** >40%
- **Daily P&L:** Positive 4+ days out of 7
- **Max drawdown:** <20%
- **Slippage:** <2% average
- **Zero manual interventions** (no emergency stops)

**If goals NOT met:**
- Stop live trading immediately
- Return to paper mode
- Analyze what went wrong
- Fix issues before re-deploying

**If goals met:**
- Increase capital: $100 → $250 → $500
- Adjust risk limits proportionally
- Add more strategies (after paper validation)

---

## Scaling Plan

### Week 1: Prove It Works ($100-250)
- Deploy minimal capital
- Focus on execution quality
- Validate risk controls
- Track all metrics

### Week 2-3: Optimize ($250-500)
- Increase capital if Week 1 profitable
- Disable underperforming strategies
- Optimize position sizing
- Add new strategies (paper tested first)

### Month 2+: Scale ($500-2500)
- Increase to $1000 if Month 1 profitable
- Implement Kelly sizing
- Add portfolio rebalancing
- Deploy on VPS for 24/7 uptime

### Month 3+: Professionalize ($2500+)
- Hot wallet rotation for security
- Multi-leg strategies (hedges, spreads)
- External data integration (news, sentiment)
- Walk-forward backtesting pipeline

---

## Troubleshooting

### "Authentication failed"
- Check `POLYMARKET_PRIVATE_KEY` is correct
- Ensure key starts with `0x`
- For email wallets: Add `POLYMARKET_SIGNATURE_TYPE=1`

### "Insufficient balance"
- Deposit USDC to Polygon (not Ethereum mainnet)
- Check balance: https://polymarket.com/portfolio
- Bridge if needed: https://wallet.polygon.technology/

### "Order not filling"
- Market may be illiquid
- Try higher-liquidity markets (>$50K volume)
- Use market orders instead of limit orders

### "Daily loss limit hit"
- Trading auto-stops (by design)
- Analyze losing trades
- Adjust strategies or risk limits
- Do NOT override limits — this is protection

### "API rate limited"
- Reduce `--max-pages` in trade command
- Add delays between cycles (built-in)
- Client has retry logic (will auto-recover)

---

## Safety Red Lines (NEVER CROSS)

1. **No revenge trading** — Never increase position size after losses
2. **No manual overrides** — Trust the system or shut it down
3. **No unvalidated strategies** — Min 2 weeks paper trading first
4. **No gambling on illiquid markets** — Require >$10K volume
5. **No ignoring drawdown limits** — Hard stop at -20% monthly
6. **No sharing private keys** — Ever. Period.

---

## Current Status (As of Mar 14, 2026)

### Paper Trading Performance
- Portfolio: $11,317 (+13.18% return)
- 67 closed trades, 48 open positions
- Win rate: 37.3% (needs improvement)
- Top strategies: MEAN_REVERSION (+$1017), TAIL (+$177), MOMENTUM (+$123)

### Live Trading Status
- ✅ Infrastructure complete (py-clob-client integrated)
- ✅ Risk limits implemented
- ✅ Monitoring scripts ready
- ✅ Quick-start wizard built
- ⏳ **Next: Deploy $100 test capital (YOU)**

### Blockers
- None (all technical work complete)
- **Only blocker: You need to run Step 1-4 above**

---

## Getting Help

**Check logs:**
```bash
tail -f logs/trading.log
tail -f logs/monitor.log
```

**Debug mode:**
```bash
polymarket-autopilot --log-level DEBUG trade --dry-run
```

**GitHub issues:**
https://github.com/yumorepos/polymarket-autopilot/issues

**Emergency contact:**
- Stop trading immediately
- Run emergency stop (see Manual Controls above)
- Export logs and open GitHub issue

---

## Next Actions (RIGHT NOW)

1. **Copy this command and run it:**
```bash
cd /Users/yumo/Projects/polymarket-autopilot && \
source .venv/bin/activate && \
cp .env.live.example .env && \
nano .env
```

2. **Add your private key to `.env`**

3. **Run Step 1:**
```bash
python scripts/live_quickstart.py --step 1
```

4. **Follow the wizard through Step 4**

5. **Monitor for 24 hours**

6. **Report back:** Win rate, P&L, issues

---

**Target: Live trading operational within 1 hour.**

**Remember: Start small ($100), validate everything, scale slowly.**
