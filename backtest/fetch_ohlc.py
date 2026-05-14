"""
fetch_ohlc.py — MT5 historical bar fetch for CFD backtesting.

Runs on WINDOWS PYTHON only (MetaTrader5 package uses Windows IPC).
Do NOT run from WSL2.

Usage:
    # M1 initial load (4×30-day windows; ~3 months coverage)
    python fetch_ohlc.py --mode bulk

    # M15 or H1 native fetch — single call per instrument; spans years
    python fetch_ohlc.py --mode bulk --resolution M15 --years 3
    python fetch_ohlc.py --mode bulk --resolution H1  --years 6

    # Daily incremental update (M1)
    python fetch_ohlc.py --mode incremental

    # Restrict to a subset of instruments
    python fetch_ohlc.py --mode bulk --resolution M15 --years 3 \\
        --instruments EURUSD,GBPUSD,USDJPY,EURGBP,US500,DE40,UK100,GOLD

Output: C:\\Users\\chris\\dev\\trading-data\\trading.db
  Table: ohlc_bars(epic, resolution, ts, open, high, low, close, volume)
"""

import argparse
import datetime
import os
import sqlite3
import sys

import MetaTrader5 as mt5

DB_PATH = r"C:\Users\chris\dev\trading-data\trading.db"

# All watchlist epics. Two commodities have different MT5 symbol names.
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

# Resolution name → (MT5 TIMEFRAME constant, bucket seconds).
# Bucket seconds drives the per-call row math against the ~100k cap.
RESOLUTION_TF = {
    "M1":  (mt5.TIMEFRAME_M1,  60),
    "M15": (mt5.TIMEFRAME_M15, 15 * 60),
    "H1":  (mt5.TIMEFRAME_H1,  60 * 60),
}


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlc_bars (
            epic       TEXT    NOT NULL,
            resolution TEXT    NOT NULL,
            ts         INTEGER NOT NULL,
            open       REAL    NOT NULL,
            high       REAL    NOT NULL,
            low        REAL    NOT NULL,
            close      REAL    NOT NULL,
            volume     INTEGER NOT NULL,
            PRIMARY KEY (epic, resolution, ts)
        )
    """)
    conn.commit()


def fetch_window(
    epic: str,
    mt5_symbol: str,
    resolution: str,
    from_dt: datetime.datetime,
    to_dt: datetime.datetime,
) -> list[tuple]:
    """Fetch one time window for one instrument at the given resolution.
    Returns rows ready for INSERT.
    """
    tf, _ = RESOLUTION_TF[resolution]

    if not mt5.symbol_select(mt5_symbol, True):
        print(f"  WARNING: symbol_select({mt5_symbol}) failed — {mt5.last_error()}")
        return []

    rates = mt5.copy_rates_range(mt5_symbol, tf, from_dt, to_dt)
    if rates is None or len(rates) == 0:
        err = mt5.last_error()
        # -2 ("Invalid params") here typically means from_dt predates available
        # history. Treat as info, not a warning, since chunked fetches
        # legitimately probe before the data exists.
        if err and err[0] == -2:
            print(f"    (no history for {mt5_symbol} before {to_dt.date()})")
        else:
            print(f"  WARNING: no data for {mt5_symbol} {from_dt.date()}→{to_dt.date()} — {err}")
        return []

    rows = [
        (epic, resolution, int(r["time"]), float(r["open"]), float(r["high"]),
         float(r["low"]), float(r["close"]), int(r["tick_volume"]))
        for r in rates
    ]
    return rows


def write_rows(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """Upsert rows; returns number of new rows inserted (duplicates silently ignored)."""
    if not rows:
        return 0
    before = conn.execute("SELECT changes()").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO ohlc_bars (epic, resolution, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    inserted = conn.execute("SELECT changes()").fetchone()[0]
    return inserted


def bulk_fetch(conn: sqlite3.Connection, instruments: dict[str, str],
               resolution: str, years: int) -> None:
    """Initial load. Window strategy depends on resolution:

    - M1: four 30-day windows per instrument (covers ~3 months; each window
      caps at ~43,200 bars, safely under MT5's ~100k per-call cap).
    - M15 / H1: yearly windows across `years`. A single big window often
      fails with -2 "Invalid params" when from_dt predates available data;
      chunking sidesteps that — early chunks may return 0 rows but later
      chunks succeed cleanly.

    Within each year:
      M15 ≈ 25k bars at 24/5 — well under cap.
      H1  ≈  6k bars at 24/5 — well under cap.
    """
    now = datetime.datetime.now()

    if resolution == "M1":
        windows = [
            (now - datetime.timedelta(days=120), now - datetime.timedelta(days=90)),
            (now - datetime.timedelta(days=90),  now - datetime.timedelta(days=60)),
            (now - datetime.timedelta(days=60),  now - datetime.timedelta(days=30)),
            (now - datetime.timedelta(days=30),  now),
        ]
    else:
        # Yearly chunks, oldest first.
        windows = []
        for i in range(years, 0, -1):
            from_dt = now - datetime.timedelta(days=i * 365)
            to_dt = now - datetime.timedelta(days=(i - 1) * 365)
            windows.append((from_dt, to_dt))

    for epic, mt5_symbol in instruments.items():
        total = 0
        for from_dt, to_dt in windows:
            print(f"  {epic} [{resolution}]: fetching {from_dt.date()} → {to_dt.date()} ...")
            rows = fetch_window(epic, mt5_symbol, resolution, from_dt, to_dt)
            inserted = write_rows(conn, rows)
            total += inserted
            print(f"    {len(rows):,} bars fetched, {inserted:,} new rows written")
        print(f"  {epic} [{resolution}]: bulk complete — {total:,} total new rows\n")


def incremental_fetch(conn: sqlite3.Connection, instruments: dict[str, str],
                      resolution: str) -> None:
    """Daily update: yesterday midnight → now for each instrument."""
    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=1)
    from_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)

    for epic, mt5_symbol in instruments.items():
        print(f"  {epic} [{resolution}]: fetching {from_dt.date()} → today ...")
        rows = fetch_window(epic, mt5_symbol, resolution, from_dt, now)
        inserted = write_rows(conn, rows)
        print(f"    {len(rows):,} bars fetched, {inserted:,} new rows written")


def bar_counts(conn: sqlite3.Connection, resolution: str) -> None:
    print(f"\nRow counts by epic [{resolution}]:")
    rows = conn.execute(
        "SELECT epic, COUNT(*) FROM ohlc_bars WHERE resolution=? GROUP BY epic ORDER BY epic",
        (resolution,),
    ).fetchall()
    if not rows:
        print("  (empty)")
    for epic, count in rows:
        print(f"  {epic:10s}  {count:>10,} bars")


def _resolve_instruments(arg: str | None) -> dict[str, str]:
    """--instruments comma-separated names → filtered MT5_SYMBOL mapping."""
    if arg is None:
        return MT5_SYMBOL
    wanted = [s.strip() for s in arg.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in MT5_SYMBOL]
    if unknown:
        print(f"ERROR: unknown instruments: {unknown}. Known: {list(MT5_SYMBOL)}")
        sys.exit(1)
    return {epic: MT5_SYMBOL[epic] for epic in wanted}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch MT5 OHLC bars into SQLite")
    parser.add_argument(
        "--mode",
        choices=["bulk", "incremental"],
        required=True,
        help="bulk: full historical load; incremental: yesterday → today",
    )
    parser.add_argument(
        "--resolution",
        choices=list(RESOLUTION_TF.keys()),
        default="M1",
        help="Bar resolution: M1 (default), M15, H1. M1 uses 4×30-day windows; "
             "M15/H1 use a single window across --years.",
    )
    parser.add_argument(
        "--years", type=int, default=3,
        help="History depth in years for M15/H1 bulk fetch (default: 3). Ignored for M1.",
    )
    parser.add_argument(
        "--instruments", default=None,
        help="Optional comma-separated subset of instruments (default: all). "
             "Example: EURUSD,GBPUSD,US500,DE40",
    )
    args = parser.parse_args()

    instruments = _resolve_instruments(args.instruments)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    print("Connecting to MetaTrader 5 ...")
    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed — {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    if account:
        print(f"Connected: {account.server}, login {account.login}, balance {account.balance} {account.currency}\n")

    try:
        conn = sqlite3.connect(DB_PATH)
        init_db(conn)

        if args.mode == "bulk":
            label = f"4×30-day windows" if args.resolution == "M1" else f"{args.years}-year single window"
            print(f"=== Bulk fetch — {args.resolution} ({label}, {len(instruments)} instruments) ===\n")
            bulk_fetch(conn, instruments, args.resolution, args.years)
        else:
            print(f"=== Incremental fetch — {args.resolution} (yesterday → today, {len(instruments)} instruments) ===\n")
            incremental_fetch(conn, instruments, args.resolution)

        bar_counts(conn, args.resolution)
        conn.close()

    finally:
        mt5.shutdown()
        print("\nDone.")


if __name__ == "__main__":
    main()
