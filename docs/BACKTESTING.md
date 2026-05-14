# Backtesting Guide

A complete reference for running, understanding, and extending the backtesting framework in this repo.

---

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
    ├── backtest/signals.py  momentum_signal() / mean_reversion_signal()
    │     └── pure functions — no I/O
    ├── backtest/engine.py  run_backtest()
    │     ├── calls signal function per bar
    │     └── calls monitor/monitor.py evaluate_position() per bar
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

Source: `src/cfd_trading/backtest/signals.py`

Both signal functions take a **list of `OHLCBar` objects in chronological order** (latest bar last) and return `"LONG"`, `"SHORT"`, or `None`.

### 4.1 Momentum signal

**Approximates:** EMA_9 crosses above/below EMA_21 with trend slope confirmation, ADX regime gate, and M30 directional bias gate.

**Minimum bars:** 22 (EMA_21 seeds at bar 21; crossover needs one prior bar). With ADX(14) enabled the effective warm-up is ~28 bars before the gate can actively suppress non-trending signals.

**Logic:**

```
1. Compute EMA_9 and EMA_21 incrementally (O(1) Wilder-style)
2. Compute ADX(14) incrementally
3. Append bar to rolling 30-bar M30 buffer
4. Suppress if ADX is valid AND ADX < adx_threshold (non-trending market)
5. Suppress if EMA gap < min_ema_gap_pct (noise crossover)
6. Compute linear trend slope over the last 22 bars
7. LONG  if EMA_9 crossed above EMA_21 AND slope > 0
       AND (M30 buffer not full OR M30 slope > 0)
8. SHORT if EMA_9 crossed below EMA_21 AND slope < 0
       AND (M30 buffer not full OR M30 slope < 0)
9. None otherwise
```

**Key detail — ADX regime gate:** Signal is suppressed when ADX(14) < 25 (default threshold). On M1 bars this detects whether the last 14 minutes are directionally trending. Passes unconditionally while ADX is warming up (first ~28 bars) to avoid missing early-session signals. Configurable via `signal_kwargs={"adx_threshold": value}` — set to `0.0` to disable entirely.

**Key detail — slope filter:** A crossover that contradicts the overall trend slope is suppressed. Prevents late-entry signals at trend exhaustion.

**Key detail — EMA gap filter:** Suppresses near-identical EMA crossovers below `_MIN_EMA_GAP_PCT` (default **0.05%** — research-validated floor; 4 pts at 8,000 = 4× a 1-pt spread). Tunable range: 0.01–0.10%. See `backtest/tune_momentum_gap.py`.

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

**`check_exit()`:** Always returns `None` — momentum exits are handled entirely by `evaluate_position()` (trailing stop, take profit, hard stop).

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

**`check_exit()` priority:**
1. **Hold cap** — returns `"Hold cap"` after `max_hold_bars` bars in trade (default **5**). If the position is not moving toward target within 5 bars, it is likely caught in a trend — exit flat.
2. **Z-score midline** — returns `"Z-score midline"` when `abs(last_z) <= zscore_exit_threshold` (default **0.5**). Fires when the expected reversion has materialised.

Hard stop and take profit in `evaluate_position()` take priority over both (checked before `check_exit()`). The engine calls `notify_entry()` / `notify_exit()` to synchronise the hold-cap bar counter with actual position state.

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
| 2 | Trailing stop ratchet | BUY: candidate_stop > current_stop / SELL: candidate_stop < current_stop | ADJUST |
| 3 | Take profit | BUY: close ≥ profitLevel / SELL: close ≤ profitLevel | CLOSE |
| 4 | Time exit | session_end_time set and within close window | CLOSE |
| 5 | Default | None of the above | HOLD |

For the **trailing stop**, the candidate stop is:

```
BUY:  candidate = close × (1 - min_distance_pct/100)
SELL: candidate = close × (1 + min_distance_pct/100)
```

The stop only ratchets in the profitable direction. Once raised (BUY) or lowered (SELL), it never reverses.

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
| `--resolution` | Target bar resolution strategies execute against: `M1` `M5` `M15` `M30` `M60` (default: `M1`). Bars are aggregated to this from `--source-resolution` if the two differ. |
| `--source-resolution` | Resolution of bars read from the DB (default: `M1`). Set to match `--resolution` to skip aggregation when native bars are stored at the target resolution (e.g. native M15 from MT5). |
| `--output PATH` | Optional. Write all completed trades from the run as a single Parquet file. One row per trade; columns match the `Trade` dataclass plus `resolution`. Requires `pyarrow` (declared in `pyproject.toml` as a runtime dep). Required for Phase A audit slicing — see `AUDIT_PLAN.md` and `/audit/A1_inventory.md`. |
| `--momentum-relaxed` | Audit-mode momentum filter relaxation: ADX threshold 20 (from 25) and EMA gap floor 0.02% (from 0.05%). Used in Phase A2 to lift momentum trade counts to a statistically sliceable density. Strict defaults remain unchanged for live runs. |

### 6.3 Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKTEST_DB_PATH` | `/mnt/c/Users/chris/dev/trading-data/trading.db` | Path to the SQLite DB with `ohlc_bars` |
| `CONFIG_DIR` | Auto-detected from package root | Path to `config/` directory |

`BACKTEST_MODE=true` is set automatically at startup.

### 6.4 Historical baseline results (Jan–May 2026, M1, gap=0.02% — pre-filter-update)

> **Note:** These results were captured with `_MIN_EMA_GAP_PCT = 0.02%` and no spread costs. Kept for comparison only. Current defaults are gap=0.05%, spread costs in, hold cap 5 bars.

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   AvgR
--------  --------------  ------  ------  ------  -------  ------  -------  -------
EURUSD    mean_reversion  3       33.3%   0.76    3.029    66.7%   0.21     -0.17R
GBPUSD    mean_reversion  2       0.0%    0.00    2.302    50.0%   0.14     -0.80R
USDJPY    mean_reversion  4       50.0%   1.45    1.511    50.0%   0.29     +0.24R
EURGBP    mean_reversion  1       0.0%    0.00    0.186    0.0%    0.07     -0.13R
US500     mean_reversion  16      18.8%   0.50    16.708   75.0%   1.11     -0.56R
DE40      mean_reversion  25      44.0%   1.48    5.252    56.0%   1.73     +0.37R
UK100     mean_reversion  17      29.4%   0.69    10.751   70.6%   1.17     -0.29R
GOLD      mean_reversion  92      31.5%   0.83    27.635   68.5%   6.21     -0.31R
XBRUSD    mean_reversion  226     41.6%   1.15    43.015   58.4%   14.54    +0.14R
BTCUSD    mean_reversion  74      35.1%   1.02    12.649   64.9%   7.33     +0.02R
ETHUSD    mean_reversion  116     34.5%   0.99    20.919   65.5%   11.49    -0.00R
EURUSD    momentum        4       25.0%   1.32    1.145    100.0%  0.29     +0.05R
GBPUSD    momentum        4       25.0%   0.18    0.798    100.0%  0.29     -0.10R
USDJPY    momentum        5       20.0%   0.01    1.912    100.0%  0.36     -0.20R
EURGBP    momentum        1       0.0%    0.00    0.117    0.0%    0.07     -0.06R
US500     momentum        13      30.8%   0.35    4.686    100.0%  0.90     -0.17R
DE40      momentum        22      22.7%   0.46    4.616    100.0%  1.52     -0.11R
UK100     momentum        15      53.3%   3.04    1.896    100.0%  1.04     +0.26R
GOLD      momentum        71      39.4%   1.52    4.539    95.8%   4.79     +0.16R
XBRUSD    momentum        201     37.3%   0.97    16.668   98.0%   12.93    -0.01R
BTCUSD    momentum        76      34.2%   0.60    8.049    100.0%  7.53     -0.05R
ETHUSD    momentum        116     35.3%   0.96    8.130    100.0%  11.49    -0.00R
```

### 6.5 M15 results (Jan–May 2026, M15 aggregated from M1, gap=0.05%, spread costs in)

Run with: `python -m cfd_trading.backtest.run --all-strategies --all-epics --resolution M15`

Run time: **~3 seconds** (73k M15 bars vs 1.1M M1 bars).

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   AvgR
--------  --------------  ------  ------  ------  -------  ------  -------  -------
EURUSD    mean_reversion  198     46.0%   0.84    1.828    0.0%    14.15    -0.00R
GBPUSD    mean_reversion  219     48.4%   0.73    2.597    0.0%    15.66    -0.01R
USDJPY    mean_reversion  221     47.5%   0.83    2.569    0.0%    15.8     -0.00R
EURGBP    mean_reversion  37      51.4%   1.05    0.317    0.0%    2.64     +0.00R
US500     mean_reversion  209     47.8%   0.89    3.395    0.0%    14.47    -0.01R
DE40      mean_reversion  194     55.7%   1.10    6.648    0.0%    13.42    +0.01R
UK100     mean_reversion  205     51.7%   0.98    3.927    0.0%    14.17    -0.00R
GOLD      mean_reversion  218     50.9%   0.73    14.337   2.8%    14.72    -0.04R
XBRUSD    mean_reversion  188     48.9%   1.07    10.946   6.9%    12.1     +0.02R
BTCUSD    mean_reversion  119     49.6%   0.80    8.446    4.2%    11.79    -0.04R
ETHUSD    mean_reversion  155     60.0%   1.07    4.927    6.5%    15.36    +0.01R
EURUSD    momentum        0       0.0%    0.00    0.0      0.0%    0.0      +0.00R
GBPUSD    momentum        0       0.0%    0.00    0.0      0.0%    0.0      +0.00R
USDJPY    momentum        0       0.0%    0.00    0.0      0.0%    0.0      +0.00R
EURGBP    momentum        0       0.0%    0.00    0.0      0.0%    0.0      +0.00R
US500     momentum        1       0.0%    0.00    0.539    100.0%  0.07     -0.27R
DE40      momentum        3       0.0%    0.00    1.707    100.0%  0.21     -0.28R
UK100     momentum        1       0.0%    0.00    0.456    100.0%  0.07     -0.23R
GOLD      momentum        6       16.7%   0.01    3.241    100.0%  0.41     -0.27R
XBRUSD    momentum        9       44.4%   0.63    3.791    100.0%  0.58     -0.10R
BTCUSD    momentum        6       33.3%   0.24    1.604    100.0%  0.59     -0.13R
ETHUSD    momentum        5       40.0%   0.18    1.998    100.0%  0.5      -0.20R
```

**Reading the M15 results:**

*Mean reversion — improved signal quality but still marginal:*
- **DE40**: PF 1.10, 194 trades — larger sample than M1, but edge has compressed. Win rate 55.7% is the highest across all instruments.
- **EURGBP**: PF 1.05, 37 trades — barely positive; only 2.6 sig/wk (gap‐bounded FX pair).
- **ETHUSD**: PF 1.07, 155 trades — borderline positive; but 6.5% stop rate and high BTCUSD/ETHUSD spreads suggest costs eating the edge.
- **XBRUSD**: PF 1.07, 188 trades — marginal improvement over M1.
- All FX pairs (EURUSD, GBPUSD, USDJPY) and GOLD are negative PF — mean reversion does not suit these.
- **Stop% ≈ 0%** across almost all instruments: positions are NOT stopping out — they hold-cap or midline-exit. The 5-bar hold cap at M15 = 75-minute maximum hold, which is reasonable but may still be too short for full reversion.

*Momentum — structurally broken at M15 with EMA9/21:*
- Near-zero signals across all instruments. EMA9/21 crossover requires ~22 bars of warm-up; at M15, 22 bars = 5.5 hours — essentially the full intraday session. An EMA crossover at M15 scale is too slow to be a useful intraday signal.
- The 0.05% gap filter (designed for M1 where it means "4 pts at 8,000") is proportionally smaller at M15 (where 15-min ATR is ~15× larger), so the filter is not the problem. The EMA periods themselves are wrong for M15.

*Conclusion — neither strategy is profitable at M15 as currently parameterised. The resolution change alone is not sufficient.*

### 6.6 ORB v1 results (Jan–May 2026, M15, 1-bar OR, fixed stop=0.5%)

Initial ORB run: single 15-min opening bar defines the range; fixed 0.5% stop; 2:1 R:R.

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   AvgR
--------  --------------  ------  ------  ------  -------  ------  -------  -------
EURUSD    orb             51      35.3%   1.09    3.016    90.2%   3.65     +0.03R
GBPUSD    orb             57      31.6%   0.55    5.14     96.5%   4.08     -0.14R
USDJPY    orb             55      29.1%   1.16    2.269    89.1%   3.93     +0.05R
EURGBP    orb             24      29.2%   0.58    1.789    95.8%   1.71     -0.13R
US500     orb             67      40.3%   0.90    6.057    89.6%   4.64     -0.04R
DE40      orb             71      32.4%   0.82    5.922    88.7%   4.91     -0.08R
UK100     orb             70      38.6%   0.94    5.193    92.9%   4.84     -0.02R
GOLD      orb             73      28.8%   0.58    7.947    95.9%   4.93     -0.21R
XBRUSD    orb             75      26.7%   0.49    19.554   90.7%   4.83     -0.45R
BTCUSD    orb             71      25.4%   0.34    17.419   97.2%   7.03     -0.44R
ETHUSD    orb             71      29.6%   0.59    12.499   91.5%   7.03     -0.25R
```

Key finding: stop rate 88–97% — false breakouts dominate. Single-bar OR is noisy; fixed stop not aligned with natural invalidation level.

### 6.7 ORB v2 results (Jan–May 2026, M15, 2-bar OR, OR-width-based stop, 2:1 R:R)

Improvements: (a) OR defined over first 2 M15 bars (30 min); (b) stop placed at OR low/high (natural invalidation); (c) TP = entry ± OR_width × 2.

Run with: `python -m cfd_trading.backtest.run --strategy orb --all-epics --resolution M15`

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   AvgR
--------  --------------  ------  ------  ------  -------  ------  -------  -------
EURUSD    orb             68      30.9%   0.80    1.528    69.1%   4.86     -0.33R
GBPUSD    orb             69      26.1%   0.54    4.001    73.9%   4.93     -0.34R
USDJPY    orb             68      36.8%   1.27    1.869    63.2%   4.86     +0.10R
EURGBP    orb             62      27.4%   0.44    3.023    74.2%   4.42     -0.37R
US500     orb             67      40.3%   1.03    4.167    88.1%   4.64     -0.12R
DE40      orb             71      39.4%   1.62    2.326    85.9%   4.91     +0.11R
UK100     orb             69      42.0%   1.19    4.421    84.1%   4.77     +0.09R
GOLD      orb             72      31.9%   0.83    4.006    91.7%   4.86     -0.34R
XBRUSD    orb             74      32.4%   1.06    9.189    94.6%   4.76     -0.31R
BTCUSD    orb             71      31.0%   0.52    12.934   90.1%   7.03     -0.36R
ETHUSD    orb             71      28.2%   0.54    14.778   90.1%   7.03     -0.38R
```

**Reading the ORB v2 results:**

- **Stop rate reduced significantly** — 63–94% vs 88–97% previously. The 30-min OR gives price more room to form a genuine range, reducing immediate false-breakout reversals.
- **Equity indices and USDJPY now profitable** — DE40 PF 1.62 (+0.11R), UK100 PF 1.19 (+0.09R), USDJPY PF 1.27 (+0.10R). US500 borderline (PF 1.03). These four instruments show a genuine ORB edge.
- **AvgR is negative despite positive PF for some instruments** — OR-width-based stops mean the risk amount varies per trade. Spread costs are large relative to OR width on thin false-breakout stops, compressing the per-trade R. The PF calculation is a more reliable signal than AvgR here.
- **FX pairs (except USDJPY), crypto, and commodities remain unprofitable** — EURUSD/GBPUSD/EURGBP PF 0.44–0.80; BTCUSD PF 0.52. Wide spreads relative to typical OR width erode edge on instruments where the ORB structural advantage is weaker.
- **XBRUSD borderline** — PF 1.06 but MaxDD 9.2%; not reliable.

### 6.8 ORB v3 results (Jan–May 2026, M15, 2-bar OR, OR-width stop, ATR-trailing exit, DE40/UK100/USDJPY only — superseded)

Improvements vs v2: (a) instrument universe restricted to confirmed-edge instruments; (b) fixed 2×OR-width TP replaced with ATR-trailing stop at 1.5×ATR(14) from bar high/low peak; TP set to 99× (effectively disabled).

Run with: `python -m cfd_trading.backtest.run --strategy orb --all-epics --resolution M15`

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   AvgR
--------  --------------  ------  ------  ------  -------  ------  -------  -------
USDJPY    orb             70      31.4%   1.22    1.052    98.6%   5.0      -0.08R
DE40      orb             71      47.9%   1.46    1.791    98.6%   4.91     +0.16R
UK100     orb             70      35.7%   1.10    3.267    100.0%  4.84     +0.06R
```

**Reading the ORB v3 results:**

- **Stop% 98–100%** — all exits are via hard stop, including ATR-ratcheted stops. With TP disabled, there are no take-profit exits by design; stop% is expected to be near 100%.
- **DE40 win rate 47.9%** — improved significantly from v2 (39.4%). ATR trailing locks in gains before reversal, converting formerly-stopped trades into winners. However PF dropped (1.62 → 1.46) because each win is smaller (trade exits at ATR trail rather than at the full 2×OR-width TP target).
- **UK100 and USDJPY slightly weaker** — PF 1.10 and 1.22 vs v2 (1.19 and 1.27). ATR trailing at 1.5×ATR is cutting winning trades too early on these instruments — the fixed 2×OR-width TP in v2 was a better target.
- **Net verdict: v2 fixed-TP is still the best configuration.** The ATR multiplier of 1.5 is too tight. The fixed 2×OR-width TP works because the OR width is already an ATR proxy for the session open volatility.

**Diagnostic — why ATR-trailing underperforms fixed TP here:**
The OR width (defined over 30 min) already encodes the session opening volatility — it is effectively a session-calibrated ATR. Using the OR width as the TP distance is therefore a natural ATR-based target. The per-bar M15 ATR(14) is more variable and at 1.5× is tighter than the 2×OR-width target on most sessions, causing premature exits on what would have been full TP hits. A larger ATR multiplier (e.g. 3.0×) or a combined approach (ATR trail with OR-width TP as a floor) may recover the v2 edge while adding upside capture on large session moves.

---

### 6.9 ORB v2 directional split — full universe (Jan–May 2026, M15)

Same ORB v2 configuration as §6.7 (2-bar OR, OR-width stop, 2:1 R:R, all instruments). The backtest output now includes a second LONG vs SHORT breakdown table, exposing regime bias in the aggregate PF figures.

Run with: `python -m cfd_trading.backtest.run --strategy orb --all-epics --resolution M15`

**Main table (combined LONG + SHORT):**

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   AvgR
--------  --------------  ------  ------  ------  -------  ------  -------  -------
EURUSD    orb             68      30.9%   0.80    1.528    69.1%   4.86     -0.33R
GBPUSD    orb             69      26.1%   0.54    4.001    73.9%   4.93     -0.34R
USDJPY    orb             68      36.8%   1.27    1.869    63.2%   4.86     +0.10R
EURGBP    orb             62      27.4%   0.44    3.023    74.2%   4.42     -0.37R
US500     orb             67      40.3%   1.03    4.167    88.1%   4.64     -0.12R
DE40      orb             71      39.4%   1.62    2.326    85.9%   4.91     +0.11R
UK100     orb             69      42.0%   1.19    4.421    84.1%   4.77     +0.09R
GOLD      orb             72      31.9%   0.83    4.006    91.7%   4.86     -0.34R
XBRUSD    orb             74      32.4%   1.06    9.189    94.6%   4.76     -0.31R
BTCUSD    orb             71      31.0%   0.52    12.934   90.1%   7.03     -0.36R
ETHUSD    orb             71      28.2%   0.54    14.778   90.1%   7.03     -0.38R
```

**Directional split:**

```
Epic      Strategy        L-Trades  L-Win%   L-PF    S-Trades  S-Win%   S-PF
--------  --------------  --------  -------  ------  --------  -------  ------
EURUSD    orb             32        31.2%    1.21    36        30.6%    0.55
GBPUSD    orb             35        31.4%    0.68    34        20.6%    0.43
USDJPY    orb             38        44.7%    1.33    30        26.7%    1.22
EURGBP    orb             28        17.9%    0.22    34        35.3%    0.69
US500     orb             42        38.1%    0.98    25        44.0%    1.12
DE40      orb             37        40.5%    2.31    34        38.2%    1.02
UK100     orb             34        41.2%    1.77    35        42.9%    0.83
GOLD      orb             36        30.6%    0.48    36        33.3%    1.30
XBRUSD    orb             41        26.8%    0.59    33        39.4%    1.69
BTCUSD    orb             39        30.8%    0.64    32        31.2%    0.39
ETHUSD    orb             36        27.8%    0.64    35        28.6%    0.43
```

**Instrument-by-instrument reading:**

| Instrument | Combined PF | L-PF | S-PF | Verdict |
|------------|-------------|------|------|---------|
| USDJPY | 1.27 | 1.33 | 1.22 | **Genuine two-way edge.** Both directions PF > 1.2. Jan-May 2026 yen volatility created session-range breakouts regardless of direction. |
| DE40 | 1.62 | 2.31 | 1.02 | **LONG-regime driven.** Jan-May 2026 was broadly bullish German equities. LONG ORB trades ride intraday momentum; SHORT ORB trades fight the trend. S-PF barely > 1.0. |
| UK100 | 1.19 | 1.77 | 0.83 | **LONG-regime driven.** Same equity bull regime. S-PF < 1.0 — the strategy is profitable overall only because LONG trades dominate. |
| US500 | 1.03 | 0.98 | 1.12 | **Slight SHORT bias** (inverse of DE40/UK100). Interesting divergence — US500 ORB SHORT was more productive. Neither direction strong enough to trade. |
| XBRUSD | 1.06 | 0.59 | 1.69 | **Strong SHORT bias.** Brent crude declined Jan-May 2026 — LONG ORB trades failed repeatedly; SHORT ORB trades caught the trend. Regime artifact. |
| GOLD | 0.83 | 0.48 | 1.30 | **SHORT bias.** Gold trended lower in this window. Even the better direction (S-PF 1.30) is driven by regime. |
| EURUSD | 0.80 | 1.21 | 0.55 | **LONG-only marginal edge.** Combined < 1.0; SHORT consistently unprofitable. Insufficient to trade. |
| BTCUSD | 0.52 | 0.64 | 0.39 | No usable edge either direction. |
| ETHUSD | 0.54 | 0.64 | 0.43 | No usable edge either direction. |
| GBPUSD | 0.54 | 0.68 | 0.43 | No usable edge either direction. |
| EURGBP | 0.44 | 0.22 | 0.69 | No usable edge either direction. |

**Key takeaway — regime bias vs structural edge:**

The directional split confirms that most ORB positive PF in this 14-week sample is regime-driven, not structural:

- **USDJPY** is the only instrument with genuine two-way ORB edge (both L-PF and S-PF > 1.2). Yen volatility at session open tends to be structural — the Bank of Japan policy uncertainty generates real range breakouts independent of trend.
- **DE40 / UK100** positive combined PF comes almost entirely from LONG trades in a bull equity regime. A similar 14-week bear leg would likely produce the mirror image (S-PF dominant, L-PF < 1.0). These instruments need multi-regime data to assess structural ORB edge.
- **Commodities (GOLD, XBRUSD)** show the opposite directional bias to equities, consistent with the same risk-off/risk-on regime rotation. The SHORT edge in these instruments during Jan-May 2026 is not structural ORB logic — it is trend-riding.

**Implication for backtesting:** 14 weeks of single-regime data cannot distinguish ORB structural edge from momentum. A meaningful test requires at least 6–12 months spanning both a bull and a bear phase. Until more data is available, instrument selection should be conservative: USDJPY is the only instrument with defensible two-way evidence.

---

## 7. Test Suite

All tests use synthetic bar sequences — no real DB file or network access required.

### 7.1 `tests/unit/test_signals.py` (32 tests)

Tests for `backtest/signals.py`:

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
| `test_mean_reversion_midline_exit` | 19 flat bars + 17 bars at 1.5; z drops to 0.5 at bar 34 (window mean rises) → `exit_reason == "Z-score midline"` (tested with `max_hold_bars=50` to isolate midline) |
| `test_hold_cap_closes_mean_reversion_trade` | Signal fires, price stays at spike level; default `max_hold_bars=5` → `exit_reason == "Hold cap"` after 5 bars |
| `test_spread_adjusts_buy_entry_and_exit` | BUY with `spread_pts=0.10`: `entry_price = open + 0.05`, `exit_price = close - 0.05` |
| `test_spread_adjusts_sell_entry_and_exit` | SELL with `spread_pts=0.10`: `entry_price = open - 0.05`, `exit_price = close + 0.05` |
| `test_hard_stop_takes_priority_over_midline_exit` | Price crashes far beyond stop level → hard stop fires before `check_exit()` is reached |

### 7.3 `tests/unit/test_signals.py` additions

| Test class | Tests added |
|---|---|
| `TestADXGate` | `test_momentum_suppressed_when_adx_below_threshold`, `test_momentum_fires_when_adx_gate_disabled`, `test_momentum_suppressed_by_high_explicit_threshold`, `test_mean_reversion_fires_in_flat_market`, `test_mean_reversion_suppressed_in_trending_market` |
| `TestATRGate` | `test_gate_blocks_when_spread_large_relative_to_atr`, `test_gate_disabled_when_spread_zero`, `test_gate_permissive_while_atr_warming_up` |
| `TestHoldCap` | `test_hold_cap_fires_after_max_bars`, `test_hold_cap_not_triggered_before_max_bars`, `test_hold_cap_cleared_after_notify_exit`, `test_hold_cap_priority_over_z_score_midline` |
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

Tests for `ORBSignalState` in `backtest/signals.py`:

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

# Full unit suite (284 tests)
pytest tests/unit/ -v
```

All 284 unit tests pass with no network access or real DB file.

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
| Sig/wk > 15 | Signal threshold too loose | Tighten z-score threshold (mean reversion) or increase `_MIN_EMA_GAP_PCT` in `signals.py` (momentum, default 0.05%, tuned range 0.01–0.10%) |
| Sig/wk = 0 | Instrument never triggers the signal | Instrument may be unsuitable for this strategy style |
| MaxDD% > 15% with PF near 1.0 | Strategy earns slowly and has catastrophic drawdowns | This risk profile is not suitable for live deployment |
| `inf` PF on < 15 trades | Sample too small to trust | Run on more data or wait for incremental DB updates |
| `0` trades on an instrument | No bars in DB for this epic | Run `fetch_ohlc.py` on Windows to populate |
| AvgR positive but PF near 1.0 (e.g. 1.05) | P&L dominated by a few big winners, not consistent edge | Check individual trades; strategy may be high-variance / lucky |

### 8.5 Instrument characteristics

Based on the current 3-month M1 dataset:

Empirically derived from baseline backtest (Jan–May 2026, M1):

| Instrument | Momentum (PF / trades) | Mean Rev (PF / trades) | Verdict |
|------------|----------------------|----------------------|---------|
| EURUSD | 1.32 / 4 | 0.76 / 3 | Both: sample too small |
| GBPUSD | 0.18 / 4 | 0.00 / 2 | Skip |
| USDJPY | 0.01 / 5 | 1.45 / 4 | Both: sample too small |
| EURGBP | 0.00 / 1 | 0.00 / 1 | Skip |
| US500 | 0.35 / 13 | 0.50 / 16 | Skip — both negative |
| DE40 | 0.46 / 22 | **1.48 / 25** | Mean rev viable; momentum no |
| UK100 | **3.04 / 15** | 0.69 / 17 | Momentum promising (small sample) |
| GOLD | **1.52 / 71** | 0.83 / 92 | Momentum viable; mean rev no |
| XBRUSD | 0.97 / 201 | **1.15 / 226** | Mean rev marginal; momentum breakeven |
| BTCUSD | 0.60 / 76 | 1.02 / 74 | Both poor; crypto too choppy |
| ETHUSD | 0.96 / 116 | 0.99 / 116 | Both breakeven; skip |

**Pattern:** Mean reversion works on range-bound instruments with moderate ATR (DE40, XBRUSD). Momentum works only on high-ATR instruments where large directional moves occur (GOLD, UK100). FX pairs generate too few signals at M1 for either strategy to be meaningful on a 3-month dataset.

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
- Time-varying spread (`backtest/spreads.py` returns a single static value per instrument; Phase A4 spread-widening checks need a multiplier or live snapshots — see `AUDIT_PLAN.md`)

### 9.3 No parallel positions

The engine holds at most one position at a time. The live system allows up to `max_open_positions` (currently 3). Multi-position interactions (margin usage, correlated drawdowns) are not tested.

### 9.4 No position sizing

P&L is in points, not currency. The backtest does not apply `target_risk_pct` or the vol-scaled sizing from `analyze_instrument`. All trades contribute equally to `profit_factor` and `max_drawdown_pct` regardless of the intended position size.

### 9.5 Time exit not active

The engine passes `session_end_time=None` to `evaluate_position()`, so the time-exit rule never fires. Trades in the backtest remain open until a price-based exit or end of data. Live trades may be closed earlier by the time exit.

### 9.6 Data coverage gaps

Capital.com 1-min data has gaps for weekends and outside normal session hours. These gaps appear as missing bars — the engine skips over them naturally (timestamps are not contiguous). Signal frequency (`Sig/wk`) is computed from the wall-clock span of the data, not bar count, so it accounts for coverage gaps correctly.

### 9.7 Three-month regime risk

All data covers January–May 2026. Results from a single market regime (rising equities, moderate FX volatility) may not generalise. A strategy that performs well in this period may underperform in a high-volatility or ranging macro environment.
