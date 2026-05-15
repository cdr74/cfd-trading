# Strategy Parameter Research

Research findings on empirically validated parameter values for momentum and mean reversion
strategies at **1-minute bar resolution**, with CFD-specific adjustments.

**Date compiled:** 2026-05-12  
**Timeframe:** 1-minute bars (intraday CFD trading)

---

## Sources

- Gao, Han, Li & Zhou (2018, JFE) — intraday time-series momentum (ITSM) on S&P 500 ETF 1993–2013
- Li, Sakkas & Urquhart — ITSM across 16 developed markets
- Ernie Chan — Hurst exponent analysis on intraday SPY/USO/GLD (2013–2016)
- Quantitativo — intraday momentum backtests on ES and NQ futures (similar to index CFDs)
- Roll (1984) — bid-ask bounce model and negative lag-1 autocorrelation
- Aït-Sahalia & Yu — microstructure noise and optimal sampling frequency
- Connors RSI research (RSI-2 on daily data) — **not applicable at 1-minute; included only for contrast**
- The Robust Trader — ADX settings backtested on SPY
- ProRealCode VWAP z-score implementation (120-bar lookback standard)

---

## Critical Structural Reality

The Hurst exponent for major equity index products at 1-minute resolution is **H ≈ 0.494–0.515**
— essentially a random walk. Neither pure momentum nor pure mean reversion has a strong
structural edge at this resolution:

| Timescale | Autocorrelation | Dominant driver |
|-----------|----------------|-----------------|
| < 1 minute | Strong negative | Bid-ask bounce (Roll 1984) |
| 1–5 minutes | Slightly negative | Bid-ask noise + weak genuine reversion |
| 5–20 minutes | Near zero | Mixed microstructure |
| 20–60 minutes | Near zero to slightly positive | Genuine momentum emerging |
| 30-minute bars | Positive (ITSM documented, JFE 2018) | Institutional order flow |
| Daily | Negative for equities | Fundamental mean reversion |

**Key implication:** The documented intraday time-series momentum effect (ITSM) operates at
**30-minute intervals** (first half-hour return predicts last half-hour return, R² = 1.6–3.3%),
not at 1-minute. A 1-minute system should use 1-minute bars as a **precise entry trigger**
while basing the signal itself on aggregated multi-minute structures.

---

## Momentum Strategy (1-minute bars)

### EMA Parameters

| EMA | Period | Purpose |
|-----|--------|---------|
| Fast | **9 bars** (= 9 min) | Signal line |
| Slow | **21 bars** (= 21 min) | Signal confirmation |
| Trend filter | **50 bars** (= 50 min) | Directional bias gate |

Entry: 9 EMA crosses above 21 EMA **while price is above 50 EMA** (long); reverse for short.

Alternative (Fibonacci-based): 8/34 EMA pair — also widely used for 1-minute scalping.

### Minimum EMA Gap Filter

Current system uses 0.02%. **This is below the noise/spread floor for most index CFDs.**

- A 0.02% gap on an instrument priced at 8,000 points = 1.6 points
- A typical fixed spread on a major index CFD is 1–2 points
- Signal-to-cost ratio of 1.6× is barely viable; false signals dominate

**Recommended minimum gap: 0.05%** (= 4 points at 8,000 — 4× a 1-point spread).
This gives a signal-to-cost ratio of 4×, consistent with positive-expectancy territory.

### RSI Confirmation

- Period: **5–7 bars**
- Long confirmation: RSI > 55
- Short confirmation: RSI < 45

### Holding Period

Hold until opposite EMA crossover or **20–50 bars maximum** (20–50 minutes). Do not hold
overnight — position must close within session.

### Signal Source

Do not generate momentum signal from 1-minute bars alone. Use a **30-minute signal**
(direction of first 30-minute bar, or 30-minute EMA slope) as the directional bias, then
use 1-minute EMA crossover as the precise entry trigger. This matches the ITSM structure
documented in the academic literature.

### Expected Performance Range

From ES/NQ futures backtests with comparable EMA structure:
- Sharpe: 1.2–1.7
- Win rate: 36–38%
- Payoff ratio: 2.1–2.3
- Edge per trade: +2 to +6 bps

---

## Mean Reversion Strategy (1-minute bars)

### Structural Warning

Fixed-spread CFDs are structurally hostile to 1-minute mean reversion:

1. Mean reversion entries happen at price extremes — you buy at the ask in a falling market,
   immediately behind by the full spread width
2. The negative lag-1 autocorrelation at 1-minute resolution is primarily **bid-ask bounce**
   (Roll 1984), not tradeable mean reversion. You cannot capture it with market orders
3. Only viable when 1-minute ATR ≥ 4× fixed spread

**Minimum ATR rule:** if ATR(1-minute, 14) < 4 × spread, do not enter mean reversion trades.

### Bollinger Band Parameters

| Parameter | 1-minute (scalping) | 5-minute (day trading) | Daily |
|-----------|--------------------|-----------------------|-------|
| MA period | **10 bars** | 20 bars | 20 bars |
| Sigma | **1.5 SD** | 2.0 SD | 2.0 SD |

The standard 20/2.0 BB is calibrated for daily/hourly bars and generates too few signals at
1-minute resolution. Use **BB(10, 1.5)** for 1-minute.

### RSI Parameters

| Parameter | 1-minute | Notes |
|-----------|----------|-------|
| Period | **7 bars** | 14-period is too slow; RSI(2) (daily) is irrelevant here |
| Oversold (long) | **< 20** | Tighter than standard 30 to filter noise |
| Overbought (short) | **> 80** | Tighter than standard 70 |

### VWAP Z-Score (Preferred Method for Intraday Mean Reversion)

VWAP is the intraday institutional fair-value anchor. VWAP-band mean reversion is more
statistically grounded than pure BB at 1-minute resolution.

| Parameter | Value |
|-----------|-------|
| Lookback | **120 bars** (= 2 rolling hours) |
| Entry | ±2.0 SD from VWAP |
| Stop | ±3.0 SD from VWAP |
| Target | VWAP mean (0 SD) |

For scalping on 1-minute bars, tighten entry to **±1.7 SD** (15% reduction from the standard
2.0 SD) to increase signal frequency while keeping the stop at ±3.0 SD.

### Holding Period

**3–5 bars maximum (3–5 minutes).** If the position is not moving toward target within 5 bars,
exit flat regardless. Mean reversion trades that stall immediately are likely caught in a trend.

### When to Use Mean Reversion

- 1-minute ATR ≥ 4× fixed spread
- ADX(14) < 20 (ranging market)
- Within first 90 minutes or last 60 minutes of session (institutional VWAP pinning activity)
- Avoid 11 AM–2 PM ET equivalent (mid-session dead zone — low volume, choppy, unreliable)

---

## Regime Detection (1-minute bars)

### ADX

- Period: **14 bars** (standard); use **7 bars** for faster regime response
- Threshold: same as daily — **25** is the consensus breakpoint

| ADX | Regime | Active Strategy |
|-----|--------|----------------|
| < 20 | Ranging | Mean reversion |
| 20–25 | Transitional | Reduce size / wait |
| > 25 | Trending | Momentum |
| > 30 | Strong trend | Momentum only; **no mean reversion** |

For breakout filtering specifically, ADX(3) with threshold 50 showed the best backtested
results in SPY analysis (The Robust Trader).

### Opening Range Breakout (ORB)

ORB is the most empirically validated intraday regime detection framework.

| ORB Duration | Use case | Notes |
|-------------|----------|-------|
| 5 minutes | Individual stocks, fast signals | Higher false-breakout rate |
| **15 minutes** | Index CFDs (recommended) | Best balance of speed vs. reliability |
| 30 minutes | Sector ETFs, conservative | Lowest false-breakout rate |

Use ORB as a regime gate: trade momentum only after price breaks above/below the ORB range.
Switch to mean reversion during mid-session when price re-enters the ORB range.

### Time-of-Day Filter (Validated by ITSM Research)

| Window (ET) | Window (GMT) | Regime | Strategy |
|-------------|-------------|--------|---------|
| First 30 min (open) | First 30 min after open | ITSM signal active | Momentum |
| 9:30–11:00 AM | 14:30–16:00 | High vol, institutional flow | Momentum / ORB |
| **11:00 AM–2:00 PM** | **16:00–19:00** | **Low volume, choppy** | **Avoid / reduce** |
| 2:00–4:00 PM | 19:00–21:00 | Rebalancing, VWAP pinning | Mean reversion |
| Last 30 min | Last 30 min before close | Highest ITSM predictability | Momentum |

For London-listed index CFDs: **London open (8–9 AM GMT)** and **London-NY overlap
(13:00–16:30 GMT)** are the maximum liquidity windows — tightest spreads, strongest moves,
highest strategy viability.

### VWAP as Directional Bias

- Price above VWAP → momentum long bias; mean reversion long only
- Price below VWAP → momentum short bias; mean reversion long (fading the drop toward VWAP)
- Use VWAP z-score (120-bar) to confirm entry magnitude

---

## Cost and Viability Thresholds

### Breakeven by Strategy Type

| Strategy | Min ATR required | Reason |
|----------|-----------------|--------|
| Momentum | ≥ 3× spread | Asymmetric payoff (2:1 RR) covers costs at 36–38% win rate |
| Mean reversion | ≥ 4× spread | Symmetric entry cost; lower hit rate at extremes |

### Spread-to-Profit Ratio

- Cap: **< 15%** (spread must be < 15% of profit target)
- Example: 1-point spread → minimum profit target 7 points

### EMA Gap Signal Viability

| Gap filter | Points at 8,000 | Signal/spread ratio | Viable? |
|------------|----------------|--------------------|----|
| 0.02% | 1.6 pts | 1.6× (1-pt spread) | Marginal |
| **0.05%** | **4.0 pts** | **4× (1-pt spread)** | **Yes** |
| 0.10% | 8.0 pts | 8× (1-pt spread) | Conservative (fewer signals) |

---

## Concrete Parameter Starting Points

```yaml
# Momentum (1-minute bars)
momentum:
  signal_timeframe: 30min          # derive directional signal from 30-min bars
  entry_timeframe: 1min            # use 1-min EMA crossover as entry trigger
  ema_fast: 9                      # bars on 1-minute chart
  ema_slow: 21
  ema_trend_filter: 50
  ema_gap_filter_pct: 0.05         # minimum gap to qualify signal (up from 0.02)
  rsi_period: 7
  rsi_long_min: 55
  rsi_short_max: 45
  max_hold_bars: 50                # 50 minutes maximum
  regime_adx_min: 25
  time_filter_avoid: "11:00-14:00 ET"

# Mean Reversion (1-minute bars)
mean_reversion:
  bb_period: 10                    # not 20; too slow for 1-minute
  bb_sigma: 1.5                    # not 2.0; that is for daily/hourly bars
  rsi_period: 7
  rsi_oversold: 20
  rsi_overbought: 80
  vwap_lookback_bars: 120          # 2 rolling hours
  vwap_entry_sd: 1.7               # tighter than daily (2.0) for 1-min scalping
  vwap_stop_sd: 3.0
  vwap_target: 0.0                 # exit at VWAP mean
  max_hold_bars: 5                 # exit flat if not moving within 5 bars
  min_atr_multiple: 4              # ATR(14) must be ≥ 4× fixed spread; else skip
  regime_adx_max: 20
  time_filter_prefer: "09:30-11:00 ET and 14:00-16:00 ET"

# Regime detection (1-minute bars)
regime:
  adx_period: 14                   # use 7 for faster response if needed
  adx_momentum_threshold: 25
  adx_reversion_threshold: 20
  orb_duration_minutes: 15         # opening range breakout window for index CFDs
  vwap_lookback_bars: 120
  time_avoid_momentum: "11:00-14:00 ET"
  time_avoid_reversion: "outside 09:30-11:00 ET and 14:00-16:00 ET"
```

---

## Key Differences from Daily-Bar Research

| Parameter | Daily bars | 1-minute bars |
|-----------|-----------|---------------|
| Lookback window | 126–252 days | 9–50 bars (9–50 min) |
| BB period | 20 | 10 |
| BB sigma | 2.0 | 1.5 |
| RSI period | 2–14 | 7 |
| RSI thresholds | 30/70 | 20/80 |
| EMA gap filter | 0.02% | **0.05%** |
| Holding period | 5–15 days | 5–50 bars (5–50 min) |
| Momentum structural edge | Moderate (H > 0.5 documented) | Weak (H ≈ 0.50, near random walk) |
| Signal source | Single timeframe | 30-min signal + 1-min entry trigger |
| Mean reversion viability | Good (daily ATR >> spread) | Structurally difficult (ATR ≈ 3–8× spread only) |

---

## Phase 2 — ORB at M15 (May 2026)

**Date compiled:** 2026-05-13  
**Timeframe:** M15 bars (aggregated in-process from M1)  
**Instruments tested:** EURUSD, GBPUSD, USDJPY, EURGBP, US500, DE40, UK100, GOLD, XBRUSD, BTCUSD, ETHUSD  

---

### ORB Structural Findings

**Hypothesis (test against the clean re-baseline `audit/RESULTS.md`):** Opening Range Breakout *should* have structural edge on European equity indices and USDJPY, and little on FX pairs, crypto, or commodities — for the mechanism reasons below.

> **⚠️ Empirical PF/Win% table removed 2026-05-15** — produced by the old (time-exit-disabled) engine. The structural reasoning below is retained; current numbers live in the clean re-baseline (`audit/RESULTS.md`, `audit/ranked_cells.csv`) from the rebuilt engine.

**Why equity indices respond to ORB:**  
The Xetra and LSE auction process creates genuine order flow imbalance at 08:00 UTC — accumulated overnight orders clear at the open, driving directional momentum that tends to persist for the first session hour. This is the mechanism Zarattini & Aziz documented on US equity index futures (ES, NQ). DE40 and UK100 exhibit the same structural property.

**Why FX does not:**  
The "London open" is a gradual increase in liquidity, not a discrete auction. There is no clearing event — institutional orders arrive continuously from 07:30 to 08:30 UTC. The ORB reference level from one 15-min bar has no special predictive value over the subsequent hour.

**OR period (key finding):**  
2-bar OR (30 min) is substantially better than 1-bar (15 min). Single-bar OR has stop rates 88–97%; 2-bar OR reduces this to 63–94%. The 30-min OR matches the research setup (Zarattini & Aziz) and gives price two bars to establish genuine support/resistance. This is consistent with the literature's finding that a 15-min OR is optimal for individual stocks while a 30-min OR is optimal for index futures.

**OR-width-based stop:**  
Stop at OR low (LONG) / OR high (SHORT) is correct — it is the natural invalidation level. Fixed-percentage stops (0.5%) are uncorrelated with the actual volatility of the OR and produce worse results. The OR width already encodes the session opening volatility, making it a natural ATR proxy for this specific strategy.

---

### ATR-Trailing Exit: Findings

**ATR-trailing at 1.5×ATR(14) underperforms the fixed 2×OR-width TP for this setup.**

The OR width itself is a session-calibrated ATR proxy. Setting TP at 2×OR-width is therefore already an ATR-relative target, making the additional per-bar ATR computation redundant. The M15 ATR(14) is more variable and at 1.5× typically tighter than 2×OR-width, causing premature exits.

> **⚠️ Empirical PF table removed 2026-05-15** (old time-exit-disabled engine). The qualitative finding — 1.5×ATR trailing tends to exit ORB winners early because OR-width is already a session-calibrated ATR proxy — drove the decision to **disable ORB trailing** in the rebuild (`orb.yaml trailing_stop.enabled: false`; catalog §13). Current ORB numbers: `audit/RESULTS.md`.

**Recommendation:** Retain the fixed 2×OR-width TP as the primary target. If ATR-trailing is revisited, use a larger multiplier (≥ 3.0×) or combine: ATR trail as a floor, OR-width TP as a cap.

---

### Instrument Universe for ORB

Initial analysis flagged DE40, UK100, and USDJPY as the best performers (PF > 1.1). However, restricting to these three is selection bias — the backtest window (Jan–May 2026) was a single bullish equity regime, and a simple "buy Feb 1, sell May" trade would have been profitable on UK100/USDJPY. An algorithm needs to demonstrate edge in adverse scenarios too.

**Directional split analysis** exposes the regime artifact:
> **⚠️ Per-instrument directional PF figures removed 2026-05-15** (time-exit-disabled engine). The methodological point this section makes — that in-sample PF over a single bullish window is selection bias, and long-only bull-regime trades can masquerade as edge — stands and is retained below.

**Current decision: no instrument filter.** The backtest runs all instruments with the directional split output visible. Until we have multi-regime data (6–12 months spanning both bull and bear phases), instrument selection based on in-sample PF is overfit. USDJPY is the only instrument with evidence of structural two-way ORB edge at this time.

---

### Suggested Next Directions (ranked)

1. **OR-width/ATR gate on DE40/UK100/USDJPY** — filter to only trade sessions where the OR width falls within a "normal" range (e.g., between 10th and 90th percentile for the instrument). Extreme OR widths (gap open days, news events) are associated with false breakouts, not continuation. Expected: reduce bad entries without reducing good ones.

2. **Zarattini ITSM momentum as parallel strategy** — implement intraday time-series momentum (first 30-min bar direction predicts last 30-min bar direction, as per JFE 2018 Gao et al.) on US500/DE40/UK100. Directly addresses the near-zero US500 PF from ORB. Uses the M30 data now available from the in-process aggregation (`aggregate_bars(bars, 30)`).

3. **True M30 bar data from MT5** — fetch M30 bars separately from MT5 on Windows (vs. aggregating M1). Separate M30 data enables the M30 directional gate for momentum that was self-defeating when computed from M1. This unlocks the EMA 9/21 momentum strategy at its correct resolution.

4. **VWAP alignment pre-filter** — only enter ORB LONG when the bar breakout closes above session VWAP; SHORT when below. VWAP confirms that the institutional bias matches the ORB direction. Requires per-session VWAP computation in the engine.

---

### Sources (Phase 2)

- Zarattini & Aziz (2024) — ORB edge documented on US equity index futures at 30-min OR resolution; Sharpe > 1.5
- Gao, Han, Li & Zhou (2018, JFE) — intraday time-series momentum (ITSM); first-half-hour return predicts last-half-hour return, R² = 1.6–3.3% on S&P 500
- Maróy (2020, *Algorithmic Trading*) — ATR-trailing stop as primary ORB exit mechanism; recommends 2–3×ATR multiplier for index futures
