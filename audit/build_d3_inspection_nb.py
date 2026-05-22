"""build_d3_inspection_nb.py — D3/BR3 trade inspection notebook generator.

Visual deep-dive for the intraday_continuation strategy run. For each instrument
in the D3 universe (US500, DE40) renders 20 best + 10 median + 20 worst trades
by pnl_points, PLUS 10 random "Session close (no bar at threshold)" trades —
the rare 3.4% fallback exits that contribute the strategy's entire positive
PnL share (the key smell from `d3_run_2026-05-21.md`).

Each chart overlays:
  • M15 OHLC high-low band + close line
  • Entry / exit markers
  • Initial hard stop (= initial Chandelier level at entry)
  • **Dynamic Chandelier stop trajectory** from Trade.stop_history (stepped
    line — this is the WHOLE exit mechanism for D3; visualising it makes the
    "is the trail behaving as designed?" question answerable by eye)
  • Hold period shaded

Run:  source .venv/bin/activate && python audit/build_d3_inspection_nb.py
Output: audit/d3_trade_inspection.ipynb (executed in place)

Modelled on `analysis/audit_archive/build_trade_inspection_nb.py` — adapted
for D3-specific overlays (Chandelier trajectory) and bucket scheme (the
Session-close special bucket).
"""

import sys
from pathlib import Path

import nbformat
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell
from nbclient import NotebookClient

REPO = Path(__file__).resolve().parents[1]
OUT = str(REPO / "audit" / "d3_trade_inspection.ipynb")
TRADES_PARQUET = str(REPO / "audit" / "d3_run_2026-05-21.parquet")
DB_PATH = "/mnt/c/Users/chris/dev/trading-data/trading.db"


INTRO = """\
# D3/BR3 (intraday_continuation) — Trade Inspection

Human-intuition pass over individual backtest trades for the
**intraday_continuation** (Zarattini-inspired, D3/BR3) strategy run.

**Scope baked into this notebook**

| Choice | Value | Why |
|---|---|---|
| Instruments | US500, DE40 (UK100 dropped — see STRATEGY_AUDIT amendment) | The pre-registered pooled universe after the UK100 data-depth amendment |
| Trade pick | 20 best + 10 median + 20 worst (by `pnl_points`) + **10 random `Session close` exits** | Standard 50-chart contrast PLUS a dedicated bucket for the 3.4% day-rollover fallback exits that drive the strategy's positive PnL share |
| Price trace | M15 OHLC H-L band + close line at M15 (the strategy's resolution) | One resolution; no resampling |
| Context window | **`CTX_BARS`** bars before entry + after exit (default 32 → ±8h on M15) | Zoom knob — edit and re-run any section |
| **Trail overlay** | Stepped Chandelier stop_level from `Trade.stop_history` | **D3-specific** — the trail IS the exit mechanism, so we draw it. Look for: smooth peak-anchored movement; "may loosen" when ATR expands; coincidence with the exit price at exit time |
| Other overlays | Entry/exit markers; initial-stop line (= initial Chandelier level); hold shaded; no TP (D3 has none by design) | Minimal |

**Key smell to look for** (from `d3_run_2026-05-21.md`):
- 96.6% of trades exit via "Hard stop" (Chandelier catching) — mean net **−1.44 bps**
- 3.4% exit via "Session close (no bar at threshold)" — mean net **+50 bps**
- The strategy's positive PnL share is **entirely** the rare 3.4%. The `SESSION_CLOSE`
  bucket below renders 10 of those for direct visual inspection.

The kernel stays warm — change `CTX_BARS` (zoom) or bucket sizes in the config
cell and re-run a section to re-render live.
"""


CFG = f'''\
import sqlite3, datetime as dt, json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

TRADES = "{TRADES_PARQUET}"
DB     = "{DB_PATH}"

# ─────────────────────────  ZOOM KNOB  ─────────────────────────
# M15 bars drawn BEFORE entry and AFTER exit. Smaller → zoom in,
# larger → zoom out. Change and re-run any section; kernel stays warm.
CTX_BARS = 32          # ±8 hours on M15
# ───────────────────────────────────────────────────────────────

EPICS = ["US500", "DE40"]
N_TOP, N_MED, N_BOT = 20, 10, 20
N_SESSION_CLOSE = 10   # the rare fallback bucket — the 3.4% smell
SEED = 42
UTC = dt.timezone.utc
plt.rcParams["figure.dpi"] = 100
'''


HELP = '''\
trades = pd.read_parquet(TRADES)
trades["exit_cat"] = trades["exit_reason"].str.extract(r"^([A-Za-z][A-Za-z ]*?)[:0-9]", expand=False).str.strip()
trades.loc[trades["exit_cat"].isna(), "exit_cat"] = trades["exit_reason"]

_frame_cache = {}

def _epic_frame(epic):
    if epic in _frame_cache:
        return _frame_cache[epic]
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT ts,open,high,low,close FROM ohlc_bars "
            "WHERE epic=? AND resolution='M15' ORDER BY ts", con, params=(epic,))
    finally:
        con.close()
    df["t"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    _frame_cache[epic] = df.reset_index(drop=True)
    return _frame_cache[epic]

def load_ohlc(epic, entry_ts, exit_ts, n):
    """`n` M15 bars before entry + in-trade bars + `n` bars after exit."""
    f = _epic_frame(epic)
    intr = f.index[(f.ts >= entry_ts) & (f.ts <= exit_ts)]
    if len(intr):
        i0, i1 = int(intr[0]), int(intr[-1])
    else:
        i0 = i1 = int(f.ts.searchsorted(entry_ts))
    return f.iloc[max(0, i0 - n): min(len(f), i1 + n + 1)]

def trail_series(stop_history):
    """Trade.stop_history is a numpy array of (ts, stop_level) pairs.
    Returns (times, stops) as parallel lists for stepped plotting.
    Handles edge cases: empty, missing, scalar-row format from parquet."""
    if stop_history is None:
        return [], []
    try:
        arr = list(stop_history)
    except TypeError:
        return [], []
    times, stops = [], []
    for row in arr:
        try:
            ts, st = float(row[0]), float(row[1])
            times.append(dt.datetime.fromtimestamp(ts, UTC))
            stops.append(st)
        except (TypeError, IndexError, ValueError):
            continue
    return times, stops

def select_trades(epic):
    s = (trades[trades.epic == epic]
         .sort_values("pnl_points", ascending=False)
         .reset_index(drop=True))
    top = s.head(N_TOP).assign(bucket="WIN")
    bot = s.tail(N_BOT)[::-1].assign(bucket="LOSS")   # worst first
    mid = len(s) // 2
    med = s.iloc[max(0, mid - N_MED // 2): max(0, mid - N_MED // 2) + N_MED].assign(bucket="MEDIAN")

    # SESSION_CLOSE bucket — the 3.4% smell. 10 random examples.
    # exit_cat for these is "Session close (no bar at threshold)" — the
    # regex strip in cell 2 leaves the full string intact (no colon to split on)
    sc = trades[(trades.epic == epic) & trades.exit_cat.str.startswith("Session close", na=False)]
    if len(sc) > N_SESSION_CLOSE:
        sc = sc.sample(n=N_SESSION_CLOSE, random_state=SEED)
    sc = sc.assign(bucket="SESSION_CLOSE").sort_values("pnl_points", ascending=False).reset_index(drop=True)

    return pd.concat([top, med, bot, sc], ignore_index=True), s

def plot_trade(t, rank, total):
    o   = load_ohlc(t.epic, t.entry_ts, t.exit_ts, CTX_BARS)
    fig, ax = plt.subplots(figsize=(13, 4.6))
    if len(o):
        ax.fill_between(o.t, o.low, o.high, color="0.82", lw=0, label="M15 H-L")
        ax.plot(o.t, o.close, color="0.35", lw=0.9, label="M15 close")

    e = dt.datetime.fromtimestamp(t.entry_ts, UTC)
    x = dt.datetime.fromtimestamp(t.exit_ts,  UTC)
    ax.axvspan(e, x, color="tab:blue", alpha=0.07)

    em  = "^" if t.direction == "BUY" else "v"
    win = t.pnl_points > 0
    ax.scatter([e], [t.entry_price], marker=em, s=140, color="black",
               zorder=5, label=f"entry {t.direction}")
    ax.scatter([x], [t.exit_price], marker="o", s=95,
               color=("tab:green" if win else "tab:red"), zorder=5, label="exit")

    # Initial Chandelier stop (= entry ∓ 1.5·ATR(entry))
    ax.hlines(t.stop_loss, e, x, ls=":", color="tab:red", lw=1.0,
              label="initial Chandelier")

    # Dynamic Chandelier trail — the whole exit mechanism for D3
    tt, ts = trail_series(t.stop_history)
    if tt:
        ax.step(tt, ts, where="post", color="tab:red", lw=1.6,
                label="Chandelier trail")

    dur_h = (t.exit_ts - t.entry_ts) / 3600.0
    n_adj = len(tt)
    ax.set_title(
        f"[{t.bucket} {rank}/{total}]  {t.epic} / {t.direction}  |  "
        f"pnl_pts={t.pnl_points:+.4g}  |  exit={t.exit_cat}  |  "
        f"hold={dur_h:.1f}h ({(t.exit_ts-t.entry_ts)//900} M15 bars)  |  "
        f"trail adj={n_adj}  |  entry {e:%Y-%m-%d %H:%M} UTC", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.legend(fontsize=7, ncol=6, loc="upper left", framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    plt.tight_layout()
    plt.show()
    plt.close(fig)

print("loaded", len(trades), "trades;",
      "entry_ts range:", trades.entry_ts.min(), "->", trades.entry_ts.max())
print("exit_cat mix:", trades.exit_cat.value_counts().to_dict())
'''


def section_md(epic: str) -> str:
    return f"""\
---
## {epic} — intraday_continuation

Stats first, then charts in order: **WIN** (best→least-best), **MEDIAN**,
**LOSS** (worst→least-worst), then **SESSION_CLOSE** (the rare day-rollover
fallback bucket — the 3.4% that drives positive PnL share).

For each chart, look for:
- Does the Chandelier trail (red stepped line) follow the running peak/trough
  smoothly?
- Does the trail ever **loosen** (move away from price)? This is the
  literature-faithful behaviour — if it never loosens we may have a bug or
  the ATR is too stable to exercise it
- Does the exit fire when the trail meets price, as expected?
- For SESSION_CLOSE trades: is the prior-day's last-bar close a realistic
  fill, or would a live market-on-close fill be materially different?
"""


def section_code(epic: str) -> str:
    return f'''\
epic = "{epic}"
sel, full = select_trades(epic)
print(f"{{epic}}:  n={{len(full)}}  win%={{(full.pnl_points>0).mean():.1%}}  "
      f"pnl_pts_sum={{full.pnl_points.sum():+.2f}}")
print("exit mix :", full.exit_cat.value_counts().to_dict())
print("selected :", sel.bucket.value_counts().to_dict(), "(", len(sel), "charts )")
for i, (_, tr) in enumerate(sel.iterrows(), 1):
    plot_trade(tr, i, len(sel))
'''


def main():
    cells = [
        new_markdown_cell(INTRO),
        new_code_cell(CFG),
        new_code_cell(HELP),
    ]
    for epic in ("US500", "DE40"):
        cells.append(new_markdown_cell(section_md(epic)))
        cells.append(new_code_cell(section_code(epic)))

    nb = new_notebook(cells=cells)
    nb.metadata.kernelspec = {
        "display_name": "Python 3", "language": "python", "name": "python3",
    }

    n_charts = 2 * (20 + 10 + 20 + 10)
    print(f"executing notebook ({n_charts} charts; this takes a minute)...")
    NotebookClient(nb, timeout=900, kernel_name="python3").execute()
    nbformat.write(nb, OUT)
    print("written:", OUT)


if __name__ == "__main__":
    sys.exit(main())
