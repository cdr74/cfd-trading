"""
fetch_ohlc.py — MT5 historical bar fetch for CFD backtesting.

Runs on WINDOWS PYTHON only (MetaTrader5 package uses Windows IPC).
Do NOT run from WSL2.

Usage:
    python fetch_ohlc.py --mode bulk         # full 3-month initial load
    python fetch_ohlc.py --mode incremental  # yesterday → today (run daily)

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

RESOLUTION = "M1"


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
    from_dt: datetime.datetime,
    to_dt: datetime.datetime,
) -> list[tuple]:
    """Fetch one time window for one instrument. Returns rows ready for INSERT."""
    if not mt5.symbol_select(mt5_symbol, True):
        print(f"  WARNING: symbol_select({mt5_symbol}) failed — {mt5.last_error()}")
        return []

    rates = mt5.copy_rates_range(mt5_symbol, mt5.TIMEFRAME_M1, from_dt, to_dt)
    if rates is None or len(rates) == 0:
        print(f"  WARNING: no data for {mt5_symbol} {from_dt.date()}→{to_dt.date()} — {mt5.last_error()}")
        return []

    rows = [
        (epic, RESOLUTION, int(r["time"]), float(r["open"]), float(r["high"]),
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


def bulk_fetch(conn: sqlite3.Connection) -> None:
    """Initial load: four 30-day windows per instrument, covering ~3 months of history.

    30-day windows cap at ~43,200 bars (24h × 60min × 30), well under MT5's 100k row limit.
    Two 60-day windows hit the cap for instruments with dense tick data (DE40, GOLD, etc.).
    """
    now = datetime.datetime.now()
    windows = [
        (now - datetime.timedelta(days=120), now - datetime.timedelta(days=90)),
        (now - datetime.timedelta(days=90),  now - datetime.timedelta(days=60)),
        (now - datetime.timedelta(days=60),  now - datetime.timedelta(days=30)),
        (now - datetime.timedelta(days=30),  now),
    ]

    for epic, mt5_symbol in MT5_SYMBOL.items():
        total = 0
        for from_dt, to_dt in windows:
            print(f"  {epic}: fetching {from_dt.date()} → {to_dt.date()} ...")
            rows = fetch_window(epic, mt5_symbol, from_dt, to_dt)
            inserted = write_rows(conn, rows)
            total += inserted
            print(f"    {len(rows):,} bars fetched, {inserted:,} new rows written")
        print(f"  {epic}: bulk complete — {total:,} total new rows\n")


def incremental_fetch(conn: sqlite3.Connection) -> None:
    """Daily update: yesterday midnight → now for each instrument."""
    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=1)
    from_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)

    for epic, mt5_symbol in MT5_SYMBOL.items():
        print(f"  {epic}: fetching {from_dt.date()} → today ...")
        rows = fetch_window(epic, mt5_symbol, from_dt, now)
        inserted = write_rows(conn, rows)
        print(f"    {len(rows):,} bars fetched, {inserted:,} new rows written")


def bar_counts(conn: sqlite3.Connection) -> None:
    print("\nRow counts by epic:")
    rows = conn.execute(
        "SELECT epic, COUNT(*) FROM ohlc_bars WHERE resolution=? GROUP BY epic ORDER BY epic",
        (RESOLUTION,),
    ).fetchall()
    if not rows:
        print("  (empty)")
    for epic, count in rows:
        print(f"  {epic:10s}  {count:>10,} bars")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch MT5 OHLC bars into SQLite")
    parser.add_argument(
        "--mode",
        choices=["bulk", "incremental"],
        required=True,
        help="bulk: full 3-month initial load (4×30-day windows); incremental: yesterday → today",
    )
    args = parser.parse_args()

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
            print(f"=== Bulk fetch (two 60-day windows per instrument) ===\n")
            bulk_fetch(conn)
        else:
            print(f"=== Incremental fetch (yesterday → today) ===\n")
            incremental_fetch(conn)

        bar_counts(conn)
        conn.close()

    finally:
        mt5.shutdown()
        print("\nDone.")


if __name__ == "__main__":
    main()
