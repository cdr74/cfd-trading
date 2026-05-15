"""Live monitor ↔ backtest parity (3c anti-drift guarantee).

Both paths share `evaluate_position` + `signal_engine`. These tests pin that:
for the same bar sequence + trade, the backtest engine's recorded exit equals
the exit produced by replaying the SAME public contract the live monitor uses
per cycle (signal_engine state + `evaluate_position(now=, signal_state,
entry_atr, peak_price)`). If a future change makes one path diverge from the
other, these fail.
"""

import datetime as dt

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.engine import run_backtest, _utc_dt, _session_end_for
from cfd_trading.monitor.monitor import evaluate_position, _SIGNAL_CLS

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


def _monitor_replay(strategy, cfg, bars, trade, kw, session_close):
    """Reproduce the monitor's per-cycle exit contract for an open trade."""
    te = cfg["risk"]["time_exit"]["enabled"]
    state = _SIGNAL_CLS[strategy](**(kw or {}))
    entry_atr = None
    peak = trade.entry_price
    entered = False
    current_stop = trade.stop_loss
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
            signal_state=state, entry_atr=entry_atr, peak_price=peak)
        if action == "CLOSE":
            return reason, b.ts
        if action == "ADJUST" and new_stop is not None:
            current_stop = new_stop
    return None, None


def _assert_parity(strategy, cfg, prices, start, kw=None, close="21:00"):
    bars = _bars(prices, start)
    res = run_backtest("EURUSD", strategy, bars, cfg, RISK_CFG,
                       signal_kwargs=kw, session_close_utc=close)
    assert res.total_trades == 1, "fixture must produce exactly one trade"
    t = res.trades[0]
    m_reason, m_ts = _monitor_replay(strategy, cfg, bars, t, kw, close)
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
