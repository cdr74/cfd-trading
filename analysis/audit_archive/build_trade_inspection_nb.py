"""
build_trade_inspection_nb.py — regenerator for audit/trade_inspection.ipynb

Visual deep-dive: price action alongside individual backtest trades, at each
trade's own resolution (momentum M30, mean_reversion/orb M15). 20 best + 20
worst + 10 median trades per pair, by pnl_points, over the full 2023-2026
backtest. Entry/exit markers + SL/TP lines only. The context window is a
bar-count zoom knob: edit CTX_BARS in the config cell and re-run.

Run:  source ../cfd-trading/.venv/bin/activate && python build_trade_inspection_nb.py
Output: audit/trade_inspection.ipynb (executed in place)
"""

import sys
import nbformat
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell
from nbclient import NotebookClient

OUT = "/home/chris/dev/trading/audit/trade_inspection.ipynb"

INTRO = """\
# Trade Inspection — Visual Deep-Dive

Human-intuition pass over individual backtest trades, ticks/price alongside the trade.

**Scope & decisions baked into this notebook**

| Choice | Value | Why |
|---|---|---|
| Instruments | MR=GOLD, MOM=US500, ORB=DE40 | Closest-to-50% win-rate per strategy with a sample big enough for 50 trades — balances good vs. bad examples within one sheet |
| Trade pick | 20 best + 10 median + 20 worst by `pnl_points` | Clearest contrast for visual pattern-spotting; both tails plus a typical middle |
| Price trace | close + high–low band at the trade's **own resolution** (momentum **M30**, mean_reversion/orb **M15**) | Matches the resolution the signal actually traded on. M30 is resampled from M15 (the DB has no native M30), the same way the backtest aggregates momentum |
| Context window | **`CTX_BARS`** bars before entry and after exit (default 96) | A zoom knob — set it in the config cell and re-run any section to zoom in/out. Bar-count (not wall-clock) so it's gap-stable |
| Overlays | entry/exit markers, SL & TP lines, hold shaded | Minimal, by request — no strategy-indicator subplots |

**Caveats:** bars are 15-/30-min, not true ticks (no finer data exists pre-2026).
Spreads and the strategy's internal signal (z-score / momentum EMAs / ORB box) are
deliberately *not* drawn. `pnl_points` is in instrument price points, not account currency.

The kernel stays warm — change **`CTX_BARS`** (zoom), `PICKS`, or the bucket sizes
in the config cell and re-run a section to re-render live.
"""


def section_md(strat, epic):
    return f"""\
---
## {strat} / {epic}

Stats first, then 50 charts in order: **WIN** (best → least-best), **MEDIAN**,
**LOSS** (worst → least-worst). Each title carries bucket, rank, direction,
`pnl_points`, exit category, hold hours, and entry timestamp.
"""


CFG = '''\
import sqlite3, datetime as dt
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

TRADES = "/home/chris/dev/trading/audit/trades_M15.parquet"
DB     = "/mnt/c/Users/chris/dev/trading-data/trading.db"

# ─────────────────────────  ZOOM KNOB  ─────────────────────────
# Bars drawn BEFORE entry and AFTER exit, at each trade's OWN resolution
# (momentum = M30, mean_reversion / orb = M15). Smaller → zoom in,
# larger → zoom out. Change it and re-run any section; kernel stays warm.
CTX_BARS = 96          # ≈ the old ±2-day window at M30
# ───────────────────────────────────────────────────────────────

PICKS  = [("mean_reversion", "GOLD"),
          ("momentum",       "US500"),
          ("orb",            "DE40")]
N_TOP, N_MED, N_BOT = 20, 10, 20
UTC = dt.timezone.utc
plt.rcParams["figure.dpi"] = 100
'''

HELP = '''\
trades = pd.read_parquet(TRADES)
trades["exit_cat"] = trades["exit_reason"].str.split(":").str[0].str.strip()

# Per-trade resolution: momentum→M30, mean_reversion/orb→M15 (stamped by run.py).
# The DB has native M1 and M15 only; M30 is resampled from M15 (gap-correct),
# matching how the backtest aggregates momentum bars.
_RES_RULE = {"M15": "15min", "M30": "30min"}
_frame_cache = {}

def _epic_frame(epic, res):
    key = (epic, res)
    if key in _frame_cache:
        return _frame_cache[key]
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql_query(
            "SELECT ts,open,high,low,close FROM ohlc_bars "
            "WHERE epic=? AND resolution='M15' ORDER BY ts", con, params=(epic,))
    finally:
        con.close()
    df["t"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    if res == "M15":
        out = df
    else:                                  # resample M15 → M30
        out = (df.set_index("t")
                 .resample(_RES_RULE[res], label="left", closed="left")
                 .agg(open=("open", "first"), high=("high", "max"),
                      low=("low", "min"), close=("close", "last"))
                 .dropna().reset_index())
        out["ts"] = out["t"].map(pd.Timestamp.timestamp).astype("int64")  # POSIX s, unit-agnostic
    out = out.reset_index(drop=True)
    _frame_cache[key] = out
    return out

def load_ohlc(epic, res, entry_ts, exit_ts, n):
    """`n` bars before entry + the in-trade bars + `n` bars after exit,
    at resolution `res`. `n` = CTX_BARS (the zoom knob)."""
    f = _epic_frame(epic, res)
    intr = f.index[(f.ts >= entry_ts) & (f.ts <= exit_ts)]
    if len(intr):
        i0, i1 = int(intr[0]), int(intr[-1])
    else:                                  # trade shorter than one bar
        i0 = i1 = int(f.ts.searchsorted(entry_ts))
    return f.iloc[max(0, i0 - n): min(len(f), i1 + n + 1)]

def select_trades(strategy, epic):
    s = (trades[(trades.strategy == strategy) & (trades.epic == epic)]
         .sort_values("pnl_points", ascending=False)
         .reset_index(drop=True))
    top = s.head(N_TOP).assign(bucket="WIN")
    bot = s.tail(N_BOT)[::-1].assign(bucket="LOSS")        # worst first
    m   = len(s) // 2
    med = s.iloc[max(0, m - N_MED // 2): max(0, m - N_MED // 2) + N_MED].assign(bucket="MEDIAN")
    return pd.concat([top, med, bot], ignore_index=True), s

def plot_trade(t, rank, total):
    res = getattr(t, "resolution", None) or "M15"
    o   = load_ohlc(t.epic, res, t.entry_ts, t.exit_ts, CTX_BARS)
    fig, ax = plt.subplots(figsize=(13, 4.2))
    if len(o):
        ax.fill_between(o.t, o.low, o.high, color="0.82", lw=0, label=f"{res} H-L")
        ax.plot(o.t, o.close, color="0.35", lw=0.9, label=f"{res} close")
    e = dt.datetime.fromtimestamp(t.entry_ts, UTC)
    x = dt.datetime.fromtimestamp(t.exit_ts,  UTC)
    ax.axvspan(e, x, color="tab:blue", alpha=0.07)
    em  = "^" if t.direction == "BUY" else "v"
    win = t.pnl_points > 0
    ax.scatter([e], [t.entry_price], marker=em, s=140, color="black",
               zorder=5, label=f"entry {t.direction}")
    ax.scatter([x], [t.exit_price], marker="o", s=95,
               color=("tab:green" if win else "tab:red"), zorder=5, label="exit")
    ax.axhline(t.stop_loss,   ls=":", color="tab:red",   lw=1.1, label="SL")
    ax.axhline(t.take_profit, ls=":", color="tab:green", lw=1.1, label="TP")
    dur = (t.exit_ts - t.entry_ts) / 3600.0
    ax.set_title(
        f"[{t.bucket} {rank}/{total}]  {t.strategy}/{t.epic}  {t.direction}  |  "
        f"pnl={t.pnl_points:+.5g}  |  exit={t.exit_cat}  |  hold={dur:.1f}h  |  "
        f"entry {e:%Y-%m-%d %H:%M} UTC", fontsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.legend(fontsize=7, ncol=6, loc="upper left", framealpha=0.9)
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    plt.tight_layout()
    plt.show()
    plt.close(fig)

print("loaded", len(trades), "trades;", trades.entry_ts.min(), "->", trades.entry_ts.max())
'''


def section_code(strat, epic):
    return f'''\
strat, epic = "{strat}", "{epic}"
sel, full = select_trades(strat, epic)
print(f"{{strat}}/{{epic}}:  n={{len(full)}}  win%={{(full.pnl_points>0).mean():.1%}}  "
      f"pnl_sum={{full.pnl_points.sum():+.2f}}")
print("exit mix :", full.exit_cat.value_counts().to_dict())
print("selected :", sel.bucket.value_counts().to_dict(), " (", len(sel), "charts )")
for i, (_, tr) in enumerate(sel.iterrows(), 1):
    plot_trade(tr, i, len(sel))
'''


def main():
    cells = [
        new_markdown_cell(INTRO),
        new_code_cell(CFG),
        new_code_cell(HELP),
    ]
    for strat, epic in [("mean_reversion", "GOLD"),
                        ("momentum", "US500"),
                        ("orb", "DE40")]:
        cells.append(new_markdown_cell(section_md(strat, epic)))
        cells.append(new_code_cell(section_code(strat, epic)))

    nb = new_notebook(cells=cells)
    nb.metadata.kernelspec = {
        "display_name": "Python 3", "language": "python", "name": "python3"}

    print("executing notebook (150 charts, this takes a minute)...")
    NotebookClient(nb, timeout=900, kernel_name="python3").execute()
    nbformat.write(nb, OUT)
    print("written:", OUT)


if __name__ == "__main__":
    sys.exit(main())
