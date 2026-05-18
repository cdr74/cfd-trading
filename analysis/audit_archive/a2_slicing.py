"""Phase A2 per-cell slicing — heatmaps + ranked tables.

Reads /audit/trades_M15.parquet and emits:
  - heatmap_instrument_strategy.png
  - heatmap_instrument_session.png
  - heatmap_strategy_hour.png
  - heatmap_month_instrument.png
  - ranked_cells.csv          (n >= 30 cells, sorted by expectancy_ratio)
  - ranked_cells_thin.csv     (cells with 1 <= n < 30, kept for visibility)

Headline metric is expectancy_ratio = expectancy_per_trade / spread_at_entry
(per-cell, where Avg Win / Avg Loss are computed from gross_pnl_points so
the ratio is gross-edge vs cost). Profit factor is shown alongside.

Sessions are UTC named buckets:
  Asia    00:00 - 07:59
  London  08:00 - 12:59
  Overlap 13:00 - 15:59
  NY      16:00 - 20:59
  Off     21:00 - 23:59
DST shifts (~1h spring/autumn) blur these by a few weeks each year.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SESSIONS = [
    ("Asia",    0,  8),
    ("London",  8,  13),
    ("Overlap", 13, 16),
    ("NY",      16, 21),
    ("Off",     21, 24),
]
INDICES_RTH_OPEN = {"US500": 14, "DE40": 8, "UK100": 8}
TRADE_FLOOR = 30


# ---------------------------------------------------------------------------
# Loading + derived columns
# ---------------------------------------------------------------------------

def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["entry_dt"] = pd.to_datetime(df["entry_ts"], unit="s", utc=True)
    df["hour_utc"] = df["entry_dt"].dt.hour
    df["month"] = df["entry_dt"].dt.to_period("M").astype(str)
    df["session"] = df["hour_utc"].apply(_session_for_hour)

    # Gross P&L: BUY = exit_mid - entry_mid; SELL = entry_mid - exit_mid.
    long_mask = df["direction"] == "BUY"
    df["gross_pnl_points"] = np.where(
        long_mask,
        df["exit_mid"] - df["entry_mid"],
        df["entry_mid"] - df["exit_mid"],
    )

    # Sanity check: gross - spread == pnl_points (the recorded net) within FP noise.
    diff = (df["gross_pnl_points"] - df["spread_at_entry"] - df["pnl_points"]).abs()
    if diff.max() > 1e-9:
        raise RuntimeError(f"Gross-vs-net recovery failed (max diff {diff.max():.2e})")

    return df


def _session_for_hour(h: int) -> str:
    for name, start, end in SESSIONS:
        if start <= h < end:
            return name
    return "Off"


# ---------------------------------------------------------------------------
# Per-cell statistics
# ---------------------------------------------------------------------------

def _cell_stats(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    if n == 0:
        return pd.Series({"n": 0, "win_pct": np.nan, "pf": np.nan,
                          "expectancy_per_trade": np.nan, "expectancy_ratio": np.nan,
                          "avg_spread": np.nan})

    gross = g["gross_pnl_points"]
    wins = gross[gross > 0]
    losses = gross[gross <= 0]
    win_pct = len(wins) / n

    gross_profit = wins.sum()
    gross_loss = -losses.sum()
    pf = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = -losses.mean() if len(losses) else 0.0
    loss_pct = 1.0 - win_pct
    expectancy = win_pct * avg_win - loss_pct * avg_loss
    avg_spread = g["spread_at_entry"].mean()
    exp_ratio = expectancy / avg_spread if avg_spread > 0 else np.nan

    return pd.Series({
        "n": n,
        "win_pct": win_pct,
        "pf": pf,
        "expectancy_per_trade": expectancy,
        "expectancy_ratio": exp_ratio,
        "avg_spread": avg_spread,
    })


def cells_by(df: pd.DataFrame, *keys: str) -> pd.DataFrame:
    out = df.groupby(list(keys), observed=True, group_keys=False).apply(_cell_stats)
    return out.reset_index()


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def heatmap(values: pd.DataFrame, title: str, fname: Path,
            counts: pd.DataFrame | None = None,
            metric_label: str = "expectancy_ratio",
            divergent_zero: float | None = 3.0,
            cmap: str = "RdYlGn", annot_fmt: str = "{:+.2f}") -> None:
    """Render a 2D pivot as a heatmap PNG.

    values: pivot DataFrame (rows × cols) of the headline metric.
    counts: optional pivot DataFrame of same shape with trade counts for annotation.
    divergent_zero: centre value for diverging colourmap. None = sequential.
    """
    fig, ax = plt.subplots(figsize=(max(6, 0.7 * values.shape[1] + 3),
                                    max(4, 0.5 * values.shape[0] + 2)))

    data = values.to_numpy(dtype=float)
    if divergent_zero is not None:
        max_dev = max(abs(np.nanmin(data)), abs(np.nanmax(data) - 2 * divergent_zero), 1.0)
        vmin = divergent_zero - max_dev
        vmax = divergent_zero + max_dev
    else:
        vmin, vmax = np.nanmin(data), np.nanmax(data)

    im = ax.imshow(data, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
    ax.set_xticks(range(values.shape[1])); ax.set_xticklabels(values.columns, rotation=45, ha="right")
    ax.set_yticks(range(values.shape[0])); ax.set_yticklabels(values.index)
    ax.set_title(f"{title}  ({metric_label})")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    for (i, j), v in np.ndenumerate(data):
        if np.isnan(v):
            continue
        n = counts.iloc[i, j] if counts is not None else None
        label = annot_fmt.format(v)
        if n is not None and not np.isnan(n):
            label += f"\nn={int(n)}"
        ax.text(j, i, label, ha="center", va="center", fontsize=7,
                color="black" if abs(v - (divergent_zero or 0)) < max_dev * 0.4 else "white")

    fig.tight_layout()
    fig.savefig(fname, dpi=120)
    plt.close(fig)


def _pivot(cells: pd.DataFrame, index: str, columns: str, value: str = "expectancy_ratio"):
    pivot = cells.pivot_table(index=index, columns=columns, values=value, observed=True)
    counts = cells.pivot_table(index=index, columns=columns, values="n", observed=True)
    return pivot, counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="/home/chris/dev/trading/audit/trades_M15.parquet")
    p.add_argument("--outdir", default="/home/chris/dev/trading/audit")
    args = p.parse_args()

    in_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Loading {in_path} ...")
    df = load(in_path)
    print(f"  {len(df):,} trades; {df['epic'].nunique()} instruments × {df['strategy'].nunique()} strategies")
    print(f"  Date range: {df['entry_dt'].min().date()} → {df['entry_dt'].max().date()}")

    # --- Heatmap 1: instrument × strategy
    cells_is = cells_by(df, "strategy", "epic")
    pivot, counts = _pivot(cells_is, "strategy", "epic")
    heatmap(pivot, "Cell #1: Instrument × Strategy", outdir / "heatmap_instrument_strategy.png",
            counts=counts)
    pivot_pf, _ = _pivot(cells_is, "strategy", "epic", value="pf")
    heatmap(pivot_pf, "Cell #1: Instrument × Strategy (PF view)",
            outdir / "heatmap_instrument_strategy_pf.png",
            counts=counts, metric_label="profit_factor", divergent_zero=1.0)

    # --- Heatmap 2: instrument × session (per strategy)
    cells_iss = cells_by(df, "strategy", "epic", "session")
    for strat in df["strategy"].unique():
        sub = cells_iss[cells_iss["strategy"] == strat]
        if sub.empty:
            continue
        # Keep session ordering
        sub = sub.copy()
        sub["session"] = pd.Categorical(sub["session"],
                                        categories=[name for name, _, _ in SESSIONS],
                                        ordered=True)
        pivot, counts = _pivot(sub, "epic", "session")
        heatmap(pivot, f"Cell #2: Instrument × Session — {strat}",
                outdir / f"heatmap_instrument_session_{strat}.png", counts=counts)

    # --- Heatmap 3: strategy × hour-of-day
    cells_sh = cells_by(df, "strategy", "hour_utc")
    pivot = cells_sh.pivot_table(index="strategy", columns="hour_utc",
                                 values="expectancy_ratio", observed=True)
    counts = cells_sh.pivot_table(index="strategy", columns="hour_utc",
                                  values="n", observed=True)
    # Fill missing hours so x-axis is 0..23 consistently
    full_cols = list(range(24))
    pivot = pivot.reindex(columns=full_cols)
    counts = counts.reindex(columns=full_cols)
    heatmap(pivot, "Cell #3: Strategy × Hour-of-day (UTC)",
            outdir / "heatmap_strategy_hour.png", counts=counts, annot_fmt="{:+.1f}")

    # --- Heatmap 4: month × instrument (per strategy)
    cells_mi = cells_by(df, "strategy", "epic", "month")
    for strat in df["strategy"].unique():
        sub = cells_mi[cells_mi["strategy"] == strat]
        if sub.empty:
            continue
        pivot, counts = _pivot(sub, "month", "epic")
        heatmap(pivot, f"Cell #4: Month × Instrument — {strat}",
                outdir / f"heatmap_month_instrument_{strat}.png",
                counts=counts, annot_fmt="{:+.1f}")

    # --- Ranked cells
    full = cells_by(df, "strategy", "epic")
    full_with_session = cells_by(df, "strategy", "epic", "session")
    combined = pd.concat([
        full.assign(scope="all-session"),
        full_with_session.assign(scope="per-session"),
    ], ignore_index=True)
    combined = combined.sort_values("expectancy_ratio", ascending=False, na_position="last")

    main_table = combined[combined["n"] >= TRADE_FLOOR].copy()
    thin_table = combined[(combined["n"] < TRADE_FLOOR) & (combined["n"] >= 1)].copy()

    main_path = outdir / "ranked_cells.csv"
    thin_path = outdir / "ranked_cells_thin.csv"
    main_table.to_csv(main_path, index=False, float_format="%.4f")
    thin_table.to_csv(thin_path, index=False, float_format="%.4f")
    print(f"  wrote {main_path} ({len(main_table)} rows ≥ n={TRADE_FLOOR})")
    print(f"  wrote {thin_path} ({len(thin_table)} thin rows)")

    # --- Top-5 quick print to stdout
    print("\nTop 10 cells by expectancy_ratio (n >= 30):")
    cols = ["scope", "strategy", "epic", "session", "n", "win_pct", "pf",
            "expectancy_per_trade", "expectancy_ratio"]
    cols = [c for c in cols if c in main_table.columns]
    print(main_table[cols].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
