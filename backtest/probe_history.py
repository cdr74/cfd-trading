"""
probe_history.py — MT5 native-history probe for backtest window extension.

Runs on WINDOWS PYTHON only (MetaTrader5 package uses Windows IPC).
Do NOT run from WSL2.

Purpose:
    Determine the earliest available bar per (instrument, resolution) on
    Capital.com's MT5 demo server. The Phase 10 fetch was M1-only and hit
    the broker's history cap at ~14 weeks. H1 and M15 typically expose
    longer history because brokers retain coarser resolutions longer.

    Output informed Phase A6 of the (now closed) strategy audit — see
    cfd-trading/docs/STRATEGY_AUDIT.md.

Usage:
    python probe_history.py
    python probe_history.py --years 5     # max walk-back (default: 5)
    python probe_history.py --csv out.csv # also write a CSV summary

How it works:
    For each (epic, resolution) pair, call mt5.copy_rates_range with the
    widest reasonable window. If MT5 returns rows, record the earliest
    timestamp and total row count. If it returns nothing, halve the
    window and retry until either we find data or the window is < 30 days.

    A single call is normally enough — MT5 silently truncates results at
    the per-call row cap (~100k), so the earliest returned bar marks the
    deepest reachable history for that resolution.
"""

import argparse
import csv
import datetime
import sys

import MetaTrader5 as mt5

# Same epic→MT5 symbol map as fetch_ohlc.py — keep in sync.
MT5_SYMBOL = {
    "EURUSD":  "EURUSD",
    "GBPUSD":  "GBPUSD",
    "USDJPY":  "USDJPY",
    "EURGBP":  "EURGBP",
    "US500":   "US500",
    "DE40":    "DE40",
    "UK100":   "UK100",
    "GOLD":    "XAUUSD",
    "XBRUSD":  "BRENTOIL",
    "BTCUSD":  "BTCUSD",
    "ETHUSD":  "ETHUSD",
}

RESOLUTIONS = [
    ("H1",  mt5.TIMEFRAME_H1),
    ("M15", mt5.TIMEFRAME_M15),
    ("M1",  mt5.TIMEFRAME_M1),
]


def probe_one(symbol: str, tf_code: int, max_years: int) -> tuple[int | None, int]:
    """Return (earliest_ts, n_bars) for one (symbol, timeframe).

    Walk-back strategy: start at max_years, halve on empty returns until
    we find data or the window shrinks below 30 days.
    """
    if not mt5.symbol_select(symbol, True):
        print(f"    WARNING: symbol_select({symbol}) failed — {mt5.last_error()}")
        return None, 0

    now = datetime.datetime.now()
    days = max_years * 365
    while days >= 30:
        from_dt = now - datetime.timedelta(days=days)
        rates = mt5.copy_rates_range(symbol, tf_code, from_dt, now)
        if rates is not None and len(rates) > 0:
            return int(rates[0]["time"]), len(rates)
        days //= 2

    return None, 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe MT5 native history depth at H1/M15/M1")
    parser.add_argument("--years", type=int, default=5, help="Maximum walk-back in years (default: 5)")
    parser.add_argument("--csv", metavar="PATH", help="Optional CSV output path")
    args = parser.parse_args()

    print("Connecting to MetaTrader 5 ...")
    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed — {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    if account:
        print(f"Connected: {account.server}, login {account.login}\n")

    results: list[tuple[str, str, int | None, int]] = []
    try:
        print(f"{'epic':10s} {'res':5s} {'earliest_utc':25s} {'n_bars':>10s}")
        print("-" * 60)
        for epic, symbol in MT5_SYMBOL.items():
            for res_name, tf_code in RESOLUTIONS:
                ts, n = probe_one(symbol, tf_code, args.years)
                if ts is None:
                    earliest = "(no data)"
                else:
                    earliest = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{epic:10s} {res_name:5s} {earliest:25s} {n:>10,}")
                results.append((epic, res_name, ts, n))
            print()
    finally:
        mt5.shutdown()

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["epic", "resolution", "earliest_ts_utc", "earliest_iso", "n_bars"])
            for epic, res, ts, n in results:
                iso = datetime.datetime.utcfromtimestamp(ts).isoformat() if ts else ""
                w.writerow([epic, res, ts or "", iso, n])
        print(f"Wrote {args.csv}")

    print("\nDone.")
    print("(Historical) strategy audit closed — see docs/STRATEGY_AUDIT.md. A6 heuristic:")
    print("  - A6 fires if H1 history >= 2x M1 history, OR M15 history >= 6 months")
    print("  - A6 skipped if history is shallow at all resolutions")


if __name__ == "__main__":
    main()
