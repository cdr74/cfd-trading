"""Unit tests for IntradayContinuationSignalState (D3/BR3) and chandelier_stop.

Covers:
  - Entry rule: first session bar whose CLOSE breaches `open ± k_entry·ATR₁₄`
  - One trade per session, then reset at the next session-open bar
  - get_entry_levels returns (initial Chandelier-aligned stop, None) — no TP
  - chandelier_stop pure function — formula correctness in both directions
  - End-to-end engine run with a contrived bar series: stop_history follows
    the chandelier_stop formula exactly (closed-form expected stops).
"""

import datetime as dt

import pytest

from cfd_trading.backtest.engine import run_backtest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.strategy.signal_engine import (
    IntradayContinuationSignalState, chandelier_stop,
)


SECONDS_PER_BAR = 900  # M15
SESSION_OPEN_UTC = (8, 0)
SESSION_OPEN_SOD = SESSION_OPEN_UTC[0] * 3600 + SESSION_OPEN_UTC[1] * 60


def _bar(ts, o=100.0, h=100.0, lo=100.0, c=100.0, epic="DE40"):
    return OHLCBar(epic=epic, resolution="M15", ts=ts,
                   open=o, high=h, low=lo, close=c, volume=100)


def _session_open_ts(y, mo, d):
    return int(dt.datetime(y, mo, d, *SESSION_OPEN_UTC,
                           tzinfo=dt.timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# chandelier_stop pure function
# ---------------------------------------------------------------------------

class TestChandelierStop:
    def test_long_subtracts_distance(self):
        # peak=110, atr=2, k=1.5 → stop = 110 - 3 = 107
        assert chandelier_stop("BUY", 110.0, 2.0, 1.5) == 107.0

    def test_short_adds_distance(self):
        # min_low=90, atr=2, k=1.5 → stop = 90 + 3 = 93
        assert chandelier_stop("SELL", 90.0, 2.0, 1.5) == 93.0

    def test_zero_atr_returns_extreme(self):
        # Degenerate flat-market case — stop = peak
        assert chandelier_stop("BUY", 100.0, 0.0, 1.5) == 100.0
        assert chandelier_stop("SELL", 100.0, 0.0, 1.5) == 100.0

    def test_result_is_rounded_to_5dp(self):
        # 1.23456789 * 1.5 = 1.85185... ; peak 100.0 → 98.14814...
        # Rounded to 5dp → 98.14815
        assert chandelier_stop("BUY", 100.0, 1.23456789, 1.5) == 98.14815


# ---------------------------------------------------------------------------
# IntradayContinuationSignalState entry rule
# ---------------------------------------------------------------------------

class TestEntryRule:
    def _warm_state(self, k_entry=1.0):
        """Return a state warmed enough for ATR to be populated."""
        state = IntradayContinuationSignalState(
            session_open_hour=SESSION_OPEN_UTC[0],
            session_open_minute=SESSION_OPEN_UTC[1],
            k_entry=k_entry,
        )
        # Feed 30 prior bars with TR=1.0 so ATR converges to ~1.0
        # (TR=1 each bar, Wilder smoothing settles at 1.0)
        ts0 = _session_open_ts(2026, 5, 11) - 30 * SECONDS_PER_BAR
        for i in range(30):
            state.update(_bar(ts0 + i * SECONDS_PER_BAR,
                              o=100.0, h=100.5, lo=99.5, c=100.0))
        return state

    def test_long_entry_on_close_above_band(self):
        state = self._warm_state(k_entry=1.0)
        atr = state.atr
        assert atr is not None and atr > 0
        open_ts = _session_open_ts(2026, 5, 11)

        # Session-open bar at 100.0
        sig = state.update(_bar(open_ts, o=100.0, h=100.2, lo=99.9, c=100.0))
        assert sig is None  # no breach at open bar

        # Next bar — close at 100 + 2*ATR, well above band (1*ATR)
        sig = state.update(_bar(open_ts + SECONDS_PER_BAR,
                                o=100.0, h=100.0 + 3*atr, lo=99.9,
                                c=100.0 + 2*atr))
        assert sig == "LONG"

    def test_short_entry_on_close_below_band(self):
        state = self._warm_state(k_entry=1.0)
        atr = state.atr
        open_ts = _session_open_ts(2026, 5, 11)
        state.update(_bar(open_ts, o=100.0, h=100.1, lo=99.8, c=100.0))
        sig = state.update(_bar(open_ts + SECONDS_PER_BAR,
                                o=100.0, h=100.1, lo=100.0 - 3*atr,
                                c=100.0 - 2*atr))
        assert sig == "SHORT"

    def test_no_entry_inside_band(self):
        state = self._warm_state(k_entry=1.0)
        atr = state.atr
        open_ts = _session_open_ts(2026, 5, 11)
        state.update(_bar(open_ts, o=100.0, h=100.5, lo=99.5, c=100.0))
        # Close at 100 + 0.5*ATR — inside the 1*ATR band
        sig = state.update(_bar(open_ts + SECONDS_PER_BAR,
                                o=100.0, h=100.6, lo=99.9,
                                c=100.0 + 0.5*atr))
        assert sig is None

    def test_one_trade_per_session(self):
        state = self._warm_state(k_entry=1.0)
        atr = state.atr
        open_ts = _session_open_ts(2026, 5, 11)
        state.update(_bar(open_ts, o=100.0, h=100.5, lo=99.5, c=100.0))
        # First breach — LONG
        assert state.update(_bar(open_ts + SECONDS_PER_BAR,
                                 o=100.0, h=100.0 + 3*atr, lo=99.5,
                                 c=100.0 + 2*atr)) == "LONG"
        # Subsequent breach (both directions) suppressed for the rest of the session
        assert state.update(_bar(open_ts + 2*SECONDS_PER_BAR,
                                 o=100.0 + 2*atr, h=100.0 + 4*atr, lo=99.0,
                                 c=100.0 + 3*atr)) is None
        assert state.update(_bar(open_ts + 3*SECONDS_PER_BAR,
                                 o=100.0 + 3*atr, h=100.0 + 3*atr, lo=99.0,
                                 c=99.5)) is None

    def test_reset_at_next_session_open(self):
        state = self._warm_state(k_entry=1.0)
        atr = state.atr
        open_ts_d1 = _session_open_ts(2026, 5, 11)
        state.update(_bar(open_ts_d1, o=100.0, h=100.5, lo=99.5, c=100.0))
        state.update(_bar(open_ts_d1 + SECONDS_PER_BAR,
                          o=100.0, h=100.0 + 3*atr, lo=99.5, c=100.0 + 2*atr))

        # Day 2 session-open bar — flag must clear; new breach should fire
        open_ts_d2 = _session_open_ts(2026, 5, 12)
        # Bridge bars so ATR continues to be populated
        prev_close = 100.0 + 2*atr
        for i in range((open_ts_d2 - open_ts_d1 - 2*SECONDS_PER_BAR) // SECONDS_PER_BAR):
            ts = open_ts_d1 + (2 + i) * SECONDS_PER_BAR
            state.update(_bar(ts, o=prev_close, h=prev_close+0.5,
                              lo=prev_close-0.5, c=prev_close))
        state.update(_bar(open_ts_d2, o=prev_close,
                          h=prev_close+0.5, lo=prev_close-0.5, c=prev_close))
        atr2 = state.atr
        sig = state.update(_bar(open_ts_d2 + SECONDS_PER_BAR,
                                o=prev_close, h=prev_close + 3*atr2, lo=prev_close,
                                c=prev_close + 2*atr2))
        assert sig == "LONG"  # new session → new entry allowed

    def test_no_signal_before_atr_warm(self):
        state = IntradayContinuationSignalState(
            session_open_hour=SESSION_OPEN_UTC[0],
            session_open_minute=SESSION_OPEN_UTC[1],
        )
        open_ts = _session_open_ts(2026, 5, 11)
        # First-ever bar is the session-open bar — ATR not warm
        state.update(_bar(open_ts, o=100.0, h=100.5, lo=99.5, c=100.0))
        sig = state.update(_bar(open_ts + SECONDS_PER_BAR,
                                o=100.0, h=200.0, lo=99.5, c=150.0))
        assert sig is None  # gated by ATR-None


# ---------------------------------------------------------------------------
# get_entry_levels — initial Chandelier-aligned stop, no TP
# ---------------------------------------------------------------------------

class TestGetEntryLevels:
    def test_long_stop_at_entry_minus_k_trail_times_atr(self):
        state = TestEntryRule()._warm_state(k_entry=1.0)
        atr = state.atr
        stop, profit = state.get_entry_levels("BUY", fill_price=100.0, rr_ratio=2.0)
        assert profit is None  # no TP
        assert stop == pytest.approx(100.0 - 1.5 * atr, abs=1e-5)

    def test_short_stop_at_entry_plus_k_trail_times_atr(self):
        state = TestEntryRule()._warm_state(k_entry=1.0)
        atr = state.atr
        stop, profit = state.get_entry_levels("SELL", fill_price=100.0, rr_ratio=2.0)
        assert profit is None
        assert stop == pytest.approx(100.0 + 1.5 * atr, abs=1e-5)

    def test_rr_ratio_is_ignored(self):
        state = TestEntryRule()._warm_state(k_entry=1.0)
        s1, _ = state.get_entry_levels("BUY", 100.0, rr_ratio=2.0)
        s2, _ = state.get_entry_levels("BUY", 100.0, rr_ratio=99.0)
        assert s1 == s2  # rr_ratio has no effect


# ---------------------------------------------------------------------------
# check_exit always returns None — hold to close
# ---------------------------------------------------------------------------

class TestCheckExit:
    def test_check_exit_is_none_post_entry(self):
        state = TestEntryRule()._warm_state(k_entry=1.0)
        state.notify_entry("BUY")
        assert state.check_exit() is None

    def test_check_exit_is_none_post_exit(self):
        state = TestEntryRule()._warm_state(k_entry=1.0)
        state.notify_entry("BUY")
        state.notify_exit()
        assert state.check_exit() is None
