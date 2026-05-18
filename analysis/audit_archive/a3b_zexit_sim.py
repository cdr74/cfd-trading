"""A3b — MR z-based invalidation exit: read-only sized simulation.

Phase A3b primary deliverable (AUDIT_PLAN.md §A3b). READ-ONLY. No engine or
monitor changes — this only replays history and re-prices counterfactual exits.

Design (locked with the user 2026-05-17):
  - Invalidation reference: ENTRY-RELATIVE z-delta. A mean_reversion position
    is invalidated when the z-score extends >= X further from the midline than
    it was at entry, i.e. the reversion thesis is broken:
        BUY  (entry_z <= -2): offside = entry_z - z      (z drops further)
        SELL (entry_z >= +2): offside = z - entry_z      (z rises further)
    fire when offside >= X for K consecutive bars.
  - Hard-stop role: INSIDE. The 1.5% hard stop / TP / z-midline / time exit all
    remain. The invalidation can ONLY pull a trade's exit *earlier* than the
    recorded exit_ts; if it would trigger at/after the recorded exit, the trade
    is unchanged. (Additive earlier bail; hard stop stays the backstop.)
  - Metric: R-multiple across all 8 instruments (pnl / risk_pts), plus raw
    points for the 4 index cells for continuity with RESULTS.md and the
    +4,260-pt persistence lower bound.

Fidelity basis:
  - z reconstructed by replaying the SAME M15 bars the engine used
    (storage.repository.get_bars from trading.db) through the SAME
    MeanReversionSignalState the live monitor + backtest engine use. z is the
    20-bar _zscore and is independent of the ADX/ATR gates, so the trajectory
    is exact regardless of gate state.
  - New exit priced exactly as engine._close does a signal-exit: fill at the
    trigger bar's CLOSE, half = spread_at_entry/2, pnl vs the recorded
    entry_price (which already embeds the entry half-spread). Formulas mirror
    backtest/engine.py _exit_fill / _pnl (lines ~228/233) verbatim.

Usage:
    BACKTEST_DB_PATH unused; DB path is the standard Windows location.
    cd cfd-trading && source .venv/bin/activate
    python /home/chris/dev/trading/audit/a3b_zexit_sim.py
"""
from __future__ import annotations

import sqlite3
import sys
from bisect import bisect_left, bisect_right
from importlib import import_module
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse the audit's exact session boundaries (do NOT re-derive them).
sys.path.insert(0, str(Path(__file__).parent))
_a2 = import_module("a2_slicing")
SESSIONS = _a2.SESSIONS
_session_for_hour = _a2._session_for_hour

from cfd_trading.storage.repository import OHLCBar, get_bars  # noqa: E402
from cfd_trading.strategy.signal_engine import MeanReversionSignalState  # noqa: E402

DB_PATH = "/mnt/c/Users/chris/dev/trading-data/trading.db"
PARQUET = Path(__file__).parent / "trades_M15.parquet"
OUT_SWEEP = Path(__file__).parent / "a3b_zexit_sweep.csv"
OUT_BYCELL = Path(__file__).parent / "a3b_zexit_by_cell.csv"
OUT_PRICE = Path(__file__).parent / "a3b_price_persist_sweep.csv"
OUT_PRICE_CELL = Path(__file__).parent / "a3b_price_persist_by_cell.csv"

X_GRID = [0.5, 1.0, 1.5]   # entry-relative z-extension thresholds
K_GRID = [1, 2, 3]         # consecutive-bar persistence (shared by both arms)
R_THRESH = 0.5             # price-persistence: adverse excursion in R (the
                           # +4,260-pt 2026-05-16 lower-bound rule, in-harness)
WARMUP_BARS = 60           # >= 20 for the z deque + ADX warm-up headroom
INDEX_EPICS = {"DE40", "GOLD", "UK100", "US500"}

# --- engine exit-pricing convention (mirrors backtest/engine.py) -------------
def _exit_fill(direction: str, bar_close: float, half: float) -> float:
    return bar_close - half if direction == "BUY" else bar_close + half


def _pnl(direction: str, entry: float, exit_price: float) -> float:
    return round(exit_price - entry if direction == "BUY" else entry - exit_price, 6)


def _load_bars(conn: sqlite3.Connection, epic: str) -> tuple[list[OHLCBar], list[int]]:
    bars = get_bars(conn, epic, "M15")
    return bars, [b.ts for b in bars]


def _ztrajectory(bars: list[OHLCBar], start_idx: int, end_idx: int) -> list[float | None]:
    """Replay bars[start_idx..end_idx] through a fresh MR signal state.

    Returns z (state._last_z) aligned to each replayed bar index in
    [start_idx, end_idx]. A fresh state per trade mirrors the engine's
    per-position signal state (no cross-trade leakage).
    """
    state = MeanReversionSignalState()
    out: list[float | None] = []
    for i in range(start_idx, end_idx + 1):
        state.update(bars[i])
        out.append(state._last_z)
    return out


def simulate() -> None:
    df = pd.read_parquet(PARQUET)
    mr = df[df["strategy"] == "mean_reversion"].copy().reset_index(drop=True)
    mr["entry_dt"] = pd.to_datetime(mr["entry_ts"], unit="s", utc=True)
    mr["session"] = mr["entry_dt"].dt.hour.apply(_session_for_hour)
    mr["rec_R"] = mr["pnl_points"] / mr["risk_pts"]
    print(f"MR trades: {len(mr)}  instruments: {sorted(mr['epic'].unique())}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = None

    # z-arm: per (X,K) the counterfactual outcome per trade.
    results: dict[tuple[float, int], list[dict]] = {
        (x, k): [] for x in X_GRID for k in K_GRID
    }
    # price-arm: per (0.5R, K) the same, for the +4,260 lower-bound rule.
    price_results: dict[tuple[float, int], list[dict]] = {
        (R_THRESH, k): [] for k in K_GRID
    }
    # diag: one row per recorded LOSER — z reach vs price reach.
    diag: list[dict] = []
    entry_z_ok = 0          # |entry_z| >= 1.9 reconstruction check
    entry_z_total = 0
    no_bar_cover = 0

    for epic, grp in mr.groupby("epic"):
        bars, bts = _load_bars(conn, epic)
        if not bars:
            print(f"  [WARN] no M15 bars for {epic} — {len(grp)} trades skipped")
            no_bar_cover += len(grp)
            continue
        cover_lo, cover_hi = bts[0], bts[-1]
        for _, t in grp.iterrows():
            ets, xts = int(t["entry_ts"]), int(t["exit_ts"])
            if ets < cover_lo or xts > cover_hi:
                no_bar_cover += 1
                continue
            # Fill bar = first bar with ts >= entry_ts; signal bar = the one before.
            fill_idx = bisect_left(bts, ets)
            if fill_idx >= len(bts) or bts[fill_idx] != ets:
                # entry_ts not exactly on a bar grid point — align to nearest <=.
                fill_idx = bisect_right(bts, ets) - 1
            sig_idx = max(0, fill_idx - 1)
            start_idx = max(0, sig_idx - WARMUP_BARS)
            exit_idx = bisect_right(bts, xts) - 1
            if exit_idx <= fill_idx:
                # Trade closed within one bar of entry — no room for an
                # earlier invalidation. Carry recorded outcome unchanged.
                for key in results:
                    results[key].append(_unchanged(t))
                for key in price_results:
                    price_results[key].append(_unchanged(t))
                continue

            ztraj = _ztrajectory(bars, start_idx, exit_idx)
            # index within ztraj: bar i -> ztraj[i - start_idx]
            entry_z = ztraj[sig_idx - start_idx]
            entry_z_total += 1
            direction = t["direction"]
            if entry_z is not None and (
                (direction == "BUY" and entry_z <= -1.9)
                or (direction == "SELL" and entry_z >= 1.9)
            ):
                entry_z_ok += 1
            if entry_z is None:
                # Could not reconstruct entry z (insufficient warm-up) — keep
                # recorded outcome; counts toward the fidelity denominator.
                for key in results:
                    results[key].append(_unchanged(t))
                for key in price_results:
                    price_results[key].append(_unchanged(t))
                continue

            half = float(t["spread_at_entry"]) / 2.0
            rec_pnl = float(t["pnl_points"])
            risk = float(t["risk_pts"])
            # Post-entry bars: fill_idx+1 .. exit_idx (offside measured on
            # closed bars strictly after the fill bar).
            for x in X_GRID:
                # For each K, the first bar where offside>=x has held k
                # consecutive bars. Computed in one pass over the path.
                first_trig: dict[int, int | None] = {k: None for k in K_GRID}
                run = 0
                for bi in range(fill_idx + 1, exit_idx + 1):
                    z = ztraj[bi - start_idx]
                    if z is None:
                        run = 0
                        continue
                    offside = (entry_z - z) if direction == "BUY" else (z - entry_z)
                    if offside >= x:
                        run += 1
                        for k in K_GRID:
                            if first_trig[k] is None and run >= k:
                                first_trig[k] = bi
                    else:
                        run = 0
                for k in K_GRID:
                    bi = first_trig[k]
                    if bi is None or bts[bi] >= xts:
                        results[(x, k)].append(_unchanged(t))
                        continue
                    nb = bars[bi]
                    nfill = _exit_fill(direction, nb.close, half)
                    npnl = _pnl(direction, float(t["entry_price"]), nfill)
                    results[(x, k)].append({
                        "epic": epic, "session": t["session"],
                        "rec_pnl": rec_pnl, "new_pnl": npnl,
                        "rec_R": rec_pnl / risk, "new_R": npnl / risk,
                        "changed": True,
                        "mins_saved": (xts - nb.ts) / 60.0,
                        "false_bail": rec_pnl > 0 and npnl < rec_pnl,
                        "good_bail": rec_pnl <= 0 and npnl > rec_pnl,
                    })

            # --- price-persistence arm (the +4,260 rule, in-harness) ---------
            # offside_R = adverse excursion at bar close, in R. Trigger when
            # >= R_THRESH for K consecutive bars; same earlier-only semantics.
            ep = float(t["entry_price"])
            ptrig: dict[int, int | None] = {k: None for k in K_GRID}
            max_adv_R = 0.0
            prun = 0
            for bi in range(fill_idx + 1, exit_idx + 1):
                adv_R = -_pnl(direction, ep, bars[bi].close) / risk
                if adv_R > max_adv_R:
                    max_adv_R = adv_R
                if adv_R >= R_THRESH:
                    prun += 1
                    for k in K_GRID:
                        if ptrig[k] is None and prun >= k:
                            ptrig[k] = bi
                else:
                    prun = 0
            for k in K_GRID:
                bi = ptrig[k]
                if bi is None or bts[bi] >= xts:
                    price_results[(R_THRESH, k)].append(_unchanged(t))
                    continue
                nb = bars[bi]
                npnl = _pnl(direction, ep, _exit_fill(direction, nb.close, half))
                price_results[(R_THRESH, k)].append({
                    "epic": epic, "session": t["session"],
                    "rec_pnl": rec_pnl, "new_pnl": npnl,
                    "rec_R": rec_pnl / risk, "new_R": npnl / risk,
                    "changed": True,
                    "mins_saved": (xts - nb.ts) / 60.0,
                    "false_bail": rec_pnl > 0 and npnl < rec_pnl,
                    "good_bail": rec_pnl <= 0 and npnl > rec_pnl,
                })

            # --- diagnostic: recorded LOSERS — z reach vs price reach --------
            if rec_pnl < 0:
                max_z_off = 0.0
                for bi in range(fill_idx + 1, exit_idx + 1):
                    z = ztraj[bi - start_idx]
                    if z is None:
                        continue
                    o = (entry_z - z) if direction == "BUY" else (z - entry_z)
                    if o > max_z_off:
                        max_z_off = o
                diag.append({
                    "epic": epic, "exit_reason": t["exit_reason"],
                    "rec_R": rec_pnl / risk,
                    "max_z_off": max_z_off,
                    "max_adv_R": max_adv_R,
                    "z_off_ge_1": max_z_off >= 1.0,
                    "price_persist_k3": ptrig[3] is not None and bts[ptrig[3]] < xts,
                })
    conn.close()

    rate = entry_z_ok / entry_z_total if entry_z_total else 0.0
    print(f"\nz-reconstruction fidelity: |entry_z|>=1.9 for "
          f"{entry_z_ok}/{entry_z_total} = {rate:.1%} of MR entries "
          f"(expect ~100% since MR enters at |z|>=2.0)")
    print(f"trades w/o M15 coverage (excluded): {no_bar_cover}")
    if rate < 0.90:
        print("  [WARN] low reconstruction match — alignment needs review "
              "before trusting the sweep.")

    _emit(results, price_results, diag, rate, no_bar_cover)


def _unchanged(t) -> dict:
    rp = float(t["pnl_points"])
    rk = float(t["risk_pts"])
    return {
        "epic": t["epic"], "session": t["session"],
        "rec_pnl": rp, "new_pnl": rp, "rec_R": rp / rk, "new_R": rp / rk,
        "changed": False, "mins_saved": 0.0,
        "false_bail": False, "good_bail": False,
    }


def _sweep_df(results: dict, p1: str, p2: str) -> pd.DataFrame:
    """Summarize a {(p1,p2): [per-trade dicts]} arm into a sweep table."""
    rows = []
    for (a, b), recs in sorted(results.items()):
        r = pd.DataFrame(recs)
        n = len(r)
        chg = int(r["changed"].sum())
        idx = r["epic"].isin(INDEX_EPICS)
        ch = r[r["changed"]]
        rows.append({
            p1: a, p2: b, "n": n, "n_changed": chg,
            "pct_changed": round(100 * chg / n, 1),
            "net_dR_all8": round(r["new_R"].sum() - r["rec_R"].sum(), 1),
            "net_dpts_idx": round(
                r.loc[idx, "new_pnl"].sum() - r.loc[idx, "rec_pnl"].sum(), 1),
            "net_dpts_all": round(r["new_pnl"].sum() - r["rec_pnl"].sum(), 1),
            "false_bails": int(r["false_bail"].sum()),
            "good_bails": int(r["good_bail"].sum()),
            "false_bail_pct_of_changed": (
                round(100 * r["false_bail"].sum() / chg, 1) if chg else 0.0),
            "median_mins_saved": (
                round(ch["mins_saved"].median(), 1) if len(ch) else 0.0),
            "win_pct_before": round(100 * (r["rec_pnl"] > 0).mean(), 1),
            "win_pct_after": round(100 * (r["new_pnl"] > 0).mean(), 1),
        })
    return pd.DataFrame(rows)


def _by_cell(recs: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(recs).groupby(["epic", "session"]).apply(
        lambda g: pd.Series({
            "n": len(g), "n_changed": int(g["changed"].sum()),
            "net_dR": round(g["new_R"].sum() - g["rec_R"].sum(), 2),
            "net_dpts": round(g["new_pnl"].sum() - g["rec_pnl"].sum(), 1),
            "false_bails": int(g["false_bail"].sum()),
            "good_bails": int(g["good_bail"].sum()),
        }), include_groups=False).reset_index()


def _emit(results: dict, price_results: dict, diag: list[dict],
          fidelity: float, no_cover: int) -> None:
    z_sweep = _sweep_df(results, "X", "K")
    z_sweep.to_csv(OUT_SWEEP, index=False)
    print("\n=== Z-ARM  X x K sweep (entry-relative z-extension) ===")
    print("(net_dR over all 8; net_dpts_idx vs the +4,260-pt price LB)")
    print(z_sweep.to_string(index=False))

    cand = z_sweep[z_sweep["false_bail_pct_of_changed"] < 40.0]
    best = (cand if len(cand) else z_sweep).sort_values(
        "net_dR_all8", ascending=False).iloc[0]
    bx, bk = float(best["X"]), int(best["K"])
    cell = _by_cell(results[(bx, bk)])
    cell.insert(0, "X", bx)
    cell.insert(1, "K", bk)
    cell.to_csv(OUT_BYCELL, index=False)
    print(f"\nZ-ARM best (max ΔR, false-bail<40%): X={bx}, K={bk} — "
          f"per-cell written to {OUT_BYCELL.name}")

    p_sweep = _sweep_df(price_results, "R", "K")
    p_sweep.to_csv(OUT_PRICE, index=False)
    print("\n=== PRICE-ARM  0.5R x K sweep (the +4,260 rule, in-harness) ===")
    print(p_sweep.to_string(index=False))
    pbest = p_sweep.sort_values("net_dR_all8", ascending=False).iloc[0]
    pk = int(pbest["K"])
    pcell = _by_cell(price_results[(R_THRESH, pk)])
    pcell.insert(0, "R", R_THRESH)
    pcell.insert(1, "K", pk)
    pcell.to_csv(OUT_PRICE_CELL, index=False)
    print(f"PRICE-ARM best: 0.5R, K={pk} — per-cell written to "
          f"{OUT_PRICE_CELL.name}")

    # --- the mechanism cross-tab: why z fails where price works ----------
    d = pd.DataFrame(diag)
    print("\n=== DIAGNOSTIC — recorded MR LOSERS (n={}) ===".format(len(d)))
    print("max z-extension reached before exit (quantiles): "
          f"p50={d['max_z_off'].median():.2f}  "
          f"p90={d['max_z_off'].quantile(0.9):.2f}  "
          f"p99={d['max_z_off'].quantile(0.99):.2f}")
    print(f"losers ever reaching z-extension >=1.0 : "
          f"{d['z_off_ge_1'].mean():.1%}")
    print(f"losers triggering price-persist 0.5R/K3: "
          f"{d['price_persist_k3'].mean():.1%}")
    print(f"losers w/ price-persist BUT never z>=1.0: "
          f"{((~d['z_off_ge_1']) & d['price_persist_k3']).mean():.1%}  "
          "<- the structural gap")
    hs = d[d["exit_reason"].str.contains("Hard stop", na=False)]
    if len(hs):
        print(f"\nHard-stop losers (n={len(hs)}): "
              f"z>=1.0 in {hs['z_off_ge_1'].mean():.1%}, "
              f"price-persist in {hs['price_persist_k3'].mean():.1%}, "
              f"median max_adv_R={hs['max_adv_R'].median():.2f}")

    print(f"\nWrote {OUT_SWEEP.name}, {OUT_BYCELL.name}, {OUT_PRICE.name}, "
          f"{OUT_PRICE_CELL.name}. fidelity={fidelity:.1%}, "
          f"excluded(no-cover)={no_cover}")


if __name__ == "__main__":
    simulate()
