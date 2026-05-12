"""Entry signal state for backtesting.

Each class maintains running indicator state and produces LONG/SHORT/None on
each new bar in O(1) time.  Create a fresh instance per instrument/strategy run.

  MomentumSignalState    — incremental EMA9/EMA21 crossover + gap filter + slope
  MeanReversionSignalState — rolling 20-bar z-score threshold

The functional wrappers (momentum_signal, mean_reversion_signal) are kept as
conveniences for unit tests.  The backtest engine uses the stateful classes
directly to achieve O(n) instead of O(n²) complexity.
"""

from collections import deque

from cfd_trading.storage.repository import OHLCBar

# Minimum fractional gap between EMA9 and EMA21 at the moment of crossover.
# Filters noise crossovers where the two EMAs are nearly identical on M1 bars.
# Tuned empirically: 0.02% gives the best trade count / signal quality balance
# across the 11-instrument watchlist.  Higher values (>0.05%) leave too few trades
# to be statistically meaningful; lower values (<0.01%) flood with noise signals.
_MIN_EMA_GAP_PCT = 0.0002   # 0.02%


# ---------------------------------------------------------------------------
# Stateful signal classes — used by the backtest engine
# ---------------------------------------------------------------------------

class MomentumSignalState:
    """O(1)-per-bar EMA9/EMA21 crossover momentum signal.

    Maintains incremental EMA state.  Slope is computed over a capped 22-bar
    window (same as the EMA warm-up period) rather than unbounded history,
    which is more appropriate for intraday M1 signals.
    """

    _ALPHA9  = 2.0 / (9  + 1)
    _ALPHA21 = 2.0 / (21 + 1)
    _MIN_BARS    = 22   # EMA21 seeds at bar 21; crossover needs one prior bar
    _SLOPE_WINDOW = 22  # cap slope window to same length

    def __init__(self, min_ema_gap_pct: float = _MIN_EMA_GAP_PCT) -> None:
        self._min_ema_gap_pct = min_ema_gap_pct
        self._n: int = 0
        self._ema9:  float | None = None
        self._ema21: float | None = None
        self._prev_ema9:  float | None = None
        self._prev_ema21: float | None = None
        self._sum9  = 0.0
        self._sum21 = 0.0
        self._slope_buf: deque[float] = deque(maxlen=self._SLOPE_WINDOW)

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        close = bar.close
        self._n += 1
        self._slope_buf.append(close)

        # Snapshot prev before updating current bar
        self._prev_ema9  = self._ema9
        self._prev_ema21 = self._ema21

        # EMA9 — seed with SMA at bar 9, then increment
        if self._n < 9:
            self._sum9 += close
        elif self._n == 9:
            self._sum9 += close
            self._ema9 = self._sum9 / 9
        else:
            self._ema9 = self._ALPHA9 * close + (1 - self._ALPHA9) * self._ema9

        # EMA21 — seed with SMA at bar 21, then increment
        if self._n < 21:
            self._sum21 += close
        elif self._n == 21:
            self._sum21 += close
            self._ema21 = self._sum21 / 21
        else:
            self._ema21 = self._ALPHA21 * close + (1 - self._ALPHA21) * self._ema21

        if self._n < self._MIN_BARS:
            return None

        # Gap filter — suppress near-identical EMA crossovers
        gap_pct = abs(self._ema9 - self._ema21) / self._ema21
        if gap_pct < self._min_ema_gap_pct:
            return None

        slope = _trend_slope(list(self._slope_buf))

        crossed_long  = self._prev_ema9 <= self._prev_ema21 and self._ema9 > self._ema21
        crossed_short = self._prev_ema9 >= self._prev_ema21 and self._ema9 < self._ema21

        if crossed_long and slope > 0:
            return "LONG"
        if crossed_short and slope < 0:
            return "SHORT"
        return None


class MeanReversionSignalState:
    """O(1)-per-bar z-score mean reversion signal.

    Maintains a rolling 20-bar deque; identical to mean_reversion_signal()
    without rebuilding the full history list each bar.
    """

    _WINDOW = 20

    def __init__(self) -> None:
        self._buf: deque[float] = deque(maxlen=self._WINDOW)

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        self._buf.append(bar.close)
        if len(self._buf) < self._WINDOW:
            return None
        z = _zscore(list(self._buf))
        if z is None:
            return None
        if z >= 2.0:
            return "SHORT"
        if z <= -2.0:
            return "LONG"
        return None


# ---------------------------------------------------------------------------
# Functional wrappers — unit-test convenience; replay bars through state class
# ---------------------------------------------------------------------------

def momentum_signal(bars: list[OHLCBar]) -> str | None:
    """Return the signal that would fire on the last bar of `bars`."""
    state = MomentumSignalState()
    result = None
    for bar in bars:
        result = state.update(bar)
    return result


def mean_reversion_signal(bars: list[OHLCBar]) -> str | None:
    """Return the signal that would fire on the last bar of `bars`."""
    state = MeanReversionSignalState()
    result = None
    for bar in bars:
        result = state.update(bar)
    return result


# ---------------------------------------------------------------------------
# Private indicator helpers
# ---------------------------------------------------------------------------

def _zscore(closes: list[float], period: int = 20) -> float | None:
    window = closes[-period:]
    if len(window) < 4:
        return None
    mu = sum(window) / len(window)
    sigma = (sum((c - mu) ** 2 for c in window) / len(window)) ** 0.5
    if sigma == 0:
        return None
    return (window[-1] - mu) / sigma


def _trend_slope(closes: list[float]) -> float:
    n = len(closes)
    if n < 4:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(closes) / n
    num = sum((i - mean_x) * (closes[i] - mean_y) for i in range(n))
    den = sum((i - mean_x) ** 2 for i in range(n))
    return num / den if den else 0.0
