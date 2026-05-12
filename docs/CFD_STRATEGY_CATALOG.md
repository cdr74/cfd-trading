# CFD Trading System — Strategy Catalog & Mathematical Reference

**Version:** 1.2  
**Date:** May 2026  
**Status:** S1 (momentum) and S2 (mean reversion) fully implemented and backtested; S3 deferred  
**Companion to:** `docs/SYSTEM_DESIGN.md`, `config/strategies/`, `src/cfd_trading/tools/scan_tools.py`, `docs/BACKTESTING.md`  
**Repo:** github.com/cdr74/cfd-trading (private)

> **Authoritativeness note:** Where this document conflicts with `README.md` or the
> `config/strategies/` files, the repo files are correct. This catalog records
> strategy ideas and intent; the YAML/MD files and tool source are the ground truth.

---

## 1. Universal Signal-Position Meta-Model

Every strategy in this catalog is an instantiation of the same three-equation decomposition. This structure enables clean component swapping — changing signal function `f` while keeping position sizing `g` and cost accounting identical.

```
signal_t   = f( price_history, volume, features )
position_t = g( signal_t, risk_constraints )
PnL_t      = position_{t-1} * r_t  -  costs
```

Where `r_t = log(S_t / S_{t-1})` (log return — additive over time).  
**Costs:** spread (bid/ask) + slippage (market impact). Most strategies look valid pre-cost and collapse post-cost.

### How Claude integrates into this model

`analyze_instrument` pre-computes the indicator inputs that `f()` depends on and returns them as structured context. Claude instantiates `f()` as probabilistic reasoning over that context. The preflight enforces `g()` through YAML risk bounds. Claude never reasons directly over raw OHLCV arrays — the tool layer computes indicators first.

**Currently computed by `analyze_instrument` (source of truth for data availability):**

| Input | Source | Notes |
|---|---|---|
| ATR_14 | 60 × 1-min bid-side OHLC bars | Average True Range, 14-bar window |
| Trend slope | 60 × 1-min close-bid prices | Linear regression slope — positive = uptrend |
| Spread | Latest bar bid/ask | Absolute spread in price units |
| Spread % of ATR | Derived | Spread cost relative to volatility |
| Recent high/low | All 60 bars | Bid-side extremes over the full window |
| Candle summary | Last 20 bars | compact OHLC bid/ask + volume |
| Sentiment | Capital.com API | `longPositionPercentage` / `shortPositionPercentage` — see §8 |
| Open positions | Live from broker | Positions open in this instrument right now |

**Additional indicators to be added per strategy** (computed in Python from the same bar data — no new API calls required unless noted):

| Indicator | Required by | How |
|---|---|---|
| EMA_fast, EMA_slow, signal z-score | S1 | Computed from 60 × 1-min bars in `analyze_instrument` |
| Rolling mean, rolling std, z_t | S2 | Computed from 60 × 1-min bars in `analyze_instrument` |
| Donchian channel upper/lower | S3 | Requires a second `get_prices` call at 5-min resolution |
| Vol-scaled suggested size | All | Needs `target_risk_pct` added to strategy YAML — see §9 |

**No news/event calendar is available.** Capital.com does not expose a news or economic calendar via its API. Strategies that reference news proximity as a gate (e.g. S2 §6.2) must rely on human awareness, not automated gating.

---

## 2. The Noise Problem — Why Most Signals Fail

Intraday price action at the 1-min / 5-min horizon approximates Geometric Brownian Motion (GBM) with a weak drift term. Every strategy is a filter trying to extract weak signal from dominant noise.

```
dS_t = mu * S_t * dt  +  sigma * S_t * dW_t
```

| Condition | Effect on strategy family |
|---|---|
| Drift (mu) > noise (sigma) at horizon | Momentum strategies are viable — signal autocorrelation persists |
| Mean-reverting micro-structure | Z-score reversion works — deviations are statistically bounded |
| Low autocorrelation, high sigma | Breakout strategies generate false positives (whipsaws) |
| Regime shift (vol expansion) | All short-horizon signals degrade — regime detection needed |

Every prompt module must instruct Claude to assess the current noise regime (ATR vs price range, trend slope vs spread, recent signal coherence) before committing to a signal interpretation. `NONE` is always a valid and encouraged output.

---

## 3. Canonical Performance Metric — Net Expectancy

Do not optimise for win rate in isolation. The governing metric is net expectancy after costs.

```
E     = P(win) * avg(win)  -  P(loss) * avg(loss)
E_net = E  -  spread  -  slippage
```

**Minimum viability threshold:** `E_net > 0` across at least 30 trades on demo before live deployment. Track separately per strategy and per asset class.

| Metric | Tracking |
|---|---|
| Win rate P(win) | Per strategy, per asset — minimum 30 trade sample |
| Average win / loss ratio | Enforced at entry via `min_rr_ratio` in strategy YAML |
| Spread cost per trade | Logged from `analyze_instrument` output at entry time |
| Slippage | Manual observation only — compare Capital.com demo fill vs quoted price |
| E_net | Primary go/no-go metric per strategy at monthly review |
| Max consecutive losses | Human-monitored circuit breaker — halt session if threshold breached |

---

## 4. S0 — Random Baseline (Control)

**Type:** CONTROL — not a deployable strategy  
**Purpose:** Statistical noise floor for expectancy comparison  
**Status:** Not implemented

At each entry cycle, flip a fair coin for direction (LONG/SHORT). Size at strategy `min_size`. Stop-loss at strategy `default_pct`.

**Promotion gate:** Any live strategy S1–S3 must beat S0 on `E_net` after 50+ demo trades with p < 0.05 on a one-tailed t-test before being promoted to live.

---

## 5. S1 — EMA Crossover Momentum

**Type:** TREND-FOLLOWING  
**Status:** YAML + prompt module implemented (`momentum.yaml`, `momentum.md`)  
**Horizon:** 1-min bars (60 available from Capital.com)  
**Assets:** EURUSD, GBPUSD, US500, DE40, GOLD  
**Signal hypothesis:** Recent returns predict near-future returns when drift dominates noise  
**Noise regime:** Degrades sharply in range-bound / low-ATR regimes

### 5.1 Mathematical Definition

```
signal_t = EMA_fast(S_t)  -  EMA_slow(S_t)
z_t      = signal_t  /  rolling_std(signal_t, window=50)
```

**Default parameters:** fast = 9 bars, slow = 21 bars (both on 1-min bid close). Starting points — tune per asset on demo data.

| Condition | Action |
|---|---|
| z_t > +1.0 AND EMA_fast slope positive | LONG signal — confirm with prior bar momentum |
| z_t < -1.0 AND EMA_fast slope negative | SHORT signal — confirm with prior bar momentum |
| -1.0 < z_t < +1.0 | No signal — output NONE |
| EMA_fast and EMA_slow converging | Signal weakening — hold only, no new entry |

### 5.2 Inputs to Claude Prompt

`analyze_instrument` computes these from Capital.com 1-min bid/ask bar data before assembling the Claude context. Claude receives structured values, not raw arrays.

| Input | Computation | Purpose |
|---|---|---|
| EMA_9 | Exponential WMA, span=9, on 1-min close-bid | Fast trend proxy |
| EMA_21 | Exponential WMA, span=21, on 1-min close-bid | Slow trend proxy |
| signal_t | `EMA_9 - EMA_21` | Raw crossover value |
| z_t | `signal_t / rolling_std(50)` | Regime-normalised signal |
| ATR_14 | Already computed by `analyze_instrument` | Volatility context for stop sizing |
| EMA_slope | `EMA_9[t] - EMA_9[t-3]` | Direction confirmation |
| spread_pct | Already computed by `analyze_instrument` | Cost-viability check |

**Implementation note:** EMA_9, EMA_21 are computed by `analyze_instrument` from the 60 × 1-min bars (Phase 9). `signal_t` (EMA_9 − EMA_21) and trend slope are also returned. The normalised z_t (signal_t / rolling_std) is not yet computed — Claude reasons qualitatively over the gap instead.

**Backtest signal note:** The deterministic backtest approximation (`backtest/signals.py`) adds a minimum EMA gap filter: the signal is suppressed if `|EMA_9 − EMA_21| / EMA_21 < 0.05%`. This guards against noise crossovers on M1 bars where the two EMAs are nearly identical (0.05% = 4 pts at 8,000 — 4× a typical 1-pt fixed spread; research-validated minimum for positive signal/cost ratio). The threshold is configurable via `_MIN_EMA_GAP_PCT` in `signals.py`.

### 5.3 Stop Loss & Take Profit Rules

| Parameter | Rule | YAML field |
|---|---|---|
| Hard stop loss | ATR_14 × 1.5 below entry (long) / above entry (short) | `risk.stop_loss.default_pct` |
| Hard stop ceiling | Must not exceed `max_pct` from YAML | `risk.stop_loss.max_pct = 5.0%` |
| Trailing stop | Ratchet-only. Initial distance = ATR_14 × 1.5. Governed by YAML bounds | `trailing_stop.min/max_distance_pct` |
| Take profit (initial) | ATR_14 × 2.5 from entry — minimum R:R 1.5:1 | `risk.take_profit.min_rr_ratio = 1.5` |
| Time exit | `close_minutes_before_session_end = 30` — enforced by monitor | `risk.time_exit` |

**EMA cross-back exit:** The monitor is a pure price-level rule engine — it cannot evaluate EMAs. If EMA_fast crosses back through EMA_slow, this is detected by Claude at the next `scan_markets` / `analyze_instrument` call (up to 60 seconds later). Claude proposes CLOSE when it detects a cross-back. This is not a monitor rule.

### 5.4 Claude Prompt Contract

The `momentum.md` prompt module defines reasoning requirements. Strategy-specific reasoning fields (trend clarity, regime assessment, EMA slope interpretation) are expressed as sub-content within the standard proposal schema fields `market_context`, `signal_basis`, and `contra_indicators` — not as extra top-level JSON fields.

**`contra_indicators` is mandatory.** Must address: Is the move overextended? Is sentiment contrarian? Are there resistance/support levels close to entry?

**Forbidden actions:** OPEN when spread > 30% of ATR. OPEN within 30 min of session end. OPEN when ATR is too low to cover spread cost.

**Scaling:** One position add permitted if profitable by 1R and EMA signal still positive. Max adds governed by `position_scaling.max_adds = 2`.

---

## 6. S2 — Z-Score Mean Reversion

**Type:** COUNTER-TREND  
**Status:** YAML + prompt module implemented (`mean_reversion.yaml`, `mean_reversion.md`)  
**Horizon:** 1-min bars (60 available from Capital.com)  
**Assets:** EURUSD, EURGBP, USDJPY, GOLD  
**Signal hypothesis:** Price deviates from local equilibrium and reverts — stationary micro-structure within a session  
**Noise regime:** Fails during trend initiation — a trending regime invalidates all signals

### 6.1 Mathematical Definition

```
mu_t    = rolling_mean(close_bid_t, window=N)
sigma_t = rolling_std(close_bid_t,  window=N)
z_t     = (close_bid_t - mu_t) / sigma_t
```

**Default parameter:** N = 30 bars (1-min). Mean reversion is only valid within a session — do not carry overnight.

| z_t value | Signal |
|---|---|
| z_t > +2.0 | SHORT entry — price extended above mean |
| z_t < -2.0 | LONG entry — price extended below mean |
| 0.0 < \|z_t\| < 0.3 | Exit zone — close existing position |
| 1.0 < \|z_t\| < 2.0 | Wait — not extended enough to justify entry |
| \|z_t\| > 3.0 | Extreme — possible trend break; do not enter, re-evaluate regime |

**Implementation note:** mu_t, sigma_t, and z_t (20-bar window) are computed by `analyze_instrument` from the 60 × 1-min bars (Phase 9). The exit zone (|z_t| < 0.3) is not enforced by the monitor — Claude detects it at the next analysis call.

### 6.2 Regime Validity Check (Critical)

Gate on all of the following before any signal evaluation:

| Check | Gate condition |
|---|---|
| EMA trend filter | `ABS(EMA_9 - EMA_21) / price < 0.001` — no strong trend present |
| ATR range | ATR_14 within normal session range — not expanding |
| Recent z-score history | z_t must have crossed zero at least twice in the last 60 bars |
| News / event proximity | **No automated gate available** — Capital.com does not provide a news calendar API. Operator must avoid running S2 during known high-impact event windows (e.g. NFP, FOMC). |

### 6.3 Stop Loss & Exit Rules

| Parameter | Rule | YAML field |
|---|---|---|
| Hard stop in price terms | Also enforce `max_pct` from YAML as absolute ceiling | `risk.stop_loss.max_pct = 3.0%` |
| Default stop | `default_pct = 1.5%` from entry | `risk.stop_loss.default_pct` |
| Target exit | When z_t returns to 0.0 — not before | Expressed in `take_profit.initial_value` at entry |
| Trailing stop | Disabled | `trailing_stop.enabled = false` |
| Time exit | Close 30 min before session end — hard rule | `risk.time_exit` |
| Scaling | NOT permitted — adding to a losing reversion trade is forbidden | `position_scaling.enabled = false` |

### 6.4 Claude Prompt Contract

The `mean_reversion.md` prompt module defines reasoning requirements. Strategy-specific reasoning (regime stationarity, z-score context, reversion history) is expressed within the standard proposal schema fields.

**`contra_indicators` is mandatory.** Must address: Could this be the start of a genuine trend? Is there a news/event risk the operator should be aware of? Is the spread wide enough to eat into the expected reversion distance?

**Hard rules:** DO NOT open a mean reversion position if the EMA crossover signal is active in the same direction as the deviation. DO NOT scale into a losing reversion trade.

---

## 7. S3 — Donchian Channel Breakout

**Type:** BREAKOUT / VOLATILITY EXPANSION  
**Status:** DEFERRED — `breakout.yaml` and `breakout.md` not yet written  
**Horizon:** 5-min bars (requires additional `get_prices` call at `resolution="5MINUTE"`)  
**Assets:** US500, DE40, UK100, BTCUSD, GOLD  
**Signal hypothesis:** Large moves tend to continue once a consolidation range is cleanly broken  
**Noise regime:** High whipsaw rate — false breakouts are the dominant failure mode; confirmation is mandatory

### 7.1 Mathematical Definition

```
upper_t = max( S_{t-k}, ..., S_{t-1} )   # Donchian upper — bid high
lower_t = min( S_{t-k}, ..., S_{t-1} )   # Donchian lower — bid low
range_t = upper_t - lower_t               # consolidation width
```

**Default parameter:** k = 20 bars at 5-min resolution. Implementation requires `analyze_instrument` to make a second `get_prices(epic, resolution="5MINUTE", max=20)` call when the selected strategy is breakout.

| Condition | Signal |
|---|---|
| S_t > upper_t AND confirmation gates pass | LONG breakout |
| S_t < lower_t AND confirmation gates pass | SHORT breakout |
| S_t within channel | No signal — price in consolidation |

### 7.2 Breakout Confirmation Gates — False Positive Mitigation

Claude must enumerate gates passed. Fewer than 4/6 = NONE.

| Confirmation gate | Minimum threshold |
|---|---|
| Close beyond channel | Candle must CLOSE beyond the boundary — not just wick |
| ATR expansion | ATR_14 at breakout > ATR_14 at session open |
| Channel width minimum | `range_t > 0.15%` of price — narrow channels excluded |
| Re-test tolerance | Allow 1 re-test of the broken level before invalidating |
| EMA alignment | EMA_9 direction must align with breakout direction |
| Recent false break count | If 2+ false breaks visible in last 20 bars, suppress signal — approximated from bar data by Claude |

**Note on false break count:** The architecture has no dedicated false-break event store. This gate is evaluated by Claude from the 5-min bar history — it is a heuristic assessment, not a precise count from a DB query.

### 7.3 Stop Loss & Take Profit Rules

| Parameter | Rule |
|---|---|
| Hard stop loss | Price closes back inside channel |
| Hard stop ceiling | `max_pct` from YAML — whichever is tighter |
| Trailing stop | Activate once +1R achieved |
| Take profit | Fixed at entry in v1 — expressed as `take_profit.initial_value` in proposal JSON. **Dynamic TP adjustment is not supported** — the monitor is a price-level rule engine only |
| Time exit | Close 30 min before session end |
| Scaling | One add if momentum confirms beyond initial breakout level |

**False-break close:** If price returns inside the channel, Claude detects this at the next scan cycle (up to 60s) and proposes CLOSE. This is not a monitor rule — the monitor cannot evaluate channel position.

### 7.4 Claude Prompt Contract

When `breakout.md` is written it must define: `channel_assessment` (range width, consolidation duration in bars), `breakout_quality` (clean close or wick), `confirmation_gates_passed` (enumerate each of the 6 gates explicitly), `contra_indicators` (mandatory: recent false break history, EMA conflict, thin liquidity window).

**Hard rules:** Never open on a wick breakout without close confirmation. Never open if 2+ false breaks are evident in recent bar history.

---

## 8. S4 — Capital.com Sentiment Overlay

**Type:** REASONING OVERLAY — not a standalone strategy  
**Status:** Sentiment data is already returned by `analyze_instrument`. Overlay reasoning to be folded into S1 and S3 prompt modules — no separate `sentiment.yaml` / `sentiment.md` pair.

### 8.1 Rationale

Capital.com provides client sentiment (% of clients long vs short) via `get_client_sentiment()`. It is already included in the `analyze_instrument` output for every instrument. At extremes, retail consensus tends to be wrong. This is aggregate retail positioning — not order book depth.

**S4 is not a strategy in the YAML+MD sense.** It has no independent position sizing, stop loss bounds, or entry conditions. It modifies how Claude interprets S1 and S3 signals. The overlay logic belongs in the S1 `momentum.md` and S3 `breakout.md` prompt modules, not in a separate strategy pair.

### 8.2 Signal Definition

```
sentiment_t = pct_long_clients_t / 100          # range: 0.0 to 1.0

extreme_long  := sentiment_t > 0.75
extreme_short := sentiment_t < 0.25
```

| Sentiment condition | Overlay effect |
|---|---|
| sentiment_t > 0.75 | Contrarian SHORT bias — suppress LONG signals from S1/S3; note as `contra_indicator` |
| sentiment_t < 0.25 | Contrarian LONG bias — suppress SHORT signals from S1/S3; note as `contra_indicator` |
| 0.40 < sentiment_t < 0.60 | No overlay — S1/S3 signal unmodified |
| 0.25–0.40 or 0.60–0.75 | Weak bias — note in reasoning, does not modify signal threshold |

For S2 (mean reversion), sentiment is part of the regime assessment but does not trigger suppression — S2 is already a contrarian strategy.

### 8.3 Integration

Sentiment extremes are noted in `contra_indicators` of the proposal JSON. The `analyze_instrument` tool always includes `sentiment.long_pct` and `sentiment.short_pct` in its output. Claude reads them and applies the above thresholds as part of S1/S3 reasoning.

**Hard rules:** Sentiment alone never triggers OPEN. Sentiment extremes can persist — do not fade a strong trend just because sentiment is extreme. Always log `sentiment_t` in `data_used.sentiment` field of the proposal JSON.

---

## 9. Cross-Strategy Position Sizing — Volatility Scaling

Position size should be scaled inversely to current volatility so each trade targets a consistent risk quantum regardless of the asset's volatility regime.

```
suggested_size = (target_risk_pct * account_balance) / (ATR_14 * price)
```

`target_risk_pct` is set per-strategy in YAML (typically 1.0–2.0%).

| Scenario | Effect |
|---|---|
| High ATR (volatile session) | Smaller position — constant risk quantum maintained |
| Low ATR (quiet session) | Larger position — bounded by `max_size` in YAML |
| ATR below minimum threshold | No trade — cost-to-volatility ratio too poor |

### Implementation (Phase 9 — Done)

1. **`target_risk_pct` is set in each strategy YAML** — `momentum.yaml`: 1.0%, `mean_reversion.yaml`: 0.5%.
2. **Account balance is fetched inside `analyze_instrument`** via `get_account_info()` and included in the `account` block of the response.
3. **`analyze_instrument` computes and returns `suggested_size`** = `target_risk_pct / 100 × balance / ATR`. Claude may propose smaller. Claude may NOT propose larger. Preflight validates against YAML `max_size`.

---

## 10. Session-Start Strategy Selection

At session start `scan_markets` classifies the regime per asset from ATR, trend slope, spread/ATR ratio, and sentiment — all currently computed. Claude selects the best-fit strategy from this output.

| Regime indicator | Dominant regime | Recommended strategy |
|---|---|---|
| Trend slope positive/negative, ATR sufficient | Trending | S1 — Momentum |
| Trend slope near zero, ATR flat, spread/ATR acceptable | Range-bound | S2 — Mean Reversion |
| Price compressing, then clean break with ATR expansion | Breakout | S3 — Donchian (deferred) |
| Sentiment > 0.75 or < 0.25 | Extreme | Note in contra_indicators — overlay on S1/S3 |
| Spread > 30% of ATR, or ATR very low | No edge | NONE — skip this instrument |

**Key constraints:** Claude does not arbitrarily pick a strategy. The scan prompt presents pre-computed regime data and Claude selects the best fit from the implemented strategies (currently S1, S2). Strategy switching mid-session requires explicit human approval.

---

## 11. Strategy YAML Risk Bounds — Actual Values

Values below reflect the current `config/strategies/*.yaml` files, which are the source of truth. Preflight enforces hard rejection of proposals outside these bounds.

| Parameter | S1 Momentum | S2 Mean Reversion | S3 Breakout |
|---|---|---|---|
| `entry.min_size` | 0.1 | 0.1 | TBD |
| `entry.max_size` | 5.0 | 3.0 | TBD |
| `stop_loss.default_pct` | 2.0% | 1.5% | TBD |
| `stop_loss.max_pct` | 5.0% | 3.0% | TBD |
| `trailing_stop.enabled` | true | false | true (post +1R) |
| `trailing_stop.min_distance_pct` | 0.5% | — | TBD |
| `trailing_stop.max_distance_pct` | 3.0% | — | TBD |
| `take_profit.min_rr_ratio` | 1.5 | 2.0 | TBD |
| `position_scaling.enabled` | true | false | true |
| `position_scaling.max_adds` | 2 | 0 | 1 |
| `time_exit.close_minutes_before_session_end` | 30 | 30 | 30 |
| `target_risk_pct` | 1.0% | 0.5% | TBD |

S4 has no YAML entry — sentiment overlay logic is embedded in S1 and S3 prompt modules.

**Global bounds (`config/risk.yaml`):** `max_loss_pct_per_trade = 5.0%`, `margin_floor_pct = 20.0%`, `max_open_positions = 3`.

---

## 12. Open Items — Strategy Layer

| Item | Priority | Status |
|---|---|---|
| `_base.md` — output schema + universal hard rules | High | **Done** |
| `scan.md` — regime classification + ranking criteria | High | **Done** |
| `momentum.md` — S1 prompt module | High | **Done** |
| `mean_reversion.md` — S2 prompt module | High | **Done** |
| EMA_9, EMA_21, trend slope in `analyze_instrument` (S1) | High | **Done** (Phase 9) |
| Rolling z-score (mu_t, sigma_t, z_t) in `analyze_instrument` (S2) | High | **Done** (Phase 9) |
| `target_risk_pct` in strategy YAMLs + vol-scaled `suggested_size` | Medium | **Done** (Phase 9) |
| Backtesting framework — validate signal edge before live tuning | High | **Done** (Phase 10) |
| Fold sentiment overlay reasoning into `momentum.md` | Medium | Not started |
| Tune EMA fast/slow windows per asset on demo data | Medium | Not started |
| Tune z-score entry/exit thresholds per asset (S2) | Medium | Not started |
| S0 random baseline for statistical comparison | Medium | Not started |
| p-value promotion gate (demo → live) — E_net > 0, 30+ trades, p < 0.05 | Medium | Not started |
| Write `breakout.yaml` and `breakout.md` (S3) | Low | Deferred |
| Calibrate Donchian lookback k per asset class (S3) | Low | Deferred |
| Evaluate Alpha Vantage for macro regime context (S1 filter) | Low | Deferred |
| Evaluate microstructure signals if Capital.com adds depth data | Low | Deferred |

---

## 13. References

| Source | Relevance |
|---|---|
| Lopez de Prado — *Advances in Financial Machine Learning* | Formal signal construction, meta-labelling, feature engineering |
| Ernie Chan — *Algorithmic Trading* | Mean reversion + momentum; practical intraday focus |
| QuantConnect Strategy Library | Open source implementations of canonical strategy families |
| Capital.com API docs (demo) | Primary data source — confirm sentiment endpoint availability per instrument |
