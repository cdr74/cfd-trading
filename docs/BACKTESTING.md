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
    ├── storage/repository.py  get_bars()
    │     └── reads ohlc_bars from trading.db via /mnt/c/...
    ├── strategy/loader.py  load_strategy()
    │     └── reads config/strategies/<name>.yaml + .md
    ├── backtest/signals.py  momentum_signal() / mean_reversion_signal()
    │     └── pure functions — no I/O
    ├── backtest/engine.py  run_backtest()
    │     ├── calls signal function per bar
    │     └── calls monitor/monitor.py evaluate_position() per bar
    └── prints summary table
```

**Key design invariant:** no Capital.com or Anthropic API calls are possible during a backtest run. `run.py` sets `BACKTEST_MODE=true` before any imports, which causes `CapitalClient` to raise `RuntimeError` at instantiation. This guard is enforced at the client level, not per-tool.

---

## 3. Data Layer

### 3.1 Fetch script (Windows-side)

`backtest/fetch_ohlc.py` runs on Windows Python (not WSL2) because MetaTrader 5 uses Windows IPC.

**MT5 constraints (empirically verified):**

| Constraint | Value |
|------------|-------|
| API method | `copy_rates_range(symbol, mt5.TIMEFRAME_M1, from_dt, to_dt)` |
| Max window per call | 60 days at M1 resolution |
| Per-call row cap | ~100,000 rows (MT5 silently truncates) |
| History depth | ~3 months (earliest Capital.com data ≈ day -120) |
| Bulk fetch strategy | 4 × 30-day windows per instrument (30 days × 1440 min ≈ 43,200 rows — safely under cap) |
| Incremental update | 1 call per instrument (yesterday → today) |

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

---

## 4. Entry Signals

Source: `src/cfd_trading/backtest/signals.py`

Both signal functions take a **list of `OHLCBar` objects in chronological order** (latest bar last) and return `"LONG"`, `"SHORT"`, or `None`.

### 4.1 Momentum signal

**Approximates:** EMA_9 crosses above/below EMA_21 with trend slope confirmation.

**Minimum bars:** 22 (EMA_21 needs 21 bars; crossover detection needs one prior bar).

**Logic:**

```
1. Compute EMA_9 and EMA_21 over all bars except the last (prev state)
2. Compute EMA_9 and EMA_21 over all bars including the last (curr state)
3. Compute linear trend slope over all close prices
4. LONG  if EMA_9 crossed above EMA_21 in the last bar AND slope > 0
5. SHORT if EMA_9 crossed below EMA_21 in the last bar AND slope < 0
6. None otherwise (includes: already in trend, flat market, slope contradicts crossover)
```

**Key detail — slope filter:** A crossover that contradicts the overall trend slope is suppressed. For example, a bullish EMA crossover in a sequence where the dominant slope is negative returns `None`. This prevents late-entry signals at trend exhaustion.

**Key detail — EMA gap filter:** Even when a crossover occurs, the signal is suppressed if the fractional gap between EMA_9 and EMA_21 is below 0.15% of EMA_21. On M1 bars the two EMAs are nearly identical most of the time; a sub-threshold gap means the "crossover" is noise rather than real momentum divergence. This filter eliminates the majority of false signals at high signal-frequency instruments (crypto, indices).

**Indicator formulas:**

```
EMA(period) = SMA(first period bars) then α×price + (1−α)×prev_ema  where  α = 2/(period+1)
slope       = OLS regression coefficient of close prices over the full bar window
```

### 4.2 Mean reversion signal

**Approximates:** Price overextended beyond 2 standard deviations from a 20-bar rolling mean.

**Minimum bars:** 20.

**Logic:**

```
1. Compute z-score of the last close over the most recent 20 bars:
   z = (close - mean) / stddev
2. SHORT if z >= +2.0  (price above mean by 2σ — fade the spike upward)
3. LONG  if z <= -2.0  (price below mean by 2σ — fade the drop)
4. None  if |z| < 2.0
```

**Key detail — windowed z-score:** Only the last 20 bars contribute to `mean` and `stddev`. Older history is ignored. This means a long-running trend will eventually reset the z-score baseline, allowing signals to fire even in trending markets if the local 20-bar window becomes mean-reverting.

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

```
stop_distance = entry_price × (default_pct / 100)

BUY:
  stop_loss    = entry_price - stop_distance
  take_profit  = entry_price + stop_distance × min_rr_ratio

SELL:
  stop_loss    = entry_price + stop_distance
  take_profit  = entry_price - stop_distance × min_rr_ratio
```

Parameters come from the strategy YAML:
- `risk.stop_loss.default_pct` — stop distance as % of entry price
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

P&L is computed in **points** (price units), not currency:

```
BUY:  pnl_points = exit_price - entry_price
SELL: pnl_points = entry_price - exit_price
```

No spread, commission, or contract size is applied. All metrics in `BacktestResult` are in price points.

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
| `trades` | `list[Trade]` | Full trade-level detail |

Each `Trade` record contains: `epic`, `strategy`, `direction` (BUY/SELL), `entry_ts`, `entry_price`, `stop_loss`, `take_profit`, `exit_ts`, `exit_price`, `exit_reason`, `pnl_points`.

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

# Override bar resolution (default: M1)
python -m cfd_trading.backtest.run --strategy mean_reversion --epic GOLD --resolution M5
```

### 6.2 Arguments

| Argument | Description |
|----------|-------------|
| `--epic EPIC` | Run a single instrument (mutually exclusive with `--all-epics`) |
| `--all-epics` | Run all instruments from `config/watchlist.yaml` |
| `--strategy NAME` | Run a single strategy (mutually exclusive with `--all-strategies`) |
| `--all-strategies` | Run all strategies discovered in `config/strategies/` (excludes `_base` and `scan`) |
| `--resolution` | Bar resolution to query from DB (default: `M1`) |

### 6.3 Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKTEST_DB_PATH` | `/mnt/c/Users/chris/dev/trading-data/trading.db` | Path to the SQLite DB with `ohlc_bars` |
| `CONFIG_DIR` | Auto-detected from package root | Path to `config/` directory |

`BACKTEST_MODE=true` is set automatically at startup.

### 6.4 Sample output

```
Epic      Strategy        Trades  Win%    PF      MaxDD%   Stop%   Sig/wk   NetPts
--------  --------------  ------  ------  ------  -------  ------  -------  ---------
EURUSD    momentum        42      54.8%   1.32    4.2      23.8%   2.80     +0.1843
GBPUSD    momentum        38      52.6%   1.18    5.1      28.9%   2.53     +0.0712
GOLD      momentum        57      56.1%   1.45    3.8      19.3%   3.80     +34.2100
EURUSD    mean_reversion  29      58.6%   1.61    2.7      10.3%   1.93     +0.2314
GBPUSD    mean_reversion  31      51.6%   1.09    4.5      16.1%   2.07     +0.0381
GOLD      mean_reversion  44      59.1%   1.72    3.1      9.1%    2.93     +47.9830
```

`NetPts` is in raw price units — the same scale as the instrument price. EURUSD trades in the range 1.05–1.15, so a net of `+0.18` represents about 180 pips total. GOLD trades near 2000 USD/oz, so `+34.21` is about 17 pips (1 point = 1 USD/oz).

---

## 7. Test Suite

All tests use synthetic bar sequences — no real DB file or network access required.

### 7.1 `tests/unit/test_signals.py` (17 tests)

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
| `test_no_signal_when_z_within_threshold` | Flat prices → z-score = 0 → `None` |
| `test_short_when_z_exceeds_positive_threshold` | 19 bars at 1.0 + spike to 1.5 → large positive z → `"SHORT"` |
| `test_long_when_z_exceeds_negative_threshold` | 19 bars at 1.0 + drop to 0.5 → large negative z → `"LONG"` |
| `test_no_signal_when_price_within_two_sigma` | Alternating 0.02 oscillation → z near 0 → `None` |
| `test_uses_last_20_bars_for_zscore` | 30 bars at 100.0 (old history), 19 bars at 1.0, spike to 0.5 → z-score based on the last 20 bars only → `"LONG"` (not distorted by old history) |

### 7.2 `tests/unit/test_engine.py` (18 tests)

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
| `test_net_pnl_pts_zero_when_no_trades` | Empty bars → `net_pnl_pts == 0.0` |

### 7.3 `tests/unit/test_run.py` (13 tests)

Tests for `backtest/run.py`:

| Test | What it verifies |
|------|-----------------|
| `test_resolve_strategies_single` | `--strategy momentum` → `["momentum"]` |
| `test_resolve_strategies_all` | `--all-strategies` with a mock config dir containing momentum + mean_reversion (plus `_base` and `scan` which are excluded) → `{"momentum", "mean_reversion"}` |
| `test_resolve_epics_single` | `--epic EURUSD` → `["EURUSD"]` |
| `test_resolve_epics_all` | `--all-epics` reads `watchlist.yaml` and flattens all groups into a single list |
| `test_load_risk` | `_load_risk()` parses `risk.yaml` correctly |
| `test_print_table_no_crash` | Table output contains epic name, strategy name, formatted win rate (`60.0%`), formatted PF (`1.80`), and NetPts with sign (`+0.1234`) |
| `test_print_table_net_pts_negative` | Negative `net_pnl_pts` renders as `-0.0567` |
| `test_print_table_inf_profit_factor` | `profit_factor = inf` renders as `"inf"` without crashing |
| `test_print_table_zero_trades` | Zero-trade result renders without division errors |
| `test_main_single_epic_strategy` | Full `main()` with a real in-memory SQLite DB and minimal config dir — no exceptions, table printed |
| `test_main_missing_db_exits` | DB file absent → `sys.exit(1)`, error message on stderr contains `"not found"` |
| `test_main_no_bars_skips_gracefully` | DB exists but `ohlc_bars` is empty → skip message printed, no crash, no results printed |

### 7.4 Running the tests

```bash
cd ~/dev/trading/cfd-trading
source .venv/bin/activate

# Backtest tests only
pytest tests/unit/test_signals.py tests/unit/test_engine.py tests/unit/test_run.py -v

# Full unit suite (190 tests)
pytest tests/unit/ -v
```

All 195 unit tests pass with no network access or real DB file.

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
| `NetPts` | Sum of all trade P&L in raw price units | Positive = net profitable; negative = net loss. Scale varies by instrument (see §8.6) |

### 8.2 Calculating net win/loss

`NetPts` is the direct bottom line: `sum(exit_price − entry_price)` for BUY trades and `sum(entry_price − exit_price)` for SELL trades, across all completed trades. It is in **raw price units**, not currency.

**Converting NetPts to currency:**

```
currency_pnl = NetPts × contract_size × position_size_lots
```

Where `contract_size` depends on the instrument:

| Instrument class | Typical contract size | Example |
|-----------------|----------------------|---------|
| FX (EURUSD, GBPUSD) | 100,000 base units per lot | `NetPts=+0.018 × 100,000 × 0.1 lot = +$180` |
| FX (USDJPY) | 100,000 USD per lot | 1 pip = 0.01; `NetPts=+1.5 × 100,000 × 0.1 = +$15,000` (note: USDJPY in pips ÷ 100) |
| Indices (US500, DE40, UK100) | $1–$10 per point per lot | Varies by broker/product |
| GOLD (XAUUSD) | 100 oz per lot | `NetPts=+34.21 × 100 × 0.1 = +$342.10` |
| XBRUSD (Brent oil) | 1,000 bbls per lot | `NetPts=+2.1 × 1,000 × 0.1 = +$210` |
| Crypto (BTCUSD, ETHUSD) | 1 coin per lot | `NetPts=+500 × 1 × 0.1 = +$50` |

The backtest uses no position sizing (`target_risk_pct` is not applied), so `NetPts` represents a 1-lot, 1-unit position throughout. Multiply by your intended size to estimate actual P&L.

**Relating NetPts to PF:**

```
NetPts = gross_profit − gross_loss
       = gross_loss × (PF − 1)
```

A PF of 1.3 means for every 1 point lost, 1.3 points are won — net 0.3 points per unit of risk. `NetPts` gives you the cumulative total of those net gains across all trades in the dataset.

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
Win% 48–60%  |  PF 1.3–2.0  |  MaxDD% < 6%  |  Stop% 20–40%  |  Sig/wk 1–5  |  NetPts > 0
```

**Healthy mean reversion run:**
```
Win% 55–70%  |  PF 1.5–2.5  |  MaxDD% < 4%  |  Stop% 10–25%  |  Sig/wk 1–4  |  NetPts > 0
```

### 8.4 Warning signs

| Pattern | Likely cause | Action |
|---------|-------------|--------|
| PF < 1.0 across multiple instruments | Strategy has no edge on this data | Re-examine signal logic or instrument suitability |
| NetPts negative despite Win% > 50% | Wins are small, losses are large (inverted R:R) | Check if stop is wider than TP in practice — can happen with trailing stop ratcheting |
| Stop% > 60% | Stop too tight OR signal fires against the trend | Widen `default_pct` or strengthen signal filter |
| Sig/wk > 15 | Signal threshold too loose | Tighten z-score threshold (mean reversion) or increase `_MIN_EMA_GAP_PCT` in `signals.py` (momentum, currently 0.15%) |
| Sig/wk = 0 | Instrument never triggers the signal | Instrument may be unsuitable for this strategy style |
| MaxDD% > 15% with PF near 1.0 | Strategy earns slowly and has catastrophic drawdowns | This risk profile is not suitable for live deployment |
| `inf` PF on < 15 trades | Sample too small to trust | Run on more data or wait for incremental DB updates |
| `0` trades on an instrument | No bars in DB for this epic | Run `fetch_ohlc.py` on Windows to populate |
| Large positive NetPts but small PF (e.g. 1.05) | P&L dominated by a few big winners, not consistent edge | Check individual trades; strategy may be high-variance / lucky |

### 8.5 Instrument characteristics

Based on the current 3-month M1 dataset:

| Instrument class | Momentum suitability | Mean reversion suitability | Notes |
|-----------------|---------------------|--------------------------|-------|
| FX (EURUSD, GBPUSD, EURGBP) | Moderate — trends form but are often shallow | Good — tight ranges, frequent z-score extremes | Lower ATR means smaller absolute NetPts per trade |
| FX (USDJPY) | Good during macro moves | Moderate | More trend-prone than EUR pairs |
| Indices (US500, DE40, UK100) | Good — strong intraday directional moves | Moderate — can gap through z-score levels | Higher ATR; larger NetPts per trade but wider stops needed |
| Commodities (GOLD, XBRUSD) | Good — GOLD trends strongly; XBRUSD more choppy | Good for XBRUSD | GOLD NetPts magnitude is large (price in USD/oz) |
| Crypto (BTCUSD, ETHUSD) | High volatility — momentum signals frequent but reversals sharp | Poor — z-score extremes are common and reversals can deepen | High stop% likely; treat as exploratory only |

### 8.6 NetPts scale by instrument

Because P&L is in raw price units, you cannot directly compare NetPts across instruments. Normalise by dividing by the average entry price:

```
NetPts_normalised = NetPts / avg_entry_price × 100  (gives %)
```

Example: GOLD at avg_entry 2000 with NetPts=+34.21 → `+1.71%`. EURUSD at avg_entry 1.08 with NetPts=+0.018 → `+1.67%`. These are now comparable.

The `MaxDD%` column already does this normalisation, which is why it is directly comparable across instruments.

### 8.7 Acting on results

The backtest output is a **filter before live tuning**, not a tuning target. Use it to:

1. **Discard clearly unprofitable combinations** — negative NetPts + PF < 0.9 with adequate trade count is a hard pass.
2. **Identify the best 2–3 instrument/strategy pairs** — highest PF with NetPts positive and trade count ≥ 30.
3. **Detect stop size mismatches** — high Stop% + low PF + negative NetPts → `default_pct` is too tight; consider increasing by 0.5%.
4. **Validate signal frequency** — if `Sig/wk < 1`, the instrument is unlikely to generate live entry opportunities during normal Claude Code sessions.
5. **Cross-check NetPts with PF** — a PF of 1.4 with only 10 trades and large NetPts is a lucky sample, not an edge. Require both PF > 1.3 and Trades ≥ 30 before trusting the result.

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

Entry is simulated at `next_bar.open`. In live trading, MARKET orders fill at the current offer (BUY) or bid (SELL), which includes the spread. The backtest therefore overstates entry accuracy by approximately half the spread per trade.

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
