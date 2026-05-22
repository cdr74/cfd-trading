"""Live monitor ↔ backtest parity (3c anti-drift guarantee).

Both paths share `evaluate_position` + `signal_engine`. These tests pin that:
for the same bar sequence + trade, the backtest engine's recorded exit equals
the exit produced by replaying the SAME public contract the live monitor uses
per cycle (signal_engine state + `evaluate_position(now=, signal_state,
entry_atr, peak_price, current_atr)`). If a future change makes one path
diverge from the other, these fail.

For strategies with a dynamic trail (intraday_continuation / dynamic_chandelier
mode), the parity check is extended to the FULL PER-BAR stop_level series and
is also asserted against the closed-form `chandelier_stop()` formula — both
paths must compute the same stops bar-by-bar, AND those stops must match the
canonical Chandelier formula independently. See STRATEGY_AUDIT Part 2.
"""

import datetime as dt

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.engine import run_backtest, _utc_dt, _session_end_for
from cfd_trading.monitor.monitor import (
    evaluate_position, _SIGNAL_CLS, _signal_kwargs_for,
)
from cfd_trading.strategy.signal_engine import chandelier_stop

RISK_CFG = {"global": {"max_loss_pct_per_trade": 5.0}}


def _uts(y, mo, d, h, mi):
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp())


def _bars(prices, start, step=900):
    return [OHLCBar(epic="EURUSD", resolution="M15", ts=start + i * step,
                    open=p, high=p, low=p, close=p, volume=100)
            for i, p in enumerate(prices)]


def _mr_cfg(time_exit=False):
    return {"risk": {
        "stop_loss": {"type": "HARD", "default_pct": 1.5, "max_pct": 3.0},
        "trailing_stop": {"enabled": False},          # no ADJUST → clean parity
        "take_profit": {"dynamic": False, "min_rr_ratio": 2.0},
        "time_exit": {"enabled": time_exit, "close_minutes_before_session_end": 30},
    }}


def _monitor_replay(strategy, cfg, bars, trade, kw, session_close, epic="EURUSD"):
    """Reproduce the monitor's per-cycle exit contract for an open trade.

    Returns (exit_reason, exit_ts, stop_history). stop_history matches the
    engine's Trade.stop_history shape (one entry per in-trade bar AFTER the
    entry bar) so per-bar parity can be asserted directly.
    """
    te = cfg["risk"]["time_exit"]["enabled"]
    ctor_kwargs = {**_signal_kwargs_for(strategy, epic), **(kw or {})}
    state = _SIGNAL_CLS[strategy](**ctor_kwargs)
    entry_atr = None
    peak = trade.entry_price
    entered = False
    current_stop = trade.stop_loss
    stop_history: list[tuple[int, float]] = []
    for b in bars:
        state.update(b)
        if not entered and b.ts >= trade.entry_ts:
            entry_atr = state.atr
            state.notify_entry(trade.direction)
            peak = trade.entry_price
            entered = True
            continue
        if not entered:
            continue
        peak = max(peak, b.high) if trade.direction == "BUY" else min(peak, b.low)
        pd = {"bid": b.close, "offer": b.close}
        pos = {"direction": trade.direction, "stopLevel": current_stop,
               "profitLevel": trade.take_profit}
        se = _session_end_for(b.ts, session_close) if te else None
        action, reason, new_stop = evaluate_position(
            pos, pd, cfg, session_end_time=se, now=_utc_dt(b.ts),
            signal_state=state, entry_atr=entry_atr, peak_price=peak,
            current_atr=state.atr)
        if action == "CLOSE":
            return reason, b.ts, stop_history
        if action == "ADJUST" and new_stop is not None:
            current_stop = new_stop
        stop_history.append((b.ts, current_stop))
    return None, None, stop_history


def _assert_parity(strategy, cfg, prices, start, kw=None, close="21:00"):
    bars = _bars(prices, start)
    res = run_backtest("EURUSD", strategy, bars, cfg, RISK_CFG,
                       signal_kwargs=kw, session_close_utc=close)
    assert res.total_trades == 1, "fixture must produce exactly one trade"
    t = res.trades[0]
    m_reason, m_ts, _ = _monitor_replay(strategy, cfg, bars, t, kw, close)
    assert (m_reason, m_ts) == (t.exit_reason, t.exit_ts), (
        f"DRIFT: engine={t.exit_reason}@{t.exit_ts} "
        f"monitor={m_reason}@{m_ts}")


class TestEngineMonitorParity:
    def test_signal_exit_zscore_midline(self):
        # SHORT at the 1.5 spike, then flat → z reverts to midline (no time-exit)
        _assert_parity("mean_reversion", _mr_cfg(time_exit=False),
                       [1.0] * 19 + [1.5] * 18, _uts(2026, 5, 15, 8, 0))

    def test_hard_stop(self):
        # SHORT at 1.5, then a 1.6 bar breaches the SELL hard stop (~1.5225)
        _assert_parity("mean_reversion", _mr_cfg(time_exit=False),
                       [1.0] * 19 + [1.5, 1.5, 1.6, 1.6], _uts(2026, 5, 15, 8, 0))

    def test_time_exit(self):
        # z-exit disabled (threshold 0) + flat → only the daily time-exit can fire
        start = _uts(2026, 5, 15, 8, 0)
        _assert_parity("mean_reversion", _mr_cfg(time_exit=True),
                       [1.0] * 19 + [1.5] * 40, start,
                       kw={"zscore_exit_threshold": 0.0})


# ---------------------------------------------------------------------------
# Dynamic Chandelier per-bar parity (intraday_continuation / D3-BR3)
# ---------------------------------------------------------------------------

def _ic_cfg():
    """intraday_continuation YAML mirrored as a dict — Chandelier trail, no TP."""
    return {"risk": {
        "stop_loss": {"type": "HARD", "default_pct": 5.0, "max_pct": 5.0},
        "trailing_stop": {
            "enabled": True,
            "mode": "dynamic_chandelier",
            "atr_multiplier": 1.5,
        },
        "take_profit": {"dynamic": False, "min_rr_ratio": 0},
        "time_exit": {"enabled": True, "close_minutes_before_session_end": 30},
    }}


def _de40_bar(ts, o, h, lo, c):
    return OHLCBar(epic="DE40", resolution="M15", ts=ts,
                   open=o, high=h, low=lo, close=c, volume=100)


def _ic_bar_series():
    """30 warm-up bars (TR=1, ATR→~1.0), then a session-open day with a clear
    upper-band breach and a sequence of post-entry bars exercising the trail.
    """
    bars: list[OHLCBar] = []
    # Day -1: 30 M15 bars from 00:00 UTC, all flat with TR=1.
    # No session-open bar in this range (DE40 session = 08:00 UTC), so the
    # strategy never tries to fire. But the 08:00 bar on day -1 IS included
    # (it just doesn't breach because price is flat).
    day_minus_1_midnight = _uts(2026, 5, 11, 0, 0)
    for i in range(30):
        ts = day_minus_1_midnight + i * 900
        bars.append(_de40_bar(ts, 100.0, 100.5, 99.5, 100.0))
    # Day 0 session open 08:00 UTC — strategy resets anchor here
    day_0_open = _uts(2026, 5, 12, 8, 0)
    bars.append(_de40_bar(day_0_open, 100.0, 100.5, 99.5, 100.0))
    # 08:15 — close at 102, decisively above session_open + 1·ATR(~1) = 101 → LONG signal
    bars.append(_de40_bar(day_0_open + 900, 100.0, 102.5, 99.9, 102.0))
    # 08:30 — entry bar (engine fills at this open); skipped by both paths
    bars.append(_de40_bar(day_0_open + 2 * 900, 102.0, 102.5, 101.8, 102.3))
    # 08:45 — peak rises to 104
    bars.append(_de40_bar(day_0_open + 3 * 900, 102.3, 104.0, 102.0, 103.5))
    # 09:00 — peak rises to 105.5
    bars.append(_de40_bar(day_0_open + 4 * 900, 103.5, 105.5, 103.0, 105.0))
    # 09:15 — flat (peak unchanged)
    bars.append(_de40_bar(day_0_open + 5 * 900, 105.0, 105.2, 104.5, 104.8))
    # 09:30 — small pullback, no new peak
    bars.append(_de40_bar(day_0_open + 6 * 900, 104.8, 105.0, 104.0, 104.2))
    # 09:45 — peak extends to 107
    bars.append(_de40_bar(day_0_open + 7 * 900, 104.2, 107.0, 104.0, 106.5))
    # 10:00 — flat
    bars.append(_de40_bar(day_0_open + 8 * 900, 106.5, 106.8, 106.0, 106.4))
    # 10:15 — sharp drop, close 100 → bid breaches the Chandelier hard stop
    # (which sits around 105 at this point). Both paths fire CLOSE here so
    # the exit alignment is unambiguous (not the "End of data" artifact).
    bars.append(_de40_bar(day_0_open + 9 * 900, 106.4, 106.4, 99.0, 100.0))
    return bars


class TestChandelierTrailParity:
    """Dynamic Chandelier trail — full per-bar stop_history parity, AND the
    series must match the canonical chandelier_stop() formula independently.
    Per STRATEGY_AUDIT Part 2: engine ≡ monitor ≡ formula, bar-by-bar.
    """

    def test_per_bar_stop_history_engine_eq_monitor(self):
        bars = _ic_bar_series()
        cfg = _ic_cfg()
        res = run_backtest("DE40", "intraday_continuation", bars, cfg, RISK_CFG,
                           session_close_utc="21:00")
        assert res.total_trades == 1
        trade = res.trades[0]
        assert trade.direction == "BUY"
        assert len(trade.stop_history) > 0, "engine recorded no in-trade bars"

        m_reason, m_ts, m_stops = _monitor_replay(
            "intraday_continuation", cfg, bars, trade, kw=None,
            session_close="21:00", epic="DE40")

        # Full per-bar series parity — the bar-by-bar stop level must match
        assert m_stops == trade.stop_history, (
            f"per-bar stop_history DRIFT\n"
            f"engine ({len(trade.stop_history)} bars): {trade.stop_history}\n"
            f"monitor ({len(m_stops)} bars): {m_stops}")
        # Exit alignment if any
        assert (m_reason, m_ts) == (trade.exit_reason, trade.exit_ts), (
            f"exit DRIFT: engine={trade.exit_reason}@{trade.exit_ts} "
            f"monitor={m_reason}@{m_ts}")

    def test_per_bar_stop_history_matches_chandelier_formula(self):
        """Independently walk the bars and compute the expected stop_history
        via chandelier_stop() directly — engine must match that series exactly.

        This is the formula-vs-implementation check: catches a both-paths-
        broken-the-same-way bug that engine≡monitor parity alone would miss.
        """
        bars = _ic_bar_series()
        cfg = _ic_cfg()
        res = run_backtest("DE40", "intraday_continuation", bars, cfg, RISK_CFG,
                           session_close_utc="21:00")
        trade = res.trades[0]
        k_trail = cfg["risk"]["trailing_stop"]["atr_multiplier"]

        # Re-walk bars with a fresh state — same construction path the engine
        # uses (kwargs from _signal_kwargs_for("intraday_continuation", "DE40"))
        ctor_kwargs = _signal_kwargs_for("intraday_continuation", "DE40")
        state = _SIGNAL_CLS["intraday_continuation"](**ctor_kwargs)
        peak = trade.entry_price
        entered = False
        current_stop = trade.stop_loss
        expected: list[tuple[int, float]] = []
        for b in bars:
            state.update(b)
            if not entered and b.ts >= trade.entry_ts:
                state.notify_entry(trade.direction)
                peak = trade.entry_price
                entered = True
                continue
            if not entered:
                continue
            # Engine fires CLOSE on the exit bar BEFORE appending to
            # stop_history — independently match that: break before append.
            if b.ts == trade.exit_ts:
                break
            peak = (max(peak, b.high) if trade.direction == "BUY"
                    else min(peak, b.low))
            atr = state.atr
            if atr is None:
                expected.append((b.ts, current_stop))
                continue
            candidate = chandelier_stop(trade.direction, peak, atr, k_trail)
            if abs(candidate - current_stop) > 1e-9:
                current_stop = candidate
            expected.append((b.ts, current_stop))

        assert expected == trade.stop_history, (
            f"engine stop_history does NOT match chandelier_stop() formula\n"
            f"engine:   {trade.stop_history}\n"
            f"expected: {expected}")
