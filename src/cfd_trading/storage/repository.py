"""CRUD operations: sessions, trades, cycle_snapshots, reasoning_traces, ohlc_bars."""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# OHLC bars
# ---------------------------------------------------------------------------

@dataclass
class OHLCBar:
    epic: str
    resolution: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: int


def get_bars(
    conn: sqlite3.Connection,
    epic: str,
    resolution: str,
    from_ts: int | None = None,
    to_ts: int | None = None,
) -> list[OHLCBar]:
    """Return bars for epic/resolution in chronological order.

    from_ts / to_ts are inclusive Unix timestamps (seconds UTC).
    Omit either bound to fetch from the beginning or up to the latest bar.
    """
    query = "SELECT epic, resolution, ts, open, high, low, close, volume FROM ohlc_bars WHERE epic=? AND resolution=?"
    params: list = [epic, resolution]

    if from_ts is not None:
        query += " AND ts >= ?"
        params.append(from_ts)
    if to_ts is not None:
        query += " AND ts <= ?"
        params.append(to_ts)

    query += " ORDER BY ts ASC"

    rows = conn.execute(query, params).fetchall()
    return [OHLCBar(*row) for row in rows]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(conn: sqlite3.Connection) -> str:
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (id, started_at, status) VALUES (?, ?, 'ACTIVE')",
        (session_id, _now()),
    )
    conn.commit()
    return session_id


def close_session(conn: sqlite3.Connection, session_id: str, summary: dict) -> None:
    conn.execute(
        "UPDATE sessions SET ended_at=?, status='CLOSED', summary=? WHERE id=?",
        (_now(), json.dumps(summary), session_id),
    )
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sessions WHERE id=?", (session_id,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Cycle snapshots
# ---------------------------------------------------------------------------

def save_cycle_snapshot(
    conn: sqlite3.Connection,
    session_id: str,
    asset: str,
    strategy: str,
    account_bal: float,
    positions: list,
    market_data: dict,
) -> int:
    cur = conn.execute(
        """INSERT INTO cycle_snapshots
           (session_id, ts, asset, strategy, account_bal, positions, market_data)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, _now(), asset, strategy,
            account_bal,
            json.dumps(positions),
            json.dumps(market_data),
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def save_trade(
    conn: sqlite3.Connection,
    session_id: str,
    cycle_id: str,
    asset: str,
    direction: str,
    size: float,
    strategy: str | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    broker_ref: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO trades
           (session_id, cycle_id, ts, asset, strategy, direction, size,
            entry_price, stop_loss, take_profit, status, broker_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROPOSED', ?)""",
        (
            session_id, cycle_id, _now(), asset, strategy, direction, size,
            entry_price, stop_loss, take_profit, broker_ref,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_trade_by_broker_ref(conn: sqlite3.Connection, broker_ref: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM trades WHERE broker_ref=? ORDER BY id DESC LIMIT 1",
        (broker_ref,),
    ).fetchone()


def update_trade_stop_loss(conn: sqlite3.Connection, trade_id: int, new_stop_loss: float) -> None:
    conn.execute("UPDATE trades SET stop_loss=? WHERE id=?", (new_stop_loss, trade_id))
    conn.commit()


VALID_STATUSES = {"PROPOSED", "APPROVED", "REJECTED", "EXECUTED", "FAILED"}


def update_trade_status(
    conn: sqlite3.Connection, trade_id: int, status: str
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}.")
    conn.execute("UPDATE trades SET status=? WHERE id=?", (status, trade_id))
    conn.commit()


def get_open_trades(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM trades WHERE status='EXECUTED'"
    ).fetchall()


# ---------------------------------------------------------------------------
# Reasoning traces
# ---------------------------------------------------------------------------

def save_reasoning_trace(
    conn: sqlite3.Connection,
    session_id: str,
    cycle_id: str,
    prompt_tokens: int,
    output_tokens: int,
    reasoning: str,
    tool_calls: list | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO reasoning_traces
           (session_id, cycle_id, ts, prompt_tokens, output_tokens, reasoning, tool_calls)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, cycle_id, _now(),
            prompt_tokens, output_tokens,
            reasoning,
            json.dumps(tool_calls or []),
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------

@dataclass
class SessionSummary:
    session_id: str
    total_trades: int
    executed_trades: int
    rejected_trades: int
    win_rate: float | None
    total_pnl: float | None
    max_drawdown: float | None


def get_session_summary(conn: sqlite3.Connection, session_id: str) -> SessionSummary:
    rows = conn.execute(
        "SELECT status FROM trades WHERE session_id=?", (session_id,)
    ).fetchall()

    total = len(rows)
    executed = sum(1 for r in rows if r["status"] == "EXECUTED")
    rejected = sum(1 for r in rows if r["status"] == "REJECTED")

    # P&L and win-rate require execution data not yet available in the schema.
    # Returning None placeholders — to be populated from broker data at session end.
    return SessionSummary(
        session_id=session_id,
        total_trades=total,
        executed_trades=executed,
        rejected_trades=rejected,
        win_rate=None,
        total_pnl=None,
        max_drawdown=None,
    )
