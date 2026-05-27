"""D2 surprise standardisation + pooled signal dataset (step 3 of build).

Authoritative spec: docs/STRATEGY_AUDIT.md Part 2 -> "D2 pre-registration"
§4 (surprise metric) + §3 (universe mapping) + §2 (window/OOS split).
Consumes d2_news_events.parquet (step 2); produces d2_signals.parquet.

Frozen decisions applied here:
- Surprise = actual - forecast, per event series (FF `ebaseId`).
- sigma = PRIOR-ONLY EXPANDING std of that series' surprises, sample ddof=1,
  arm-eligible only after >= WARMUP priors (user decision 2026-05-26, warmup=8).
  -> NO look-ahead: sigma_t uses only surprises strictly before t, exactly what
     a live system could compute at release time.
- z = surprise / sigma_t ; pooled across ALL series (never per-event cells).
- Entry arming: |z| >= Z_THRESHOLD (1.0), single pinned value, no grid.
- Non-scalar events (BoE vote-splits, Fed 'Pass') already null-actual -> excluded.
- Currency -> instrument expansion per §3; one candidate row per (event, instrument).

NOT done here (step 4, needs OHLC + engine): direction (sign of first post-release
M15 bar), entry-next-bar, hold horizon, cost, the gates. This dataset is the set
of *armed signals* the engine will consume.

Run:  .venv/bin/python analysis/d2_news/build_dataset.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
IN_PARQUET = HERE / "d2_news_events.parquet"
OUT_PARQUET = HERE / "d2_signals.parquet"

WARMUP = 8           # min prior occurrences before a series can arm (user decision)
Z_THRESHOLD = 1.0    # pre-registered entry threshold, pinned
OOS_BOUNDARY = pd.Timestamp("2025-01-21", tz="UTC")  # §2, mirrors D3

# §3 currency -> instrument map. JPY -> USDJPY only (thin, 19 events).
CCY_TO_INSTR = {
    "USD": ["US500", "GOLD", "EURUSD", "GBPUSD", "USDJPY"],
    "EUR": ["DE40", "EURUSD", "EURGBP"],
    "GBP": ["GBPUSD", "EURGBP"],
    "JPY": ["USDJPY"],
}
INSTR_CLASS = {
    "US500": "Index", "DE40": "Index",
    "EURUSD": "FX", "GBPUSD": "FX", "USDJPY": "FX", "EURGBP": "FX",
    "GOLD": "Commodity",
}


def build_standardized(events: pd.DataFrame) -> pd.DataFrame:
    """Per-series prior-only expanding-sigma standardisation."""
    df = events[events.currency.isin(CCY_TO_INSTR)].copy()
    df = df[df.actual.notna() & df.forecast.notna()].copy()  # drop non-scalar
    df["surprise"] = df.actual - df.forecast
    df = df.sort_values(["ebaseId", "datetime_utc"]).reset_index(drop=True)

    g = df.groupby("ebaseId")["surprise"]
    # prior-only: shift(1) so occurrence t sees only surprises strictly before t
    df["sigma"] = g.transform(lambda s: s.shift(1).expanding(min_periods=WARMUP).std(ddof=1))
    df["n_prior"] = g.cumcount()  # 0-based; == #priors available
    df = df[(df.n_prior >= WARMUP) & df.sigma.notna() & (df.sigma > 0)].copy()
    df["z"] = df.surprise / df.sigma
    df["armed"] = df.z.abs() >= Z_THRESHOLD
    return df


def expand_to_instruments(armed: pd.DataFrame) -> pd.DataFrame:
    """One row per (armed event, mapped instrument)."""
    rows = []
    for _, e in armed.iterrows():
        for instr in CCY_TO_INSTR[e.currency]:
            rows.append({
                "event_id": e.id,
                "ebaseId": e.ebaseId,
                "name": e["name"],
                "currency": e.currency,
                "release_utc": e.datetime_utc,
                "surprise": e.surprise,
                "sigma": e.sigma,
                "z": e.z,
                "instrument": instr,
                "instr_class": INSTR_CLASS[instr],
                "split": "IS" if e.datetime_utc < OOS_BOUNDARY else "OOS",
            })
    return pd.DataFrame(rows)


def main() -> int:
    events = pd.read_parquet(IN_PARQUET)
    std = build_standardized(events)
    armed = std[std.armed].copy()

    print("D2 standardisation — prior-only expanding sigma, warmup>=%d, |z|>=%.1f\n" % (WARMUP, Z_THRESHOLD))
    print(f"  warmup-eligible standardized events: {len(std)}")
    print(f"  armed (|z|>=1.0):                    {len(armed)} "
          f"({100*len(armed)/len(std):.0f}% of eligible)")
    print(f"  arm rate by split: IS={ (std.datetime_utc<OOS_BOUNDARY).pipe(lambda m: (armed.datetime_utc<OOS_BOUNDARY).sum()) }"
          f"  OOS={ (armed.datetime_utc>=OOS_BOUNDARY).sum() }")

    sig = expand_to_instruments(armed)
    sig.to_parquet(OUT_PARQUET, index=False)
    print(f"\n  wrote {OUT_PARQUET.name}: {len(sig)} candidate (event,instrument) signals")
    print(f"  by split: {sig.split.value_counts().to_dict()}")
    print(f"  OOS candidates per instrument:")
    oos = sig[sig.split == "OOS"].instrument.value_counts()
    for instr, n in oos.items():
        print(f"    {instr:7s} {n}")
    print(f"\n  pre-reg OOS floor = ~100 pooled trades; OOS candidate ceiling = {len(sig[sig.split=='OOS'])}")
    print("  (candidates are an upper bound; final N drops events lacking a valid")
    print("   reaction bar / entry bar — resolved at engine step 4.)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
