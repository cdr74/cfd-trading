"""Backtest CLI runner.

Usage:
    python -m cfd_trading.backtest.run --strategy momentum --epic EURUSD
    python -m cfd_trading.backtest.run --all-strategies --all-epics
    python -m cfd_trading.backtest.run --strategy mean_reversion --all-epics --resolution M1

Environment:
    BACKTEST_DB_PATH  — path to SQLite DB with ohlc_bars (default: /mnt/c/Users/chris/dev/trading-data/trading.db)
    CONFIG_DIR        — path to config/ directory (default: auto-detected from package root)
"""

import argparse
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

    conn = get_connection(db_path)

    results: list[BacktestResult] = []
    for strategy_name in strategies:
        strat = load_strategy(strategy_name, config_dir)
        for epic in epics:
            bars = get_bars(conn, epic, args.resolution)
            if not bars:
                print(f"  [skip] {epic}/{strategy_name} — no bars in DB for resolution {args.resolution}")
                continue
            result = run_backtest(epic, strategy_name, bars, strat.config, risk_config)
            results.append(result)

    conn.close()

    if not results:
        print("No results — check that the DB contains bars for the requested epics.")
        return

    _print_table(results)


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

    strat_group = p.add_mutually_exclusive_group(required=True)
    strat_group.add_argument("--strategy", metavar="NAME", help="Strategy name (e.g. momentum)")
    strat_group.add_argument("--all-strategies", action="store_true", help="Run all available strategies")

    p.add_argument("--resolution", default="M1", help="Bar resolution (default: M1)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_strategies(args: argparse.Namespace, config_dir: Path) -> list[str]:
    if args.all_strategies:
        return list_strategies(config_dir)
    return [args.strategy]


def _resolve_epics(args: argparse.Namespace, config_dir: Path) -> list[str]:
    if args.all_epics:
        wl_path = config_dir / "watchlist.yaml"
        with open(wl_path) as f:
            wl = yaml.safe_load(f)
        epics: list[str] = []
        for group_epics in wl.values():
            epics.extend(group_epics)
        return epics
    return [args.epic]


def _load_risk(config_dir: Path) -> dict:
    with open(config_dir / "risk.yaml") as f:
        return yaml.safe_load(f)


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


if __name__ == "__main__":
    main()
