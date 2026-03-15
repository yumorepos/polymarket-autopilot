# Live Trading Readiness Checklist

**Status:** Paper trading → Pre-live validation → Small capital deployment

---

## Phase 1: Validate Edge (Paper Trading Extended Run)

- [ ] Run paper trading continuously for **14-21 days**
- [ ] Log all signals, entries, exits, and outcomes
- [ ] Generate daily performance reports
- [ ] Track per-strategy metrics:
  - [ ] Win rate (target: >50%)
  - [ ] Sharpe ratio (target: >1.0)
  - [ ] Max drawdown (target: <20%)
  - [ ] Average profit per trade
  - [ ] Trade frequency
- [ ] Identify top 2-3 strategies with consistent positive returns
- [ ] Disable/remove underperforming strategies

**Current Status:**
- Portfolio: $11,317.78 (+13.18% return)
- 48 open positions, 67 closed trades
- Win rate: 37.3% (needs improvement)
- Top performers: MEAN_REVERSION (+$1017), TAIL (+$177), MOMENTUM (+$123)
- **Action:** Extended paper run to validate edge consistency

---

## Phase 2: Pre-Live System Hardening

### Authentication & API Setup
- [ ] Create Polymarket account with real funds
- [ ] Generate API keys (read + trade permissions)
- [ ] Store credentials securely in `.env` (never commit)
- [ ] Test authenticated API connection (read-only first)
- [ ] Verify order placement endpoint works (sandbox/testnet if available)

### Risk Controls (Hard Limits)
- [ ] **Max daily loss:** $20 (2% of $1000 starting capital)
- [ ] **Max position size:** $50 per trade
- [ ] **Max open positions:** 10 concurrent
- [ ] **Max total deployed capital:** 80%
- [ ] **Emergency kill switch:** Manual override + auto-stop on API errors

### Execution Quality Checks
- [ ] Add slippage tracking (expected vs actual fill)
- [ ] Log order latency (signal → order placement)
- [ ] Monitor API rate limits
- [ ] Add retry logic for transient failures
- [ ] Test order cancellation flow

### Monitoring & Alerts
- [ ] Real-time P&L dashboard (paper vs live toggle)
- [ ] Telegram/email alerts for:
  - Daily loss threshold hit
  - Unusual P&L swings (>5% in 1 hour)
  - API connection failures
  - Strategy performance degradation
- [ ] Daily automated performance summary

---

## Phase 3: Small Capital Deployment

### Initial Live Trading Parameters
- **Starting capital:** $100-500 (risk capital only)
- **Enabled strategies:** Top 2 from paper testing (likely MEAN_REVERSION + TAIL)
- **Position sizing:** Kelly-lite (0.5 * Kelly fraction)
- **Max bet size:** $25 per trade (5% of $500 bankroll)
- **Stop-loss:** Mandatory on all positions
- **Take-profit:** 2:1 reward:risk minimum

### Pre-Launch Checklist
- [ ] All Phase 2 items completed
- [ ] Code review of live execution module
- [ ] Backup of paper trading database
- [ ] Test run with $10 over 24 hours
- [ ] Manual verification of first 5 trades
- [ ] Document all trades in live trading log

### Week 1 Live Trading Goals
- Execute 10-20 trades
- Stay within risk limits
- Validate execution quality (slippage <2%)
- Maintain win rate >40%
- No manual intervention required

---

## Phase 4: Scale & Optimize

### Capital Growth Plan
- **Week 1-2:** $100-500 (proof of execution)
- **Week 3-4:** $500-1000 (if Week 1-2 profitable)
- **Month 2:** $1000-2500 (if Month 1 positive Sharpe)
- **Month 3+:** Scale to $5K-10K with proven edge

### Strategy Evolution
- [ ] A/B test new strategies in paper mode before live deployment
- [ ] Optimize position sizing (Kelly criterion refinement)
- [ ] Add multi-leg strategies (hedges, spreads)
- [ ] Build strategy correlation matrix (avoid redundant exposure)
- [ ] Implement dynamic strategy allocation based on recent performance

### Infrastructure Upgrades
- [ ] Migrate to VPS for 24/7 uptime
- [ ] Add database backups (hourly)
- [ ] Implement hot wallet rotation for security
- [ ] Build backtesting pipeline with walk-forward analysis
- [ ] Create portfolio rebalancing logic

---

## Red Lines (Never Cross)

1. **No revenge trading** — Never increase bet size after losses
2. **No manual overrides** — Trust the system or shut it down
3. **No unvalidated strategies** — Minimum 2 weeks paper trading first
4. **No gambling on low-liquidity markets** — Require >$10K volume
5. **No ignoring drawdown limits** — Hard stop at -20% monthly loss

---

## Current Action Items (Priority Order)

1. ✅ Document current paper trading performance
2. ⏳ **Run extended paper trading for 14 days** (collect more data)
3. ⏳ Build live execution module (`src/live_trading.py`)
4. ⏳ Add real-time monitoring dashboard (paper vs live toggle)
5. ⏳ Set up Telegram alerts for risk events
6. ⏳ Test authenticated API connection (read-only)
7. ⏳ Implement hard risk limits in code
8. ⏳ Deploy $10 test trade to validate execution flow
9. ⏳ Begin Week 1 live trading with $100-500

**Next Review:** After 14-day paper trading run is complete
