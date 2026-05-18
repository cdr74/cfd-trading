"""A3b §8 — Index MR stop-cap probe: read-only sized simulation.

Follow-on to A3B_FINDINGS.md. READ-ONLY. No engine/monitor changes.

A3b located 100% of MR's loss in two tails; the dominant one is **76 hard
stops (−13,247 pts, ~all index)** — the flat 1.5% stop is far too wide for
index point-values. This probe replaces the 1.5% MR stop with tighter
candidates and re-prices, fidelity-true, to size the saved tail vs the cost
of stopping good trades out early.

Design (locked with the user 2026-05-17):
  - Stop geometries swept vs the flat 1.5%:
      fixed-% in {0.3, 0.5, 0.75, 1.0}   (intuitive 'is 1.5% mis-sized')
      ATR14@entry × m in {1.5, 2, 2.5, 3} (volatility-adaptive)
  - TP / risk: PRESERVE R:R (engine-faithful). The engine sets
    take_profit = entry ± rr_ratio × risk at entry; with a tighter stop the
    new risk distance d gives new_tp = entry ± 2.0·d. R is normalized to the
    NEW risk distance. ΔR and Δpts both reported.
  - Scope: applied to all 8 (proves FX unharmed) but headlined on the 4
    indices; an 'index-only application' net (tighter stop ONLY on
    DE40/GOLD/UK100/US500, others keep recorded outcome) is the realistic
    deploy form.

Fidelity basis (mirrors backtest/engine.py exactly):
  - **Bar-close stop model.** The engine evaluates the hard stop on
    bid/offer = bar.close ∓ half (NOT intrabar high/low) and fills via
    _close at the trigger bar's CLOSE. Replicated here verbatim
    (engine.py L150/L166, _exit_fill L228, _pnl L233).
  - ATR14@entry from the same MeanReversionSignalState the engine uses
    (state.atr = Wilder ATR via _ADXState), snapshotted at the entry bar.
  - The tighter stop REPLACES the 1.5% stop and the rr-scaled TP replaces the
    old TP. Earliest-wins among {new stop, new TP, the recorded
    z-midline/time/session/EoD exit}. A trade's lifetime never exceeds its
    recorded lifetime (no look-ahead): if recorded reason was z-midline/time/
    session and neither new level fires within it, the recorded exit stands;
    if recorded reason was the old Hard stop / Take profit and neither new
    level fires, the trade is carried to the recorded exit bar and closed at
    its close (conservative — slightly understates the benefit).

Additive (2026-05-18): also emits per-trade ATR×2.5 rows
(`a3b_stopcap_atrx25_trades.csv`) for the MR Option-1 step-2 FX+indices slice
(A3B_FINDINGS §11) — §8 sweep/by-cell outputs and logic are unchanged.

Usage:
    cd cfd-trading && source .venv/bin/activate
    python /home/chris/dev/trading/audit/a3b_stopcap_sim.py
"""
from __future__ import annotations

import sqlite3
import sys
from bisect import bisect_left, bisect_right
from importlib import import_module
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
_a2 = import_module("a2_slicing")
_session_for_hour = _a2._session_for_hour

from cfd_trading.storage.repository import OHLCBar, get_bars  # noqa: E402
from cfd_trading.strategy.signal_engine import MeanReversionSignalState  # noqa: E402

DB_PATH = "/mnt/c/Users/chris/dev/trading-data/trading.db"
PARQUET = Path(__file__).parent / "trades_M15.parquet"
OUT_SWEEP = Path(__file__).parent / "a3b_stopcap_sweep.csv"
OUT_BYCELL = Path(__file__).parent / "a3b_stopcap_by_cell.csv"
# Additive (2026-05-18, MR Option-1 step 2): per-trade ATR×2.5 rows so the
# single global candidate can be sliced to the FX+indices universe WITHOUT
# per-instrument fitting. Does not alter the §8 sweep/by-cell outputs or logic.
OUT_ATRX25 = Path(__file__).parent / "a3b_stopcap_atrx25_trades.csv"

FIXED_PCT = [0.3, 0.5, 0.75, 1.0]      # tighter than the 1.5% default
ATR_MULT = [1.5, 2.0, 2.5, 3.0]
RR_RATIO = 2.0                          # MR yaml take_profit.min_rr_ratio
WARMUP_BARS = 60
INDEX_EPICS = {"DE40", "GOLD", "UK100", "US500"}
# Recorded exit families whose timing is independent of the stop geometry.
_KEEP_REASONS = ("Z-score midline", "Time exit", "Session close", "End of data")


def _exit_fill(direction: str, bar_close: float, half: float) -> float:
    return bar_close - half if direction == "BUY" else bar_close + half


def _pnl(direction: str, entry: float, exit_price: float) -> float:
    return round(exit_price - entry if direction == "BUY" else entry - exit_price, 6)


def _keep(reason: str) -> bool:
    return any(reason.startswith(r) for r in _KEEP_REASONS)


def _candidates() -> list[tuple[str, str, float]]:
    """(label, kind, param) — kind in {'pct','atr'}."""
    out = [("flat_1.5pct", "pct", 1.5)]
    out += [(f"fix_{p}pct", "pct", p) for p in FIXED_PCT]
    out += [(f"atr_x{m}", "atr", m) for m in ATR_MULT]
    return out


def simulate() -> None:
    df = pd.read_parquet(PARQUET)
    mr = df[df["strategy"] == "mean_reversion"].copy().reset_index(drop=True)
    mr["session"] = pd.to_datetime(mr["entry_ts"], unit="s", utc=True).dt.hour.apply(
        _session_for_hour)
    print(f"MR trades: {len(mr)}")

    conn = sqlite3.connect(DB_PATH)
    cands = _candidates()
    results: dict[str, list[dict]] = {c[0]: [] for c in cands}
    atr_ok = atr_tot = no_cover = 0

    for epic, grp in mr.groupby("epic"):
        bars = get_bars(conn, epic, "M15")
        if not bars:
            no_cover += len(grp)
            continue
        bts = [b.ts for b in bars]
        lo, hi = bts[0], bts[-1]
        for _, t in grp.iterrows():
            ets, xts = int(t["entry_ts"]), int(t["exit_ts"])
            direction = t["direction"]
            ep = float(t["entry_price"])
            half = float(t["spread_at_entry"]) / 2.0
            rec_pnl = float(t["pnl_points"])
            reason = str(t["exit_reason"])
            if ets < lo or xts > hi:
                no_cover += 1
                for c in cands:
                    results[c[0]].append(_row(t, rec_pnl, rec_pnl, ep, direction,
                                               False, reason))
                continue
            fill_idx = bisect_left(bts, ets)
            if fill_idx >= len(bts) or bts[fill_idx] != ets:
                fill_idx = bisect_right(bts, ets) - 1
            exit_idx = bisect_right(bts, xts) - 1
            start_idx = max(0, fill_idx - 1 - WARMUP_BARS)

            # Reconstruct ATR14 at the entry bar via the engine's MR state.
            state = MeanReversionSignalState()
            for j in range(start_idx, fill_idx + 1):
                state.update(bars[j])
            entry_atr = state.atr
            atr_tot += 1
            if entry_atr is not None and entry_atr > 0:
                atr_ok += 1

            for label, kind, prm in cands:
                if kind == "pct":
                    d = prm / 100.0 * ep
                else:
                    if entry_atr is None or entry_atr <= 0:
                        # ATR not reconstructable — carry recorded outcome.
                        results[label].append(
                            _row(t, rec_pnl, rec_pnl, ep, direction, False, reason))
                        continue
                    d = prm * entry_atr
                if direction == "BUY":
                    s_lvl, tp_lvl = ep - d, ep + RR_RATIO * d
                else:
                    s_lvl, tp_lvl = ep + d, ep - RR_RATIO * d

                trig_bi = None
                trig_reason = None
                for bi in range(fill_idx + 1, exit_idx + 1):
                    c = bars[bi].close
                    bid, offer = c - half, c + half
                    if direction == "BUY":
                        if bid <= s_lvl:
                            trig_bi, trig_reason = bi, "stopcap"
                            break
                        if bid >= tp_lvl:
                            trig_bi, trig_reason = bi, "tp_rr"
                            break
                    else:
                        if offer >= s_lvl:
                            trig_bi, trig_reason = bi, "stopcap"
                            break
                        if offer <= tp_lvl:
                            trig_bi, trig_reason = bi, "tp_rr"
                            break

                if trig_bi is not None and bts[trig_bi] < xts:
                    nb = bars[trig_bi]
                    npnl = _pnl(direction, ep, _exit_fill(direction, nb.close, half))
                    results[label].append(
                        _row(t, rec_pnl, npnl, ep, direction, True, trig_reason, d))
                elif _keep(reason):
                    # Recorded z/time/session exit stands (stop never triggered).
                    results[label].append(
                        _row(t, rec_pnl, rec_pnl, ep, direction, False, reason, d))
                else:
                    # Recorded reason was old Hard stop / TP: neither new level
                    # fired within the known lifetime — carry to recorded exit
                    # bar, close at its close (conservative).
                    nb = bars[exit_idx]
                    npnl = _pnl(direction, ep, _exit_fill(direction, nb.close, half))
                    results[label].append(
                        _row(t, rec_pnl, npnl, ep, direction, True, "carried", d))
    conn.close()

    rate = atr_ok / atr_tot if atr_tot else 0.0
    print(f"ATR14@entry reconstructed for {atr_ok}/{atr_tot} = {rate:.1%} of "
          f"MR entries; excluded(no-cover)={no_cover}")
    _emit(results)


def _row(t, rec_pnl, new_pnl, ep, direction, changed, reason, d=None):
    # R normalized to the NEW risk distance d (preserve-R:R decision); for the
    # flat baseline / carried rows d falls back to recorded risk_pts.
    risk = d if (d is not None and d > 0) else float(t["risk_pts"])
    return {
        "epic": t["epic"], "session": t["session"],
        "is_index": t["epic"] in INDEX_EPICS,
        "rec_reason": str(t["exit_reason"]),
        "rec_pnl": rec_pnl, "new_pnl": new_pnl,
        "rec_R": rec_pnl / risk, "new_R": new_pnl / risk,
        "changed": changed, "new_reason": reason,
        "win_to_loss": rec_pnl > 0 and new_pnl <= 0,
        "loser_saved": rec_pnl < 0 and new_pnl > rec_pnl,
        "was_hardstop": str(t["exit_reason"]).startswith("Hard stop"),
    }


def _emit(results: dict) -> None:
    rows = []
    for label, recs in results.items():
        r = pd.DataFrame(recs)
        idx = r["is_index"]
        hs = r[r["was_hardstop"]]
        # index-only application: non-index trades keep recorded outcome.
        io_new = r["new_pnl"].where(idx, r["rec_pnl"])
        rows.append({
            "candidate": label, "n_changed": int(r["changed"].sum()),
            "net_dpts_idx": round(r.loc[idx, "new_pnl"].sum()
                                   - r.loc[idx, "rec_pnl"].sum(), 1),
            "net_dR_idx": round(r.loc[idx, "new_R"].sum()
                                 - r.loc[idx, "rec_R"].sum(), 1),
            "net_dpts_all8": round(r["new_pnl"].sum() - r["rec_pnl"].sum(), 1),
            "net_dpts_idxonly_appl": round(io_new.sum() - r["rec_pnl"].sum(), 1),
            "win_to_loss": int(r["win_to_loss"].sum()),
            "loser_saved": int(r["loser_saved"].sum()),
            "hardstop_dpts": round(hs["new_pnl"].sum() - hs["rec_pnl"].sum(), 1),
            "idx_net_pts_after": round(r.loc[idx, "new_pnl"].sum(), 1),
        })
    sweep = pd.DataFrame(rows)
    sweep.to_csv(OUT_SWEEP, index=False)
    base = sweep[sweep.candidate == "flat_1.5pct"].iloc[0]
    print(f"\nBaseline flat_1.5pct: index net after = {base['idx_net_pts_after']} "
          f"pts (recorded MR index net ≈ -6,857)")
    print("\n=== STOP-CAP SWEEP (Δ vs recorded; preserve-R:R, bar-close model) ===")
    print(sweep.to_string(index=False))

    cand = sweep[sweep.candidate != "flat_1.5pct"]
    best = cand.sort_values("net_dpts_idxonly_appl", ascending=False).iloc[0]
    bl = best["candidate"]
    print(f"\nBest (max index-only-application Δpts): {bl} — "
          f"+{best['net_dpts_idxonly_appl']} pts, "
          f"{best['win_to_loss']} winner→loser, "
          f"{best['loser_saved']} losers saved")

    rb = pd.DataFrame(results[bl])
    cell = rb[rb.is_index].groupby(["epic", "session"]).apply(
        lambda g: pd.Series({
            "n": len(g), "n_changed": int(g["changed"].sum()),
            "net_dpts": round(g["new_pnl"].sum() - g["rec_pnl"].sum(), 1),
            "net_dR": round(g["new_R"].sum() - g["rec_R"].sum(), 2),
            "win_to_loss": int(g["win_to_loss"].sum()),
            "loser_saved": int(g["loser_saved"].sum()),
        }), include_groups=False).reset_index()
    cell.insert(0, "candidate", bl)
    cell.to_csv(OUT_BYCELL, index=False)
    print(f"\n=== {bl} — per index (instrument, session) ===")
    print(cell.to_string(index=False))
    if "atr_x2.5" in results:
        pd.DataFrame(results["atr_x2.5"]).to_csv(OUT_ATRX25, index=False)
        print(f"Wrote {OUT_ATRX25.name} (per-trade ATR×2.5 — step-2 slicing)")

    print(f"\nWrote {OUT_SWEEP.name}, {OUT_BYCELL.name}")


if __name__ == "__main__":
    simulate()
