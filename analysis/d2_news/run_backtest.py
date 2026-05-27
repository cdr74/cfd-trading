"""D2 news-proximity backtest — signal -> trades (step 4a).

Authoritative spec: docs/STRATEGY_AUDIT.md Part 2 -> "D2 pre-registration"
§4/§5/§6, incl. the 2026-05-26 horizon-grid amendment ({1,3,5 h}).

Consumes d2_signals.parquet (armed event,instrument candidates from step 3) +
the M15 OHLC store; emits d2_run_<DATE>.parquet, one row per realised trade per
horizon, in the SAME schema audit/d3_analyze.py expects (so the gate math is
reused verbatim — see d2_analyze.py).

Fidelity basis (state before any result, per the project rule):
- Bars loaded via `cfd_trading.storage.repository.get_bars` — the SAME loader
  the live monitor/engine uses; no re-implementation, no resampling.
- Entry/exit on bar OPENs, bisect on bar-start ts — the D3/a3b convention.
- NO look-ahead: direction reads the reaction bar's close, entry is the NEXT
  bar's open (reaction bar fully closed first).
- Cost: pre-registered realised round-trip per class (Index 0.753 / FX 0.835 /
  Commodity 1.280 bps), deducted from gross to give net pnl_points; the same
  value is written as spread_at_entry so d3_analyze's pooled cost/hurdle
  recovers it exactly.
- Concurrency: signals collapsing onto the SAME (instrument, reaction bar) are
  one trade (deduped, max|z|) — simultaneous releases (e.g. the 12:30 USD
  cluster: NFP + Avg Hourly Earnings + Unemployment Rate) move one bar once;
  counting them as N trades would fabricate non-independent observations.

Rules NOT yet covered (intentional, documented): hold is N TRADING bars (not
wall-clock) so a rare weekend-spanning hold uses N market bars; overlapping
trades from DISTINCT release times on one instrument are kept (standard event
study) and their serial dependence is handled by the moving-block bootstrap.

Run:  .venv/bin/python analysis/d2_news/run_backtest.py
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from bisect import bisect_left
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
REPO = HERE.parents[1]
sys.path.insert(0, str((REPO / "src").resolve()))
from cfd_trading.storage.repository import get_bars  # noqa: E402

OHLC_DB = "/mnt/c/Users/chris/dev/trading-data/trading.db"
SIGNALS = HERE / "d2_signals.parquet"
OUT = HERE / f"d2_run_{dt.date.today().isoformat()}.parquet"

# Pre-registered horizon grid (amendment 2026-05-26): {1h,3h,5h} = M15 bars.
HORIZONS = {"1h": 4, "3h": 12, "5h": 20}
# Pre-registered realised round-trip cost per class, bps (STRATEGY_AUDIT §5).
COST_BPS = {"Index": 0.753, "FX": 0.835, "Commodity": 1.280}


def build() -> pd.DataFrame:
    sig = pd.read_parquet(SIGNALS)
    sig["release_ts"] = sig["release_utc"].to_numpy().astype("datetime64[s]").astype("int64")
    conn = sqlite3.connect(OHLC_DB)

    trades: list[dict] = []
    skip_no_reaction = skip_flat = skip_no_room = 0

    for epic, grp in sig.groupby("instrument"):
        bars = get_bars(conn, epic, "M15")
        if not bars:
            print(f"  [WARN] no M15 bars for {epic}; {len(grp)} signals skipped")
            continue
        bts = [b.ts for b in bars]
        n = len(bars)

        # Resolve each signal to its reaction-bar index, then collapse signals
        # landing on the same (epic, reaction bar) to one trade (max |z|).
        per_react: dict[int, dict] = {}
        for _, s in grp.iterrows():
            r_idx = bisect_left(bts, int(s["release_ts"]))
            if r_idx >= n:
                skip_no_reaction += 1
                continue
            prev = per_react.get(r_idx)
            if prev is None or abs(s["z"]) > abs(prev["z"]):
                per_react[r_idx] = s.to_dict() | {"r_idx": r_idx}

        for r_idx, s in per_react.items():
            react = bars[r_idx]
            move = react.close - react.open
            if move == 0:
                skip_flat += 1
                continue
            sign = 1 if move > 0 else -1
            direction = "BUY" if sign > 0 else "SELL"

            for hname, hbars in HORIZONS.items():
                entry_idx = r_idx + 1
                exit_idx = entry_idx + hbars
                if exit_idx >= n:
                    skip_no_room += 1
                    continue
                e_bar, x_bar = bars[entry_idx], bars[exit_idx]
                entry_mid = e_bar.open
                gross_pts = sign * (x_bar.open - e_bar.open)
                cost_pts = (COST_BPS[s["instr_class"]] / 10_000) * entry_mid
                trades.append({
                    # --- d3_analyze schema ---
                    "entry_ts": e_bar.ts,
                    "exit_ts": x_bar.ts,
                    "pnl_points": gross_pts - cost_pts,      # NET of round-trip
                    "entry_mid": entry_mid,
                    "spread_at_entry": cost_pts,             # -> recovers class cost
                    "epic": epic,
                    "direction": direction,
                    "exit_reason": f"time_exit:{hname}",
                    # --- D2 metadata ---
                    "horizon": hname,
                    "z": s["z"],
                    "instr_class": s["instr_class"],
                    "name": s["name"],
                    "split": s["split"],
                    "release_ts": int(s["release_ts"]),
                    "hold_secs": x_bar.ts - e_bar.ts,
                })

    df = pd.DataFrame(trades)
    print(f"signals in: {len(sig)} | skips: no_reaction_bar={skip_no_reaction} "
          f"flat_reaction={skip_flat} no_room_for_horizon={skip_no_room}")
    return df


def main() -> int:
    df = build()
    df.to_parquet(OUT, index=False)
    print(f"\nwrote {OUT.name}: {len(df)} trade-rows ({len(df)//len(HORIZONS)} per horizon approx)\n")
    for hname in HORIZONS:
        h = df[df.horizon == hname]
        for split in ("IS", "OOS"):
            sub = h[h.split == split]
            if len(sub):
                net = (sub.pnl_points / sub.entry_mid * 1e4)
                print(f"  {hname:3s} {split:3s}: N={len(sub):4d}  "
                      f"mean_net={net.mean():+.3f} bps  win={ (sub.pnl_points>0).mean():.0%}")
    # weekend/gap diagnostic
    for hname, hb in HORIZONS.items():
        h = df[df.horizon == hname]
        if len(h):
            expect = hb * 15 * 60
            gap = (h.hold_secs > expect * 1.5).mean()
            print(f"  [{hname}] holds exceeding 1.5x expected wall-clock (gap-spanning): {gap:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
