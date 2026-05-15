"""Unit tests for ORBSignalState in strategy/signal_engine.py."""

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.strategy.signal_engine import ORBSignalState

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
    """The first OR bar (session open) for `day`."""
    ts = day * _DAY + _SESSION_SECONDS
    return _bar(ts, high=or_high, low=or_low)


def _session_bar(day: int = 0, bar_num: int = 1,
                 high: float = 1.0, low: float = 1.0) -> OHLCBar:
    """A bar `bar_num` M15 periods after the OR bar in the same session."""
    ts = day * _DAY + _SESSION_SECONDS + bar_num * 900
    return _bar(ts, high=high, low=low)


def _state(or_bars: int = 2) -> ORBSignalState:
    return ORBSignalState(session_open_hour=_SESSION_HOUR,
                          session_open_minute=_SESSION_MINUTE,
                          or_bars=or_bars)


def _setup_or(state: ORBSignalState,
              day: int = 0,
              or_high: float = 1.02, or_low: float = 0.98) -> None:
    """Feed the state two OR bars (30 min) with the given range, ready for breakout detection."""
    state.update(_or_bar(day=day, or_high=or_high, or_low=or_low))
    state.update(_session_bar(day=day, bar_num=1))   # second OR bar, neutral range


class TestORBBasic:

    def test_no_signal_on_first_or_bar(self):
        state = _state()
        assert state.update(_or_bar()) is None

    def test_no_signal_on_second_or_bar(self):
        # Second bar is still in OR collection phase
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        assert state.update(_session_bar(bar_num=1, high=1.025, low=0.975)) is None

    def test_no_signal_before_first_session_open(self):
        state = _state()
        bar = _bar(ts=_SESSION_SECONDS - 900)
        assert state.update(bar) is None

    def test_no_signal_within_range(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        assert state.update(_session_bar(bar_num=2, high=1.015, low=0.985)) is None

    def test_long_on_break_above_or_high(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        assert state.update(_session_bar(bar_num=2, high=1.025, low=0.99)) == "LONG"

    def test_short_on_break_below_or_low(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        assert state.update(_session_bar(bar_num=2, high=1.01, low=0.975)) == "SHORT"

    def test_long_not_fired_on_exact_or_high(self):
        # Strict inequality — touching the OR is not a breakout
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        assert state.update(_session_bar(bar_num=2, high=1.02, low=0.99)) is None

    def test_short_not_fired_on_exact_or_low(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        assert state.update(_session_bar(bar_num=2, high=1.01, low=0.98)) is None


class TestORBMultiBarOR:

    def test_or_extends_when_second_bar_is_wider(self):
        # Second OR bar has a wider range — OR should expand to cover both bars
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        state.update(_session_bar(bar_num=1, high=1.03, low=0.97))  # wider
        # 1.025 is above first bar's high but below extended OR high (1.03) → no signal
        assert state.update(_session_bar(bar_num=2, high=1.025)) is None
        # 1.035 exceeds extended OR high → LONG
        assert state.update(_session_bar(bar_num=3, high=1.035)) == "LONG"

    def test_or_bars_1_uses_single_bar(self):
        # or_bars=1 reproduces the original single-bar OR behaviour
        state = _state(or_bars=1)
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        assert state.update(_session_bar(bar_num=1, high=1.025)) == "LONG"

    def test_no_signal_during_or_collection(self):
        # Even a bar that breaks the first bar's range returns None during collection
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        assert state.update(_session_bar(bar_num=1, high=1.05, low=0.95)) is None

    def test_or_bars_3_requires_three_bars_before_breakout(self):
        state = _state(or_bars=3)
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        assert state.update(_session_bar(bar_num=1)) is None   # collecting (neutral)
        assert state.update(_session_bar(bar_num=2)) is None   # collecting (neutral)
        assert state.update(_session_bar(bar_num=3, high=1.025)) == "LONG"


class TestORBSessionReset:

    def test_one_signal_per_session(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        assert state.update(_session_bar(bar_num=2, high=1.025)) == "LONG"
        assert state.update(_session_bar(bar_num=3, high=1.03)) is None

    def test_reset_on_new_session(self):
        state = _state()
        _setup_or(state, day=0, or_high=1.02, or_low=0.98)
        state.update(_session_bar(day=0, bar_num=2, high=1.025))
        # Day 1: new session resets everything — should fire again
        _setup_or(state, day=1, or_high=2.02, or_low=1.98)
        assert state.update(_session_bar(day=1, bar_num=2, high=2.025)) == "LONG"

    def test_no_crossover_from_previous_session_or(self):
        # A bar between sessions should not trigger on the previous day's OR
        state = _state()
        _setup_or(state, day=0, or_high=1.02, or_low=0.98)
        bar_before_open = _bar(ts=1 * _DAY + _SESSION_SECONDS - 900,
                                high=1.03, low=0.97)
        assert state.update(bar_before_open) is None

    def test_no_entry_after_first_signal_across_two_bars(self):
        # LONG fires; a subsequent SHORT-side move in the same session is ignored
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        state.update(_session_bar(bar_num=2, high=1.025))   # → LONG
        assert state.update(_session_bar(bar_num=3, high=0.99, low=0.97)) is None


class TestORBEntryLevels:

    def test_long_stop_at_or_low(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        state.update(_session_bar(bar_num=2, high=1.025))   # fire LONG
        stop, tp = state.get_entry_levels("BUY", 1.025, 2.0)
        assert stop == pytest.approx(0.98)
        # OR width = 0.04; TP = 1.025 + 0.04 * 2 = 1.105
        assert tp == pytest.approx(1.105, rel=1e-5)

    def test_short_stop_at_or_high(self):
        state = _state()
        _setup_or(state, or_high=1.02, or_low=0.98)
        state.update(_session_bar(bar_num=2, high=1.01, low=0.975))   # fire SHORT
        stop, tp = state.get_entry_levels("SELL", 0.975, 2.0)
        assert stop == pytest.approx(1.02)
        # OR width = 0.04; TP = 0.975 - 0.04 * 2 = 0.895
        assert tp == pytest.approx(0.895, rel=1e-5)

    def test_entry_levels_reflect_extended_or(self):
        # When second OR bar is wider, get_entry_levels uses the extended range
        state = _state()
        state.update(_or_bar(or_high=1.02, or_low=0.98))
        state.update(_session_bar(bar_num=1, high=1.03, low=0.97))   # widens OR
        state.update(_session_bar(bar_num=2, high=1.035))             # LONG
        stop, tp = state.get_entry_levels("BUY", 1.035, 2.0)
        assert stop == pytest.approx(0.97)      # extended OR low
        # OR width = 1.03 - 0.97 = 0.06; TP = 1.035 + 0.12 = 1.155
        assert tp == pytest.approx(1.155, rel=1e-5)


class TestORBInterface:

    def test_check_exit_always_none(self):
        assert _state().check_exit() is None

    def test_notify_entry_no_op(self):
        state = _state()
        _setup_or(state)
        state.notify_entry("BUY")   # accepts direction; no-op for ORB

    def test_notify_exit_no_op(self):
        state = _state()
        _setup_or(state)
        state.notify_exit()    # should not raise

    def test_custom_session_open_time(self):
        # US500: 14:30 UTC, 2-bar OR
        state = ORBSignalState(session_open_hour=14, session_open_minute=30)
        or_ts = 14 * 3600 + 30 * 60
        assert state.update(_bar(ts=or_ts, high=5200.0, low=5190.0)) is None   # OR bar 1
        assert state.update(_bar(ts=or_ts + 900, high=5202.0, low=5191.0)) is None  # OR bar 2
        assert state.update(_bar(ts=or_ts + 1800, high=5205.0, low=5192.0)) == "LONG"
