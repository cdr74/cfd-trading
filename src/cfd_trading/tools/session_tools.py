"""MCP tools: start_session, end_session, get_session_status."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from cfd_trading.broker.capital_client import CapitalClient
from cfd_trading.storage.db import get_connection, init_db
from cfd_trading.storage import repository as repo
from cfd_trading.strategy.loader import list_strategies
from cfd_trading.tools._state import SessionState, clear_state, get_state, require_state, set_state

_CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).parents[3] / "config")))
_DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parents[3] / "data" / "trading.db"))
_AUDIT_LOG = os.getenv("AUDIT_LOG_PATH", str(Path(__file__).parents[3] / "data" / "audit.jsonl"))
_MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL_SECONDS", "60"))


def start_session(session_end_time: str | None = None) -> str:
    """
    Authenticate with Capital.com, initialise the database, start the monitor subprocess,
    and return a session summary.

    session_end_time — optional ISO8601 UTC datetime string for time-exit rule,
                       e.g. "2026-04-18T17:00:00+00:00". If omitted, time exit is disabled.
    """
    if get_state() is not None:
        state = get_state()
        return json.dumps({
            "status": "already_active",
            "session_id": state.session_id,
            "started_at": state.started_at.isoformat(),
            "message": "A session is already active. Call end_session first.",
        })

    client = CapitalClient()
    if not client.authenticate():
        return json.dumps({"status": "error", "message": "Authentication with Capital.com failed. Check credentials."})

    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(_AUDIT_LOG).parent.mkdir(parents=True, exist_ok=True)

    init_db(_DB_PATH)
    conn = get_connection(_DB_PATH)
    session_id = repo.create_session(conn)

    parsed_end_time = None
    if session_end_time:
        try:
            parsed_end_time = datetime.fromisoformat(session_end_time)
        except ValueError:
            return json.dumps({"status": "error", "message": f"Invalid session_end_time format: {session_end_time}"})

    monitor_proc = _start_monitor(session_id, parsed_end_time)

    state = SessionState(
        session_id=session_id,
        conn=conn,
        client=client,
        config_dir=_CONFIG_DIR,
        db_path=_DB_PATH,
        audit_log_path=_AUDIT_LOG,
        started_at=datetime.now(timezone.utc),
        monitor_proc=monitor_proc,
        session_end_time=parsed_end_time,
    )
    set_state(state)

    open_positions = client.get_positions().get("positions", [])
    account_info = client.get_account_info()
    balance = _extract_balance(account_info)
    strategies = list_strategies(_CONFIG_DIR)

    return json.dumps({
        "status": "ok",
        "session_id": session_id,
        "started_at": state.started_at.isoformat(),
        "session_end_time": session_end_time,
        "account_balance": balance,
        "open_positions": len(open_positions),
        "available_strategies": strategies,
        "monitor_started": monitor_proc is not None,
        "monitor_interval_seconds": _MONITOR_INTERVAL,
    })


def end_session(close_positions: bool = True) -> str:
    """
    Stop the monitor, optionally close all open positions, write a session summary, and clear state.

    close_positions — if True, close all open Capital.com positions before ending.
                      if False, leave positions open (stop losses already registered at broker).
    """
    state = require_state()

    _stop_monitor(state.monitor_proc)

    closed = []
    if close_positions:
        positions_resp = state.client.get_positions()
        for pos in positions_resp.get("positions", []):
            deal_id = pos.get("position", {}).get("dealId", "")
            epic = pos.get("market", {}).get("epic", "")
            if deal_id:
                result = state.client.close_position(deal_id)
                closed.append({"epic": epic, "deal_id": deal_id, "result": result})
                trade = repo.get_trade_by_broker_ref(state.conn, deal_id)
                if trade:
                    repo.update_trade_status(state.conn, trade["id"], "EXECUTED")

    summary = repo.get_session_summary(state.conn, state.session_id)
    duration_sec = (datetime.now(timezone.utc) - state.started_at).total_seconds()
    summary_dict = {
        "session_id": state.session_id,
        "started_at": state.started_at.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "duration_minutes": round(duration_sec / 60, 1),
        "total_trades": summary.total_trades,
        "executed_trades": summary.executed_trades,
        "rejected_trades": summary.rejected_trades,
        "positions_closed_on_exit": len(closed),
    }
    repo.close_session(state.conn, state.session_id, summary_dict)
    state.conn.close()
    clear_state()

    return json.dumps({"status": "ok", "summary": summary_dict, "closed": closed})


def get_session_status() -> str:
    """Return current positions, unrealised P&L, monitor status, and session duration."""
    state = require_state()

    positions_resp = state.client.get_positions()
    positions = positions_resp.get("positions", [])

    position_summaries = []
    total_pnl = 0.0
    for pos in positions:
        p = pos.get("position", {})
        m = pos.get("market", {})
        pnl = p.get("upl", 0.0) or 0.0
        total_pnl += pnl
        position_summaries.append({
            "epic": m.get("epic"),
            "direction": p.get("direction"),
            "size": p.get("size"),
            "entry_price": p.get("level"),
            "stop_level": p.get("stopLevel"),
            "profit_level": p.get("profitLevel"),
            "unrealised_pnl": pnl,
        })

    monitor_alive = False
    if state.monitor_proc is not None:
        monitor_alive = state.monitor_proc.poll() is None

    duration_sec = (datetime.now(timezone.utc) - state.started_at).total_seconds()

    return json.dumps({
        "session_id": state.session_id,
        "started_at": state.started_at.isoformat(),
        "duration_minutes": round(duration_sec / 60, 1),
        "open_positions": position_summaries,
        "total_unrealised_pnl": round(total_pnl, 2),
        "monitor_alive": monitor_alive,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_monitor(session_id: str, session_end_time: datetime | None) -> subprocess.Popen | None:
    cmd = [
        sys.executable, "-m", "cfd_trading.monitor.monitor",
        "--session-id", session_id,
        "--db-path", _DB_PATH,
        "--config-dir", str(_CONFIG_DIR),
        "--audit-log", _AUDIT_LOG,
        "--interval", str(_MONITOR_INTERVAL),
    ]
    if session_end_time:
        cmd += ["--session-end", session_end_time.isoformat()]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc
    except Exception as e:
        # Monitor failure is non-fatal — session continues, positions protected by broker SL
        import logging
        logging.getLogger("session").warning(f"Monitor subprocess failed to start: {e}")
        return None


def _stop_monitor(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _extract_balance(account_info: dict) -> float | None:
    accounts = account_info.get("accounts", [])
    if accounts:
        return accounts[0].get("balance", {}).get("available")
    return None
