"""Shared session state — lives for the duration of one start_session / end_session pair."""

import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class SessionState:
    session_id: str
    conn: sqlite3.Connection
    client: object                          # CapitalClient — typed as object to avoid circular import
    config_dir: Path
    db_path: str
    audit_log_path: str
    started_at: datetime
    monitor_proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    session_end_time: Optional[datetime] = None


_active: Optional[SessionState] = None


def get_state() -> Optional[SessionState]:
    return _active


def set_state(state: SessionState) -> None:
    global _active
    _active = state


def clear_state() -> None:
    global _active
    _active = None


def require_state() -> SessionState:
    if _active is None:
        raise RuntimeError("No active session. Call start_session first.")
    return _active
