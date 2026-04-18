"""Unit tests for MCP tools — CapitalClient mocked, no network calls."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import cfd_trading.tools._state as _state_mod
from cfd_trading.storage.db import get_connection, init_db
from cfd_trading.tools._state import SessionState
from cfd_trading.tools.trade_tools import execute_trade, validate_proposal
from cfd_trading.tools.scan_tools import (
    _compute_atr,
    _compute_trend_slope,
    _compute_spread,
    _summarise_candles,
    scan_markets,
    analyze_instrument,
)
from cfd_trading.tools.session_tools import get_session_status

CONFIG_DIR = Path(__file__).parents[2] / "config"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    conn = get_connection(path)
    from cfd_trading.storage.repository import create_session
    sid = create_session(conn)
    return conn, sid


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_positions.return_value = {"positions": []}
    client.get_account_info.return_value = {
        "accounts": [{"balance": {"available": 8000.0, "deposit": 2000.0}}]
    }
    client.get_client_sentiment.return_value = {
        "clientSentiments": [{"longPositionPercentage": 55.0, "shortPositionPercentage": 45.0}]
    }
    return client


@pytest.fixture(autouse=True)
def clean_state():
    """Ensure state is cleared before and after every test."""
    _state_mod.clear_state()
    yield
    _state_mod.clear_state()


@pytest.fixture
def active_state(db, mock_client):
    conn, session_id = db
    state = SessionState(
        session_id=session_id,
        conn=conn,
        client=mock_client,
        config_dir=CONFIG_DIR,
        db_path=":memory:",
        audit_log_path="/tmp/audit.jsonl",
        started_at=datetime.now(timezone.utc),
    )
    _state_mod.set_state(state)
    return state


def _mock_bars(n=30, base=1.0800, step=0.0002):
    bars = []
    price = base
    for i in range(n):
        price += step
        bars.append({
            "snapshotTime": f"2026-04-18T{9 + i // 60:02d}:{i % 60:02d}:00",
            "openPrice":  {"bid": price - 0.0005, "ask": price},
            "highPrice":  {"bid": price + 0.0010, "ask": price + 0.0011},
            "lowPrice":   {"bid": price - 0.0010, "ask": price - 0.0009},
            "closePrice": {"bid": price, "ask": price + 0.0001},
            "lastTradedVolume": 100,
        })
    return bars


# ---------------------------------------------------------------------------
# Market analysis helpers
# ---------------------------------------------------------------------------

def test_compute_atr_returns_float():
    bars = _mock_bars(20)
    atr = _compute_atr(bars)
    assert atr is not None
    assert atr > 0


def test_compute_atr_too_few_bars_returns_none():
    assert _compute_atr([_mock_bars(1)[0]]) is None


def test_compute_trend_slope_uptrend():
    bars = _mock_bars(30, base=1.0800, step=0.0005)  # increasing prices
    slope = _compute_trend_slope(bars)
    assert slope > 0


def test_compute_trend_slope_downtrend():
    bars = _mock_bars(30, base=1.0900, step=-0.0005)  # decreasing prices
    slope = _compute_trend_slope(bars)
    assert slope < 0


def test_compute_spread_returns_positive():
    bars = _mock_bars(5)
    atr = _compute_atr(bars)
    spread, pct = _compute_spread(bars, atr)
    assert spread is not None
    assert spread > 0
    assert pct is not None and pct > 0


def test_summarise_candles_returns_last_n():
    bars = _mock_bars(30)
    summary = _summarise_candles(bars, n=10)
    assert len(summary) == 10
    assert "close_bid" in summary[0]
    assert "time" in summary[0]


# ---------------------------------------------------------------------------
# validate_proposal
# ---------------------------------------------------------------------------

def _valid_proposal_json(strategy="momentum", action="OPEN", size=1.0, sl_pct=2.0):
    return json.dumps({
        "cycle_id": "test-cycle",
        "timestamp": "2026-04-18T09:00:00+00:00",
        "asset": "EURUSD",
        "strategy": strategy,
        "decision": {
            "action": action,
            "direction": "LONG",
            "size": size,
            "entry_type": "MARKET",
            "entry_level": None,
            "stop_loss": {"type": "HARD", "value": 1.0750, "pct_from_entry": sl_pct},
            "trailing_stop": None,
            "take_profit": None,
        },
        "reasoning": {
            "market_context": "uptrend",
            "signal_basis": "breakout",
            "risk_considerations": "low spread",
            "contra_indicators": "RSI overbought",
        },
        "data_used": {"candles": "60x1min", "sentiment": "55% long", "positions_open": 0},
    })


def test_validate_proposal_valid_passes(active_state):
    result = json.loads(validate_proposal(_valid_proposal_json()))
    assert result["passed"] is True
    assert result["violations"] == []


def test_validate_proposal_invalid_json(active_state):
    result = json.loads(validate_proposal("not json"))
    assert result["passed"] is False
    assert any("Invalid JSON" in v for v in result["violations"])


def test_validate_proposal_missing_stop_loss_rejected(active_state):
    proposal = json.loads(_valid_proposal_json())
    proposal["decision"]["stop_loss"] = None
    result = json.loads(validate_proposal(json.dumps(proposal)))
    assert result["passed"] is False
    assert any("stop_loss" in v for v in result["violations"])


def test_validate_proposal_size_too_large_rejected(active_state):
    result = json.loads(validate_proposal(_valid_proposal_json(size=99.0)))
    assert result["passed"] is False
    assert any("exceeds strategy maximum" in v for v in result["violations"])


def test_validate_proposal_missing_contra_indicators_rejected(active_state):
    proposal = json.loads(_valid_proposal_json())
    proposal["reasoning"]["contra_indicators"] = ""
    result = json.loads(validate_proposal(json.dumps(proposal)))
    assert result["passed"] is False
    assert any("contra_indicators" in v for v in result["violations"])


def test_validate_proposal_unknown_strategy_rejected(active_state):
    result = json.loads(validate_proposal(_valid_proposal_json(strategy="ghost")))
    assert result["passed"] is False
    assert any("Strategy load failed" in v for v in result["violations"])


def test_validate_proposal_returns_open_position_count(active_state):
    active_state.client.get_positions.return_value = {
        "positions": [{"position": {"dealId": "d1"}, "market": {"epic": "GBPUSD"}}]
    }
    result = json.loads(validate_proposal(_valid_proposal_json()))
    assert result["open_positions_count"] == 1


# ---------------------------------------------------------------------------
# execute_trade
# ---------------------------------------------------------------------------

def test_execute_trade_calls_create_position(active_state):
    active_state.client.create_position.return_value = {"dealReference": "ref-001"}
    active_state.client.confirm_deal.return_value = {
        "dealId": "deal-001", "dealStatus": "ACCEPTED", "level": 1.0802
    }
    result = json.loads(execute_trade(_valid_proposal_json()))
    assert result["status"] == "executed"
    assert result["deal_id"] == "deal-001"
    active_state.client.create_position.assert_called_once()


def test_execute_trade_does_not_call_broker_if_preflight_fails(active_state):
    # size=99 will fail preflight
    result = json.loads(execute_trade(_valid_proposal_json(size=99.0)))
    assert result["status"] == "rejected"
    active_state.client.create_position.assert_not_called()


def test_execute_trade_broker_error_returned(active_state):
    active_state.client.create_position.return_value = {
        "error": "Insufficient margin", "details": "..."
    }
    result = json.loads(execute_trade(_valid_proposal_json()))
    assert result["status"] == "error"
    assert "Insufficient margin" in result["message"]


def test_execute_trade_logs_to_db(active_state, db):
    conn, session_id = db
    active_state.client.create_position.return_value = {"dealReference": "ref-002"}
    active_state.client.confirm_deal.return_value = {
        "dealId": "deal-002", "dealStatus": "ACCEPTED", "level": 1.0802
    }
    execute_trade(_valid_proposal_json())
    trades = conn.execute("SELECT * FROM trades WHERE session_id=?", (session_id,)).fetchall()
    assert len(trades) == 1
    assert trades[0]["status"] == "EXECUTED"
    assert trades[0]["strategy"] == "momentum"


def test_execute_trade_invalid_json_returns_error(active_state):
    result = json.loads(execute_trade("{bad json"))
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# scan_markets
# ---------------------------------------------------------------------------

def test_scan_markets_returns_expected_keys(active_state):
    bars = _mock_bars(30)
    active_state.client.get_prices.return_value = {"prices": bars}
    result = json.loads(scan_markets("EURUSD"))
    assert "instruments" in result
    assert "scan_prompt" in result
    assert len(result["instruments"]) == 1
    inst = result["instruments"][0]
    assert inst["epic"] == "EURUSD"
    assert "atr" in inst
    assert "trend_direction" in inst


def test_scan_markets_skips_api_errors(active_state):
    active_state.client.get_prices.return_value = {"error": "unavailable"}
    result = json.loads(scan_markets("EURUSD"))
    assert result["instruments"] == []


def test_scan_markets_uses_watchlist_yaml_by_default(active_state):
    bars = _mock_bars(30)
    active_state.client.get_prices.return_value = {"prices": bars}
    result = json.loads(scan_markets())
    # watchlist.yaml has multiple instruments
    assert len(result["instruments"]) > 1


# ---------------------------------------------------------------------------
# analyze_instrument
# ---------------------------------------------------------------------------

def test_analyze_instrument_returns_expected_keys(active_state):
    bars = _mock_bars(60)
    active_state.client.get_prices.return_value = {"prices": bars}
    result = json.loads(analyze_instrument("EURUSD", "momentum"))
    assert "base_prompt" in result
    assert "strategy_prompt" in result
    assert "strategy_config" in result
    assert "candles" in result
    assert "analysis" in result
    assert len(result["candles"]) <= 20


def test_analyze_instrument_includes_sentiment(active_state):
    active_state.client.get_prices.return_value = {"prices": _mock_bars(60)}
    result = json.loads(analyze_instrument("EURUSD", "momentum"))
    assert result["sentiment"]["long_pct"] == 55.0


def test_analyze_instrument_flags_existing_position(active_state):
    active_state.client.get_prices.return_value = {"prices": _mock_bars(60)}
    active_state.client.get_positions.return_value = {
        "positions": [{"position": {"dealId": "d1"}, "market": {"epic": "EURUSD"}}]
    }
    result = json.loads(analyze_instrument("EURUSD", "momentum"))
    assert len(result["open_positions_in_instrument"]) == 1


# ---------------------------------------------------------------------------
# get_session_status
# ---------------------------------------------------------------------------

def test_get_session_status_no_positions(active_state):
    result = json.loads(get_session_status())
    assert result["open_positions"] == []
    assert result["total_unrealised_pnl"] == 0.0
    assert "duration_minutes" in result
    assert "monitor_alive" in result


def test_get_session_status_with_positions(active_state):
    active_state.client.get_positions.return_value = {
        "positions": [{
            "position": {
                "dealId": "d1", "direction": "BUY", "size": 1.0,
                "level": 1.08, "stopLevel": 1.07, "profitLevel": 1.10, "upl": 25.0
            },
            "market": {"epic": "EURUSD"}
        }]
    }
    result = json.loads(get_session_status())
    assert len(result["open_positions"]) == 1
    assert result["total_unrealised_pnl"] == 25.0


def test_get_session_status_requires_active_session():
    _state_mod.clear_state()
    with pytest.raises(RuntimeError, match="No active session"):
        get_session_status()
