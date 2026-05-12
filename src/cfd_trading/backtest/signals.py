"""Deterministic entry signal rules for backtesting.

Each function takes a list of OHLCBar objects (chronological order, latest last)
and returns "LONG", "SHORT", or None.

These are rule-based approximations of the live entry logic:
  - momentum: EMA_9/EMA_21 crossover confirmed by trend slope
  - mean_reversion: |z_score| >= 2.0, direction opposite to z_score sign
"""

from cfd_trading.storage.repository import OHLCBar

# Minimum bars required before each signal can fire.
_MIN_BARS_MOMENTUM = 22       # EMA_21 needs 21 bars; crossover needs one prior bar
_MIN_BARS_MEAN_REVERSION = 20  # z-score window


def momentum_signal(bars: list[OHLCBar]) -> str | None:
    """Return LONG/SHORT on an EMA_9/EMA_21 crossover confirmed by trend slope, else None."""
    if len(bars) < _MIN_BARS_MOMENTUM:
        return None

    closes_prev = [b.close for b in bars[:-1]]
    closes_curr = [b.close for b in bars]

    ema9_prev  = _ema(closes_prev, 9)
    ema21_prev = _ema(closes_prev, 21)
    ema9_curr  = _ema(closes_curr, 9)
    ema21_curr = _ema(closes_curr, 21)

    if any(v is None for v in (ema9_prev, ema21_prev, ema9_curr, ema21_curr)):
        return None

    slope = _trend_slope(closes_curr)

    crossed_long  = ema9_prev <= ema21_prev and ema9_curr > ema21_curr
    crossed_short = ema9_prev >= ema21_prev and ema9_curr < ema21_curr

    if crossed_long and slope > 0:
        return "LONG"
    if crossed_short and slope < 0:
        return "SHORT"
    return None


def mean_reversion_signal(bars: list[OHLCBar]) -> str | None:
    """Return SHORT when z >= 2.0, LONG when z <= -2.0, else None."""
    if len(bars) < _MIN_BARS_MEAN_REVERSION:
        return None

    closes = [b.close for b in bars]
    z = _zscore(closes)
    if z is None:
        return None

    if z >= 2.0:
        return "SHORT"
    if z <= -2.0:
        return "LONG"
    return None


# ---------------------------------------------------------------------------
# Private indicator helpers — operate on plain float lists, no I/O
# ---------------------------------------------------------------------------

def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = alpha * c + (1 - alpha) * ema
    return ema


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
