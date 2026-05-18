# Backtesting Guide

A complete reference for running, understanding, and extending the backtesting framework in this repo.

> **Abbreviations & terms:** see [`docs/GLOSSARY.md`](GLOSSARY.md) — single source of truth for every acronym used in this repo.

---

> **⚠️ ALL RESULTS INVALIDATED (2026-05-15).** Every backtest result previously in
> this document was produced with the intraday time-exit disabled (the engine never
> passed `session_end_time` to `evaluate_position`). §6.4–6.9, §8.5 and §8.8 were
> removed. No validated results exist yet — the engine is being rebuilt to mirror the
> live mechanical monitor. This document currently describes methodology only.


## Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Architecture](#2-architecture)
3. [Data Layer](#3-data-layer)
4. [Entry Signals](#4-entry-signals)
5. [Backtest Engine](#5-backtest-engine)
6. [CLI Runner](#6-cli-runner)
7. [Test Suite](#7-test-suite)
8. [Reading the Results](#8-reading-the-results)
9. [Limitations and Approximations](#9-limitations-and-approximations)

---

## 1. Purpose and Scope

The backtesting framework answers one question: **do the entry signal rules produce real edge before committing to live tuning?**

The live system uses Claude's reasoning to make entry decisions — non-deterministic, expensive, and impossible to replay at scale. The backtest replaces the entry decision with deterministic rule-based approximations derived directly from the strategy prompts (EMA crossover for momentum; z-score threshold for mean reversion), then reuses the identical `monitor.py` rule engine for exits. This approach is honest about what it is: a fast, reproducible proxy for the live entry logic, not a perfect replica.

**What backtesting covers:**
- Whether entry signals fire at a reasonable frequency
- Whether exits via the rule engine (hard stop, trailing stop, take profit) produce positive expectancy
- Which instruments respond better to which strategies
- Whether the stop/take-profit configuration produces acceptable profit factor and drawdown

**What backtesting does not cover:**
- Spread cost (not deducted from P&L — bars use mid prices)
- Slippage at entry (assumes fill at next bar's open)
- Margin and position sizing (size is not simulated — all P&L is in points)
- Live execution quirks (Capital.com create → confirm two-step, deal reference delays)

---

## 2. Architecture

```
Windows (MetaTrader 5)
  backtest/fetch_ohlc.py
    └── writes ohlc_bars to trading.db
          └── C:\Users\chris\dev\trading-data\trading.db

WSL2 (cfd-trading package)
  backtest/run.py  (CLI entry point)
    ├── storage/repository.py  get_bars()  ← always fetches M1
    │     └── reads ohlc_bars from trading.db via /mnt/c/...
    ├── backtest/aggregate.py  aggregate_bars()  ← M1→M15/M30/… in-process
    ├── strategy/loader.py  load_strategy()
    │     └── reads config/strategies/<name>.yaml + .md
    ├── strategy/signal_engine.py  MomentumSignalState / MeanReversionSignalState / ORBSignalState
    │     └── SHARED with the live monitor — streaming O(1)/bar, no I/O
    ├── backtest/engine.py  run_backtest()
    │     ├── update()s the signal_engine state per bar (entry)
    │     └── calls monitor/monitor.py evaluate_position() per bar (exit)
    └── prints summary table
```

**Performance:** The engine is O(n) per instrument — signal state is updated incrementally each bar rather than recomputed from scratch. The full 11-instrument × 2-strategy matrix over 1.1M M1 bars runs in approximately **17 seconds**.

**Key design invariant:** no Capital.com or Anthropic API calls are possible during a backtest run. `run.py` sets `BACKTEST_MODE=true` before any imports, which causes `CapitalClient` to raise `RuntimeError` at instantiation. This guard is enforced at the client level, not per-tool.

---

## 3. Data Layer

### 3.1 Fetch script (Windows-side)

`backtest/fetch_ohlc.py` runs on Windows Python (not WSL2) because MetaTrader 5 uses Windows IPC.

**Resolutions supported:** `M1`, `M15`, `H1` via `--resolution` (default `M1`).
The MT5 server retains coarser resolutions for far longer than M1; see
`probe_history.py` to inspect the actual depth.

**Usage:**

```cmd
:: M1 initial load (4×30-day windows)
python fetch_ohlc.py --mode bulk

:: Native M15 across 3 years
python fetch_ohlc.py --mode bulk --resolution M15 --years 3

:: Native H1 across 6 years
python fetch_ohlc.py --mode bulk --resolution H1 --years 6

:: Restrict to an audit universe
python fetch_ohlc.py --mode bulk --resolution M15 --years 3 ^
    --instruments EURUSD,GBPUSD,USDJPY,EURGBP,US500,DE40,UK100,GOLD
```

**MT5 constraints (empirically verified):**

| Constraint | Value |
|------------|-------|
| API method | `copy_rates_range(symbol, timeframe, from_dt, to_dt)` |
| Per-call row cap | ~100,000 rows (MT5 silently truncates) |
| M1 history depth | ~14 weeks on Capital.com demo (4×30-day windows) |
| M15 history depth | ~2.5 years on most instruments (yearly chunks; older chunks may legitimately return 0) |
| H1 history depth | ~5 years on most instruments (yearly chunks) |
| Incremental update | 1 call per instrument (yesterday → today) |

**Why yearly chunks for M15/H1:** a single deep `copy_rates_range` whose
`from_dt` predates available data returns `(-2, "Invalid params")` instead
of empty rows. Yearly chunks sidestep this — earliest chunks may return 0
("predates history") cleanly while later chunks succeed. The script logs
these as info, not warnings.

**Symbol map** — two instruments use different names in MT5 vs the watchlist:

| Watchlist epic | MT5 symbol |
|----------------|-----------|
| GOLD | XAUUSD |
| XBRUSD | BRENTOIL |
| All others | Exact match |

The fetch script translates at write time — the SQLite `epic` column always stores the watchlist name (e.g. `GOLD`, not `XAUUSD`).

**Data sources ruled out:**

| Source | Reason |
|--------|--------|
| Capital.com REST API | Only ~17 hours of 1-min history; no date-range queries |
| Alpha Vantage | 25 free API requests/day — insufficient for bulk fetch |
| Histdata.com | Data ends at 2021 |
| Yahoo Finance | 1-min data only last 7 days; wrong IDs for CFD indices |

### 3.2 SQLite schema

```sql
ohlc_bars (
  epic        TEXT     -- watchlist name: EURUSD, GOLD, XBRUSD, etc.
  resolution  TEXT     -- "M1" for 1-minute bars
  ts          INTEGER  -- Unix timestamp (seconds UTC)
  open        REAL
  high        REAL
  low         REAL
  close       REAL
  volume      INTEGER
  PRIMARY KEY (epic, resolution, ts)
)
```

### 3.3 Current data state (as of 2026-05-11)

- **Total:** ~1.1M M1 bars across 11 instruments
- **Coverage:** ~98% for 24/7 crypto, ~71% for FX (weekdays only), ~68% for indices (shorter daily session)
- **DB path:** `C:\Users\chris\dev\trading-data\trading.db` (Windows) / `/mnt/c/Users/chris/dev/trading-data/trading.db` (WSL2)

### 3.4 Reading bars in Python

```python
from cfd_trading.storage.db import get_connection
from cfd_trading.storage.repository import get_bars

conn = get_connection("/mnt/c/Users/chris/dev/trading-data/trading.db")
bars = get_bars(conn, "EURUSD", "M1")          # all bars
bars = get_bars(conn, "GOLD", "M1", from_ts=1_700_000_000)  # from timestamp
```

`get_bars()` always returns bars in chronological order (ascending `ts`).

### 3.5 Native-history probe (Windows-side)

`backtest/probe_history.py` queries MT5 for the deepest available bar at
H1, M15, and M1 per instrument. Used to decide whether the backtest
window can be extended beyond the current ~14 weeks by switching to a
higher native resolution. Runs on Windows Python (same IPC constraint as
`fetch_ohlc.py`).

```cmd
python probe_history.py
python probe_history.py --years 5 --csv probe.csv
```

Output is a per-(epic, resolution) earliest-bar table. Higher
resolutions (H1, M15) typically expose substantially more history than
M1 because brokers retain coarser bars longer. Phase A6 of the audit
plan keys off the result.

---

## 4. Entry Signals

Source: `src/cfd_trading/strategy/signal_engine.py` — the **shared** module imported by *both* the live monitor and the backtest engine (promoted from the former `backtest/signals.py` on 2026-05-15 so live and backtest cannot drift).

The signal classes (`MomentumSignalState`, `MeanReversionSignalState`, `ORBSignalState`) stream one `OHLCBar` per `update()` call and return `"LONG"`, `"SHORT"`, or `None`. The `momentum_signal()` / `mean_reversion_signal()` functional wrappers (list of bars → last decision) are unit-test conveniences.

### 4.1 Momentum signal

**Approximates:** EMA_9 crosses above/below EMA_21 with trend slope confirmation, ADX regime gate, and M30 directional bias gate.

**Minimum bars:** 22 (EMA_21 seeds at bar 21; crossover needs one prior bar). With ADX(14) enabled the effective warm-up is ~28 bars before the gate can actively suppress non-trending signals.

**Logic:**

*(Redesigned 2026-05-15 — pending crossover + confirmation window. Runs on
**M30** bars. Replaces the old fire-on-cross logic, whose gap filter was
evaluated at the cross bar where the EMAs are ≈coincident and so rejected ~99%
of crossovers — a filter-placement bug, not the algorithm.)*

```
1. EMA_9, EMA_21, ADX(14) incrementally; append bar to 30-bar M30 buffer
2. A raw EMA_9/EMA_21 crossover opens (or replaces) a PENDING signal in
   that direction, age 0 — it does NOT fire on the cross bar
3. On each subsequent bar the pending ages; if age > confirm_bars
   (default 6) it expires (no trade)
4. While pending and within the window, FIRE in the pending direction iff
   ALL hold AT THIS bar:
     - |EMA_9 − EMA_21| / EMA_21 ≥ min_ema_gap_pct
     - ADX is warming up OR ADX ≥ adx_threshold
     - 22-bar trend slope sign matches the pending direction
     - M30 buffer not full OR M30 slope sign matches
   A confirm-fail keeps the pending alive for later bars in the window;
   only fire / expiry / a new crossover clears it
5. None otherwise
```

**Key detail — ADX regime gate:** Signal is suppressed when ADX(14) < 25 (default threshold). On M1 bars this detects whether the last 14 minutes are directionally trending. Passes unconditionally while ADX is warming up (first ~28 bars) to avoid missing early-session signals. Configurable via `signal_kwargs={"adx_threshold": value}` — set to `0.0` to disable entirely.

**Key detail — slope filter:** A crossover that contradicts the overall trend slope is suppressed. Prevents late-entry signals at trend exhaustion.

**Key detail — EMA gap filter (now a *confirmation*, not a cross-bar gate):** the `min_ema_gap_pct` floor (default **0.05%**) is checked on the post-cross confirmation bars, not at the crossover itself (where the gap is structurally ≈0). This is the fix for the old filter-placement bug. ADX, slope and M30 are likewise evaluated at the confirm bar. `confirm_bars` (default 6) is a tunable `MomentumSignalState` constructor arg.

**Key detail — slope window:** Slope computed over a fixed **22-bar window**, not unbounded history.

**Key detail — M30 directional bias gate:** Each M1 bar is appended to a rolling 30-bar buffer. When the buffer reaches 30 bars, OLS slope of those closes defines the 30-bar (≈30-min) trend direction. LONG entries are blocked when the M30 trend is bearish; SHORT entries are blocked when M30 is bullish. Permissive while the buffer is warming up (<30 bars). Disable via `signal_kwargs={"m30_gate": False}`.

**Indicator formulas:**

```
EMA(period) = SMA(first period bars) then α×price + (1−α)×prev_ema  where  α = 2/(period+1)
ADX(14)     = Wilder-smoothed DX over 14 bars; DX = |+DI − −DI| / (+DI + −DI) × 100
slope       = OLS regression coefficient of close prices over the last 22 bars
gap_pct     = |EMA_9 − EMA_21| / EMA_21  (must exceed 0.05% to fire)
m30_bullish = OLS slope of the last 30 closes > 0
```

**`check_exit()`:** Returns `"EMA cross-back"` when EMA-fast crosses back through EMA-slow *against* the open position — the deterministic momentum **signal-exit** (SYSTEM_DESIGN §3.7 rule 4), evaluated every cycle by both the live monitor and this engine. Returns `None` while there is no open position or no adverse cross. Hard stop, trailing stop, and take profit in `evaluate_position()` are all evaluated *before* `check_exit()`, so they take priority.

### 4.2 Mean reversion signal

**Approximates:** Price overextended beyond 2 standard deviations from a 20-bar rolling mean, in a non-trending regime.

**Minimum bars:** 20 (z-score window). ADX gate activates after ~28 bars.

**Logic:**

```
1. Compute z-score of the last close over the most recent 20 bars:
   z = (close - mean) / stddev
2. Cache z as last_z for check_exit()
3. Suppress if ADX is valid AND ADX >= adx_threshold (trending market)
4. SHORT if z >= +2.0  (price above mean by 2σ — fade the spike upward)
5. LONG  if z <= -2.0  (price below mean by 2σ — fade the drop)
6. None  if |z| < 2.0
```

**Key detail — ADX regime gate:** Signal is suppressed when ADX(14) ≥ 25. Mean reversion logic breaks down in trending markets — spreads diverge rather than converge. Set `adx_threshold=float("inf")` to disable. Passes while ADX is warming up.

**Key detail — ATR viability gate:** Signal is suppressed when `ATR(14) < 4 × spread_pts`. At M1 resolution, the dominant negative autocorrelation is bid-ask bounce (Roll 1984), not tradeable mean reversion — the gate enforces minimum volatility to justify the fixed spread cost. Disabled when `spread_pts=0.0` (default). Permissive while ATR is warming up (first ~14 bars).

**Key detail — windowed z-score:** Only the last 20 bars contribute to `mean` and `stddev`. Older history is ignored.

**`check_exit()`:** Returns `"Z-score midline"` when `abs(last_z) <= zscore_exit_threshold` (default **0.5**) — the deterministic MR **signal-exit** (SYSTEM_DESIGN §3.7 rule 4), fired when the expected reversion has materialised. Returns `None` otherwise.

> *(2026-05-15)* The former **hold-cap** (`"Hold cap"` after `max_hold_bars`, default 5) was a backtest-only stand-in for the missing time-exit. It was **removed** when the real time-exit was wired and `signal_engine` became shared with the live monitor — `MeanReversionSignalState` no longer accepts a `max_hold_bars` arg (`tests/unit/test_signals.py::test_max_hold_bars_constructor_arg_removed` asserts this). Only the z-midline half was promoted into the shared module.

Hard stop and take profit in `evaluate_position()` are evaluated *before* `check_exit()`, so they take priority. The engine calls `notify_entry()` / `notify_exit()` to keep the signal state's position side in sync.

**Indicator formula:**

```
mean  = sum(last 20 closes) / 20
sigma = sqrt(sum((c - mean)² for c in last 20) / 20)
z     = (close[-1] - mean) / sigma
```

---

## 5. Backtest Engine

Source: `src/cfd_trading/backtest/engine.py`

### 5.1 How the loop works

```
for each bar i in chronological order:

  if a position is open:
    call evaluate_position(position, price={bid: bar.close, offer: bar.close}, strategy_config)
    if CLOSE:   record trade exit; clear open position
    if ADJUST:  update current_stop to new_stop

  else (no open position):
    call signal_fn(bars[:i+1])   ← growing window, not fixed lookback
    if signal and i+1 < len(bars):
      entry_price = bars[i+1].open   ← fill at next bar's open
      compute stop_loss and take_profit from strategy config
      open a new Trade
```

**One position at a time.** The engine does not open a new position while one is already open. A new entry signal while in a position is silently ignored.

**End-of-data handling.** If a position is still open when bars are exhausted, it is closed at the last bar's close price with `exit_reason = "End of data"`. These trades are included in all metrics.

### 5.2 Stop and take profit calculation

Stop and TP are computed from the actual fill price (after spread adjustment):

```
fill_price    = next_bar.open ± spread_pts/2
stop_distance = fill_price × (default_pct / 100)

BUY:
  stop_loss    = fill_price - stop_distance
  take_profit  = fill_price + stop_distance × min_rr_ratio

SELL:
  stop_loss    = fill_price + stop_distance
  take_profit  = fill_price - stop_distance × min_rr_ratio
```

Parameters come from the strategy YAML:
- `risk.stop_loss.default_pct` — stop distance as % of fill price
- `risk.take_profit.min_rr_ratio` — take profit as a multiple of the stop distance

### 5.3 Exit rules (via `evaluate_position`)

The engine delegates per-bar exit decisions to `monitor/monitor.py::evaluate_position()` — the same function used by the live monitor. Rules are evaluated in priority order:

| Priority | Rule | Condition | Action |
|----------|------|-----------|--------|
| 1 | Hard stop | BUY: close ≤ stopLevel / SELL: close ≥ stopLevel | CLOSE |
| 2 | Trailing stop ratchet | momentum only (MR & ORB disabled): BUY candidate_stop > current_stop / SELL candidate_stop < current_stop | ADJUST (ratchet only — never widens) |
| 3 | Take profit | BUY: close ≥ profitLevel / SELL: close ≤ profitLevel | CLOSE |
| 4 | **Signal exit** | per-strategy `signal_engine.check_exit()` — MR: `\|z\| ≤ 0.5`; momentum: EMA cross-back; ORB: none | CLOSE |
| 5 | Time exit | session_end_time set and within close window | CLOSE |
| 6 | Default | None of the above | HOLD |

This is the **same 6-rule ordered set** as the live monitor (SYSTEM_DESIGN §3.7) — `evaluate_position()` is one function shared by both, so the priority order cannot drift.

For the **momentum trailing stop**, the candidate stop is `best_favourable_price ∓ trail_distance`, where `trail_distance = ATR₁₄@entry × atr_multiplier` (1.5) — captured once at entry from the shared `signal_engine` and **fixed for the trade** (resolved 2026-05-15; superseded the old `min/max_distance_pct` fixed-% model):

```
BUY:  candidate = max_close_since_entry  − ATR14@entry × 1.5
SELL: candidate = min_close_since_entry  + ATR14@entry × 1.5
```

The stop only ratchets in the profitable direction. Once raised (BUY) or lowered (SELL), it never reverses. MR and ORB set `trailing_stop.enabled: false`, so rule 2 is inert for them.

> **Shared deterministic exit path (implemented 2026-05-15).**
> Exits are the full ordered set: hard stop → trailing → take-profit → **signal-exit**
> → **time-exit**. The signal-exit (SYSTEM_DESIGN §3.7 rule 4) is a deterministic
> per-strategy predicate (MR: z-back-to-midline; momentum: EMA cross-back; ORB: none)
> living in the shared `strategy/signal_engine` module imported by *both* the live
> monitor and this engine — so the two cannot drift. The former backtest-only
> `check_exit()` hold-cap hack is dropped; its z-midline half is promoted into that
> shared module. Time-exit is now actually supplied (`session_end_time` + injected
> bar-time `now`, §5.6); previously it was dead (`session_end_time=None`). Because the
> z-midline/cross-back exits are now present in *both* live and backtest, the earlier
> "MR reads worse than live / mechanical floor" caveat no longer applies.

### 5.4 P&L calculation

P&L is computed in **points** (price units), not currency. Spread costs are embedded in the fill prices when `spread_pts > 0`:

```
entry_fill (BUY)  = next_bar.open + spread_pts/2   (buy at ask)
entry_fill (SELL) = next_bar.open - spread_pts/2   (sell at bid)
exit_fill  (BUY)  = bar.close    - spread_pts/2    (close BUY by selling at bid)
exit_fill  (SELL) = bar.close    + spread_pts/2    (close SELL by buying at ask)

BUY:  pnl_points = exit_fill - entry_fill  →  net cost = full spread
SELL: pnl_points = entry_fill - exit_fill  →  net cost = full spread
```

Spread values come from `backtest/spreads.py` (Capital.com typical mid-session values per instrument). No commission or contract size is applied. All metrics in `BacktestResult` are in price points.

### 5.5 Output — `BacktestResult` dataclass

| Field | Type | Description |
|-------|------|-------------|
| `epic` | `str` | Instrument epic |
| `strategy` | `str` | Strategy name |
| `total_trades` | `int` | All completed trades (including end-of-data closes) |
| `winning_trades` | `int` | Trades with `pnl_points > 0` |
| `win_rate` | `float` | `winning_trades / total_trades` (0.0–1.0) |
| `profit_factor` | `float` | `gross_profit / gross_loss`; `inf` if no losing trades |
| `max_drawdown_pct` | `float` | Peak-to-trough cumulative P&L loss as % of average entry price |
| `stop_out_rate` | `float` | Fraction of trades closed by hard stop |
| `signal_frequency` | `float` | Trades per week over the full bar span |
| `net_pnl_pts` | `float` | Sum of all `pnl_points`; positive = net profit, negative = net loss (in price units) |
| `avg_r` | `float` | Expectancy per trade in R-multiples: `net_pnl_pts / (n × avg_entry × stop_pct)` |
| `trades` | `list[Trade]` | Full trade-level detail |

Each `Trade` record contains:

| Field | Description |
|---|---|
| `epic`, `strategy`, `direction` | identification |
| `entry_ts`, `exit_ts` | UTC seconds |
| `entry_price`, `exit_price` | fill prices — **include half-spread** in the appropriate direction |
| `stop_loss`, `take_profit` | levels at time of entry |
| `exit_reason` | e.g. `Hard stop`, `Take profit`, `Z-score midline`, `End of data` |
| `pnl_points` | **net** of spread (derived from fills) |
| `risk_pts` | actual stop distance in price units; used for per-trade AvgR |
| `entry_mid`, `exit_mid` | un-spread-adjusted prices (`next_bar.open` at entry, `bar.close` at exit). Together with `spread_at_entry` allows gross-vs-net cost decomposition: `gross = exit_mid − entry_mid` (BUY) or `entry_mid − exit_mid` (SELL); `net = gross − spread_at_entry` |
| `spread_at_entry` | the `spread_pts` value used to adjust the entry fill |
| `resolution` | bar resolution this trade was generated at (stamped by `run.py`, not the engine) |

### 5.6 Session model & time-exit (backtest)

> **Implemented 2026-05-15.** Supersedes the former
> "time-exit not active" behaviour (§9.5).

Live closes intraday because the human passes `session_end_time` to `start_session`;
the monitor's time-exit fires `close_minutes_before_session_end` before it. The
backtest reproduces this with a **single global daily close**:

- `--session-close-utc HH:MM` (default `21:00`) — close time-of-day, UTC, applied to
  every simulated trading day for every instrument.
- For each bar the engine derives that bar's session end = `bar_date @ session_close_utc`
  (UTC) and passes it, plus the bar's UTC timestamp as injected `now`, to
  `evaluate_position()`. The per-strategy YAML `time_exit.close_minutes_before_session_end`
  (30) is unchanged — effective last-hold ≈ 20:30 UTC at the default.
- **No overnight/weekend holds.** Every position is force-flattened by the daily
  time-exit. If a UTC day has no bar in the close window (early close / data gap),
  the engine flattens at that day's last available bar with reason
  `Session close (no bar at threshold)`.
- **No new entries** at/after `session_end − close_minutes_before_session_end` on a
  given day — the deterministic stand-in for "Claude would not open a fresh position
  minutes before the forced flatten". Weekends/holidays have no bars → no entries.

`End of data` remains only for the final partial day of the dataset.

---

## 6. CLI Runner

Source: `src/cfd_trading/backtest/run.py`

### 6.1 Basic usage

```bash
cd ~/dev/trading/cfd-trading
source .venv/bin/activate

# Single instrument, single strategy
BACKTEST_DB_PATH=/mnt/c/Users/chris/dev/trading-data/trading.db \
  python -m cfd_trading.backtest.run --strategy momentum --epic EURUSD

# Full matrix — all strategies × all 11 watchlist instruments
BACKTEST_DB_PATH=/mnt/c/Users/chris/dev/trading-data/trading.db \
  python -m cfd_trading.backtest.run --all-strategies --all-epics

# 15-minute bars — M1 data fetched from DB and aggregated in-process
BACKTEST_DB_PATH=/mnt/c/Users/chris/dev/trading-data/trading.db \
  python -m cfd_trading.backtest.run --all-strategies --all-epics --resolution M15
```

### 6.2 Arguments

| Argument | Description |
|----------|-------------|
| `--epic EPIC` | Run a single instrument (mutually exclusive with `--all-epics`) |
| `--all-epics` | Run all instruments from `config/watchlist.yaml` |
| `--strategy NAME` | Run a single strategy (mutually exclusive with `--all-strategies`) |
| `--all-strategies` | Run all strategies discovered in `config/strategies/` (excludes `_base` and `scan`) |
| `--instruments LIST` | Comma-separated subset of watchlist epics (mutually exclusive with `--epic` and `--all-epics`). Used for audit runs on a trimmed universe. Errors if any name is not in `config/watchlist.yaml`. Example: `EURUSD,GBPUSD,US500,DE40,GOLD`. |
| `--resolution` | Target bar resolution: `M1` `M5` `M15` `M30` `M60`. **Default: each strategy's own `resolution:` from its YAML** (single source of truth, shared with the live monitor — **momentum `M30`**, mean_reversion `M1`, orb `M15`). Pass this only to override for an experiment. The audit re-baseline runs momentum at its YAML M30 and pins MR/ORB to `M15` via this flag. Bars are aggregated from `--source-resolution` if the two differ. |
| `--source-resolution` | Resolution of bars read from the DB (default: `M1`). Set to match `--resolution` to skip aggregation when native bars are stored at the target resolution (e.g. native M15 from MT5). |
| `--output PATH` | Optional. Write all completed trades from the run as a single Parquet file. One row per trade; columns match the `Trade` dataclass plus `resolution`. Requires `pyarrow` (declared in `pyproject.toml` as a runtime dep). Required for the Phase A audit slicing — see `docs/STRATEGY_AUDIT.md` (audit closed 2026-05-18). |
| `--momentum-relaxed` | Audit-mode momentum filter relaxation: ADX threshold 20 (from 25) and EMA gap floor 0.02% (from 0.05%). Used in Phase A2 to lift momentum trade counts to a statistically sliceable density. Strict defaults remain unchanged for live runs. |
| `--session-close-utc HH:MM` | Daily intraday close time (UTC), applied to every simulated trading day for every instrument. Default `21:00`. Drives the mechanical time-exit (`close_minutes_before_session_end` before this). Guarantees no overnight/weekend holds. See §5.6. |

### 6.3 Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKTEST_DB_PATH` | `/mnt/c/Users/chris/dev/trading-data/trading.db` | Path to the SQLite DB with `ohlc_bars` |
| `CONFIG_DIR` | Auto-detected from package root | Path to `config/` directory |

`BACKTEST_MODE=true` is set automatically at startup.

<!-- §6.4–6.9 (all empirical results) removed 2026-05-15 — see top banner -->

---

## 7. Test Suite

All tests use synthetic bar sequences — no real DB file or network access required.

### 7.1 `tests/unit/test_signals.py`

> *(2026-05-15)* Suite total is now **329**. `signals.py` was promoted to
> `strategy/signal_engine.py` (shared with the live monitor); momentum entry
> tests were migrated to the pending-crossover **confirmation-window** semantics
> and a `tests/unit/test_parity.py` (live↔backtest) was added. The row
> descriptions below predate that redesign — the test files are the source of
> truth for exact fixtures/assertions.

Tests for `strategy/signal_engine.py`:

| Test | What it verifies |
|------|-----------------|
| `test_insufficient_bars_returns_none` (both) | Signal returns `None` when bar count is below the minimum (21 for momentum, 19 for mean reversion) |
| `test_exactly_minimum_bars_does_not_raise` | 22 flat bars at minimum bar count — no crash, returns `None` (no crossover) |
| `test_long_signal_on_upward_crossover` | 21 flat bars then spike to 1.10 → EMA_9 crosses above EMA_21 with positive slope → `"LONG"` |
| `test_short_signal_on_downward_crossover` | 21 flat bars then drop to 0.90 → EMA_9 crosses below EMA_21 with negative slope → `"SHORT"` |
| `test_no_signal_when_ema9_already_above_ema21` | Monotonically rising 40-bar sequence — crossover happened before the window → `None` |
| `test_no_signal_when_ema9_already_below_ema21` | Monotonically falling sequence — same logic, `None` |
| `test_long_requires_positive_slope` | Sharp fall then small uptick — upward crossover exists but overall slope is negative → not `"LONG"` |
| `test_gap_filter_suppresses_tiny_crossover` | Spike of 0.1% produces a crossover but EMA gap < 0.15% minimum → `None` |
| `test_gap_filter_allows_large_crossover` | Spike of 10% → EMA gap well above 0.15% minimum → `"LONG"` |
| `test_returns_string_not_bool` | Return type is `str`, not `bool` |
| `test_fires_on_correct_bar_mid_sequence` (state) | `MomentumSignalState` fires at bar 22 (crossover bar), not on subsequent flat bars |
| `test_ema_stays_current_during_position` (state) | EMA continues updating across bars after signal fires |
| `test_new_instance_starts_fresh` (state) | Two instances fed identical bars produce identical results |
| `test_matches_functional_wrapper_on_crossover` (state) | `MomentumSignalState` and `momentum_signal()` agree on last bar |
| `test_no_signal_before_min_bars` (state) | Returns `None` for all bars below warmup threshold |
| `test_no_signal_when_z_within_threshold` | Flat prices → z-score = 0 → `None` |
| `test_short_when_z_exceeds_positive_threshold` | 19 bars at 1.0 + spike to 1.5 → large positive z → `"SHORT"` |
| `test_long_when_z_exceeds_negative_threshold` | 19 bars at 1.0 + drop to 0.5 → large negative z → `"LONG"` |
| `test_no_signal_when_price_within_two_sigma` | Alternating 0.02 oscillation → z near 0 → `None` |
| `test_uses_last_20_bars_for_zscore` | 30 bars at 100.0 (old history), 19 bars at 1.0, spike to 0.5 → z-score based on the last 20 bars only → `"LONG"` (not distorted by old history) |
| `test_fires_on_correct_bar` (state) | `MeanReversionSignalState` fires at the spike bar, not on earlier flat bars |
| `test_matches_functional_wrapper` (state) | `MeanReversionSignalState` and `mean_reversion_signal()` agree on last bar |
| `test_no_signal_before_window_full` (state) | Returns `None` for the first 19 bars |

### 7.2 `tests/unit/test_engine.py` (25 tests)

Tests for `backtest/engine.py`:

**Entry tests:**

| Test | What it verifies |
|------|-----------------|
| `test_momentum_long_opens_trade` | Momentum LONG signal → BUY trade opened with `entry_price = next_bar.open` |
| `test_momentum_short_opens_trade` | Momentum SHORT signal → SELL trade opened |
| `test_mean_reversion_long_opens_trade` | z < −2.0 → BUY trade opened |
| `test_mean_reversion_short_opens_trade` | z > +2.0 → SELL trade opened |
| `test_no_signal_produces_no_trades` | 30 flat bars — no crossover, no z-score extreme → 0 trades |
| `test_stop_and_take_profit_set_correctly` | Entry at 1.10, `default_pct=2.0`, `min_rr_ratio=1.5` → stop ≈ 1.078, TP ≈ 1.133 |
| `test_unknown_strategy_raises` | `ValueError` raised for unregistered strategy name |

**Exit tests:**

| Test | What it verifies |
|------|-----------------|
| `test_hard_stop_closes_trade` | BUY entered at 1.10, price crashes to 0.50 → `exit_reason` contains `"Hard stop"`, `pnl_points < 0` |
| `test_take_profit_closes_trade` | BUY entered at 1.10 with trailing stop disabled, price hits 2.0 (above TP 1.133) → `exit_reason` contains `"Take profit"`, `pnl_points > 0` |
| `test_trailing_stop_ratchets_upward` | BUY entered at 1.10, price rises to 2.0 (stop ratchets to ≈1.99), then crashes to 1.50 → closed by hard stop with `pnl_points > 0` (ratcheted stop is above entry) |
| `test_end_of_data_closes_open_trade` | Signal fires but no more exit-triggering bars → `exit_reason == "End of data"` |

**Metrics tests:**

| Test | What it verifies |
|------|-----------------|
| `test_win_rate_computed_correctly` | Single winning trade → `win_rate == 1.0`, `winning_trades == 1` |
| `test_stop_out_rate_computed_correctly` | Single stop-out → `stop_out_rate == 1.0` |
| `test_profit_factor_with_winning_trade` | All winning trades → `profit_factor == inf` |
| `test_empty_bars_returns_zero_trades` | Empty bar list → all metrics zero, no crash |
| `test_result_fields_populated` | `epic` and `strategy` fields copied correctly to result |
| `test_net_pnl_pts_is_sum_of_trade_pnl` | Single stop-out trade; `net_pnl_pts` equals `trade.pnl_points` and is negative |
| `test_avg_r_computed_correctly` | Stop-out at 0.50 from entry 1.10; asserts `avg_r = net_pnl_pts / (1 × 1.10 × 0.02)` and is negative |
| `test_avg_r_zero_when_no_trades` | Empty bars → `avg_r == 0.0` |
| `test_mean_reversion_midline_exit` | z reverts toward 0; `exit_reason == "Z-score midline"` (the MR signal-exit; no hold-cap involved — that arg no longer exists) |
| `test_spread_adjusts_buy_entry_and_exit` | BUY with `spread_pts=0.10`: `entry_price = open + 0.05`, `exit_price = close - 0.05` |
| `test_spread_adjusts_sell_entry_and_exit` | SELL with `spread_pts=0.10`: `entry_price = open - 0.05`, `exit_price = close + 0.05` |
| `test_hard_stop_takes_priority_over_midline_exit` | Price crashes far beyond stop level → hard stop fires before `check_exit()` is reached |

### 7.3 `tests/unit/test_signals.py` additions

| Test class | Tests added |
|---|---|
| `TestADXGate` | `test_momentum_suppressed_when_adx_below_threshold`, `test_momentum_fires_when_adx_gate_disabled`, `test_momentum_suppressed_by_high_explicit_threshold`, `test_mean_reversion_fires_in_flat_market`, `test_mean_reversion_suppressed_in_trending_market` |
| `TestATRGate` | `test_gate_blocks_when_spread_large_relative_to_atr`, `test_gate_disabled_when_spread_zero`, `test_gate_permissive_while_atr_warming_up` |
| `TestHoldCapRemoved` | `test_max_hold_bars_constructor_arg_removed` (constructor rejects `max_hold_bars`), `test_long_hold_never_returns_hold_cap` (`"Hold cap"` is never returned — exit only via z-midline / price / time rules) |
| `TestMomentumCheckExit` | `test_long_exits_on_downward_cross_back`, `test_short_exits_on_upward_cross_back` (→ `"EMA cross-back"`), `test_momentum_check_exit_none_without_position` |
| `TestM30Gate` | `test_gate_passes_long_when_m30_bullish`, `test_gate_blocks_long_when_m30_bearish`, `test_gate_disabled_when_m30_gate_false`, `test_gate_blocks_short_when_m30_bullish`, `test_gate_permissive_during_warmup` |
| `TestCheckExit` | `test_mean_reversion_check_exit_none_before_window_full`, `test_mean_reversion_check_exit_none_when_z_large`, `test_mean_reversion_check_exit_fires_when_z_small`, `test_momentum_check_exit_always_none` |

### 7.4a `tests/unit/test_spreads.py` (8 tests)

| Test | What it verifies |
|------|-----------------|
| `test_fx_major_returns_one_pip` | EURUSD / GBPUSD / EURGBP → 0.0001 |
| `test_usdjpy_returns_one_pip_in_yen` | USDJPY → 0.01 |
| `test_index_returns_absolute_points` | US500 → 0.5; DE40 / UK100 → 1.0 |
| `test_gold_returns_absolute_usd` | GOLD → 0.35 |
| `test_oil_returns_absolute_usd` | XBRUSD → 0.04 |
| `test_crypto_scales_with_price` | BTCUSD/ETHUSD → 0.07% × price |
| `test_unknown_epic_returns_zero` | Unknown epic → 0.0 |
| `test_spread_positive_for_all_watchlist_epics` | All 11 watchlist instruments return > 0 |

### 7.4b `tests/unit/test_aggregate.py` (15 tests)

| Test class | What it verifies |
|---|---|
| `TestAggregateIdentity` | `period=1` returns input unchanged; empty list returns empty |
| `TestAggregateOHLC` | Single-group OHLC: 15 bars → 1 M15 bar; open=first, high=max, low=min, close=last, volume=sum; resolution label; ts=first bar; epic preserved |
| `TestAggregateMultipleGroups` | 30 bars → 2 groups; correct group boundary timestamps; partial end-group included; M5 label correct |

### 7.4c `tests/unit/test_sessions.py` (8 tests)

Tests for `backtest/sessions.py`:

| Test | What it verifies |
|------|-----------------|
| `test_us500_nyse_open` | US500 → (14, 30) |
| `test_de40_xetra_open` | DE40 → (8, 0) |
| `test_uk100_lse_open` | UK100 → (8, 0) |
| `test_fx_london_open` | EURUSD/GBPUSD/USDJPY/EURGBP → (8, 0) |
| `test_commodities_london_open` | GOLD / XBRUSD → (8, 0) |
| `test_crypto_midnight_utc` | BTCUSD / ETHUSD → (0, 0) |
| `test_unknown_epic_defaults_to_london_open` | Unknown epics → (8, 0) |
| `test_returns_tuple_of_two_ints` | Return type is `tuple[int, int]` |

### 7.4d `tests/unit/test_orb.py` (23 tests)

Tests for `ORBSignalState` in `strategy/signal_engine.py`:

| Test class | What it verifies |
|---|---|
| `TestORBBasic` | No signal on OR bars (both); no signal before session open; within-range bar returns None; LONG/SHORT breakout fires; strict inequality (touching OR high/low is not a breakout) |
| `TestORBMultiBarOR` | OR extends to wider second bar; `or_bars=1` reproduces original single-bar behaviour; no signal during collection even if bar breaks first bar's range; `or_bars=3` blocks entry for first 2 bars after open |
| `TestORBSessionReset` | One signal per session (second breakout in same session suppressed); reset on new session open; no crossover from previous session's OR; first signal wins (LONG, then SHORT-side move ignored) |
| `TestORBEntryLevels` | LONG stop at OR low; SHORT stop at OR high; TP = entry ± OR_width × rr_ratio; levels reflect extended OR when second collection bar widened the range |
| `TestORBInterface` | `check_exit()` always None; `notify_entry/exit` are no-ops; custom session time (14:30 UTC) with 2-bar OR fires at correct bar |

### 7.4e `tests/unit/test_run.py` (13 tests)

Tests for `backtest/run.py`:

| Test | What it verifies |
|------|-----------------|
| `test_resolve_strategies_single` | `--strategy momentum` → `["momentum"]` |
| `test_resolve_strategies_all` | `--all-strategies` with a mock config dir containing momentum + mean_reversion (plus `_base` and `scan` which are excluded) → `{"momentum", "mean_reversion"}` |
| `test_resolve_epics_single` | `--epic EURUSD` → `["EURUSD"]` |
| `test_resolve_epics_all` | `--all-epics` reads `watchlist.yaml` and flattens all groups into a single list |
| `test_load_risk` | `_load_risk()` parses `risk.yaml` correctly |
| `test_print_table_no_crash` | Table output contains epic name, strategy name, formatted win rate (`60.0%`), formatted PF (`1.80`), and AvgR with sign (`+0.16R`) |
| `test_print_table_avg_r_negative` | Negative `avg_r` renders as `-0.04R` |
| `test_print_table_inf_profit_factor` | `profit_factor = inf` renders as `"inf"` without crashing |
| `test_print_table_zero_trades` | Zero-trade result renders without division errors |
| `test_main_single_epic_strategy` | Full `main()` with a real in-memory SQLite DB and minimal config dir — no exceptions, table printed |
| `test_main_missing_db_exits` | DB file absent → `sys.exit(1)`, error message on stderr contains `"not found"` |
| `test_main_no_bars_skips_gracefully` | DB exists but `ohlc_bars` is empty → skip message printed, no crash, no results printed |

### 7.5 Running the tests

```bash
cd ~/dev/trading/cfd-trading
source .venv/bin/activate

# Backtest tests only
pytest tests/unit/test_signals.py tests/unit/test_engine.py tests/unit/test_run.py -v

# Full unit suite (329 tests)
pytest tests/unit/ -v
```

All 329 unit tests pass with no network access or real DB file.

---

## 8. Reading the Results

### 8.1 Column definitions

| Column | Meaning | Interpretation |
|--------|---------|---------------|
| `Trades` | Total completed trades | Low count (< 20) means limited statistical confidence in other metrics |
| `Win%` | % of trades that closed in profit | Needs to be read alongside PF — a 40% win rate with PF 2.0 can still be profitable |
| `PF` | Profit factor = gross profit / gross loss | < 1.0: strategy loses money overall; 1.0–1.2: marginal; > 1.3: meaningful edge; `inf`: no losing trades (common on small samples) |
| `MaxDD%` | Largest peak-to-trough equity drop as % of average entry price | High MaxDD% relative to PF indicates the strategy earns slowly and loses fast — unfavourable |
| `Stop%` | % of trades closed by hard stop | Very high Stop% (> 50%) suggests signal is firing into adverse conditions or stop distance is too tight |
| `Sig/wk` | Average entry signals per week | < 1: strategy is too selective for the instrument; > 10: signals may be noise |
| `AvgR` | Expectancy per trade in R-multiples: `(net_pnl / n) ÷ (entry × stop%)` | Positive = profitable expectancy; 0.0 = breakeven; negative = losing strategy. Comparable across all instruments. See §8.6 for interpretation. |

**Directional split table** (second table in the output):

| Column | Meaning | Interpretation |
|--------|---------|---------------|
| `L-Trades` | Completed LONG/BUY trades | Compare to `S-Trades` — large imbalance suggests the signal has a directional bias |
| `L-Win%` | Win rate for LONG trades only | Isolated from SHORT performance |
| `L-PF` | Profit factor for LONG trades only | Primary indicator of directional edge: `—` means no trades in that direction |
| `S-Trades` | Completed SHORT/SELL trades | |
| `S-Win%` | Win rate for SHORT trades only | |
| `S-PF` | Profit factor for SHORT trades only | When L-PF and S-PF both > 1.2, the strategy has two-way edge regardless of market regime |

**Interpreting the directional split:** If combined PF > 1.0 but one directional PF < 1.0, the aggregate edge comes entirely from one direction and is likely regime-driven. A strategy with genuine structural edge should show PF > 1.0 in both directions across a representative sample period.

### 8.2 Calculating net win/loss

The underlying P&L field `net_pnl_pts` (accessible via `BacktestResult.net_pnl_pts`) is `sum(exit_price − entry_price)` for BUY trades and `sum(entry_price − exit_price)` for SELL trades, in **raw price units**. The displayed `AvgR` column normalises this by dividing by `(n × avg_entry × stop_pct)`, making it comparable across instruments.

**Converting raw points to currency** (if you need the actual monetary P&L):

```
currency_pnl = net_pnl_pts × contract_size × position_size_lots
```

Where `contract_size` depends on the instrument:

| Instrument class | Typical contract size | Example |
|-----------------|----------------------|---------|
| FX (EURUSD, GBPUSD) | 100,000 base units per lot | `net_pnl_pts=+0.018 × 100,000 × 0.1 lot = +$180` |
| FX (USDJPY) | 100,000 USD per lot | 1 pip = 0.01; `net_pnl_pts=+1.5 × 100,000 × 0.1 = +$15,000` (note: USDJPY in pips ÷ 100) |
| Indices (US500, DE40, UK100) | $1–$10 per point per lot | Varies by broker/product |
| GOLD (XAUUSD) | 100 oz per lot | `net_pnl_pts=+34.21 × 100 × 0.1 = +$342.10` |
| XBRUSD (Brent oil) | 1,000 bbls per lot | `net_pnl_pts=+2.1 × 1,000 × 0.1 = +$210` |
| Crypto (BTCUSD, ETHUSD) | 1 coin per lot | `net_pnl_pts=+500 × 1 × 0.1 = +$50` |

The backtest uses no position sizing (`target_risk_pct` is not applied), so `net_pnl_pts` represents a 1-lot, 1-unit position throughout. Multiply by your intended size to estimate actual P&L.

**Relating net_pnl_pts to PF:**

```
net_pnl_pts = gross_profit − gross_loss
            = gross_loss × (PF − 1)
```

A PF of 1.3 means for every 1 point lost, 1.3 points are won — net 0.3 points per unit of risk. `AvgR` expresses this per trade and per stop-distance, so it is comparable across all instruments without unit conversion.

**Break-even win rate:**

Given your R:R ratio (from `min_rr_ratio` in the YAML), the minimum win rate needed to break even is:

```
break_even_win_rate = 1 / (1 + min_rr_ratio)
```

| `min_rr_ratio` | Break-even Win% |
|---------------|----------------|
| 1.5 (momentum) | 40.0% |
| 2.0 (mean reversion) | 33.3% |

Any Win% above these thresholds with consistent trade count is generating positive expectancy.

### 8.3 What good results look like

**Minimum bar for confidence:** at least 30 completed trades. Below 20 trades, treat metrics as indicative only.

**Healthy momentum run:**
```
Win% 48–60%  |  PF 1.3–2.0  |  MaxDD% < 6%  |  Stop% 20–40%  |  Sig/wk 1–5  |  AvgR > +0.05R
```

**Healthy mean reversion run:**
```
Win% 55–70%  |  PF 1.5–2.5  |  MaxDD% < 4%  |  Stop% 10–25%  |  Sig/wk 1–4  |  AvgR > +0.05R
```

### 8.4 Warning signs

| Pattern | Likely cause | Action |
|---------|-------------|--------|
| PF < 1.0 across multiple instruments | Strategy has no edge on this data | Re-examine signal logic or instrument suitability |
| AvgR negative despite Win% > 50% | Wins are small, losses are large (inverted R:R) | Check if stop is wider than TP in practice — can happen with trailing stop ratcheting |
| Stop% > 60% | Stop too tight OR signal fires against the trend | Widen `default_pct` or strengthen signal filter |
| Sig/wk > 15 | Signal threshold too loose | Tighten z-score threshold (mean reversion) or increase `_MIN_EMA_GAP_PCT` in `strategy/signal_engine.py` (momentum, default 0.05%, research-validated floor — see `RESEARCH.md`) |
| Sig/wk = 0 | Instrument never triggers the signal | Instrument may be unsuitable for this strategy style |
| MaxDD% > 15% with PF near 1.0 | Strategy earns slowly and has catastrophic drawdowns | This risk profile is not suitable for live deployment |
| `inf` PF on < 15 trades | Sample too small to trust | Run on more data or wait for incremental DB updates |
| `0` trades on an instrument | No bars in DB for this epic | Run `fetch_ohlc.py` on Windows to populate |
| AvgR positive but PF near 1.0 (e.g. 1.05) | P&L dominated by a few big winners, not consistent edge | Check individual trades; strategy may be high-variance / lucky |

<!-- §8.5 (instrument result table) removed 2026-05-15 — see top banner -->

### 8.6 Reading AvgR

**AvgR** is the average P&L per trade expressed as a multiple of the risk taken (1R = one stop distance). It is instrument-price-agnostic: +0.16R on GOLD and +0.16R on EURUSD represent identical proportional outcomes despite the raw price difference being enormous.

**Formula:**
```
R_per_trade = avg_entry_price × stop_pct
AvgR        = net_pnl_pts / (total_trades × R_per_trade)
```

**Practical interpretation** with a $100 account and 10% stop loss (risk per trade = $10):

| AvgR | Meaning per trade | 50-trade expectancy |
|------|-------------------|---------------------|
| +0.16R | +$1.60 avg profit | +$80 |
| +0.02R | +$0.20 avg profit | +$10 |
| 0.00R | breakeven | $0 |
| -0.10R | -$1.00 avg loss | -$50 |

**Rules of thumb:**
- AvgR > +0.10R with 30+ trades: strong edge, candidate for live deployment
- +0.02R to +0.10R: marginal positive — investigate if spread costs would erase it
- AvgR ≤ 0.00R: no edge; do not deploy regardless of trade count or PF

The `MaxDD%` column uses the same normalisation (peak-to-trough P&L as % of avg entry), which is why MaxDD% is already comparable across instruments.

### 8.7 Acting on results

The backtest output is a **filter before live tuning**, not a tuning target. Use it to:

1. **Discard clearly unprofitable combinations** — AvgR ≤ 0.00R with adequate trade count is a hard pass.
2. **Identify the best 2–3 instrument/strategy pairs** — highest AvgR with trade count ≥ 30 and PF > 1.1.
3. **Detect stop size mismatches** — high Stop% + low PF + negative AvgR → `default_pct` is too tight; consider increasing by 0.5%.
4. **Validate signal frequency** — if `Sig/wk < 1`, the instrument is unlikely to generate live entry opportunities during normal Claude Code sessions.
5. **Cross-check AvgR with PF and trade count** — a PF of 1.4 with only 10 trades is a lucky sample. Require AvgR > +0.05R, PF > 1.3, and Trades ≥ 30 before trusting the result.

Do not curve-fit the YAML parameters to maximise backtest PF — the dataset is only 3 months of one market regime.

---

## 9. Limitations and Approximations

### 9.1 Entry logic approximation

The live system uses Claude's full reasoning over `analyze_instrument` output (EMA, z-score, ATR, sentiment, spread). The backtest uses only the EMA crossover and z-score threshold. The following live signals are **not replicated**:

- ATR expansion/contraction filter
- Spread/ATR ratio filter (execution cost check)
- Client sentiment (contrarian signal)
- Multi-timeframe context
- Prior support/resistance levels

This means backtest win rates and signal frequency will differ from live performance. The backtest is a lower bound on signal quality — Claude's additional filters should improve the live hit rate.

### 9.2 Fill price assumption

Entry is simulated at `next_bar.open ± spread_pts/2` (BUY at ask, SELL at bid); exit is simulated at `bar.close ∓ spread_pts/2`. Spread is therefore deducted from P&L via the fill prices — `Trade.pnl_points` is **net of spread**. Gross-vs-net decomposition is available via `entry_mid`, `exit_mid`, and `spread_at_entry` on each `Trade` record (added 2026-05-14 for the Phase A audit).

What is **not** modelled:
- Slippage beyond the half-spread fill assumption (live MARKET orders may slip further during news / thin liquidity)
- Time-varying spread (`backtest/spreads.py` returns a single static value per instrument; A4 news-proximity / spread-widening was carried into the next-phase debate — see `docs/STRATEGY_AUDIT.md`)

### 9.3 No parallel positions

The engine holds at most one position at a time. The live system allows up to `max_open_positions` (currently 3). Multi-position interactions (margin usage, correlated drawdowns) are not tested.

### 9.4 No position sizing

P&L is in points, not currency. The backtest does not apply `target_risk_pct` or the vol-scaled sizing from `analyze_instrument`. All trades contribute equally to `profit_factor` and `max_drawdown_pct` regardless of the intended position size.

### 9.5 Time exit — backtest session model

**Resolved & implemented 2026-05-15.** Previously the engine
passed `session_end_time=None`, so time-exit never fired and momentum/ORB held
multi-day/multi-week positions — the defect that invalidated all prior results. The
engine now supplies a per-day `session_end_time` from `--session-close-utc` (default
21:00 UTC) and injects bar-time as `now`. Remaining approximation: a *single global*
close time, not per-instrument exchange hours (a documented future refinement).

### 9.6 Data coverage gaps

Capital.com 1-min data has gaps for weekends and outside normal session hours. These gaps appear as missing bars — the engine skips over them naturally (timestamps are not contiguous). Signal frequency (`Sig/wk`) is computed from the wall-clock span of the data, not bar count, so it accounts for coverage gaps correctly.

### 9.7 Three-month regime risk

All data covers January–May 2026. Results from a single market regime (rising equities, moderate FX volatility) may not generalise. A strategy that performs well in this period may underperform in a high-volatility or ranging macro environment.
