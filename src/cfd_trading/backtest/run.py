"""Backtest CLI runner.

Usage:
    python -m cfd_trading.backtest.run --strategy momentum --epic EURUSD
    python -m cfd_trading.backtest.run --all-strategies --all-epics
    python -m cfd_trading.backtest.run --all-strategies --all-epics --resolution M15
    python -m cfd_trading.backtest.run --all-strategies --all-epics --resolution M15 --output trades.parquet
    # Audit run on a restricted universe using native M15 bars (no aggregation):
    python -m cfd_trading.backtest.run --all-strategies --all-epics --resolution M15 \\
        --source-resolution M15 --instruments EURUSD,GBPUSD,US500,DE40

Resolution handling:
    --resolution selects the target bar resolution for strategies.
    --source-resolution selects what the engine reads from the DB:
      - default M1: read M1 bars and aggregate to --resolution in-process.
      - non-M1:     read --source-resolution bars directly. If
                    --source-resolution == --resolution, no aggregation.
                    If smaller (e.g. M15 → H1), aggregate in-process.

Universe:
    --instruments LIST overrides --all-epics with a comma-separated subset
    (audit universe). Errors if a name is not in config/watchlist.yaml.

Trade-log output:
    --output PATH writes all completed trades from this run as a single
    Parquet file (one row per trade, columns matching the Trade dataclass
    plus the resolution this run was executed at). Required for the Phase A
    audit slicing.

Environment:
    BACKTEST_DB_PATH  — path to SQLite DB with ohlc_bars (default: /mnt/c/Users/chris/dev/trading-data/trading.db)
    CONFIG_DIR        — path to config/ directory (default: auto-detected from package root)
"""

import argparse
import dataclasses
import os
import sys
from pathlib import Path

import yaml

# Engage the backtest guard before any other cfd_trading imports
os.environ.setdefault("BACKTEST_MODE", "true")

from cfd_trading.storage.db import get_connection
from cfd_trading.storage.repository import get_bars
from cfd_trading.strategy.loader import load_strategy, list_strategies
from cfd_trading.backtest.engine import run_backtest, BacktestResult
from cfd_trading.backtest.spreads import spread_points
from cfd_trading.backtest.aggregate import aggregate_bars
from cfd_trading.backtest.sessions import session_open_utc

_DEFAULT_DB_PATH = "/mnt/c/Users/chris/dev/trading-data/trading.db"
_CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).parents[3] / "config")))


def main() -> None:
    args = _parse_args()
    db_path = os.getenv("BACKTEST_DB_PATH", _DEFAULT_DB_PATH)
    config_dir = _CONFIG_DIR

    if not Path(db_path).exists():
        print(f"Error: OHLC database not found at {db_path}", file=sys.stderr)
        print("Set BACKTEST_DB_PATH or run backtest/fetch_ohlc.py on Windows first.", file=sys.stderr)
        sys.exit(1)

    strategies = _resolve_strategies(args, config_dir)
    epics = _resolve_epics(args, config_dir)
    risk_config = _load_risk(config_dir)
    period = _parse_resolution(args.resolution)
    source_period = _parse_resolution(args.source_resolution)
    if source_period > period:
        print(f"ERROR: --source-resolution ({args.source_resolution}) is coarser than "
              f"--resolution ({args.resolution}). Cannot aggregate up.", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(db_path)

    results: list[BacktestResult] = []
    for strategy_name in strategies:
        strat = load_strategy(strategy_name, config_dir)
        for epic in epics:
            if not _instrument_allowed(epic, strat.config):
                print(f"  [skip] {epic}/{strategy_name} — not in strategy instrument list")
                continue
            bars_source = get_bars(conn, epic, args.source_resolution)
            if not bars_source:
                print(f"  [skip] {epic}/{strategy_name} — no {args.source_resolution} bars in DB")
                continue
            if source_period == period:
                bars = bars_source
            else:
                bars = aggregate_bars(bars_source, period)
            sp = spread_points(epic, bars[0].close)
            # M30 gate is self-defeating until true M30 bars are available (see BACKTESTING.md §4.1)
            signal_kwargs = _build_signal_kwargs(strategy_name, args, epic)
            result = run_backtest(epic, strategy_name, bars, strat.config, risk_config,
                                  spread_pts=sp, signal_kwargs=signal_kwargs)
            # Stamp resolution on each trade — the engine doesn't know it.
            for t in result.trades:
                t.resolution = args.resolution
            results.append(result)

    conn.close()

    if not results:
        print("No results — check that the DB contains bars for the requested epics.")
        return

    _print_table(results)
    _print_directional_table(results)

    if args.output:
        _write_trade_log(results, args.output)


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run backtests against locally stored OHLC bars.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    epic_group = p.add_mutually_exclusive_group(required=True)
    epic_group.add_argument("--epic", metavar="EPIC", help="Single instrument epic (e.g. EURUSD)")
    epic_group.add_argument("--all-epics", action="store_true", help="Run all watchlist instruments")
    epic_group.add_argument(
        "--instruments", metavar="LIST", default=None,
        help="Comma-separated subset of watchlist epics (audit universe). "
             "Example: EURUSD,GBPUSD,US500,DE40",
    )

    strat_group = p.add_mutually_exclusive_group(required=True)
    strat_group.add_argument("--strategy", metavar="NAME", help="Strategy name (e.g. momentum)")
    strat_group.add_argument("--all-strategies", action="store_true", help="Run all available strategies")

    p.add_argument(
        "--resolution", default="M1",
        help="Target bar resolution: M1 M5 M15 M30 M60 (default: M1). "
             "Source bars are aggregated to this in-process if needed.",
    )
    p.add_argument(
        "--source-resolution", default="M1",
        help="Resolution of bars to read from DB (default: M1). Useful when "
             "native higher-resolution bars are stored — set to match "
             "--resolution to skip aggregation entirely.",
    )
    p.add_argument(
        "--output", metavar="PATH", default=None,
        help="Write all trades from this run as a single Parquet file. "
             "Required for Phase A audit slicing.",
    )
    p.add_argument(
        "--momentum-relaxed", action="store_true",
        help="Audit-mode momentum filter relaxation: ADX threshold 20 (from 25) "
             "and EMA gap floor 0.02%% (from 0.05%%). Used in Phase A2 to lift "
             "momentum trade counts to a statistically sliceable density.",
    )
    return p.parse_args()


def _parse_resolution(resolution: str) -> int:
    """Parse 'M15' → 15.  Raises ValueError for unrecognised formats."""
    if resolution.startswith("M"):
        try:
            minutes = int(resolution[1:])
            if minutes > 0:
                return minutes
        except ValueError:
            pass
    raise ValueError(f"Unsupported resolution '{resolution}'. Use M1, M5, M15, M30, M60.")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_strategies(args: argparse.Namespace, config_dir: Path) -> list[str]:
    if args.all_strategies:
        return list_strategies(config_dir)
    return [args.strategy]


def _resolve_epics(args: argparse.Namespace, config_dir: Path) -> list[str]:
    # --epic mode: no need to read the watchlist.
    if not args.all_epics and getattr(args, "instruments", None) is None:
        return [args.epic]

    wl_path = config_dir / "watchlist.yaml"
    with open(wl_path) as f:
        wl = yaml.safe_load(f)
    all_epics: list[str] = []
    for group_epics in wl.values():
        all_epics.extend(group_epics)

    if args.all_epics:
        return all_epics

    # --instruments: comma-separated subset, validated against watchlist.
    wanted = [s.strip() for s in args.instruments.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in all_epics]
    if unknown:
        raise ValueError(f"Unknown instruments {unknown}. Watchlist: {all_epics}")
    return wanted


def _load_risk(config_dir: Path) -> dict:
    with open(config_dir / "risk.yaml") as f:
        return yaml.safe_load(f)


def _instrument_allowed(epic: str, config: dict) -> bool:
    """Return True if the strategy config permits this epic.

    Strategies can declare an `instruments` list at the top level of their YAML.
    If present, only listed epics are traded.  If absent, all epics are allowed.
    """
    allowed = config.get("instruments")
    return allowed is None or epic in allowed


def _build_signal_kwargs(strategy_name: str, args: argparse.Namespace, epic: str) -> dict:
    """Per-strategy keyword args passed into the signal state constructor."""
    signal_kwargs: dict = {}
    if strategy_name == "momentum":
        # M30 gate is self-defeating until true M30 bars are available (BACKTESTING.md §4.1)
        signal_kwargs["m30_gate"] = False
        if getattr(args, "momentum_relaxed", False):
            signal_kwargs["adx_threshold"] = 20.0
            signal_kwargs["min_ema_gap_pct"] = 0.0002
    elif strategy_name == "orb":
        hour, minute = session_open_utc(epic)
        signal_kwargs["session_open_hour"] = hour
        signal_kwargs["session_open_minute"] = minute
    return signal_kwargs


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_COLS = [
    ("Epic",        8,  "epic"),
    ("Strategy",    14, "strategy"),
    ("Trades",      6,  "total_trades"),
    ("Win%",        6,  "win_rate"),
    ("PF",          6,  "profit_factor"),
    ("MaxDD%",      7,  "max_drawdown_pct"),
    ("Stop%",       6,  "stop_out_rate"),
    ("Sig/wk",      7,  "signal_frequency"),
    ("AvgR",        7,  "avg_r"),
]

_DIR_COLS = [
    ("Epic",       8,  "epic"),
    ("Strategy",   14, "strategy"),
    ("L-Trades",   8,  "long_trades"),
    ("L-Win%",     7,  "long_win_rate"),
    ("L-PF",       6,  "long_profit_factor"),
    ("S-Trades",   8,  "short_trades"),
    ("S-Win%",     7,  "short_win_rate"),
    ("S-PF",       6,  "short_profit_factor"),
]


def _print_table(results: list[BacktestResult]) -> None:
    header = "  ".join(label.ljust(width) for label, width, _ in _COLS)
    sep = "  ".join("-" * width for _, width, _ in _COLS)
    print(header)
    print(sep)
    for r in results:
        row_vals = []
        for label, width, attr in _COLS:
            val = getattr(r, attr)
            if attr == "win_rate":
                cell = f"{val * 100:.1f}%"
            elif attr == "stop_out_rate":
                cell = f"{val * 100:.1f}%"
            elif attr == "profit_factor":
                cell = f"{val:.2f}" if val != float("inf") else "inf"
            elif attr == "avg_r":
                cell = f"{val:+.2f}R"
            else:
                cell = str(val)
            row_vals.append(cell.ljust(width))
        print("  ".join(row_vals))


def _write_trade_log(results: list[BacktestResult], path: str) -> None:
    """Flatten Trade records across all results into a single Parquet file.

    Each row corresponds to one completed trade. Columns are the fields of
    the Trade dataclass; resolution is already stamped on each trade by
    main() before this is called.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows: list[dict] = []
    for r in results:
        for t in r.trades:
            rows.append(dataclasses.asdict(t))

    if not rows:
        print(f"No trades to write — skipping {path}")
        return

    table = pa.Table.from_pylist(rows)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out_path)
    print(f"\nWrote {len(rows):,} trades to {out_path}")


def _print_directional_table(results: list[BacktestResult]) -> None:
    """Second table: LONG vs SHORT split — shows directional bias vs genuine two-way edge."""
    print()
    print("Directional split  (L = LONG/BUY   S = SHORT/SELL)")
    header = "  ".join(label.ljust(width) for label, width, _ in _DIR_COLS)
    sep    = "  ".join("-" * width         for _, width, _ in _DIR_COLS)
    print(header)
    print(sep)
    for r in results:
        row_vals = []
        for label, width, attr in _DIR_COLS:
            val = getattr(r, attr)
            if attr in ("long_win_rate", "short_win_rate"):
                cell = f"{val * 100:.1f}%" if val else "—"
            elif attr in ("long_profit_factor", "short_profit_factor"):
                if val == 0.0:
                    cell = "—"
                elif val == float("inf"):
                    cell = "inf"
                else:
                    cell = f"{val:.2f}"
            else:
                cell = str(val) if val else "—"
            row_vals.append(cell.ljust(width))
        print("  ".join(row_vals))


if __name__ == "__main__":
    main()
