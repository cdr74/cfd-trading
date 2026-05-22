"""D3/BR3 (intraday_continuation) — DSR / min-sample / cost-gate analysis.

Implements the pre-registered methodology from STRATEGY_AUDIT.md Part 2 →
"Task #6 — DSR pre-registration" (locked 2026-05-21, amended same day for the
UK100 data-depth issue). Reads `audit/d3_run_2026-05-21.parquet`, splits into
IS/OOS at the locked boundary, runs the 4 gates per window, and writes
`audit/d3_run_2026-05-21.md`.

Per the pre-registration the kill protocol REQUIRES manual pre-kill
introspection — this script outputs the numbers; the introspection step is
performed manually with the report's diagnostics in hand.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

from statistics import NormalDist

import numpy as np
import pandas as pd

_norm = NormalDist()


# ---------------------------------------------------------------------------
# Locked pre-registration values (STRATEGY_AUDIT.md Part 2, 2026-05-21)
# ---------------------------------------------------------------------------

TRADES_PATH = Path(__file__).parent / "d3_run_2026-05-21.parquet"
REPORT_PATH = Path(__file__).parent / "d3_run_2026-05-21.md"

UNIVERSE = ("US500", "DE40")  # UK100 dropped per pre-run amendment
IS_OOS_BOUNDARY = dt.datetime(2025, 1, 21, tzinfo=dt.timezone.utc)
WINDOW_START = dt.datetime(2023, 5, 21, tzinfo=dt.timezone.utc)
WINDOW_END = dt.datetime(2026, 5, 21, tzinfo=dt.timezone.utc)

K_TRIALS = 121          # audit's 120 + D3
BOOTSTRAP_BLOCK = 20    # moving-block, block length 20 trades
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 42     # reproducibility — locked once written
MIN_SAMPLE = 100

# D1 anchor — bps/trade per-index (STRATEGY_AUDIT.md Part 2 "D1 anchor")
D1_COST_BPS = {"US500": 0.89, "DE40": 0.49}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def per_trade_sharpe(pnl: np.ndarray) -> float:
    """Per-trade Sharpe: mean(pnl) / std(pnl). Unitless across all instruments."""
    if len(pnl) < 2:
        return float("nan")
    s = pnl.std(ddof=1)
    return float(pnl.mean() / s) if s > 0 else float("nan")


def deflated_sharpe_p(pnl: np.ndarray, k_trials: int) -> tuple[float, float, float]:
    """Bailey & López de Prado Deflated Sharpe Ratio.

    Returns (DSR, SR̂, SR₀_estimate).
    DSR = Φ( (SR̂ − SR₀) · √(T−1) / √(1 − γ₃·SR̂ + (γ₄−1)/4·SR̂²) )
    where γ₃ = skew, γ₄ = (un-excess) kurtosis = excess_kurt + 3.

    SR₀ is estimated as σ_SR · ((1−γ_E)·Φ⁻¹(1−1/K) + γ_E·Φ⁻¹(1−1/(K·e))),
    where σ_SR comes from §5's empirical cross-cell dispersion. We use the
    audit's frozen σ_SR (back-derived from SR₀=0.282 at K=120). The exact
    audit-script value is the source of truth; this is a faithful
    reproduction at K=121.
    """
    sr_hat = per_trade_sharpe(pnl)
    if math.isnan(sr_hat):
        return float("nan"), sr_hat, float("nan")
    t = len(pnl)
    gamma3 = float(pd.Series(pnl).skew())
    gamma4 = float(pd.Series(pnl).kurt()) + 3.0   # convert excess → standard

    # SR₀ via Bailey: factor × σ_SR
    euler = 0.5772156649
    factor = (1 - euler) * _norm.inv_cdf(1 - 1 / k_trials) \
             + euler * _norm.inv_cdf(1 - 1 / (k_trials * math.e))
    # σ_SR back-derived from §5: SR₀=0.282 @ K=120 → σ_SR = 0.282 / factor_at_120
    factor_at_120 = (1 - euler) * _norm.inv_cdf(1 - 1 / 120) \
                    + euler * _norm.inv_cdf(1 - 1 / (120 * math.e))
    sigma_sr = 0.282 / factor_at_120
    sr0 = sigma_sr * factor

    # DSR — guard against pathological denominator
    denom_sq = 1 - gamma3 * sr_hat + (gamma4 - 1) / 4 * sr_hat ** 2
    if denom_sq <= 0:
        return float("nan"), sr_hat, sr0
    z = (sr_hat - sr0) * math.sqrt(t - 1) / math.sqrt(denom_sq)
    p = float(_norm.cdf(z))
    return p, sr_hat, sr0


def moving_block_bootstrap_sharpe_ci(
    pnl: np.ndarray, block: int = 20, n_resamples: int = 10_000,
    seed: int = 42, ci: float = 0.95,
) -> tuple[float, float]:
    """Moving-block bootstrap 95% CI on per-trade Sharpe.

    Resamples overlapping blocks of length `block` from the trade series,
    concatenates to the original length, and computes the Sharpe of each
    resample. Returns (lo, hi) at the given confidence level.
    """
    n = len(pnl)
    if n < block:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    n_blocks_needed = math.ceil(n / block)
    starts_pop = np.arange(n - block + 1)
    out = np.empty(n_resamples)
    for i in range(n_resamples):
        starts = rng.choice(starts_pop, size=n_blocks_needed, replace=True)
        resample = np.concatenate([pnl[s:s + block] for s in starts])[:n]
        s = resample.std(ddof=1)
        out[i] = resample.mean() / s if s > 0 else 0.0
    alpha = (1 - ci) / 2
    return float(np.quantile(out, alpha)), float(np.quantile(out, 1 - alpha))


# ---------------------------------------------------------------------------
# Cost gate
# ---------------------------------------------------------------------------

def pooled_cost_bps(df: pd.DataFrame) -> tuple[float, float]:
    """Pooled N-weighted per-trade round-trip cost in bps.

    `spread_at_entry` is the FULL spread in price points; the engine deducts
    half-spread on entry AND half-spread on exit, so the recorded spread
    equals the realized round-trip cost. Converts to bps via entry_mid.
    Returns (cost_bps, hurdle_bps = 3 × cost). Matches the audit D1 anchor
    method (verified: DE40 1.0 pts / 20217 × 10000 = 0.49 bps ≡ audit).
    """
    if "spread_at_entry" not in df.columns or "entry_mid" not in df.columns:
        return float("nan"), float("nan")
    cost_pts = df["spread_at_entry"].fillna(0.0)  # full round-trip already
    cost_bps_per_trade = (cost_pts / df["entry_mid"]) * 10_000
    pooled = float(cost_bps_per_trade.mean())
    return pooled, 3.0 * pooled


def net_bps_per_trade(df: pd.DataFrame) -> float:
    """Mean per-trade net PnL in bps of entry mid price."""
    if "pnl_points" not in df.columns or "entry_mid" not in df.columns:
        return float("nan")
    return float(((df["pnl_points"] / df["entry_mid"]) * 10_000).mean())


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def split_window(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    boundary_ts = int(IS_OOS_BOUNDARY.timestamp())
    return df[df["entry_ts"] < boundary_ts].copy(), df[df["entry_ts"] >= boundary_ts].copy()


def gate_table(df: pd.DataFrame, label: str) -> dict:
    pnl = df["pnl_points"].to_numpy()
    n = len(df)
    if n < MIN_SAMPLE:
        return {"window": label, "n": n, "passed_min": False, "sr": float("nan"),
                "dsr": float("nan"), "sr0": float("nan"), "ci": (float("nan"),) * 2,
                "cost": float("nan"), "hurdle": float("nan"), "net": float("nan"),
                "pass_dsr": False, "pass_ci": False, "pass_cost": False}
    dsr, sr_hat, sr0 = deflated_sharpe_p(pnl, K_TRIALS)
    ci_lo, ci_hi = moving_block_bootstrap_sharpe_ci(
        pnl, BOOTSTRAP_BLOCK, BOOTSTRAP_N, BOOTSTRAP_SEED)
    cost, hurdle = pooled_cost_bps(df)
    net = net_bps_per_trade(df)
    return {
        "window": label, "n": n, "passed_min": n >= MIN_SAMPLE,
        "sr": sr_hat, "dsr": dsr, "sr0": sr0,
        "ci": (ci_lo, ci_hi), "cost": cost, "hurdle": hurdle, "net": net,
        "pass_dsr": dsr >= 0.95,
        "pass_ci": ci_lo > 0,
        "pass_cost": net >= hurdle,
    }


def per_instrument_diagnostic(df: pd.DataFrame) -> pd.DataFrame:
    """Diagnostic-only (not gated): N / win-rate / net-bps per instrument."""
    rows = []
    for epic in UNIVERSE:
        sub = df[df["epic"] == epic]
        n = len(sub)
        if n == 0:
            rows.append({"epic": epic, "n": 0, "win_rate": float("nan"),
                         "net_bps": float("nan"), "pf": float("nan")})
            continue
        wins = (sub["pnl_points"] > 0).sum()
        gross_win = sub.loc[sub["pnl_points"] > 0, "pnl_points"].sum()
        gross_loss = -sub.loc[sub["pnl_points"] < 0, "pnl_points"].sum()
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        net = float(((sub["pnl_points"] / sub["entry_mid"]) * 10_000).mean())
        rows.append({"epic": epic, "n": n, "win_rate": wins / n,
                     "net_bps": net, "pf": pf})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Introspection diagnostics — objective data to support the manual review
# ---------------------------------------------------------------------------

def monthly_equity(df: pd.DataFrame) -> pd.DataFrame:
    """Per-month cumulative net bps (pooled + per-epic)."""
    if len(df) == 0:
        return pd.DataFrame()
    d = df.copy()
    d["month"] = pd.to_datetime(d["entry_ts"], unit="s", utc=True).dt.to_period("M")
    d["net_bps"] = (d["pnl_points"] / d["entry_mid"]) * 10_000
    monthly = d.groupby(["month", "epic"])["net_bps"].agg(["sum", "count"]).reset_index()
    pivot_sum = monthly.pivot(index="month", columns="epic", values="sum").fillna(0.0)
    pivot_cnt = monthly.pivot(index="month", columns="epic", values="count").fillna(0).astype(int)
    pivot_sum["pooled"] = pivot_sum.sum(axis=1)
    pivot_sum["cum_pooled"] = pivot_sum["pooled"].cumsum()
    out = pd.concat([pivot_cnt.add_prefix("n_"), pivot_sum.add_prefix("sum_")], axis=1)
    return out


def pnl_distribution(df: pd.DataFrame) -> dict:
    """Per-trade net bps distribution stats."""
    if len(df) == 0:
        return {}
    s = (df["pnl_points"] / df["entry_mid"]) * 10_000
    q = s.quantile([0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99]).to_dict()
    return {
        "mean": float(s.mean()), "std": float(s.std(ddof=1)),
        "skew": float(s.skew()), "kurt_excess": float(s.kurt()),
        "min": float(s.min()), "max": float(s.max()),
        "q01": q[0.01], "q05": q[0.05], "q25": q[0.25],
        "q50": q[0.50], "q75": q[0.75], "q95": q[0.95], "q99": q[0.99],
    }


def drawdown_summary(df: pd.DataFrame) -> dict:
    """Pooled cumulative bps + max drawdown."""
    if len(df) == 0:
        return {}
    d = df.sort_values("entry_ts").copy()
    d["net_bps"] = (d["pnl_points"] / d["entry_mid"]) * 10_000
    cum = d["net_bps"].cumsum().to_numpy()
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    end_i = int(np.argmin(dd))
    # find peak that started the drawdown
    start_i = int(np.argmax(cum[:end_i + 1])) if end_i > 0 else 0
    start_ts = int(d.iloc[start_i]["entry_ts"])
    end_ts = int(d.iloc[end_i]["entry_ts"])
    return {
        "max_dd_bps": float(dd.min()),
        "final_cum_bps": float(cum[-1]),
        "start": dt.datetime.fromtimestamp(start_ts, dt.timezone.utc).date().isoformat(),
        "end": dt.datetime.fromtimestamp(end_ts, dt.timezone.utc).date().isoformat(),
        "trades_in_dd": end_i - start_i,
    }


def exit_reason_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    """Exit reason class × count × mean net bps."""
    if len(df) == 0:
        return pd.DataFrame()
    d = df.copy()
    d["reason_class"] = d["exit_reason"].str.extract(
        r"^([A-Za-z][A-Za-z ]*?)[:0-9]", expand=False).str.strip()
    d.loc[d["reason_class"].isna(), "reason_class"] = d["exit_reason"]
    d["net_bps"] = (d["pnl_points"] / d["entry_mid"]) * 10_000
    out = d.groupby("reason_class")["net_bps"].agg(["count", "mean", "sum"]).reset_index()
    return out.sort_values("count", ascending=False)


def random_trade_sample(df: pd.DataFrame, n: int = 20, seed: int = 42) -> pd.DataFrame:
    """Random trade sample with stop_history length + last stop level."""
    if len(df) == 0:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(df), size=min(n, len(df)), replace=False)
    sub = df.iloc[idx].copy().sort_values("entry_ts")
    sub["entry_utc"] = pd.to_datetime(sub["entry_ts"], unit="s", utc=True)
    sub["exit_utc"] = pd.to_datetime(sub["exit_ts"], unit="s", utc=True)
    sub["bars_held"] = ((sub["exit_ts"] - sub["entry_ts"]) // 900).astype(int)
    sub["net_bps"] = ((sub["pnl_points"] / sub["entry_mid"]) * 10_000).round(2)

    def _trail_summary(h):
        if h is None or len(h) == 0:
            return "(none)"
        # h is an array of (ts, stop) pairs
        try:
            stops = [float(x[1]) for x in h]
            return f"{len(stops)} adj; first={stops[0]:.5f} last={stops[-1]:.5f}"
        except (TypeError, IndexError, ValueError):
            return f"(unparsable: type={type(h).__name__})"

    sub["trail_summary"] = sub["stop_history"].apply(_trail_summary)
    cols = ["epic", "direction", "entry_utc", "exit_utc", "bars_held",
            "entry_price", "exit_price", "stop_loss", "exit_reason",
            "net_bps", "trail_summary"]
    return sub[cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def fmt(x, p=3):
    if isinstance(x, tuple):
        return "[" + ", ".join(fmt(v, p) for v in x) + "]"
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "nan"
    return f"{x:.{p}f}"


def _render_exit_reasons(df: pd.DataFrame) -> str:
    er = exit_reason_breakdown(df)
    lines = ["| reason | count | share | mean net (bps) | sum net (bps) |",
             "|---|--:|--:|--:|--:|"]
    total = int(er["count"].sum()) if len(er) else 0
    for _, r in er.iterrows():
        share = r["count"] / total if total else 0
        lines.append(f"| {r['reason_class']} | {int(r['count']):,} | {share:.1%} | "
                     f"{r['mean']:.3f} | {r['sum']:.1f} |")
    return "\n".join(lines)


def _render_pnl_dist_row(label: str, df: pd.DataFrame) -> str:
    d = pnl_distribution(df)
    if not d:
        return f"| {label} | (empty) |" + " |" * 13
    return (f"| {label} | {d['mean']:.3f} | {d['std']:.3f} | {d['skew']:.3f} | "
            f"{d['kurt_excess']:.3f} | {d['min']:.2f} | {d['q01']:.2f} | "
            f"{d['q05']:.2f} | {d['q25']:.2f} | {d['q50']:.2f} | {d['q75']:.2f} | "
            f"{d['q95']:.2f} | {d['q99']:.2f} | {d['max']:.2f} |")


def _render_dd_row(label: str, df: pd.DataFrame) -> str:
    d = drawdown_summary(df)
    if not d:
        return f"| {label} | (empty) | | | | |"
    return (f"| {label} | {d['max_dd_bps']:.1f} | {d['final_cum_bps']:.1f} | "
            f"{d['start']} | {d['end']} | {d['trades_in_dd']:,} |")


def _render_monthly(df: pd.DataFrame) -> str:
    m = monthly_equity(df)
    if len(m) == 0:
        return "_(empty)_"
    lines = ["| month | n US500 | n DE40 | sum US500 (bps) | sum DE40 (bps) | pooled (bps) | cum pooled (bps) |",
             "|---|--:|--:|--:|--:|--:|--:|"]
    for month, row in m.iterrows():
        lines.append(f"| {month} | {int(row.get('n_US500', 0)):,} | {int(row.get('n_DE40', 0)):,} | "
                     f"{row.get('sum_US500', 0.0):.1f} | {row.get('sum_DE40', 0.0):.1f} | "
                     f"{row.get('sum_pooled', 0.0):.1f} | {row.get('sum_cum_pooled', 0.0):.1f} |")
    return "\n".join(lines)


def _render_random_trades(df: pd.DataFrame) -> str:
    s = random_trade_sample(df, n=20, seed=42)
    if len(s) == 0:
        return "_(empty)_"
    lines = ["| epic | dir | entry (UTC) | exit (UTC) | bars | entry | exit | initial stop | exit reason | net (bps) | trail |",
             "|---|---|---|---|--:|--:|--:|--:|---|--:|---|"]
    for _, r in s.iterrows():
        lines.append(f"| {r['epic']} | {r['direction']} | {r['entry_utc'].strftime('%Y-%m-%d %H:%M')} | "
                     f"{r['exit_utc'].strftime('%Y-%m-%d %H:%M')} | {r['bars_held']} | "
                     f"{r['entry_price']:.5f} | {r['exit_price']:.5f} | {r['stop_loss']:.5f} | "
                     f"{r['exit_reason'][:60]} | {r['net_bps']:.2f} | {r['trail_summary']} |")
    return "\n".join(lines)


def write_report(full_df: pd.DataFrame, is_df: pd.DataFrame, oos_df: pd.DataFrame,
                 gates: dict, path: Path) -> None:
    is_g, oos_g = gates["IS"], gates["OOS"]

    def gate_row(g):
        verdict_min = "PASS" if g["passed_min"] else "FAIL"
        verdict_dsr = "PASS" if g["pass_dsr"] else "FAIL"
        verdict_ci = "PASS" if g["pass_ci"] else "FAIL"
        verdict_cost = "PASS" if g["pass_cost"] else "FAIL"
        return (
            f"| {g['window']} | {g['n']:,} ({verdict_min}) | "
            f"SR̂={fmt(g['sr'])} vs SR₀={fmt(g['sr0'])} → DSR P={fmt(g['dsr'])} ({verdict_dsr}) | "
            f"95% CI {fmt(g['ci'])} ({verdict_ci}) | "
            f"net={fmt(g['net'])} bps vs hurdle={fmt(g['hurdle'])} bps ({verdict_cost}) |"
        )

    any_oos_fail = not (oos_g["passed_min"] and oos_g["pass_dsr"]
                        and oos_g["pass_ci"] and oos_g["pass_cost"])

    diag_is = per_instrument_diagnostic(is_df)
    diag_oos = per_instrument_diagnostic(oos_df)
    diag_lines = ["| window | epic | N | win-rate | net (bps) | PF |", "|---|---|---:|---:|---:|---:|"]
    for w, d in [("IS", diag_is), ("OOS", diag_oos)]:
        for _, r in d.iterrows():
            diag_lines.append(
                f"| {w} | {r['epic']} | {int(r['n']):,} | {fmt(r['win_rate'])} | "
                f"{fmt(r['net_bps'])} | {fmt(r['pf'])} |")

    content = f"""# D3/BR3 (intraday_continuation) — Run Report

**Run date:** 2026-05-21
**Trades parquet:** `{TRADES_PATH.name}` ({len(full_df):,} trades)
**Pre-registration:** `docs/STRATEGY_AUDIT.md` Part 2 → "Task #6 — DSR
pre-registration" (locked 2026-05-21, UK100 amendment same day)

## Run inputs (frozen pre-run)

- Universe: pooled US500 + DE40 (UK100 dropped — see amendment in
  STRATEGY_AUDIT.md Part 2)
- Window: {WINDOW_START.date()} → {WINDOW_END.date()}
- OOS boundary: {IS_OOS_BOUNDARY.date()} (67/33 by calendar time)
- K (trials): {K_TRIALS}
- Bootstrap: moving-block, block = {BOOTSTRAP_BLOCK} trades,
  N = {BOOTSTRAP_N:,}, seed = {BOOTSTRAP_SEED}
- Min-sample floor: N ≥ {MIN_SAMPLE} per window

## Four gates × two windows

| Window | Min-sample N≥100 | DSR (P≥0.95) | Bootstrap 95% CI (lower > 0) | 3× cost gate |
|---|---|---|---|---|
{gate_row(is_g)}
{gate_row(oos_g)}

## Per-instrument diagnostic (NOT GATED — pooled metric is the gate)

{chr(10).join(diag_lines)}

## Verdict (pre-introspection)

OOS pass-all: **{'YES' if not any_oos_fail else 'NO'}** —
{"all four OOS gates passed" if not any_oos_fail else "OOS fails ≥1 gate"}.

**The kill-criterion is NOT applied yet.** Per the pre-registration's
non-optional pre-kill introspection step, this verdict is provisional.

## Introspection diagnostics (objective data)

### Exit-reason breakdown (pooled, full window)

{_render_exit_reasons(full_df)}

### PnL distribution (per-trade, net bps)

| window | mean | std | skew | kurt (excess) | min | q01 | q05 | q25 | q50 | q75 | q95 | q99 | max |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
{_render_pnl_dist_row("IS", is_df)}
{_render_pnl_dist_row("OOS", oos_df)}

### Drawdown summary (pooled, in bps)

| window | max DD (bps) | final cum (bps) | DD start | DD end | trades in DD |
|---|--:|--:|---|---|--:|
{_render_dd_row("IS", is_df)}
{_render_dd_row("OOS", oos_df)}

### Monthly equity (pooled net bps, per month)

{_render_monthly(full_df)}

### Random 20-trade sample (seed=42, full window)

{_render_random_trades(full_df)}

## Pre-kill introspection (mandatory — manual)

To be filled in by the user / analyst before declaring D3 dead or alive.
The pre-registration requires inspection of at minimum:

- [ ] **Equity curve** (pooled and per-instrument) — any single trade or
      week dominating the result?
- [ ] **PnL distribution** — fat-tail check, outliers, distribution shape
      vs Sharpe assumption.
- [ ] **≥20 random trades manually verified** against the bar series —
      entry timing, stop placement, trail behaviour, exit reason.
- [ ] **Drawdown periods** — any single bad period explaining the OOS gate
      result?
- [ ] **Parity test re-run** — `pytest tests/unit/test_parity.py` after
      engine touches (all 346 tests passed at run time on 2026-05-21).
- [ ] **Sanity vs literature** — Zarattini SPY reported Sharpe 1.33 at
      1-min (un-re-costed); on M15 retail CFD we expect SUBSTANTIALLY
      lower. Anything wildly outside ~0.05–1.5 needs explanation.

**Findings:** _(fill in)_

**Resolution:** _(bug-fix-and-rerun / clear)_

## Final decision (post-introspection)

_(Filled in only after introspection clears.)_

**Status:** _(KILL / KEEP — candidate edge, proceed to walk-forward + MTRL)_

**Rationale:** _(per the four gates and the introspection findings)_
"""
    path.write_text(content)


def main():
    if not TRADES_PATH.exists():
        print(f"ERROR: trades parquet not found at {TRADES_PATH}", file=sys.stderr)
        sys.exit(1)

    # Guard against accidentally clobbering the finalised introspection +
    # verdict sections of the report. The initial report had placeholder
    # `_(fill in)_` markers; once those are gone the report is finalised.
    # Pass --force to overwrite anyway (e.g. if you're re-running with a
    # methodology fix that pre-registration justifies).
    if REPORT_PATH.exists() and "_(fill in)_" not in REPORT_PATH.read_text() \
            and "--force" not in sys.argv:
        print(f"ERROR: {REPORT_PATH.name} is finalised (no `_(fill in)_` "
              "placeholder). Refusing to overwrite. Pass --force if intended.",
              file=sys.stderr)
        sys.exit(2)

    df = pd.read_parquet(TRADES_PATH)
    df = df[df["epic"].isin(UNIVERSE)].copy()
    df = df[(df["entry_ts"] >= int(WINDOW_START.timestamp()))
            & (df["entry_ts"] < int(WINDOW_END.timestamp()))].copy()
    df = df.sort_values("entry_ts").reset_index(drop=True)

    is_df, oos_df = split_window(df)

    gates = {
        "IS": gate_table(is_df, "IS (2023-05-21 → 2025-01-21)"),
        "OOS": gate_table(oos_df, "OOS (2025-01-21 → 2026-05-21)"),
    }
    write_report(df, is_df, oos_df, gates, REPORT_PATH)

    # Console summary
    print(f"\nWrote report: {REPORT_PATH}")
    print(f"\nIS  N={gates['IS']['n']:,}  SR={fmt(gates['IS']['sr'])}  "
          f"DSR P={fmt(gates['IS']['dsr'])}  CI={fmt(gates['IS']['ci'])}  "
          f"net={fmt(gates['IS']['net'])} bps  hurdle={fmt(gates['IS']['hurdle'])} bps")
    print(f"OOS N={gates['OOS']['n']:,}  SR={fmt(gates['OOS']['sr'])}  "
          f"DSR P={fmt(gates['OOS']['dsr'])}  CI={fmt(gates['OOS']['ci'])}  "
          f"net={fmt(gates['OOS']['net'])} bps  hurdle={fmt(gates['OOS']['hurdle'])} bps")
    g = gates["OOS"]
    oos_pass = g["passed_min"] and g["pass_dsr"] and g["pass_ci"] and g["pass_cost"]
    print(f"\nOOS all-gates-pass: {'YES' if oos_pass else 'NO'} (kill if NO, "
          f"BUT pre-kill introspection is mandatory — see report)")


if __name__ == "__main__":
    main()
