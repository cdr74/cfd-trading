"""Unit tests for backtest/signals.py — pure functions, no I/O."""

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.signals import momentum_signal, mean_reversion_signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bars(closes: list[float]) -> list[OHLCBar]:
    """Build minimal OHLCBar list from a close-price sequence."""
    return [
        OHLCBar(epic="EURUSD", resolution="M1", ts=i * 60,
                open=c, high=c, low=c, close=c, volume=100)
        for i, c in enumerate(closes)
    ]


def _flat_then_spike(n_flat: int, flat_price: float, spike_price: float) -> list[OHLCBar]:
    """n_flat bars at flat_price, then one bar at spike_price."""
    return _bars([flat_price] * n_flat + [spike_price])


# ---------------------------------------------------------------------------
# momentum_signal
# ---------------------------------------------------------------------------

class TestMomentumSignal:

    def test_insufficient_bars_returns_none(self):
        # Need at least 22 bars; 21 is one short
        assert momentum_signal(_bars([1.0] * 21)) is None

    def test_exactly_minimum_bars_does_not_raise(self):
        # 22 bars of flat prices — no crossover, but should not raise
        result = momentum_signal(_bars([1.0] * 22))
        assert result is None  # flat: no crossover

    def test_long_signal_on_upward_crossover(self):
        # 21 bars flat at 1.0, then spike to 1.10 → EMA_9 crosses above EMA_21
        bars = _flat_then_spike(21, 1.0, 1.10)
        assert momentum_signal(bars) == "LONG"

    def test_short_signal_on_downward_crossover(self):
        # 21 bars flat at 1.0, then drop to 0.90 → EMA_9 crosses below EMA_21
        bars = _flat_then_spike(21, 1.0, 0.90)
        assert momentum_signal(bars) == "SHORT"

    def test_no_signal_when_ema9_already_above_ema21(self):
        # Rising trend throughout — crossover already happened before our window
        closes = [1.0 + i * 0.01 for i in range(40)]
        assert momentum_signal(_bars(closes)) is None

    def test_no_signal_when_ema9_already_below_ema21(self):
        # Falling trend throughout — crossover already happened before our window
        closes = [2.0 - i * 0.01 for i in range(40)]
        assert momentum_signal(_bars(closes)) is None

    def test_long_requires_positive_slope(self):
        # Spike up but preceded by a sharper fall → slope is negative → no LONG signal
        # 10 bars falling sharply, then 11 flat, then 1 small upward tick
        closes = [2.0 - i * 0.05 for i in range(10)] + [1.5] * 11 + [1.51]
        result = momentum_signal(_bars(closes))
        # slope of the full series is negative; crossover (if any) should be suppressed
        assert result != "LONG"

    def test_returns_string_not_bool(self):
        bars = _flat_then_spike(21, 1.0, 1.10)
        result = momentum_signal(bars)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# mean_reversion_signal
# ---------------------------------------------------------------------------

class TestMeanReversionSignal:

    def test_insufficient_bars_returns_none(self):
        assert mean_reversion_signal(_bars([1.0] * 19)) is None

    def test_no_signal_when_z_within_threshold(self):
        # Flat prices → z-score = 0
        assert mean_reversion_signal(_bars([1.0] * 25)) is None

    def test_short_when_z_exceeds_positive_threshold(self):
        # 19 bars at 1.0, then final bar at 1.5 → large positive z → SHORT
        bars = _bars([1.0] * 19 + [1.5])
        result = mean_reversion_signal(bars)
        assert result == "SHORT"

    def test_long_when_z_exceeds_negative_threshold(self):
        # 19 bars at 1.0, then final bar at 0.5 → large negative z → LONG
        bars = _bars([1.0] * 19 + [0.5])
        result = mean_reversion_signal(bars)
        assert result == "LONG"

    def test_no_signal_when_price_within_two_sigma(self):
        # Alternating prices give sigma ≈ 0.01; final bar near mean → z ≈ 0
        # (flat identical bars would give sigma≈0, inflating z for any deviation)
        closes = [1.0 + (i % 2) * 0.02 for i in range(19)] + [1.01]
        assert mean_reversion_signal(_bars(closes)) is None

    def test_uses_last_20_bars_for_zscore(self):
        # Old bars at high price, recent 20 bars at 1.0, final bar spikes down
        # z-score should be based on the last 20 bars (recent window), not all history
        old_bars = [100.0] * 30   # ignored by 20-bar window
        recent   = [1.0] * 19
        spike    = [0.5]
        bars = _bars(old_bars + recent + spike)
        # z-score uses last 20: 19 bars at 1.0 + 1 bar at 0.5 → LONG
        assert mean_reversion_signal(bars) == "LONG"

    def test_returns_string_not_bool(self):
        bars = _bars([1.0] * 19 + [1.5])
        result = mean_reversion_signal(bars)
        assert isinstance(result, str)
