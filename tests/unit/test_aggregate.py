"""Unit tests for backtest/aggregate.py."""

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.aggregate import aggregate_bars


def _bar(ts: int, open_: float, high: float, low: float, close: float, volume: int = 100) -> OHLCBar:
    return OHLCBar(epic="DE40", resolution="M1", ts=ts,
                   open=open_, high=high, low=low, close=close, volume=volume)


def _m1_bars(n: int, base_ts: int = 0, price: float = 1.0) -> list[OHLCBar]:
    """n M1 bars at consecutive 60-second timestamps, all OHLC at price."""
    return [_bar(base_ts + i * 60, price, price, price, price) for i in range(n)]


class TestAggregateIdentity:

    def test_period_1_returns_input_unchanged(self):
        bars = _m1_bars(5)
        assert aggregate_bars(bars, 1) is bars

    def test_empty_list_returns_empty(self):
        assert aggregate_bars([], 15) == []


class TestAggregateOHLC:

    def test_15_bars_produce_one_m15_bar(self):
        bars = _m1_bars(15, base_ts=0)
        result = aggregate_bars(bars, 15)
        assert len(result) == 1

    def test_resolution_label_set_correctly(self):
        bars = _m1_bars(15)
        result = aggregate_bars(bars, 15)
        assert result[0].resolution == "M15"

    def test_ts_is_first_bar_in_group(self):
        bars = _m1_bars(15, base_ts=900)  # first bar at t=900
        result = aggregate_bars(bars, 15)
        assert result[0].ts == 900

    def test_open_is_first_bar_open(self):
        bars = [
            _bar(0,   1.0, 1.1, 0.9, 1.05),
            _bar(60,  1.05, 1.2, 1.0, 1.1),
            _bar(120, 1.1, 1.3, 1.05, 1.2),
        ]
        # all three fall in the same 15-min bucket (ts 0..839)
        result = aggregate_bars(bars, 15)
        assert result[0].open == pytest.approx(1.0)

    def test_high_is_max_of_group(self):
        bars = [
            _bar(0,   1.0, 1.5, 0.9, 1.0),
            _bar(60,  1.0, 1.8, 0.8, 1.0),
            _bar(120, 1.0, 1.2, 0.7, 1.0),
        ]
        result = aggregate_bars(bars, 15)
        assert result[0].high == pytest.approx(1.8)

    def test_low_is_min_of_group(self):
        bars = [
            _bar(0,   1.0, 1.5, 0.9, 1.0),
            _bar(60,  1.0, 1.8, 0.8, 1.0),
            _bar(120, 1.0, 1.2, 0.7, 1.0),
        ]
        result = aggregate_bars(bars, 15)
        assert result[0].low == pytest.approx(0.7)

    def test_close_is_last_bar_close(self):
        bars = [
            _bar(0,   1.0, 1.1, 0.9, 1.05),
            _bar(60,  1.05, 1.2, 1.0, 1.1),
            _bar(120, 1.1, 1.3, 1.05, 1.99),
        ]
        result = aggregate_bars(bars, 15)
        assert result[0].close == pytest.approx(1.99)

    def test_volume_is_sum_of_group(self):
        bars = [
            _bar(0,   1.0, 1.0, 1.0, 1.0, volume=100),
            _bar(60,  1.0, 1.0, 1.0, 1.0, volume=200),
            _bar(120, 1.0, 1.0, 1.0, 1.0, volume=300),
        ]
        result = aggregate_bars(bars, 15)
        assert result[0].volume == 600

    def test_epic_preserved(self):
        bar = OHLCBar(epic="GOLD", resolution="M1", ts=0,
                      open=2300.0, high=2310.0, low=2290.0, close=2305.0, volume=50)
        result = aggregate_bars([bar], 15)
        assert result[0].epic == "GOLD"


class TestAggregateMultipleGroups:

    def test_30_bars_produce_two_m15_groups(self):
        # First 15 bars in bucket 0 (ts 0..840), next 15 in bucket 900 (ts 900..1740)
        bars = _m1_bars(15, base_ts=0) + _m1_bars(15, base_ts=900)
        result = aggregate_bars(bars, 15)
        assert len(result) == 2

    def test_group_boundary_at_correct_ts(self):
        bars = _m1_bars(15, base_ts=0) + _m1_bars(15, base_ts=900)
        result = aggregate_bars(bars, 15)
        assert result[0].ts == 0
        assert result[1].ts == 900

    def test_partial_group_at_end_included(self):
        # 17 M1 bars: first 15 complete a bucket, last 2 form a partial group
        bars = _m1_bars(15, base_ts=0) + _m1_bars(2, base_ts=900)
        result = aggregate_bars(bars, 15)
        assert len(result) == 2
        assert result[1].volume == 200  # 2 bars × volume 100

    def test_m5_aggregation(self):
        bars = _m1_bars(10, base_ts=0)   # 2 complete M5 buckets
        result = aggregate_bars(bars, 5)
        assert len(result) == 2
        assert result[0].resolution == "M5"
