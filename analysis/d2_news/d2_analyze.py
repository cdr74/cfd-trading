"""D2 news-proximity — gate analysis across the {1,3,5 h} horizon grid (step 4b).

Reuses audit/d3_analyze.py's statistics VERBATIM (deflated Sharpe, moving-block
bootstrap CI, pooled cost/hurdle, net bps) so the D2 gates are provably the same
machinery that killed D3 — the strongest fidelity claim available. Only two
module constants are overridden: the trial count K (deflated for the 3 added
horizon trials) and the diagnostic UNIVERSE (the 7 D2 instruments).

Pre-registered gates (STRATEGY_AUDIT §6 + 2026-05-26 amendment):
  (a) DSR P < 0.05, deflated for K trials   (b) 95% bootstrap CI lower > 0
  (c) net bps/trade >= 3x cost hurdle        (d) >= ~100 pooled OOS trades
  (e) robustness: {1,3,5h} profile positive & smooth, no lone spike

Run:  .venv/bin/python analysis/d2_news/d2_analyze.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
REPO = HERE.parents[1]
sys.path.insert(0, str((REPO / "audit").resolve()))
import d3_analyze as d3  # noqa: E402

# --- Trial bookkeeping: cumulative audit trials (121 after D3) + 3 D2 horizons.
d3.K_TRIALS = 124
d3.UNIVERSE = ("US500", "DE40", "GOLD", "EURUSD", "GBPUSD", "USDJPY", "EURGBP")

RUN = HERE / f"d2_run_{dt.date.today().isoformat()}.parquet"
HORIZONS = ("1h", "3h", "5h")


def fmt_gate(g: dict) -> str:
    ci = g["ci"]
    return (f"N={g['n']:4d}  SR={g['sr']:+.4f}  DSR_P={g['dsr']:.3f}"
            f"  CI=[{ci[0]:+.3f},{ci[1]:+.3f}]  net={g['net']:+.3f}  hurdle={g['hurdle']:.3f}")


def main() -> int:
    df = pd.read_parquet(RUN)
    print(f"D2 GATE ANALYSIS — {RUN.name}  (K_trials={d3.K_TRIALS}, DSR pass at P<0.05 i.e. dsr>=0.95)\n")

    profile = {}
    for h in HORIZONS:
        hdf = df[df.horizon == h]
        is_df, oos_df = d3.split_window(hdf)
        gi = d3.gate_table(is_df, f"{h}/IS")
        go = d3.gate_table(oos_df, f"{h}/OOS")
        profile[h] = go
        print(f"── horizon {h} " + "─" * 50)
        print(f"   IS : {fmt_gate(gi)}")
        print(f"   OOS: {fmt_gate(go)}")
        flags = []
        flags.append(f"DSR{'✓' if go['pass_dsr'] else '✗'}")
        flags.append(f"CI{'✓' if go['pass_ci'] else '✗'}")
        flags.append(f"cost{'✓' if go['pass_cost'] else '✗'}")
        flags.append(f"min-N{'✓' if go['passed_min'] else '✗'}")
        print(f"   OOS gates: {'  '.join(flags)}\n")

    # ---- (e) robustness verdict across the grid (OOS) ----
    nets = [profile[h]["net"] for h in HORIZONS]
    srs = [profile[h]["sr"] for h in HORIZONS]
    all_pos = all(n > 0 for n in nets)
    signs = {("+" if s > 0 else "-") for s in srs}
    smooth = len(signs) == 1  # consistent SR sign across the grid
    any_gate_full = any(profile[h]["pass_dsr"] and profile[h]["pass_ci"]
                        and profile[h]["pass_cost"] and profile[h]["passed_min"]
                        for h in HORIZONS)

    print("── robustness profile (OOS) " + "─" * 36)
    print(f"   net bps by horizon {HORIZONS}: {[round(n,3) for n in nets]}")
    print(f"   SR  by horizon      {HORIZONS}: {[round(s,4) for s in srs]}")
    print(f"   all horizons net>0 : {all_pos}")
    print(f"   consistent SR sign : {smooth}  (signs seen: {signs})")
    print(f"   any horizon clears ALL of (a)-(d): {any_gate_full}")

    print("\n── VERDICT " + "─" * 52)
    if any_gate_full and all_pos and smooth:
        print("   PROVISIONAL PASS — manual pre-kill introspection required before any claim.")
    else:
        reasons = []
        if not any_gate_full:
            reasons.append("no horizon clears gates (a)-(d)")
        if not all_pos:
            reasons.append("OOS net not positive across all horizons")
        if not smooth:
            reasons.append("SR sign inconsistent across grid (spike, not plateau)")
        print("   FAIL signature — " + "; ".join(reasons) + ".")
        print("   Pre-kill introspection still required before the formal KILL is recorded.")

    # ---- per-instrument diagnostic (OOS, representative horizon = 3h) ----
    print("\n── per-instrument diagnostic (OOS, 3h; diagnostic only, not gated) " + "─" * 5)
    _, oos3 = d3.split_window(df[df.horizon == "3h"])
    with pd.option_context("display.width", 120):
        print(d3.per_instrument_diagnostic(oos3).to_string(index=False))

    # ---- distribution + cost reality (3h OOS) ----
    print("\n── pnl distribution (3h OOS, net bps) " + "─" * 26)
    dist = d3.pnl_distribution(oos3)
    print(f"   mean={dist['mean']:+.3f} std={dist['std']:.2f} skew={dist['skew']:+.2f} "
          f"kurt_excess={dist['kurt_excess']:+.2f}")
    print(f"   q05={dist['q05']:+.2f} q50={dist['q50']:+.2f} q95={dist['q95']:+.2f} "
          f"min={dist['min']:+.2f} max={dist['max']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
