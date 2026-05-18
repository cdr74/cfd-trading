# CFD Trading System — Strategy Catalog & Mathematical Reference

**Version:** 1.3  
**Date:** 2026-05-17  
**Status:** S1 (momentum), S2 (mean reversion) and S5 (ORB) fully implemented and backtested; S3 (Donchian breakout) and S4 (sentiment overlay) deferred  
> **⚠️ STRATEGY AUDIT VERDICT 2026-05-18 (`docs/STRATEGY_AUDIT.md`):** Phase A kill-criterion triggered — **S2/mean-reversion DROPPED** (non-viable on retail CFD; literature-confirmed); **S1/momentum & S5/ORB UNVALIDATED** (no edge survived Deflated-Sharpe / out-of-sample). No strategy is deploy-ready; the system is pivoting to a fundamental strategy debate. The maths/specs below remain accurate as *implemented*, not as *validated edges*.  
**Companion to:** `docs/SYSTEM_DESIGN.md`, `docs/GLOSSARY.md`, `config/strategies/`, `src/cfd_trading/tools/scan_tools.py`, `docs/BACKTESTING.md`  
**Repo:** github.com/cdr74/cfd-trading (private)

> **Abbreviations & terms:** see [`docs/GLOSSARY.md`](GLOSSARY.md) — single source of truth for every acronym used in this repo.

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

**Per-strategy indicators** (computed in Python from the same bar data — no new API calls unless noted):

| Indicator | Required by | Status / How |
|---|---|---|
| `ema_9`, `ema_21` | S1 | **Done (Phase 9)** — `_compute_ema(bars, 9/21)` on 60×1-min closes |
| `zscore` (20-bar price z) | S1, S2 | **Done (Phase 9)** — single `_compute_zscore(bars, 20)`, shared by S1 & S2 |
| Donchian channel upper/lower | S3 | **Deferred** — would need a second `get_prices` at 5-min resolution |
| Vol-scaled `suggested_size` | All | **Done (Phase 9)** — `target_risk_pct` in strategy YAML; see §9 |

Note: `analyze_instrument` returns **one** 20-bar price `zscore` (the same `_compute_zscore`
helper for both S1 and S2) — there is no separate "signal z-score" vs "z_t"; the §5.1
`signal_t/rolling_std(50)` normalisation is a design concept, not implemented.

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
**Horizon:** deterministic signal runs on **M30** bars (2026-05-15); Claude's live `analyze_instrument` context still uses 60 × 1-min bars (see §5.2)  
**Assets:** EURUSD, GBPUSD, US500, DE40, GOLD  
**Signal hypothesis:** Recent returns predict near-future returns when drift dominates noise  
**Noise regime:** Degrades sharply in range-bound / low-ATR regimes

### 5.1 Mathematical Definition

```
signal_t = EMA_fast(S_t)  -  EMA_slow(S_t)
z_t      = signal_t  /  rolling_std(signal_t, window=50)     # design concept — NOT implemented
```

**Default parameters:** fast = **9 bars**, slow = **21 bars** — bar-count windows applied at
the **strategy resolution**: **M30** bars for the deterministic `signal_engine` (live monitor +
backtest); 1-min closes only in Claude's separate `analyze_instrument` context (§5.2). "Starting
points — tune per asset" remains the intent.

> **Implemented entry ≠ this z_t table.** The `signal_t / rolling_std(50)` normalisation and the
> ±1.0 z-threshold table below are the original *design concept* and are **not** what the code
> does. The implemented momentum entry is the **pending-crossover + confirmation-window**
> mechanism in §5.2 (`MomentumSignalState`, M30, no normalised z_t). The table is retained for
> design rationale only.

| Condition (design concept) | Action |
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
| signal_t | `EMA_9 - EMA_21` (Claude derives from the two EMAs) | Raw crossover value |
| zscore | `(close − mean) / std` over the last **20** 1-min closes (`_compute_zscore(bars, 20)`) | Price-extension context |
| ATR_14 | Already computed by `analyze_instrument` | Volatility context for stop sizing |
| spread_pct | Already computed by `analyze_instrument` | Cost-viability check |

**Implementation note (code reality):** `analyze_instrument` (`tools/scan_tools.py`) fetches
`get_prices(epic, resolution="MINUTE", max=60)` and returns `ema_9`, `ema_21`, and a 20-bar
**price** `zscore` (`_compute_zscore`, the *same* helper S2 uses — `(close−μ)/σ` over the last
20 closes), **not** the `signal_t / rolling_std(50)` normalisation of §5.1 (which is not
implemented anywhere). Claude derives the EMA gap and reasons qualitatively over it; there is no
`EMA_slope = EMA_9[t]−EMA_9[t-3]` field — slope is the deterministic engine's 22-bar OLS, used
inside `signal_engine`, not surfaced to the prompt.

**Backtest signal note** *(redesigned 2026-05-15; shared `strategy/signal_engine.py`, used by both the backtest and the live monitor):*

Momentum runs on **M30** bars (`momentum.yaml resolution: M30` — single source of truth; M15 is a documented future option). Entry is a **pending crossover with a confirmation window**, not a fire-on-cross:

- An EMA_9/EMA_21 crossover does **not** fire on the cross bar (the EMAs are ≈coincident there, so a gap test could never pass — the old "gap filter at the cross bar" rejected ~99% of crossovers; that was a filter-placement bug, not the algorithm). Instead the crossover opens a **pending** signal in that direction.
- On each of the next `confirm_bars` bars (tunable constructor arg, default **6**) the pending **fires** once *all* confirm at that later bar: `|EMA_9 − EMA_21|/EMA_21 ≥ min_ema_gap_pct` (default 0.05%); `ADX(14) ≥ adx_threshold` (default 25; disable via `signal_kwargs={"adx_threshold": 0.0}`); trend slope sign matches; M30 directional bias matches (rolling 30-bar OLS slope; disable via `m30_gate=False`; permissive while <30 bars).
- If no bar within the window confirms, the pending expires (no trade). A new opposite crossover replaces it. Net effect on the 3-yr re-baseline: momentum fires ~1,770 trades vs ~44 under the old fire-on-cross gap test.

### 5.3 Stop Loss & Take Profit Rules

| Parameter | Rule (deterministic engine — code reality) | YAML field |
|---|---|---|
| Hard stop loss | `stop_distance = fill_price × default_pct/100` (= **2.0%** of entry); BUY `fill − stop_distance`, SELL `fill + stop_distance`. **Not ATR-based.** | `risk.stop_loss.default_pct = 2.0%` |
| Hard stop ceiling | `default_pct` must not exceed `max_pct`; preflight hard-rejects proposals beyond it | `risk.stop_loss.max_pct = 5.0%` |
| Trailing stop | *(Resolved 2026-05-15.)* Ratchet-only. Distance = **ATR₁₄ at entry × 1.5, fixed for the trade** (not recomputed per bar). Stop = best-favourable-price ∓ distance; never loosens. ATR comes from the shared streaming `signal_engine` (live + backtest, identical). This is the **only** place ATR enters momentum stops. | `trailing_stop.atr_multiplier = 1.5` |
| Take profit (initial) | `stop_distance × min_rr_ratio` (= **1.5 ×** the % stop distance), not ATR×2.5 | `risk.take_profit.min_rr_ratio = 1.5` |
| Time exit | `close_minutes_before_session_end = 30` — enforced by monitor | `risk.time_exit` |

> **Engine rule vs Claude proposal.** The table above is the *deterministic* rule used by the
> backtest and as the live preflight bound (a fixed % stop, TP a multiple of it). In the live
> entry flow Claude *may propose* an ATR-derived stop/TP from `analyze_instrument` context (e.g.
> ATR₁₄×1.5 stop, ×2.5 TP — the original §5.1 heuristic); `preflight.py` then enforces it
> against `default_pct`/`max_pct`/`min_rr_ratio`. The ATR multiples are a Claude sizing
> heuristic, **not** the mechanical rule.

**EMA cross-back exit:** *(Revised 2026-05-15.)* This is now a **deterministic monitor rule** — SYSTEM_DESIGN §3.7 rule 4 (signal-exit). The monitor maintains the strategy's streaming signal state (shared `strategy/signal_engine`, warm-up back-filled on start) and closes the position when EMA-fast crosses back through EMA-slow against it, evaluated every 60 s — no longer dependent on Claude noticing it in the conversation. Claude may still propose CLOSE earlier from its own analysis; the monitor rule is the guaranteed floor.

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

**Default parameter:** N = **20** bars (M1) — `MeanReversionSignalState._WINDOW = 20` in `strategy/signal_engine.py` is the ground truth (live + backtest). Mean reversion is only valid within a session — do not carry overnight.

| z_t value | Signal |
|---|---|
| z_t > +2.0 | SHORT entry — price extended above mean |
| z_t < -2.0 | LONG entry — price extended below mean |
| \|z_t\| ≤ 0.5 | Exit zone — close existing position (`zscore_exit_threshold` default **0.5**; deterministic monitor signal-exit, §3.7 rule 4) |
| 1.0 < \|z_t\| < 2.0 | Wait — not extended enough to justify entry |
| \|z_t\| > 3.0 | Extreme — possible trend break; do not enter, re-evaluate regime |

**Implementation note:** mu_t, sigma_t, and z_t (20-bar window) are computed by `analyze_instrument` from the 60 × 1-min bars (Phase 9). *(Revised 2026-05-15.)* The reversion exit (`|z_t| ≤ zscore_exit_threshold`) **is now enforced by the monitor** as a deterministic signal-exit rule (SYSTEM_DESIGN §3.7 rule 4) running every 60 s via the shared `strategy/signal_engine` — it no longer depends on Claude detecting it at the next analysis call.

### 6.2 Regime Validity Check (Critical)

**Implemented gates (`MeanReversionSignalState`, code reality).** A signal fires only when *all*
hold; the conceptual table below is the design rationale, not the literal predicates:

| Implemented gate | Exact predicate |
|---|---|
| Window | rolling **20** M1 closes (`_WINDOW = 20`); no signal until full |
| Entry threshold | `z ≥ +2.0` → SHORT; `z ≤ −2.0` → LONG; else None |
| ADX regime gate | suppressed when `ADX(14) ≥ adx_threshold` (default **25.0**) — trending market; permissive while ADX warming up |
| ATR viability gate | suppressed when `ATR(14) < 4 × spread_pts`; disabled when `spread_pts = 0.0` (the default in unit tests) |
| Reversion exit | `\|z\| ≤ zscore_exit_threshold` (default **0.5**) → deterministic monitor signal-exit (§3.7 rule 4) |
| News / event proximity | **No automated gate** — Capital.com has no news-calendar API. Operator must avoid running S2 during known high-impact windows (NFP, FOMC). |

**Conceptual model (design intent — not the implemented predicates):**

| Check | Gate condition |
|---|---|
| EMA trend filter | `ABS(EMA_9 - EMA_21) / price < 0.001` — no strong trend present |
| ATR range | ATR_14 within normal session range — not expanding |
| Recent z-score history | z_t must have crossed zero at least twice in the last 60 bars |
| News / event proximity | as above — operator-managed, no API |

The implemented **ADX ≥ 25** suppression is the code's stand-in for the conceptual "EMA trend
filter" / "ATR range" intent; the "z crossed zero twice in 60 bars" history check is **not**
implemented.

### 6.3 Stop Loss & Exit Rules

| Parameter | Rule | YAML field |
|---|---|---|
| Hard stop in price terms | Also enforce `max_pct` from YAML as absolute ceiling | `risk.stop_loss.max_pct = 3.0%` |
| Default stop | `default_pct = 1.5%` from entry | `risk.stop_loss.default_pct` |
| Target exit | When `|z_t| ≤ zscore_exit_threshold` (z back to midline) | **Deterministic monitor signal-exit** (§3.7 rule 4) via shared `signal_engine`, every 60 s |
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
| `trailing_stop.atr_multiplier` | **1.5** (dist = ATR₁₄@entry × mult, fixed, ratchet-only) | — | TBD |

> *(Resolved 2026-05-15.)* S1 trailing is ATR-based (`atr_multiplier`), superseding the
> former `min/max_distance_pct` fixed-% fields. S2 trailing disabled. **S5/ORB trailing
> is disabled** (`orb.yaml trailing_stop.enabled = false`) — OR-width stop + fixed
> 2×OR-width TP only; ATR-trailing was tested and reverted (see §13, RESEARCH).
> yaml files now reflect this: `momentum.yaml trailing_stop.atr_multiplier: 1.5`
> (fixed-% fields removed); `orb.yaml trailing_stop.enabled: false`.
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

## 13. S5 — Opening Range Breakout (ORB)

**Research basis:** Zarattini & Aziz (2024) — Sharpe > 1.5 on US equity index futures at 15-min resolution. Structural edge: the first bar of the session captures order flow imbalance at the open; the breakout direction predicts session continuation.

**Algorithm (current: 2-bar OR, OR-width-based stop):**

```
1. Collect the Opening Range over the first `or_bars` M15 bars of each session
   (default: 2 bars = 30 min). First OR bar identified by UTC timestamp alignment.
2. OR high = max(bar.high) over collection bars
   OR low  = min(bar.low)  over collection bars
3. For each subsequent M15 bar in the same session:
     if bar.high > OR high → LONG (break above range)
     if bar.low  < OR low  → SHORT (break below range)
4. At most one signal per session — first breakout direction wins
5. Stop: OR low (LONG) / OR high (SHORT) — natural invalidation at OR boundary
   TP:   entry ± OR_width × rr_ratio (rr_ratio = 2.0)
6. Reset OR on the next session open bar
```

**Key design choices:**
- **2-bar OR (30 min):** more robust than 1-bar; price has two bars to establish genuine support/resistance rather than a single noisy open candle. Matches the 30-min OR in the Zarattini & Aziz research setup.
- **OR-width-based stop:** stop at opposite OR boundary is the natural invalidation level. Tighter stop for narrow ranges (fewer false breakout losses), wider for wide ranges (respects the actual range). Implemented via `get_entry_levels()` on `ORBSignalState`.
- **No trailing stop** *(resolved 2026-05-15)*: ORB exits on the OR-width hard stop, the fixed 2×OR-width TP, or the time-exit only. ATR×1.5 trailing was tested and reverted — OR-width is already a session-calibrated ATR proxy, so trailing exits winners early (see RESEARCH "ATR-Trailing Exit"). `orb.yaml trailing_stop.enabled = false`.
- **Strict inequality:** touching OR level is not a breakout; requires `bar.high > OR high`
- **min_rr_ratio = 2.0:** TP = entry ± OR_width × 2. ORB targets session continuation; higher R:R than momentum (1.5).

**Session open times (UTC) — `backtest/sessions.py`:**

| Instrument | Session | UTC Open |
|---|---|---|
| US500 | NYSE | 14:30 |
| DE40 | Xetra | 08:00 |
| UK100 | LSE | 08:00 |
| FX (all) | London | 08:00 |
| GOLD, XBRUSD | London/ICE | 08:00 |
| BTCUSD, ETHUSD | Daily (no session) | 00:00 |

**DST caveat:** Session open times are fixed UTC values. European instruments shift by 1 hour around March/October clock changes. For a 4-month dataset this affects ~2 weeks of sessions and is acceptable for backtesting purposes.

**Implementation:** `ORBSignalState` in `strategy/signal_engine.py` — the shared module imported by *both* the live monitor and the backtest engine (no `backtest/signals.py` — that file was promoted to `strategy/signal_engine.py` on 2026-05-15). Runs at the strategy's YAML `resolution: M15` (M1 bars aggregated in-process; override with `--resolution`). The per-instrument session time is looked up in `backtest/sessions.py` and passed via `signal_kwargs`.

---

<!-- §14 Backtest Experiment Log removed 2026-05-15 — all results invalidated (time-exit disabled in engine); see project rebuild -->

---

## 15. References

| Source | Relevance |
|---|---|
| Lopez de Prado — *Advances in Financial Machine Learning* | Formal signal construction, meta-labelling, feature engineering |
| Ernie Chan — *Algorithmic Trading* | Mean reversion + momentum; practical intraday focus |
| QuantConnect Strategy Library | Open source implementations of canonical strategy families |
| Capital.com API docs (demo) | Primary data source — confirm sentiment endpoint availability per instrument |
| Zarattini & Aziz (2024) — *Opening Range Breakout* | Sharpe > 1.5 on US equity index futures at 15-min resolution; empirical basis for ORB (S5) |
| Gao, Han, Li & Zhou (2018 JFE) — *ITSM* | Intraday technical signal model: first half-hour predicts last half-hour; R² 1.6–3.3% |
