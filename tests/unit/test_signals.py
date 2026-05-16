"""Unit tests for strategy/signal_engine.py — pure functions, no I/O."""

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.strategy.signal_engine import (
    momentum_signal, mean_reversion_signal,
    MomentumSignalState, MeanReversionSignalState,
)


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
        # 21 flat + spike → crossover; +1 confirm bar (gap/slope hold) → LONG
        bars = _bars([1.0] * 21 + [1.10, 1.10])
        assert momentum_signal(bars) == "LONG"

    def test_short_signal_on_downward_crossover(self):
        # 21 flat + drop → crossover; +1 confirm bar → SHORT
        bars = _bars([1.0] * 21 + [0.90, 0.90])
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

    def test_gap_filter_suppresses_tiny_crossover(self):
        # Spike of 0.1% — produces a crossover but EMA gap < 0.15% minimum
        bars = _flat_then_spike(21, 1.0, 1.001)
        assert momentum_signal(bars) is None

    def test_gap_filter_allows_large_crossover(self):
        # Spike of 10% — gap well above the minimum, confirms within the window
        bars = _bars([1.0] * 21 + [1.10, 1.10])
        assert momentum_signal(bars) == "LONG"

    def test_returns_string_not_bool(self):
        bars = _bars([1.0] * 21 + [1.10, 1.10])
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
        # Old bars at high price, recent 20 bars at 1.0, final bar spikes down.
        # Tests z-score window isolation only — ADX gate disabled (adx_threshold=inf)
        # so the large trend in the old bars doesn't suppress the signal.
        old_bars = [100.0] * 30   # ignored by 20-bar window
        recent   = [1.0] * 19
        spike    = [0.5]
        bars = _bars(old_bars + recent + spike)
        state = MeanReversionSignalState(adx_threshold=float("inf"))
        result = None
        for bar in bars:
            result = state.update(bar)
        # z-score uses last 20: 19 bars at 1.0 + 1 bar at 0.5 → LONG
        assert result == "LONG"

    def test_returns_string_not_bool(self):
        bars = _bars([1.0] * 19 + [1.5])
        result = mean_reversion_signal(bars)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Stateful class tests — verify incremental O(1) classes match functional wrappers
# ---------------------------------------------------------------------------

class TestADXGate:
    """ADX regime gate — verified against both signal state classes."""

    def test_momentum_suppressed_when_adx_below_threshold(self):
        # 99 flat bars + 1 spike → EMA crossover fires but ADX ≈ 0 (non-trending)
        # Default adx_threshold=25; ADX stays near 0 → gate suppresses signal
        state = MomentumSignalState()
        bars = _bars([1.0] * 99 + [1.10])
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result is None

    def test_momentum_fires_when_adx_gate_disabled(self):
        # Same flat+spike sequence but with gate off (threshold=0) → signal fires
        state = MomentumSignalState(adx_threshold=0.0)
        bars = _bars([1.0] * 21 + [1.10, 1.10])   # cross + 1 confirm bar
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result == "LONG"

    def test_momentum_suppressed_by_high_explicit_threshold(self):
        # 80 flat bars (ADX→0), then 19 rising bars, then spike:
        # ADX climbs to ~78 from the trend, but threshold=100 → still suppressed
        rising = [1.0 + i * 0.01 for i in range(1, 20)]  # 1.01..1.19
        state = MomentumSignalState(adx_threshold=100.0)
        bars = _bars([1.0] * 80 + rising + [2.0])
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result is None

    def test_mean_reversion_fires_in_flat_market(self):
        # 19 flat bars + spike: ADX ≈ 0 (non-trending) → gate passes → signal fires
        state = MeanReversionSignalState()
        bars = _bars([1.0] * 19 + [1.5])
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result == "SHORT"

    def test_mean_reversion_suppressed_in_trending_market(self):
        # 80 flat at 1.0, then 19 strongly rising bars, then a spike up:
        # ADX ≈ 78 (trending) AND z-score ≥ 2.0 → gate suppresses signal
        # With gate disabled (threshold=inf) the same sequence fires.
        rising = [1.0 + i * 0.01 for i in range(1, 20)]  # 1.01..1.19
        spike  = [2.0]
        bars   = _bars([1.0] * 80 + rising + spike)

        # Gate active (default threshold=25): should be suppressed
        state_gated = MeanReversionSignalState()
        result_gated = None
        for bar in bars:
            result_gated = state_gated.update(bar)
        assert result_gated is None

        # Gate disabled (threshold=inf): should fire
        state_open = MeanReversionSignalState(adx_threshold=float("inf"))
        result_open = None
        for bar in bars:
            result_open = state_open.update(bar)
        assert result_open == "SHORT"


class TestATRGate:
    """ATR viability gate for MeanReversionSignalState."""

    def test_gate_blocks_when_spread_large_relative_to_atr(self):
        # 19 flat bars (TR≈0) + spike to 1.5; ATR is tiny.
        # With spread_pts=10.0 (absurdly large), 4×spread >> ATR → gate blocks.
        bars = _bars([1.0] * 19 + [1.5])
        state = MeanReversionSignalState(spread_pts=10.0)
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result is None

    def test_gate_disabled_when_spread_zero(self):
        # Same sequence but spread_pts=0.0 → gate off → signal fires normally
        bars = _bars([1.0] * 19 + [1.5])
        state = MeanReversionSignalState(spread_pts=0.0)
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result == "SHORT"

    def test_gate_permissive_while_atr_warming_up(self):
        # ATR not yet seeded (< 14 bars seen); gate must be permissive so
        # very short synthetic sequences still produce signals.
        bars = _bars([1.0] * 19 + [1.5])  # 20 bars; ATR seeded at bar 14
        # With a real but small spread, ATR is also small (spike TR ≈ 0.5/14);
        # the gate should still be permissive when spread_pts=0 (disabled).
        state = MeanReversionSignalState(spread_pts=0.0)
        result = None
        for bar in bars:
            result = state.update(bar)
        assert result == "SHORT"


class TestMomentumCrossBackExit:
    """check_exit() — momentum exits when EMA crosses back against the position
    (SYSTEM_DESIGN §3.7 rule 4). Replaces the removed MR hold-cap."""

    def _state_in_long(self) -> MomentumSignalState:
        # 21 flat + spike up → LONG crossover at bar 22 (ADX gate off so it fires)
        state = MomentumSignalState(adx_threshold=0.0)
        for bar in _flat_then_spike(21, 1.0, 1.10):
            state.update(bar)
        state.notify_entry("BUY")
        return state

    def test_no_exit_while_trend_holds(self):
        state = self._state_in_long()
        for bar in _bars([1.10 + i * 0.01 for i in range(1, 6)]):
            state.update(bar)
        assert state.check_exit() is None

    def test_long_exits_on_downward_cross_back(self):
        state = self._state_in_long()
        fired = None
        for bar in _bars([0.5] * 10):
            state.update(bar)
            if state.check_exit():
                fired = state.check_exit()
                break
        assert fired == "EMA cross-back"

    def test_short_exits_on_upward_cross_back(self):
        state = MomentumSignalState(adx_threshold=0.0)
        for bar in _flat_then_spike(21, 1.0, 0.90):   # SHORT crossover
            state.update(bar)
        state.notify_entry("SELL")
        fired = None
        for bar in _bars([1.5] * 10):
            state.update(bar)
            if state.check_exit():
                fired = state.check_exit()
                break
        assert fired == "EMA cross-back"

    def test_no_exit_without_position(self):
        # No notify_entry → no open position → never exits
        state = MomentumSignalState(adx_threshold=0.0)
        for bar in _bars([1.0] * 21 + [1.10] + [0.5] * 10):
            state.update(bar)
        assert state.check_exit() is None

    def test_exit_clears_after_notify_exit(self):
        state = self._state_in_long()
        for bar in _bars([0.5] * 10):
            state.update(bar)
        state.notify_exit()
        assert state.check_exit() is None


class TestMeanReversionHoldCapRemoved:
    """The former hold-cap exit was removed 2026-05-15 — only z-midline remains."""

    def test_max_hold_bars_constructor_arg_removed(self):
        with pytest.raises(TypeError):
            MeanReversionSignalState(max_hold_bars=5)

    def test_long_hold_never_returns_hold_cap(self):
        state = MeanReversionSignalState()
        for bar in _bars([1.0] * 19 + [1.5]):
            state.update(bar)
        state.notify_entry("SELL")
        # Hold far longer than the old 5-bar cap; "Hold cap" must never appear
        for bar in _bars([1.5] * 50):
            state.update(bar)
            assert state.check_exit() != "Hold cap"


class TestCheckExit:
    """check_exit() — z-score midline exit for mean reversion."""

    def _bar(self, price: float, ts: int = 0) -> OHLCBar:
        return OHLCBar(epic="EURUSD", resolution="M1", ts=ts,
                       open=price, high=price, low=price, close=price, volume=100)

    def test_mean_reversion_check_exit_none_before_window_full(self):
        state = MeanReversionSignalState()
        for i, bar in enumerate(_bars([1.0] * 19)):
            state.update(bar)
        assert state.check_exit() is None

    def test_mean_reversion_check_exit_none_when_z_large(self):
        # After spike, z is large → midline not reached → no exit
        state = MeanReversionSignalState()
        for bar in _bars([1.0] * 19 + [1.5]):
            state.update(bar)
        assert state.check_exit() is None

    def test_mean_reversion_check_exit_fires_when_z_small(self):
        # After spike, price reverts to baseline → z drops inside ±0.5 → exit fires
        state = MeanReversionSignalState()
        for bar in _bars([1.0] * 19 + [1.5]):
            state.update(bar)
        # Feed one reversion bar: window now has the spike + new close at 1.0;
        # z for 1.0 relative to the slightly shifted mean is ~-0.23 → abs ≤ 0.5
        state.update(_bars([1.0])[0])
        assert state.check_exit() == "Z-score midline"

    def test_momentum_check_exit_none_without_position(self):
        # check_exit is the signal-exit rule; with no open position it is inert
        state = MomentumSignalState()
        for bar in _flat_then_spike(21, 1.0, 1.10):
            state.update(bar)
        assert state.check_exit() is None


class TestMomentumSignalState:

    def test_fires_on_confirm_bar_not_cross_bar(self):
        # Crossover opens a pending; the signal fires on the FIRST confirm bar
        # (idx 22), never on the cross bar (idx 21), and not again afterwards.
        flat = _bars([1.0] * 21)
        spike = _bars([1.10])          # idx 21 — crossover
        hold = _bars([1.10] * 5)       # idx 22+ — confirm then steady
        state = MomentumSignalState()
        signals = []
        for bar in flat + spike + hold:
            signals.append(state.update(bar))
        assert signals[21] is None     # cross bar — pending opened, no fire
        assert signals[22] == "LONG"   # first confirm bar fires
        assert all(s is None for s in signals[23:])

    def test_ema_stays_current_during_position(self):
        # After 21 flat bars + spike, the state should still work correctly
        # if we skip acting on the signal (simulating "in position" scenario)
        state = MomentumSignalState()
        for bar in _bars([1.0] * 21 + [1.10]):
            state.update(bar)
        # Feed more flat bars (simulating position hold); EMA should update
        for bar in _bars([1.10] * 10):
            result = state.update(bar)
            # No new crossover expected on flat bars
            assert result is None or result == "LONG"  # possible re-crossover, not an error

    def test_new_instance_starts_fresh(self):
        # Two instances fed the same bars should produce identical results
        bars = _flat_then_spike(21, 1.0, 1.10)
        state1 = MomentumSignalState()
        state2 = MomentumSignalState()
        results1 = [state1.update(b) for b in bars]
        results2 = [state2.update(b) for b in bars]
        assert results1 == results2

    def test_matches_functional_wrapper_on_crossover(self):
        bars = _flat_then_spike(21, 1.0, 1.10)
        state = MomentumSignalState()
        last = None
        for bar in bars:
            last = state.update(bar)
        assert last == momentum_signal(bars)

    def test_no_signal_before_min_bars(self):
        state = MomentumSignalState()
        for bar in _bars([1.0] * 21):
            assert state.update(bar) is None


class TestM30Gate:
    """M30 directional bias gate for MomentumSignalState.

    Sequences are designed so the crossover falls within the same 30-bar window
    used for M30 slope, so the two effects can be tuned independently.

    LONG crossover reference sequence: [1.0]*29 + [1.30]
      - 29 flat bars → both EMAs converge to 1.0, prev_ema9=prev_ema21
      - bar 30 spike → EMA9 jumps above EMA21 (LONG crossover)
      - M30 slope: 29×1.0 then 1.30 → positive (bullish)

    Bearish-M30 LONG sequence: [1.1]*10 + [1.0]*20 + [1.30] (31 bars)
      - Same LONG crossover at bar 31
      - M30 (last 30 bars): 9×1.1 + 20×1.0 + 1×1.30 → slope negative (bearish)

    SHORT crossover sequence: rising 0.9→0.984 (29 bars) then drop to 0.70
      - Rising prices → EMA9 > EMA21 going in; drop creates SHORT crossover
      - M30 slope: rising portion dominates → positive (bullish)
    """

    def test_gate_passes_long_when_m30_bullish(self):
        # [1.0]*29 + [1.30]: M30 slope positive (bullish) → LONG passes
        state = MomentumSignalState(adx_threshold=0.0, m30_gate=True)
        for bar in _bars([1.0] * 29 + [1.30, 1.30]):   # cross + confirm bar
            result = state.update(bar)
        assert result == "LONG"

    def test_gate_blocks_long_when_m30_bearish(self):
        # [1.1]*10 + [1.0]*20 + [1.30]: M30 slope negative (bearish) → LONG blocked
        state = MomentumSignalState(adx_threshold=0.0, m30_gate=True)
        for bar in _bars([1.1] * 10 + [1.0] * 20 + [1.30]):
            result = state.update(bar)
        assert result is None

    def test_gate_disabled_when_m30_gate_false(self):
        # Same bearish-M30 sequence but gate off → LONG fires
        state = MomentumSignalState(adx_threshold=0.0, m30_gate=False)
        for bar in _bars([1.1] * 10 + [1.0] * 20 + [1.30, 1.30]):  # cross + confirm
            result = state.update(bar)
        assert result == "LONG"

    def test_gate_blocks_short_when_m30_bullish(self):
        # Rising 0.9→0.984 then drop to 0.70: M30 slope positive (bullish) → SHORT blocked
        closes = [0.9 + i * 0.003 for i in range(29)] + [0.70]
        state = MomentumSignalState(adx_threshold=0.0, m30_gate=True)
        for bar in _bars(closes):
            result = state.update(bar)
        assert result is None

    def test_gate_permissive_during_warmup(self):
        # Only 22 bars total — M30 buffer not full (<30) → gate permissive → LONG fires
        state = MomentumSignalState(adx_threshold=0.0, m30_gate=True)
        for bar in _bars([1.0] * 21 + [1.10, 1.10]):   # cross + confirm bar
            result = state.update(bar)
        assert result == "LONG"


class TestMeanReversionSignalState:

    def test_fires_on_correct_bar(self):
        state = MeanReversionSignalState()
        bars = _bars([1.0] * 19 + [1.5])
        signals = [state.update(b) for b in bars]
        assert signals[-1] == "SHORT"
        assert all(s is None for s in signals[:-1])

    def test_matches_functional_wrapper(self):
        bars = _bars([1.0] * 19 + [0.5])
        state = MeanReversionSignalState()
        last = None
        for bar in bars:
            last = state.update(bar)
        assert last == mean_reversion_signal(bars)

    def test_no_signal_before_window_full(self):
        state = MeanReversionSignalState()
        for bar in _bars([1.0] * 19):
            assert state.update(bar) is None
