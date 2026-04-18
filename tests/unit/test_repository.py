"""Unit tests for storage/db.py and storage/repository.py."""

import pytest
import sqlite3

from cfd_trading.storage.db import init_db, get_connection
from cfd_trading.storage import repository as repo


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    init_db.__wrapped__(c) if hasattr(init_db, "__wrapped__") else _init_in_memory(c)
    return c


def _init_in_memory(conn: sqlite3.Connection) -> None:
    """Apply schema directly to an in-memory connection."""
    from cfd_trading.storage.db import _SCHEMA
    conn.executescript(_SCHEMA)


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return get_connection(db_path)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def test_create_session_returns_uuid(conn):
    session_id = repo.create_session(conn)
    assert len(session_id) == 36
    row = repo.get_session(conn, session_id)
    assert row is not None
    assert row["status"] == "ACTIVE"
    assert row["ended_at"] is None


def test_close_session(conn):
    session_id = repo.create_session(conn)
    repo.close_session(conn, session_id, summary={"trades": 3, "pnl": 12.5})
    row = repo.get_session(conn, session_id)
    assert row["status"] == "CLOSED"
    assert row["ended_at"] is not None
    import json
    summary = json.loads(row["summary"])
    assert summary["trades"] == 3


def test_get_session_unknown_returns_none(conn):
    assert repo.get_session(conn, "does-not-exist") is None


# ---------------------------------------------------------------------------
# Cycle snapshots
# ---------------------------------------------------------------------------

def test_save_cycle_snapshot(conn):
    session_id = repo.create_session(conn)
    row_id = repo.save_cycle_snapshot(
        conn, session_id,
        asset="EURUSD", strategy="momentum",
        account_bal=10000.0,
        positions=[{"epic": "EURUSD", "size": 1.0}],
        market_data={"bid": 1.08, "ask": 1.0801},
    )
    assert row_id == 1
    row = conn.execute("SELECT * FROM cycle_snapshots WHERE id=1").fetchone()
    assert row["asset"] == "EURUSD"
    assert row["session_id"] == session_id


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def test_save_trade_defaults_to_proposed(conn):
    session_id = repo.create_session(conn)
    trade_id = repo.save_trade(
        conn, session_id, cycle_id="cyc-1",
        asset="EURUSD", direction="LONG", size=1.0,
        entry_price=1.0800, stop_loss=1.0750, take_profit=1.0900,
    )
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    assert row["status"] == "PROPOSED"


def test_update_trade_status_transitions(conn):
    session_id = repo.create_session(conn)
    trade_id = repo.save_trade(
        conn, session_id, "cyc-1", "EURUSD", "LONG", 1.0
    )
    for status in ("APPROVED", "EXECUTED"):
        repo.update_trade_status(conn, trade_id, status)
        row = conn.execute("SELECT status FROM trades WHERE id=?", (trade_id,)).fetchone()
        assert row["status"] == status


def test_update_trade_status_invalid_raises(conn):
    session_id = repo.create_session(conn)
    trade_id = repo.save_trade(conn, session_id, "cyc-1", "EURUSD", "LONG", 1.0)
    with pytest.raises(ValueError, match="Invalid status"):
        repo.update_trade_status(conn, trade_id, "OPEN")


def test_get_open_trades_returns_executed_only(conn):
    session_id = repo.create_session(conn)
    t1 = repo.save_trade(conn, session_id, "cyc-1", "EURUSD", "LONG", 1.0)
    t2 = repo.save_trade(conn, session_id, "cyc-2", "GBPUSD", "SHORT", 0.5)
    repo.update_trade_status(conn, t1, "EXECUTED")
    repo.update_trade_status(conn, t2, "REJECTED")
    open_trades = repo.get_open_trades(conn)
    assert len(open_trades) == 1
    assert open_trades[0]["asset"] == "EURUSD"


# ---------------------------------------------------------------------------
# Reasoning traces
# ---------------------------------------------------------------------------

def test_save_reasoning_trace(conn):
    session_id = repo.create_session(conn)
    trace_id = repo.save_reasoning_trace(
        conn, session_id, cycle_id="cyc-1",
        prompt_tokens=500, output_tokens=200,
        reasoning='{"action": "HOLD"}',
        tool_calls=[{"tool": "get_prices", "args": {}}],
    )
    row = conn.execute("SELECT * FROM reasoning_traces WHERE id=?", (trace_id,)).fetchone()
    assert row["prompt_tokens"] == 500
    assert row["session_id"] == session_id


def test_save_reasoning_trace_no_tool_calls(conn):
    session_id = repo.create_session(conn)
    trace_id = repo.save_reasoning_trace(
        conn, session_id, "cyc-1", 100, 50, '{"action": "HOLD"}'
    )
    import json
    row = conn.execute("SELECT tool_calls FROM reasoning_traces WHERE id=?", (trace_id,)).fetchone()
    assert json.loads(row["tool_calls"]) == []


# ---------------------------------------------------------------------------
# Session summary
# ---------------------------------------------------------------------------

def test_session_summary_counts(conn):
    session_id = repo.create_session(conn)
    t1 = repo.save_trade(conn, session_id, "cyc-1", "EURUSD", "LONG", 1.0)
    t2 = repo.save_trade(conn, session_id, "cyc-2", "GBPUSD", "SHORT", 0.5)
    t3 = repo.save_trade(conn, session_id, "cyc-3", "USDJPY", "LONG", 0.3)
    repo.update_trade_status(conn, t1, "EXECUTED")
    repo.update_trade_status(conn, t2, "REJECTED")
    # t3 stays PROPOSED

    summary = repo.get_session_summary(conn, session_id)
    assert summary.total_trades == 3
    assert summary.executed_trades == 1
    assert summary.rejected_trades == 1


def test_session_summary_empty_session(conn):
    session_id = repo.create_session(conn)
    summary = repo.get_session_summary(conn, session_id)
    assert summary.total_trades == 0
    assert summary.executed_trades == 0
