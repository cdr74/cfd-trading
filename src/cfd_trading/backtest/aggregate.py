"""OHLC bar aggregation — collapse M1 bars to higher-resolution bars.

aggregate_bars(bars, period_minutes) groups M1 bars by UTC time bucket and
merges each group into a single OHLC bar.  Partial groups at the end (when the
total bar count is not a multiple of period_minutes) are included as-is.

Session boundaries are handled naively — a bucket spanning a gap in the data
still produces one bar, with the gap visible as a low bar-count group.  This is
acceptable for backtesting purposes where boundary bars are a small minority.
"""

from cfd_trading.storage.repository import OHLCBar


def aggregate_bars(bars: list[OHLCBar], period_minutes: int) -> list[OHLCBar]:
    """Aggregate M1 bars into period_minutes-resolution OHLC bars.

    period_minutes=1 returns the input list unchanged.
    Groups are keyed by floor(unix_ts_seconds / (period_minutes * 60)).
    """
    if period_minutes == 1 or not bars:
        return bars

    resolution = f"M{period_minutes}"
    bucket_seconds = period_minutes * 60

    result: list[OHLCBar] = []
    current_bucket: int | None = None
    group: list[OHLCBar] = []

    for bar in bars:
        b = bar.ts // bucket_seconds
        if b != current_bucket:
            if group:
                result.append(_merge(group, resolution))
            group = [bar]
            current_bucket = b
        else:
            group.append(bar)

    if group:
        result.append(_merge(group, resolution))

    return result


def _merge(group: list[OHLCBar], resolution: str) -> OHLCBar:
    return OHLCBar(
        epic=group[0].epic,
        resolution=resolution,
        ts=group[0].ts,
        open=group[0].open,
        high=max(b.high for b in group),
        low=min(b.low for b in group),
        close=group[-1].close,
        volume=sum(b.volume for b in group),
    )
