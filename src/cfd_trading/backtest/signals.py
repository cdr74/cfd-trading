"""Entry signal state for backtesting.

Each class maintains running indicator state and produces LONG/SHORT/None on
each new bar in O(1) time.  Create a fresh instance per instrument/strategy run.

  MomentumSignalState    — incremental EMA9/EMA21 crossover + gap filter + slope
                           + ADX(14) regime gate (suppressed when ADX < threshold)
                           + notify_entry/notify_exit (no-ops; satisfy shared interface)
  MeanReversionSignalState — rolling 20-bar z-score threshold
                             + ADX(14) regime gate (suppressed when ADX >= threshold)
                             + ATR(14) ≥ 4× spread gate (skips low-vol entries)
                             + check_exit(): hold cap (max_hold_bars) then z-score midline
                             + notify_entry/notify_exit for hold-cap bar counting

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
_MIN_EMA_GAP_PCT = 0.0005   # 0.05% — research-validated floor for index CFDs (4 pts at 8,000)


# ---------------------------------------------------------------------------
# Private indicator helpers
# ---------------------------------------------------------------------------

class _ADXState:
    """Wilder's smoothed ADX(period).  O(1) per bar.

    Uses OHLC to compute True Range and directional movement.  Returns None
    until warmed up (requires 2 × period bars after the first).  When ATR is
    zero (flat market) ADX is set to 0.0 — definitively non-trending.
    """

    def __init__(self, period: int = 14) -> None:
        self._period = period
        self._n = 0          # bars processed after the first (which seeds prev state)
        self._prev_high: float | None = None
        self._prev_low:  float | None = None
        self._prev_close: float | None = None
        # Accumulators for the Wilder seed sum (first period bars)
        self._sum_tr  = 0.0
        self._sum_pdm = 0.0
        self._sum_ndm = 0.0
        # Wilder's smoothed components (None until seeded)
        self._atr:   float | None = None
        self._pdm_s: float | None = None
        self._ndm_s: float | None = None
        # ADX seed accumulator and current value
        self._dx_seed: list[float] = []
        self._adx: float | None = None

    def update(self, bar: OHLCBar) -> float | None:
        """Return current ADX value, or None while warming up."""
        h, l, c = bar.high, bar.low, bar.close

        if self._prev_close is None:
            self._prev_high  = h
            self._prev_low   = l
            self._prev_close = c
            return None

        # True Range and directional movement
        tr      = max(h - l, abs(h - self._prev_close), abs(l - self._prev_close))
        move_up = h - self._prev_high
        move_dn = self._prev_low - l
        pdm = move_up if (move_up > move_dn and move_up > 0) else 0.0
        ndm = move_dn if (move_dn > move_up and move_dn > 0) else 0.0

        self._prev_high  = h
        self._prev_low   = l
        self._prev_close = c
        self._n += 1

        if self._n < self._period:
            self._sum_tr  += tr
            self._sum_pdm += pdm
            self._sum_ndm += ndm
            return None

        if self._n == self._period:
            # Seed Wilder's smoothed values with the sum of the first period bars
            self._sum_tr  += tr
            self._sum_pdm += pdm
            self._sum_ndm += ndm
            self._atr   = self._sum_tr
            self._pdm_s = self._sum_pdm
            self._ndm_s = self._sum_ndm
        else:
            # Wilder's smoothing: new = old − old/period + new_raw
            self._atr   = self._atr   - self._atr   / self._period + tr
            self._pdm_s = self._pdm_s - self._pdm_s / self._period + pdm
            self._ndm_s = self._ndm_s - self._ndm_s / self._period + ndm

        # Compute DX; flat market (ATR=0) → DX=0 (definitively non-trending)
        if self._atr == 0.0:
            dx = 0.0
        else:
            pdi     = self._pdm_s / self._atr * 100
            ndi     = self._ndm_s / self._atr * 100
            di_sum  = pdi + ndi
            dx      = abs(pdi - ndi) / di_sum * 100 if di_sum > 0 else 0.0

        # Seed ADX with the first period DX values; Wilder-smooth thereafter
        if self._adx is None:
            self._dx_seed.append(dx)
            if len(self._dx_seed) >= self._period:
                self._adx = sum(self._dx_seed) / self._period
                self._dx_seed.clear()
            return None

        self._adx = (self._adx * (self._period - 1) + dx) / self._period
        return self._adx

    @property
    def atr(self) -> float | None:
        """Standard Wilder ATR in price units, or None while warming up.

        Internally _atr stores the Wilder-smoothed sum (= period × standard ATR)
        so the smoothing update formula is additive rather than multiplicative.
        Dividing by period here restores the conventional per-bar ATR value.
        """
        if self._atr is None:
            return None
        return self._atr / self._period


# ---------------------------------------------------------------------------
# Stateful signal classes — used by the backtest engine
# ---------------------------------------------------------------------------

class MomentumSignalState:
    """O(1)-per-bar EMA9/EMA21 crossover momentum signal.

    Maintains incremental EMA state.  Slope is computed over a capped 22-bar
    window (same as the EMA warm-up period) rather than unbounded history,
    which is more appropriate for intraday M1 signals.

    ADX regime gate: signal is suppressed when ADX < adx_threshold (non-trending
    market).  During ADX warm-up (ADX is None) the gate is permissive so that
    short test sequences and the first bars of a live run are unaffected.
    """

    _ALPHA9  = 2.0 / (9  + 1)
    _ALPHA21 = 2.0 / (21 + 1)
    _MIN_BARS     = 22   # EMA21 seeds at bar 21; crossover needs one prior bar
    _SLOPE_WINDOW = 22   # cap slope window to same length

    def __init__(
        self,
        min_ema_gap_pct: float = _MIN_EMA_GAP_PCT,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
    ) -> None:
        self._min_ema_gap_pct = min_ema_gap_pct
        self._adx_threshold   = adx_threshold
        self._n: int = 0
        self._ema9:  float | None = None
        self._ema21: float | None = None
        self._prev_ema9:  float | None = None
        self._prev_ema21: float | None = None
        self._sum9  = 0.0
        self._sum21 = 0.0
        self._slope_buf: deque[float] = deque(maxlen=self._SLOPE_WINDOW)
        self._adx_state = _ADXState(adx_period)

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        close = bar.close
        self._n += 1
        self._slope_buf.append(close)

        adx = self._adx_state.update(bar)

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

        # ADX regime gate — require a trending market; pass when ADX not yet warmed up
        if adx is not None and adx < self._adx_threshold:
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

    def check_exit(self) -> str | None:
        """Return an exit reason if the current indicator state suggests closing.

        Momentum has no indicator-based mid-trade exit; exits are handled
        entirely by evaluate_position() (trailing stop / take profit / hard stop).
        """
        return None

    def notify_entry(self) -> None:
        pass

    def notify_exit(self) -> None:
        pass


class MeanReversionSignalState:
    """O(1)-per-bar z-score mean reversion signal.

    Maintains a rolling 20-bar deque.

    ADX regime gate: signal is suppressed when ADX >= adx_threshold (trending
    market — mean reversion logic breaks down).  During ADX warm-up the gate
    is permissive.

    ATR viability gate: signal is suppressed when ATR(14) < 4 × spread_pts
    (volatility too low relative to fixed spread cost to produce positive
    expectancy).  Disabled when spread_pts=0.0 (default).

    check_exit() priority:
      1. Hold cap  — fires after max_hold_bars bars in trade (default 5)
      2. Z-score midline — fires when |z| ≤ zscore_exit_threshold (default 0.5)

    notify_entry() / notify_exit() are called by the engine to synchronise the
    hold-cap bar counter with actual position state.
    """

    _WINDOW = 20

    def __init__(
        self,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        zscore_exit_threshold: float = 0.5,
        spread_pts: float = 0.0,
        max_hold_bars: int = 5,
    ) -> None:
        self._adx_threshold         = adx_threshold
        self._zscore_exit_threshold = zscore_exit_threshold
        self._spread_pts            = spread_pts
        self._max_hold_bars         = max_hold_bars
        self._buf: deque[float] = deque(maxlen=self._WINDOW)
        self._adx_state = _ADXState(adx_period)
        self._last_z: float | None = None
        self._bars_in_trade: int | None = None  # None = no open position

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        self._buf.append(bar.close)
        adx = self._adx_state.update(bar)
        atr = self._adx_state.atr

        if self._bars_in_trade is not None:
            self._bars_in_trade += 1

        if len(self._buf) < self._WINDOW:
            self._last_z = None
            return None

        z = _zscore(list(self._buf))
        self._last_z = z

        if z is None:
            return None

        # ATR viability gate — only feasible when volatility >> spread cost
        # Permissive while ATR is warming up (first ~14 bars)
        if self._spread_pts > 0 and atr is not None and atr < 4.0 * self._spread_pts:
            return None

        # ADX regime gate — require a non-trending market; pass when ADX not yet warmed up
        if adx is not None and adx >= self._adx_threshold:
            return None

        if z >= 2.0:
            return "SHORT"
        if z <= -2.0:
            return "LONG"
        return None

    def check_exit(self) -> str | None:
        """Return an exit reason if the position should close based on indicator state.

        Priority: hold cap before z-score midline.
        Hard stop and take profit (evaluated by evaluate_position) take priority
        over both — they are checked before check_exit() is called by the engine.
        """
        if self._bars_in_trade is not None and self._bars_in_trade >= self._max_hold_bars:
            return "Hold cap"
        if self._last_z is not None and abs(self._last_z) <= self._zscore_exit_threshold:
            return "Z-score midline"
        return None

    def notify_entry(self) -> None:
        """Called by the engine immediately after a trade is opened."""
        self._bars_in_trade = 0

    def notify_exit(self) -> None:
        """Called by the engine immediately after a trade is closed."""
        self._bars_in_trade = None


# ---------------------------------------------------------------------------
# Functional wrappers — unit-test convenience; replay bars through state class
# ---------------------------------------------------------------------------

def momentum_signal(bars: list[OHLCBar]) -> str | None:
    """Return the signal that would fire on the last bar of `bars`.

    ADX gate is active (default threshold 25.0).  During warm-up (fewer than
    ~28 bars) the gate is permissive, so short test sequences still work.
    """
    state = MomentumSignalState()
    result = None
    for bar in bars:
        result = state.update(bar)
    return result


def mean_reversion_signal(bars: list[OHLCBar]) -> str | None:
    """Return the signal that would fire on the last bar of `bars`.

    ADX gate is active (default threshold 25.0).  During warm-up the gate is
    permissive, so short test sequences still work.
    """
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
