"""Unit tests for risk/preflight.py — every validation rule and rejection path."""

import pytest
from cfd_trading.risk.preflight import validate_entry_proposal, validate_monitor_decision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def global_cfg():
    return {
        "max_loss_pct_per_trade": 5.0,
        "margin_floor_pct": 20.0,
        "max_open_positions": 3,
    }


@pytest.fixture
def momentum_cfg():
    return {
        "entry": {"min_size": 0.1, "max_size": 5.0},
        "risk": {
            "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
            "trailing_stop": {
                "enabled": True,
                "min_distance_pct": 0.5,
                "max_distance_pct": 3.0,
            },
            "take_profit": {"dynamic": True, "min_rr_ratio": 1.5, "max_pct": 10.0},
        },
    }


@pytest.fixture
def mean_rev_cfg():
    return {
        "entry": {"min_size": 0.1, "max_size": 3.0},
        "risk": {
            "stop_loss": {"type": "HARD", "default_pct": 1.5, "max_pct": 3.0},
            "trailing_stop": {"enabled": False},
            "take_profit": {"dynamic": False, "min_rr_ratio": 2.0, "max_pct": 6.0},
        },
    }


def _valid_proposal(**overrides) -> dict:
    """Return a minimal valid OPEN proposal; override any field for negative tests."""
    base = {
        "decision": {
            "action": "OPEN",
            "direction": "LONG",
            "size": 1.0,
            "stop_loss": {"type": "HARD", "value": 1.0750, "pct_from_entry": 2.0},
            "trailing_stop": None,
            "take_profit": None,
            "entry_level": None,
        },
        "reasoning": {
            "market_context": "uptrend",
            "signal_basis": "breakout",
            "risk_considerations": "low spread",
            "contra_indicators": "RSI slightly overbought",
        },
    }
    for key, value in overrides.items():
        keys = key.split(".")
        d = base
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value
    return base


def _open_proposal_with_levels(direction="LONG") -> dict:
    """Proposal with entry_level, SL value, and TP value — enables R:R check."""
    if direction == "LONG":
        return {
            "decision": {
                "action": "OPEN",
                "direction": "LONG",
                "size": 1.0,
                "entry_level": 1.0800,
                "stop_loss": {"type": "HARD", "value": 1.0700, "pct_from_entry": 0.93},
                "take_profit": {"initial_value": 1.0950, "dynamic": True},
                "trailing_stop": None,
            },
            "reasoning": {"contra_indicators": "minor resistance ahead"},
        }
    else:  # SHORT
        return {
            "decision": {
                "action": "OPEN",
                "direction": "SHORT",
                "size": 1.0,
                "entry_level": 1.0800,
                "stop_loss": {"type": "HARD", "value": 1.0900, "pct_from_entry": 0.93},
                "take_profit": {"initial_value": 1.0600, "dynamic": True},
                "trailing_stop": None,
            },
            "reasoning": {"contra_indicators": "minor support ahead"},
        }


# ---------------------------------------------------------------------------
# Happy path — valid proposal passes all checks
# ---------------------------------------------------------------------------

def test_valid_proposal_passes(momentum_cfg, global_cfg):
    result = validate_entry_proposal(_valid_proposal(), momentum_cfg, global_cfg)
    assert result.passed
    assert result.violations == []


def test_none_action_passes(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["action"] = "NONE"
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed


def test_close_action_passes_without_size_or_sl(momentum_cfg, global_cfg):
    p = {
        "decision": {"action": "CLOSE", "direction": None, "size": None, "stop_loss": None},
        "reasoning": {"contra_indicators": "taking profit"},
    }
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed


# ---------------------------------------------------------------------------
# stop_loss checks
# ---------------------------------------------------------------------------

def test_missing_stop_loss_is_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["stop_loss"] = None
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("stop_loss" in v for v in result.violations)


def test_stop_loss_above_strategy_max_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["stop_loss"]["pct_from_entry"] = 5.1  # strategy max is 5.0
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("strategy maximum" in v for v in result.violations)


def test_stop_loss_above_global_ceiling_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["stop_loss"]["pct_from_entry"] = 5.1  # global max also 5.0
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("global hard ceiling" in v for v in result.violations)


def test_stop_loss_missing_pct_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["stop_loss"] = {"type": "HARD", "value": 1.07}  # no pct_from_entry
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("pct_from_entry" in v for v in result.violations)


def test_stop_loss_at_exactly_strategy_max_passes(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["stop_loss"]["pct_from_entry"] = 5.0  # exactly at limit
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed


# ---------------------------------------------------------------------------
# size checks
# ---------------------------------------------------------------------------

def test_size_above_max_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["size"] = 5.1  # max is 5.0
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("exceeds strategy maximum" in v for v in result.violations)


def test_size_below_min_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["size"] = 0.05  # min is 0.1
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("below strategy minimum" in v for v in result.violations)


def test_missing_size_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["size"] = None
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("size" in v.lower() for v in result.violations)


def test_size_at_exact_bounds_passes(momentum_cfg, global_cfg):
    for size in (0.1, 5.0):
        p = _valid_proposal()
        p["decision"]["size"] = size
        result = validate_entry_proposal(p, momentum_cfg, global_cfg)
        assert result.passed, f"size={size} should pass but got violations: {result.violations}"


# ---------------------------------------------------------------------------
# contra_indicators check
# ---------------------------------------------------------------------------

def test_missing_contra_indicators_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["reasoning"]["contra_indicators"] = ""
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("contra_indicators" in v for v in result.violations)


def test_null_contra_indicators_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["reasoning"]["contra_indicators"] = None
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed


def test_whitespace_only_contra_indicators_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["reasoning"]["contra_indicators"] = "   "
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed


# ---------------------------------------------------------------------------
# max open positions check
# ---------------------------------------------------------------------------

def test_at_max_positions_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    result = validate_entry_proposal(p, momentum_cfg, global_cfg, open_positions_count=3)
    assert not result.passed
    assert any("maximum" in v and "positions" in v for v in result.violations)


def test_below_max_positions_passes(momentum_cfg, global_cfg):
    p = _valid_proposal()
    result = validate_entry_proposal(p, momentum_cfg, global_cfg, open_positions_count=2)
    assert result.passed


# ---------------------------------------------------------------------------
# margin floor check
# ---------------------------------------------------------------------------

def test_margin_below_floor_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    result = validate_entry_proposal(p, momentum_cfg, global_cfg, margin_pct=15.0)
    assert not result.passed
    assert any("margin" in v.lower() for v in result.violations)


def test_margin_at_floor_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    result = validate_entry_proposal(p, momentum_cfg, global_cfg, margin_pct=20.0)
    # margin_pct < floor means 20.0 < 20.0 is False → should pass
    assert result.passed


def test_margin_above_floor_passes(momentum_cfg, global_cfg):
    p = _valid_proposal()
    result = validate_entry_proposal(p, momentum_cfg, global_cfg, margin_pct=50.0)
    assert result.passed


# ---------------------------------------------------------------------------
# trailing stop checks
# ---------------------------------------------------------------------------

def test_trailing_stop_distance_too_small_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["trailing_stop"] = {"enabled": True, "initial_distance_pct": 0.3}  # min 0.5
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("below strategy minimum" in v for v in result.violations)


def test_trailing_stop_distance_too_large_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["trailing_stop"] = {"enabled": True, "initial_distance_pct": 4.0}  # max 3.0
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("exceeds strategy maximum" in v for v in result.violations)


def test_trailing_stop_not_in_strategy_rejected(mean_rev_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["trailing_stop"] = {"enabled": True, "initial_distance_pct": 1.0}
    result = validate_entry_proposal(p, mean_rev_cfg, global_cfg)
    assert not result.passed
    assert any("not enabled in strategy" in v for v in result.violations)


def test_trailing_stop_valid_passes(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["trailing_stop"] = {"enabled": True, "initial_distance_pct": 1.5}
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed


def test_trailing_stop_disabled_skips_check(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["trailing_stop"] = {"enabled": False}
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed


# ---------------------------------------------------------------------------
# R:R ratio check
# ---------------------------------------------------------------------------

def test_rr_ratio_below_minimum_rejected(momentum_cfg, global_cfg):
    # LONG: entry=1.08, SL=1.07 (risk=0.01), TP=1.085 (reward=0.005) → R:R=0.5, min=1.5
    p = _open_proposal_with_levels("LONG")
    p["decision"]["take_profit"]["initial_value"] = 1.0850  # only 0.005 reward vs 0.01 risk
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("R:R" in v for v in result.violations)


def test_rr_ratio_meets_minimum_passes(momentum_cfg, global_cfg):
    # LONG: entry=1.08, SL=1.07 (risk=0.01), TP=1.095 (reward=0.015) → R:R=1.5 ✓
    p = _open_proposal_with_levels("LONG")
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed, f"Violations: {result.violations}"


def test_rr_ratio_short_direction(momentum_cfg, global_cfg):
    # SHORT: entry=1.08, SL=1.09 (risk=0.01), TP=1.065 (reward=0.015) → R:R=1.5 ✓
    p = _open_proposal_with_levels("SHORT")
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed, f"Violations: {result.violations}"


def test_rr_ratio_skipped_without_entry_level(momentum_cfg, global_cfg):
    # No entry_level → R:R check is skipped, proposal passes on other checks
    p = _valid_proposal()
    p["decision"]["take_profit"] = {"initial_value": 1.0850, "dynamic": True}
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert result.passed


def test_sl_on_wrong_side_rejected(momentum_cfg, global_cfg):
    # LONG with SL above entry
    p = _open_proposal_with_levels("LONG")
    p["decision"]["stop_loss"]["value"] = 1.0900  # above entry 1.08 on a LONG
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("wrong side" in v for v in result.violations)


# ---------------------------------------------------------------------------
# Invalid action
# ---------------------------------------------------------------------------

def test_invalid_action_rejected(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["action"] = "BUY"
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert any("Invalid action" in v for v in result.violations)


# ---------------------------------------------------------------------------
# Multiple violations reported together
# ---------------------------------------------------------------------------

def test_multiple_violations_all_reported(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["size"] = 99.0
    p["decision"]["stop_loss"]["pct_from_entry"] = 99.0
    p["reasoning"]["contra_indicators"] = ""
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    assert not result.passed
    assert len(result.violations) >= 3


# ===========================================================================
# Monitor decision tests
# ===========================================================================

def _position(direction="BUY", stop_level=1.0750) -> dict:
    return {"position": {"direction": direction, "stopLevel": stop_level, "level": 1.0800}}


def _hold_decision() -> dict:
    return {"action": "HOLD", "stop_loss": None}


def _adjust_decision(new_sl_value: float, pct: float = 1.5) -> dict:
    return {"action": "ADJUST", "stop_loss": {"value": new_sl_value, "pct_from_entry": pct}}


# ---------------------------------------------------------------------------
# HOLD / CLOSE pass through
# ---------------------------------------------------------------------------

def test_hold_passes(momentum_cfg, global_cfg):
    result = validate_monitor_decision(
        _hold_decision(), _position(), momentum_cfg, global_cfg
    )
    assert result.passed


def test_close_passes(momentum_cfg, global_cfg):
    result = validate_monitor_decision(
        {"action": "CLOSE"}, _position(), momentum_cfg, global_cfg
    )
    assert result.passed


# ---------------------------------------------------------------------------
# Invalid monitor action
# ---------------------------------------------------------------------------

def test_invalid_monitor_action_rejected(momentum_cfg, global_cfg):
    result = validate_monitor_decision(
        {"action": "OPEN"}, _position(), momentum_cfg, global_cfg
    )
    assert not result.passed
    assert any("Invalid monitor action" in v for v in result.violations)


# ---------------------------------------------------------------------------
# ADJUST — stop loss checks
# ---------------------------------------------------------------------------

def test_adjust_missing_stop_loss_rejected(momentum_cfg, global_cfg):
    result = validate_monitor_decision(
        {"action": "ADJUST"}, _position(), momentum_cfg, global_cfg
    )
    assert not result.passed
    assert any("stop_loss" in v for v in result.violations)


def test_adjust_stop_loss_above_strategy_max_rejected(momentum_cfg, global_cfg):
    result = validate_monitor_decision(
        _adjust_decision(1.0500, pct=6.0),  # 6% > strategy max 5%
        _position(),
        momentum_cfg,
        global_cfg,
    )
    assert not result.passed
    assert any("strategy maximum" in v for v in result.violations)


# ---------------------------------------------------------------------------
# Trailing stop ratchet — LONG position
# ---------------------------------------------------------------------------

def test_ratchet_long_moving_up_passes(momentum_cfg, global_cfg):
    # LONG, current SL=1.0750, new SL=1.0780 (moving up ✓)
    result = validate_monitor_decision(
        _adjust_decision(1.0780),
        _position(direction="BUY", stop_level=1.0750),
        momentum_cfg,
        global_cfg,
    )
    assert result.passed


def test_ratchet_long_moving_down_rejected(momentum_cfg, global_cfg):
    # LONG, current SL=1.0750, new SL=1.0720 (moving down ✗)
    result = validate_monitor_decision(
        _adjust_decision(1.0720),
        _position(direction="BUY", stop_level=1.0750),
        momentum_cfg,
        global_cfg,
    )
    assert not result.passed
    assert any("ratchet" in v.lower() for v in result.violations)


# ---------------------------------------------------------------------------
# Trailing stop ratchet — SHORT position
# ---------------------------------------------------------------------------

def test_ratchet_short_moving_down_passes(momentum_cfg, global_cfg):
    # SHORT, current SL=1.0850, new SL=1.0820 (moving down ✓)
    result = validate_monitor_decision(
        _adjust_decision(1.0820),
        _position(direction="SELL", stop_level=1.0850),
        momentum_cfg,
        global_cfg,
    )
    assert result.passed


def test_ratchet_short_moving_up_rejected(momentum_cfg, global_cfg):
    # SHORT, current SL=1.0850, new SL=1.0880 (moving up ✗)
    result = validate_monitor_decision(
        _adjust_decision(1.0880),
        _position(direction="SELL", stop_level=1.0850),
        momentum_cfg,
        global_cfg,
    )
    assert not result.passed
    assert any("ratchet" in v.lower() for v in result.violations)


# ---------------------------------------------------------------------------
# Monitor margin floor
# ---------------------------------------------------------------------------

def test_monitor_margin_below_floor_rejected(momentum_cfg, global_cfg):
    result = validate_monitor_decision(
        _hold_decision(), _position(), momentum_cfg, global_cfg, margin_pct=10.0
    )
    assert not result.passed
    assert any("margin" in v.lower() for v in result.violations)


# ---------------------------------------------------------------------------
# Each violation produces a specific, readable message
# ---------------------------------------------------------------------------

def test_violations_are_human_readable(momentum_cfg, global_cfg):
    p = _valid_proposal()
    p["decision"]["stop_loss"]["pct_from_entry"] = 99.0
    result = validate_entry_proposal(p, momentum_cfg, global_cfg)
    for v in result.violations:
        assert len(v) > 20, f"Violation message too short: {v!r}"
        assert v[0].isupper(), f"Violation should start with capital letter: {v!r}"
