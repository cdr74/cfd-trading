"""SQLite initialisation and schema creation."""

import sqlite3
from pathlib import Path


def get_connection(db_path: str = "") -> sqlite3.Connection:
    path = db_path or _default_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = "") -> None:
    """Create schema if not present. Safe to call on every startup."""
    with get_connection(db_path) as conn:
        conn.executescript(_SCHEMA)


def _default_db_path() -> str:
    import os
    env_path = os.getenv("DB_PATH")
    if env_path:
        return env_path
    data_dir = Path(__file__).parents[4] / "data"
    data_dir.mkdir(exist_ok=True)
    return str(data_dir / "trading.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlc_bars (
    epic        TEXT    NOT NULL,
    resolution  TEXT    NOT NULL,
    ts          INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL,
    PRIMARY KEY (epic, resolution, ts)
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS cycle_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    ts          TEXT NOT NULL,
    asset       TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    account_bal REAL,
    positions   TEXT,
    market_data TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    cycle_id    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    asset       TEXT NOT NULL,
    strategy    TEXT,
    direction   TEXT NOT NULL,
    size        REAL NOT NULL,
    entry_price REAL,
    stop_loss   REAL,
    take_profit REAL,
    status      TEXT NOT NULL DEFAULT 'PROPOSED',
    broker_ref  TEXT
);

CREATE TABLE IF NOT EXISTS reasoning_traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(id),
    cycle_id      TEXT NOT NULL,
    ts            TEXT NOT NULL,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    reasoning     TEXT,
    tool_calls    TEXT
);
"""
