"""Momentum EMA gap filter tuning script.

Runs momentum backtest across all watchlist instruments for each candidate
gap threshold value and prints a comparison table so the optimal value can
be chosen.

Usage:
    python -m cfd_trading.backtest.tune_momentum_gap

Environment:
    BACKTEST_DB_PATH  — path to SQLite DB (default: same as run.py)
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("BACKTEST_MODE", "true")

import yaml

from cfd_trading.storage.db import get_connection
from cfd_trading.storage.repository import get_bars
from cfd_trading.strategy.loader import load_strategy
from cfd_trading.backtest.engine import run_backtest, BacktestResult

_DEFAULT_DB_PATH = "/mnt/c/Users/chris/dev/trading-data/trading.db"
_CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).parents[3] / "config")))

# Candidate gap thresholds to test (fractional, e.g. 0.002 = 0.2%)
_GAP_CANDIDATES = [0.0, 0.0002, 0.0005, 0.001, 0.002, 0.003, 0.005, 0.008, 0.010, 0.015]


def main() -> None:
    db_path = os.getenv("BACKTEST_DB_PATH", _DEFAULT_DB_PATH)
    if not Path(db_path).exists():
        print(f"Error: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    config_dir = _CONFIG_DIR
    strat = load_strategy("momentum", config_dir)
    risk_cfg = _load_risk(config_dir)
    epics = _load_epics(config_dir)

    conn = get_connection(db_path)
    bars_by_epic = {e: get_bars(conn, e, "M1") for e in epics}
    conn.close()

    # --- Per-gap aggregate metrics ---
    print(f"\nTuning momentum EMA gap filter across {len(epics)} instruments\n")
    _print_header()

    for gap in _GAP_CANDIDATES:
        results: list[BacktestResult] = []
        for epic, bars in bars_by_epic.items():
            if not bars:
                continue
            r = run_backtest(
                epic, "momentum", bars, strat.config, risk_cfg,
                signal_kwargs={"min_ema_gap_pct": gap},
            )
            results.append(r)
        _print_gap_row(gap, results)

    print()
    print("Columns: gap%  instruments_with_trades  total_trades  "
          "avg_sig/wk  avg_stop%  avg_PF  positive_PF_count")


def _print_header() -> None:
    print(f"{'Gap%':>6}  {'w/trades':>8}  {'trades':>7}  "
          f"{'sig/wk':>7}  {'stop%':>6}  {'avg_PF':>7}  {'PF>1':>5}")
    print("-" * 60)


def _print_gap_row(gap: float, results: list[BacktestResult]) -> None:
    active = [r for r in results if r.total_trades > 0]
    if not active:
        print(f"{gap * 100:>5.3f}%  {'0':>8}  {'0':>7}  "
              f"{'—':>7}  {'—':>6}  {'—':>7}  {'—':>5}")
        return

    total_trades  = sum(r.total_trades for r in active)
    avg_sig_wk    = sum(r.signal_frequency for r in active) / len(active)
    avg_stop_pct  = sum(r.stop_out_rate for r in active) / len(active) * 100
    finite_pf     = [r.profit_factor for r in active if r.profit_factor != float("inf")]
    avg_pf        = sum(finite_pf) / len(finite_pf) if finite_pf else float("inf")
    pf_above_1    = sum(1 for r in active if r.profit_factor > 1.0)

    pf_str = f"{avg_pf:.3f}" if avg_pf != float("inf") else "  inf"
    print(f"{gap * 100:>5.3f}%  {len(active):>8}  {total_trades:>7}  "
          f"{avg_sig_wk:>7.2f}  {avg_stop_pct:>5.1f}%  {pf_str:>7}  {pf_above_1:>5}")


def _load_risk(config_dir: Path) -> dict:
    with open(config_dir / "risk.yaml") as f:
        return yaml.safe_load(f)


def _load_epics(config_dir: Path) -> list[str]:
    with open(config_dir / "watchlist.yaml") as f:
        wl = yaml.safe_load(f)
    epics: list[str] = []
    for group_epics in wl.values():
        epics.extend(group_epics)
    return epics


if __name__ == "__main__":
    main()
