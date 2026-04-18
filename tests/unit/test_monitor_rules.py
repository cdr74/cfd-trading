"""Unit tests for monitor/monitor.py — evaluate_position rule logic only.

No network calls, no DB, no CapitalClient. All tests exercise the pure
evaluate_position function with constructed position and price dicts.
"""

import pytest
from datetime import datetime, timezone, timedelta

from cfd_trading.monitor.monitor import evaluate_position


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def momentum_cfg():
    return {
        "risk": {
            "stop_loss": {"type": "HARD", "max_pct": 5.0},
            "trailing_stop": {
                "enabled": True,
                "min_distance_pct": 1.0,
                "max_distance_pct": 3.0,
            },
            "take_profit": {"dynamic": False, "min_rr_ratio": 1.5},
            "time_exit": {"enabled": True, "close_minutes_before_session_end": 30},
        }
    }


@pytest.fixture
def mean_rev_cfg():
    return {
        "risk": {
            "stop_loss": {"type": "HARD", "max_pct": 3.0},
            "trailing_stop": {"enabled": False},
            "take_profit": {"dynamic": False, "min_rr_ratio": 2.0},
            "time_exit": {"enabled": True, "close_minutes_before_session_end": 30},
        }
    }


def _long_position(stop_level=1.0750, profit_level=1.0950) -> dict:
    return {
        "position": {
            "dealId": "deal-001",
            "direction": "BUY",
            "level": 1.0800,
            "size": 1.0,
            "stopLevel": stop_level,
            "profitLevel": profit_level,
        },
        "market": {"epic": "EURUSD"},
    }


def _short_position(stop_level=1.0850, profit_level=1.0650) -> dict:
    return {
        "position": {
            "dealId": "deal-002",
            "direction": "SELL",
            "level": 1.0800,
            "size": 1.0,
            "stopLevel": stop_level,
            "profitLevel": profit_level,
        },
        "market": {"epic": "EURUSD"},
    }


def _price(bid: float, ask: float | None = None) -> dict:
    return {"bid": bid, "offer": ask if ask is not None else bid + 0.0001}


def _session_end(minutes_from_now: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)


# ---------------------------------------------------------------------------
# HOLD — no conditions met
# Use mean_rev_cfg (trailing stop disabled) so only hard stop / TP / time exit apply.
# ---------------------------------------------------------------------------

def test_hold_when_no_conditions_met(mean_rev_cfg):
    action, reason, new_stop = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0820),
        mean_rev_cfg,
        session_end_time=_session_end(120),
    )
    assert action == "HOLD"
    assert new_stop is None


def test_hold_short_no_conditions_met(mean_rev_cfg):
    action, reason, new_stop = evaluate_position(
        _short_position(stop_level=1.0900, profit_level=1.0600),
        _price(bid=1.0750, ask=1.0751),
        mean_rev_cfg,
        session_end_time=_session_end(120),
    )
    assert action == "HOLD"


# ---------------------------------------------------------------------------
# Hard stop
# ---------------------------------------------------------------------------

def test_hard_stop_long_triggers_close(momentum_cfg):
    # LONG: bid falls to exactly the stop level
    action, reason, new_stop = evaluate_position(
        _long_position(stop_level=1.0750),
        _price(1.0750),
        momentum_cfg,
    )
    assert action == "CLOSE"
    assert "Hard stop" in reason
    assert new_stop is None


def test_hard_stop_long_below_stop_triggers_close(momentum_cfg):
    action, reason, _ = evaluate_position(
        _long_position(stop_level=1.0750),
        _price(1.0730),
        momentum_cfg,
    )
    assert action == "CLOSE"
    assert "Hard stop" in reason


def test_hard_stop_long_above_stop_does_not_trigger(momentum_cfg):
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0750),
        _price(1.0751),
        momentum_cfg,
    )
    assert action != "CLOSE" or "stop" not in _.lower()


def test_hard_stop_short_triggers_close(momentum_cfg):
    # SHORT: ask rises to exactly the stop level
    action, reason, _ = evaluate_position(
        _short_position(stop_level=1.0850),
        _price(bid=1.0849, ask=1.0850),
        momentum_cfg,
    )
    assert action == "CLOSE"
    assert "Hard stop" in reason


def test_hard_stop_short_above_stop_triggers_close(momentum_cfg):
    action, reason, _ = evaluate_position(
        _short_position(stop_level=1.0850),
        _price(bid=1.0860, ask=1.0861),
        momentum_cfg,
    )
    assert action == "CLOSE"
    assert "Hard stop" in reason


# ---------------------------------------------------------------------------
# Trailing stop ratchet
# ---------------------------------------------------------------------------

def test_trailing_stop_ratchet_long_moves_up(momentum_cfg):
    # LONG, current stop=1.0700, bid=1.0900, dist=1% → new_stop=1.0800 (ratchet up)
    action, reason, new_stop = evaluate_position(
        _long_position(stop_level=1.0700),
        _price(1.0900),
        momentum_cfg,
    )
    assert action == "ADJUST"
    assert new_stop is not None
    assert new_stop > 1.0700
    assert "ratchet" in reason.lower()


def test_trailing_stop_ratchet_long_no_move_when_not_profitable(momentum_cfg):
    # LONG, current stop=1.0750, bid=1.0760 — new candidate=1.0752 < current stop=1.0750? No → HOLD
    # 1% below 1.0760 = 1.06523... which is less than 1.0750 → no ratchet
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0750),
        _price(1.0760),
        momentum_cfg,
    )
    # new candidate = 1.0760 * 0.99 = 1.06524 < 1.0750 → no ratchet → should HOLD or take profit
    assert action in ("HOLD", "CLOSE")  # not ADJUST


def test_trailing_stop_ratchet_short_moves_down(momentum_cfg):
    # SHORT, current stop=1.0900, ask=1.0700, dist=1% → candidate=1.0707 < 1.0900 → ADJUST
    action, reason, new_stop = evaluate_position(
        _short_position(stop_level=1.0900),
        _price(bid=1.0699, ask=1.0700),
        momentum_cfg,
    )
    assert action == "ADJUST"
    assert new_stop is not None
    assert new_stop < 1.0900
    assert "ratchet" in reason.lower()


def test_trailing_stop_ratchet_short_no_move_when_not_profitable(momentum_cfg):
    # SHORT, current stop=1.0850, ask=1.0849 — new candidate=1.0849*1.01=1.0957 > 1.0850 → no ratchet
    action, _, _ = evaluate_position(
        _short_position(stop_level=1.0850),
        _price(bid=1.0848, ask=1.0849),
        momentum_cfg,
    )
    assert action in ("HOLD", "CLOSE")  # not ADJUST


def test_trailing_stop_disabled_skips_ratchet(mean_rev_cfg):
    # mean_reversion has trailing_stop disabled
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0700),
        _price(1.0900),
        mean_rev_cfg,
    )
    assert action != "ADJUST"


# ---------------------------------------------------------------------------
# Take profit
# Use mean_rev_cfg (trailing stop disabled) so ratchet doesn't shadow TP check.
# ---------------------------------------------------------------------------

def test_take_profit_long_triggers_close(mean_rev_cfg):
    action, reason, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0950),
        mean_rev_cfg,
    )
    assert action == "CLOSE"
    assert "Take profit" in reason


def test_take_profit_long_above_target_triggers_close(mean_rev_cfg):
    action, reason, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0980),
        mean_rev_cfg,
    )
    assert action == "CLOSE"
    assert "Take profit" in reason


def test_take_profit_long_below_target_no_trigger(mean_rev_cfg):
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0820),
        mean_rev_cfg,
        session_end_time=_session_end(120),
    )
    assert action == "HOLD"


def test_take_profit_short_triggers_close(mean_rev_cfg):
    # SHORT: ask falls to exactly the profit level
    action, reason, _ = evaluate_position(
        _short_position(stop_level=1.0900, profit_level=1.0650),
        _price(bid=1.0649, ask=1.0650),
        mean_rev_cfg,
    )
    assert action == "CLOSE"
    assert "Take profit" in reason


def test_take_profit_short_below_target_triggers_close(mean_rev_cfg):
    action, _, _ = evaluate_position(
        _short_position(stop_level=1.0900, profit_level=1.0650),
        _price(bid=1.0620, ask=1.0621),
        mean_rev_cfg,
    )
    assert action == "CLOSE"


# ---------------------------------------------------------------------------
# Time exit
# Use mean_rev_cfg (trailing stop disabled) so ratchet doesn't shadow time-exit check.
# ---------------------------------------------------------------------------

def test_time_exit_triggers_close_within_window(mean_rev_cfg):
    action, reason, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0820),
        mean_rev_cfg,
        session_end_time=_session_end(20),  # 20 min left, threshold=30
    )
    assert action == "CLOSE"
    assert "Time exit" in reason


def test_time_exit_does_not_trigger_outside_window(mean_rev_cfg):
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0820),
        mean_rev_cfg,
        session_end_time=_session_end(60),  # 60 min left, well outside 30 min threshold
    )
    assert action == "HOLD"


def test_time_exit_no_session_end_skips_check(mean_rev_cfg):
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0820),
        mean_rev_cfg,
        session_end_time=None,
    )
    assert action == "HOLD"


def test_time_exit_disabled_in_strategy_skips_check():
    cfg = {
        "risk": {
            "trailing_stop": {"enabled": False},
            "time_exit": {"enabled": False, "close_minutes_before_session_end": 30},
        }
    }
    action, _, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0820),
        cfg,
        session_end_time=_session_end(10),  # inside window but disabled
    )
    assert action == "HOLD"


# ---------------------------------------------------------------------------
# Rule priority — hard stop beats trailing stop beats take profit beats time exit
# ---------------------------------------------------------------------------

def test_hard_stop_beats_trailing_stop(momentum_cfg):
    # Hard stop should trigger even when trailing stop would also fire
    action, reason, _ = evaluate_position(
        _long_position(stop_level=1.0800),  # stop = current bid → hard stop
        _price(1.0800),
        momentum_cfg,
    )
    assert action == "CLOSE"
    assert "Hard stop" in reason


def test_trailing_stop_beats_take_profit_when_ratchet_fires(momentum_cfg):
    # Price at 1.0900 triggers ratchet AND is below profit_level=1.0950
    # Trailing stop (ADJUST) should fire before take profit (CLOSE) check
    action, reason, _ = evaluate_position(
        _long_position(stop_level=1.0700, profit_level=1.0950),
        _price(1.0900),
        momentum_cfg,
    )
    assert action == "ADJUST"
    assert "ratchet" in reason.lower()


# ---------------------------------------------------------------------------
# Missing / null price data
# ---------------------------------------------------------------------------

def test_no_price_data_returns_hold(momentum_cfg):
    action, _, _ = evaluate_position(
        _long_position(),
        {"bid": None, "offer": None},
        momentum_cfg,
    )
    assert action == "HOLD"


def test_no_stop_level_skips_hard_stop(momentum_cfg):
    pos = _long_position()
    pos["position"]["stopLevel"] = None
    action, _, _ = evaluate_position(
        pos,
        _price(1.0600),  # would trigger stop if stopLevel set
        momentum_cfg,
    )
    # Without a stop level, hard stop check is skipped; may trigger take profit or hold
    assert action in ("HOLD", "CLOSE")
