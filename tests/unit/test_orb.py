"""Unit tests for ORBSignalState in backtest/signals.py."""

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.signals import ORBSignalState

# All tests use session open at 08:00 UTC (seconds_in_day = 28800)
_SESSION_HOUR   = 8
_SESSION_MINUTE = 0
_SESSION_SECONDS = _SESSION_HOUR * 3600 + _SESSION_MINUTE * 60  # 28800
_DAY = 86400


def _bar(ts: int, high: float = 1.0, low: float = 1.0,
         open_: float = 1.0, close: float = 1.0) -> OHLCBar:
    return OHLCBar(epic="DE40", resolution="M15", ts=ts,
                   open=open_, high=high, low=low, close=close, volume=100)


def _or_bar(day: int = 0, or_high: float = 1.02, or_low: float = 0.98) -> OHLCBar:
    """The opening range bar for `day` (08:00 UTC)."""
    ts = day * _DAY + _SESSION_SECONDS
    return _bar(ts, high=or_high, low=or_low)


def _session_bar(day: int = 0, bar_num: int = 1,
                 high: float = 1.0, low: float = 1.0) -> OHLCBar:
    """A non-OR bar `bar_num` periods after the OR bar in the same session."""
    ts = day * _DAY + _SESSION_SECONDS + bar_num * 900
    return _bar(ts, high=high, low=low)


def _state() -> ORBSignalState:
    return ORBSignalState(session_open_hour=_SESSION_HOUR,
                          session_open_minute=_SESSION_MINUTE)


class TestORBBasic:

    def test_no_signal_on_or_bar(self):
        state = _state()
        result = state.update(_or_bar())
        assert result is None

    def test_no_signal_before_first_session_open(self):
        state = _state()
        # A bar at 07:45 UTC — before the session open, no OR recorded yet
        bar = _bar(ts=_SESSION_SECONDS - 900)
        assert state.update(bar) is None

    def test_no_signal_within_range(self):
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        # Bar that stays within the range
        bar = _session_bar(high=1.015, low=0.985)
        assert state.update(bar) is None

    def test_long_on_break_above_or_high(self):
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        bar = _session_bar(high=1.025, low=0.99)   # high > 1.02
        assert state.update(bar) == "LONG"

    def test_short_on_break_below_or_low(self):
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        bar = _session_bar(high=1.01, low=0.975)   # low < 0.98
        assert state.update(bar) == "SHORT"

    def test_long_not_fired_on_exact_or_high(self):
        # Strict inequality — touching the OR is not a breakout
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        bar = _session_bar(high=1.02, low=0.99)    # high == or_high exactly
        assert state.update(bar) is None

    def test_short_not_fired_on_exact_or_low(self):
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        bar = _session_bar(high=1.01, low=0.98)    # low == or_low exactly
        assert state.update(bar) is None


class TestORBSessionReset:

    def test_one_signal_per_session(self):
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        # First breakout fires
        assert state.update(_session_bar(bar_num=1, high=1.025)) == "LONG"
        # Second bar also breaks out — should be suppressed
        assert state.update(_session_bar(bar_num=2, high=1.03)) is None

    def test_reset_on_new_session(self):
        state = _state()
        # Day 0: fire LONG
        state.update(_or_bar(day=0, or_high=1.02, or_low=0.98))
        state.update(_session_bar(day=0, bar_num=1, high=1.025))
        # Day 1: new session open resets state → should fire again on breakout
        state.update(_or_bar(day=1, or_high=2.02, or_low=1.98))
        result = state.update(_session_bar(day=1, bar_num=1, high=2.025))
        assert result == "LONG"

    def test_no_crossover_from_previous_session_or(self):
        # After day 0 session ends, day 1 bars before the open should not fire
        state = _state()
        state.update(_or_bar(day=0, or_high=1.02, or_low=0.98))
        # Day 1 bar that comes BEFORE the day-1 session open
        bar_before_open = _bar(ts=1 * _DAY + _SESSION_SECONDS - 900,
                                high=1.03, low=0.97)
        assert state.update(bar_before_open) is None

    def test_no_entry_after_first_signal_across_two_bars(self):
        # LONG fires, then a SHORT-side breakout in the same session is ignored
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        state.update(_session_bar(bar_num=1, high=1.025))   # → LONG
        result = state.update(_session_bar(bar_num=2, high=0.99, low=0.97))  # would be SHORT
        assert result is None


class TestORBInterface:

    def test_check_exit_always_none(self):
        assert _state().check_exit() is None

    def test_notify_entry_no_op(self):
        state = _state()
        state.update(_or_bar())
        state.notify_entry()   # should not raise

    def test_notify_exit_no_op(self):
        state = _state()
        state.update(_or_bar())
        state.notify_exit()    # should not raise

    def test_custom_session_open_time(self):
        # US500: 14:30 UTC
        state = ORBSignalState(session_open_hour=14, session_open_minute=30)
        or_ts = 14 * 3600 + 30 * 60   # 52200
        or_bar = _bar(ts=or_ts, high=5200.0, low=5190.0)
        assert state.update(or_bar) is None  # OR bar: no signal
        next_bar = _bar(ts=or_ts + 900, high=5205.0, low=5192.0)
        assert state.update(next_bar) == "LONG"
