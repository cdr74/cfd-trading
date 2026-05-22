"""Shared deterministic signal engine — used by BOTH the live monitor and the backtest.

Single source of truth for entry signals and the signal-exit rule (SYSTEM_DESIGN
§3.7 rule 4), so the live and backtest exit paths cannot drift. Each class
maintains running indicator state and produces LONG/SHORT/None on each new bar in
O(1) time. Create a fresh instance per instrument/strategy run; the live monitor
back-fills a warm-up window then feeds one bar per cycle.

  MomentumSignalState    — incremental EMA9/EMA21 crossover + gap filter + slope
                           + ADX(14) regime gate (suppressed when ADX < threshold)
                           + M30 directional bias gate (blocks entries against 30-bar trend)
                           + check_exit(): EMA cross-back against the open position
  MeanReversionSignalState — rolling 20-bar z-score threshold
                             + ADX(14) regime gate (suppressed when ADX >= threshold)
                             + ATR(14) ≥ 4× spread gate (skips low-vol entries)
                             + check_exit(): z-score returned to midline
  ORBSignalState           — Opening Range Breakout on aggregated bars (designed for M15)
                             First `or_bars` bars (default 2 = 30 min) define OR high/low
                             Break above OR high → LONG; break below OR low → SHORT
                             Stop at OR low (LONG) / OR high (SHORT) via get_entry_levels()
                             One signal per session; resets on each new session open
                             check_exit() → None (one-shot breakout; no reversal)
  IntradayContinuationSignalState — Volatility-band intraday continuation
                             (Zarattini-inspired, D3/BR3 — SYSTEM_DESIGN §3.7.1,
                             CFD_STRATEGY_CATALOG §14). On M15:
                             entry = first session bar whose CLOSE breaches
                             `open ± k_entry · ATR₁₄(closed bars)`, direction =
                             breach side; at most one entry/session.
                             get_entry_levels() returns initial Chandelier-aligned
                             hard stop (entry ∓ k_trail·ATR(entry)) and no TP.
                             check_exit() → None (hold to close via the dynamic
                             Chandelier trail + time-exit; no signal-reversal).

The `chandelier_stop()` pure function below is the canonical formula shared by
the backtest engine and the live monitor for the dynamic Chandelier trail mode
(`trailing_stop.mode: dynamic_chandelier` in the strategy YAML). Both paths call
this single function — parity by construction.

Shared interface (all classes): `update(bar) -> "LONG"|"SHORT"|None`,
`check_exit() -> reason|None`, `notify_entry(direction)`, `notify_exit()`.
`direction` is the broker side, "BUY" or "SELL". The functional wrappers
(momentum_signal, mean_reversion_signal) are unit-test conveniences.
"""

from collections import deque

from cfd_trading.storage.repository import OHLCBar

# Minimum fractional |EMA9 - EMA21| / EMA21 required to *confirm* a pending
# crossover.  It is checked on the post-cross confirmation bars, NOT at the cross
# itself (there the two EMAs are ≈coincident and the gap is structurally ~0 — the
# old "gap test at the cross bar" rejected ~99% of crossovers; see momentum §5.2).
# 0.05% is the research-validated floor for index CFDs: ≈4 pts at 8,000 = 4× a
# typical 1-pt spread (signal-to-cost ratio 4×, positive-expectancy territory).
# RESEARCH.md rejects the older 0.02% as below the noise/spread floor (ratio ~1.6×).
_MIN_EMA_GAP_PCT = 0.0005   # 0.05% — see docs/RESEARCH.md "Minimum EMA Gap Filter"


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

    M30 directional bias gate: each M1 bar is added to a rolling 30-bar buffer.
    When the buffer is full (≥30 bars), OLS slope of the 30 closes defines the
    M30 bias.  LONG entries are blocked when M30 is bearish; SHORT entries are
    blocked when M30 is bullish.  Permissive while warming up (<30 bars).
    Disable via m30_gate=False (e.g. signal_kwargs={"m30_gate": False}).
    """

    _ALPHA9  = 2.0 / (9  + 1)
    _ALPHA21 = 2.0 / (21 + 1)
    _MIN_BARS     = 22   # EMA21 seeds at bar 21; crossover needs one prior bar
    _SLOPE_WINDOW = 22   # cap slope window to same length
    _M30_WINDOW   = 30   # bars in the M30 directional bias buffer

    def __init__(
        self,
        min_ema_gap_pct: float = _MIN_EMA_GAP_PCT,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        m30_gate: bool = True,
        confirm_bars: int = 6,
    ) -> None:
        self._min_ema_gap_pct = min_ema_gap_pct
        self._adx_threshold   = adx_threshold
        self._m30_gate        = m30_gate
        self._confirm_bars    = confirm_bars
        # Pending crossover awaiting confirmation: {"dir": "LONG"|"SHORT",
        # "age": int}. A crossover only opens this; it fires on a later bar
        # (age 1..confirm_bars) once gap/ADX/slope/M30 confirm — the EMAs are
        # ≈coincident *at* the cross, so confirmation must come after it.
        self._pending: dict | None = None
        self._n: int = 0
        self._ema9:  float | None = None
        self._ema21: float | None = None
        self._prev_ema9:  float | None = None
        self._prev_ema21: float | None = None
        self._sum9  = 0.0
        self._sum21 = 0.0
        self._slope_buf: deque[float]   = deque(maxlen=self._SLOPE_WINDOW)
        self._m30_buf:   deque[OHLCBar] = deque(maxlen=self._M30_WINDOW)
        self._adx_state = _ADXState(adx_period)
        self._position_dir: str | None = None   # "BUY"/"SELL" while a trade is open
        self._last_xover:   str | None = None   # raw EMA crossover on the latest bar

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        close = bar.close
        self._n += 1
        self._slope_buf.append(close)
        self._m30_buf.append(bar)

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
            self._last_xover = None
            return None

        # Raw EMA crossover for THIS bar — computed before the entry gates so the
        # signal-exit rule (check_exit) can see a cross-back even when the entry
        # filters (ADX / gap / M30) would suppress a fresh entry signal.
        crossed_long  = self._prev_ema9 <= self._prev_ema21 and self._ema9 > self._ema21
        crossed_short = self._prev_ema9 >= self._prev_ema21 and self._ema9 < self._ema21
        self._last_xover = "LONG" if crossed_long else "SHORT" if crossed_short else None

        # --- Entry: pending crossover + confirmation window ---
        # A fresh crossover opens (or replaces) a pending signal but does NOT
        # fire on the cross bar — the EMAs are ≈coincident there, so the gap
        # filter could never pass. It fires on one of the next `confirm_bars`
        # bars, once gap / ADX / slope / M30 all confirm at that later bar.
        if crossed_long or crossed_short:
            self._pending = {"dir": "LONG" if crossed_long else "SHORT", "age": 0}
            return None

        if self._pending is None:
            return None

        self._pending["age"] += 1
        if self._pending["age"] > self._confirm_bars:
            self._pending = None          # window elapsed unconfirmed
            return None

        # Confirmation gates — evaluated at THIS post-cross bar. A failure
        # keeps the pending alive; it may still confirm on a later bar in
        # the window. Only fire / expiry / a new crossover clears it.
        if adx is not None and adx < self._adx_threshold:
            return None
        gap_pct = abs(self._ema9 - self._ema21) / self._ema21
        if gap_pct < self._min_ema_gap_pct:
            return None
        slope = _trend_slope(list(self._slope_buf))
        pdir = self._pending["dir"]
        m30_bullish: bool | None = None
        if self._m30_gate and len(self._m30_buf) >= self._M30_WINDOW:
            m30_bullish = _trend_slope([b.close for b in self._m30_buf]) > 0

        if pdir == "LONG" and slope > 0:
            if m30_bullish is not None and not m30_bullish:
                return None
            self._pending = None
            return "LONG"
        if pdir == "SHORT" and slope < 0:
            if m30_bullish is not None and m30_bullish:
                return None
            self._pending = None
            return "SHORT"
        return None

    def check_exit(self) -> str | None:
        """Signal-exit: EMA crosses back through, against the open position.

        SYSTEM_DESIGN §3.7 rule 4. Hard stop / trailing / TP take priority —
        they are evaluated before this in the shared exit path.
        """
        if self._position_dir == "BUY" and self._last_xover == "SHORT":
            return "EMA cross-back"
        if self._position_dir == "SELL" and self._last_xover == "LONG":
            return "EMA cross-back"
        return None

    def notify_entry(self, direction: str) -> None:
        """Record the open position's broker side ('BUY' or 'SELL')."""
        self._position_dir = direction

    def notify_exit(self) -> None:
        self._position_dir = None

    @property
    def atr(self) -> float | None:
        """Wilder ATR(14), or None while warming up. Source for ATR trailing."""
        return self._adx_state.atr


class MeanReversionSignalState:
    """O(1)-per-bar z-score mean reversion signal.

    Maintains a rolling 20-bar deque.

    ADX regime gate: signal is suppressed when ADX >= adx_threshold (trending
    market — mean reversion logic breaks down).  During ADX warm-up the gate
    is permissive.

    ATR viability gate: signal is suppressed when ATR(14) < 4 × spread_pts
    (volatility too low relative to fixed spread cost to produce positive
    expectancy).  Disabled when spread_pts=0.0 (default).

    check_exit(): z-score returned to midline — fires when
      |z| ≤ zscore_exit_threshold (default 0.5). This is the deterministic
      version of the catalog's "exit when z returns to 0" target exit
      (SYSTEM_DESIGN §3.7 rule 4). The former hold-cap was removed 2026-05-15
      (a backtest-only hack standing in for the missing time-exit).
    """

    _WINDOW = 20

    def __init__(
        self,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        zscore_exit_threshold: float = 0.5,
        spread_pts: float = 0.0,
    ) -> None:
        self._adx_threshold         = adx_threshold
        self._zscore_exit_threshold = zscore_exit_threshold
        self._spread_pts            = spread_pts
        self._buf: deque[float] = deque(maxlen=self._WINDOW)
        self._adx_state = _ADXState(adx_period)
        self._last_z: float | None = None
        self._position_dir: str | None = None  # "BUY"/"SELL" while a trade is open

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        self._buf.append(bar.close)
        adx = self._adx_state.update(bar)
        atr = self._adx_state.atr

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
        """Signal-exit: z-score returned to the midline (reversion complete).

        SYSTEM_DESIGN §3.7 rule 4. Hard stop / TP take priority — they are
        evaluated before this in the shared exit path.
        """
        if self._last_z is not None and abs(self._last_z) <= self._zscore_exit_threshold:
            return "Z-score midline"
        return None

    def notify_entry(self, direction: str) -> None:
        """Record the open position's broker side ('BUY' or 'SELL')."""
        self._position_dir = direction

    def notify_exit(self) -> None:
        self._position_dir = None

    @property
    def atr(self) -> float | None:
        """Wilder ATR(14), or None while warming up."""
        return self._adx_state.atr


class ORBSignalState:
    """Opening Range Breakout signal — designed for M15 aggregated bars.

    The first `or_bars` bars of each session define the Opening Range (default: 2
    bars = 30 min).  Break above OR high → LONG; break below OR low → SHORT.
    At most one signal per session; resets automatically at each new session open.

    Stop and TP levels are OR-width-based (call get_entry_levels after a signal).
    The engine calls get_entry_levels in preference to the config default_pct.

    session_open_hour / session_open_minute (UTC): identify the first OR bar by
    checking bar.ts % 86400 == session_open_hour * 3600 + session_open_minute * 60.
    DST is not accounted for — see backtest/sessions.py for the caveat.
    """

    def __init__(
        self,
        session_open_hour: int = 8,
        session_open_minute: int = 0,
        or_bars: int = 2,
    ) -> None:
        self._session_open_seconds = session_open_hour * 3600 + session_open_minute * 60
        self._or_bars = or_bars
        self._or_high: float | None = None
        self._or_low:  float | None = None
        self._session_day: int | None = None
        self._traded: bool = False
        self._or_complete: bool = False
        self._or_collected: int = 0

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        day = bar.ts // 86400
        sod = bar.ts % 86400

        # Session open bar — begin OR collection
        if sod == self._session_open_seconds:
            self._or_high     = bar.high
            self._or_low      = bar.low
            self._session_day = day
            self._traded      = False
            self._or_collected = 1
            self._or_complete  = self._or_bars <= 1
            return None

        # Extend OR during collection phase
        if (not self._or_complete
                and self._or_high is not None
                and self._session_day == day):
            self._or_high = max(self._or_high, bar.high)
            self._or_low  = min(self._or_low,  bar.low)
            self._or_collected += 1
            if self._or_collected >= self._or_bars:
                self._or_complete = True
            return None

        # Breakout detection
        if (self._or_complete
                and not self._traded
                and self._session_day == day):
            if bar.high > self._or_high:
                self._traded = True
                return "LONG"
            if bar.low < self._or_low:
                self._traded = True
                return "SHORT"

        return None

    def get_entry_levels(
        self, direction: str, fill_price: float, rr_ratio: float
    ) -> tuple[float, float]:
        """Return (stop_level, profit_level) using OR boundaries as natural risk reference.

        Stop sits at the opposite OR boundary (OR low for LONG, OR high for SHORT).
        TP = entry ± OR_width × rr_ratio.
        """
        or_width = self._or_high - self._or_low
        if direction == "BUY":
            return round(self._or_low, 5), round(fill_price + or_width * rr_ratio, 5)
        return round(self._or_high, 5), round(fill_price - or_width * rr_ratio, 5)

    def check_exit(self) -> str | None:
        return None  # one-shot breakout — no signal reversal to bail on

    def notify_entry(self, direction: str) -> None:
        pass

    def notify_exit(self) -> None:
        pass

    @property
    def atr(self) -> float | None:
        return None  # ORB has no ATR trailing (OR-width stop + fixed TP)


class IntradayContinuationSignalState:
    """Volatility-band intraday continuation (D3/BR3) — designed for M15.

    Entry rule (CFD_STRATEGY_CATALOG §14):
      1. Anchor to the session open price on the session-open bar.
      2. Volatility band = open ± k_entry · ATR₁₄(closed bars), using the
         engine's existing Wilder ATR primitive — one shared vol primitive,
         fidelity-clean.
      3. First session bar whose CLOSE breaches the band → entry in the breach
         direction. At most one entry per session; resets at the next
         session-open bar.

    Exit is governed entirely by the shared exit path (SYSTEM_DESIGN §3.7):
      hard stop → dynamic Chandelier trail → time-exit. No take-profit, no
      signal-reversal exit ("hold to close" is the literature-faithful design).

    Companion: the dynamic Chandelier trail in evaluate_position
    (`trailing_stop.mode: dynamic_chandelier`) calls `chandelier_stop()` below
    every bar — peak-anchored, ATR-recomputed, may loosen on vol expansion.

    session_open_hour / session_open_minute (UTC): identify the first session
    bar by checking bar.ts % 86400 == session_open_hour * 3600 + ... * 60.
    DST is not accounted for — see backtest/sessions.py for the caveat.
    """

    def __init__(
        self,
        session_open_hour: int = 8,
        session_open_minute: int = 0,
        k_entry: float = 1.0,
        k_trail: float = 1.5,
        adx_period: int = 14,
    ) -> None:
        self._session_open_seconds = session_open_hour * 3600 + session_open_minute * 60
        self._k_entry = k_entry
        self._k_trail = k_trail
        self._adx_state = _ADXState(adx_period)
        self._session_day: int | None = None
        self._session_open_price: float | None = None
        self._traded: bool = False

    def update(self, bar: OHLCBar) -> str | None:
        """Consume one bar; return 'LONG', 'SHORT', or None."""
        # ATR primitive advances on every closed bar — never reset per session.
        self._adx_state.update(bar)

        day = bar.ts // 86400
        sod = bar.ts % 86400

        # Session-open bar — anchor + clear traded flag
        if sod == self._session_open_seconds:
            self._session_open_price = bar.open
            self._session_day = day
            self._traded = False

        if (self._session_day != day or self._session_open_price is None
                or self._traded):
            return None

        atr = self._adx_state.atr
        if atr is None:
            return None

        band = self._k_entry * atr
        if bar.close > self._session_open_price + band:
            self._traded = True
            return "LONG"
        if bar.close < self._session_open_price - band:
            self._traded = True
            return "SHORT"
        return None

    def get_entry_levels(
        self, direction: str, fill_price: float, rr_ratio: float
    ) -> tuple[float, float | None]:
        """Initial Chandelier-aligned hard stop; no take-profit.

        Stop sits at the same distance the dynamic trail will manage from —
        entry ∓ k_trail · ATR(entry). The trail then maintains it from the
        running extreme. `rr_ratio` is ignored (no TP — literature-faithful).
        """
        atr = self._adx_state.atr
        if atr is None or atr == 0.0:
            # Defensive fallback — signal can't fire with ATR unset, but be
            # explicit. A 0.5% backstop is well inside the YAML max_pct.
            backstop = fill_price * 0.005
            distance = backstop
        else:
            distance = self._k_trail * atr
        if direction == "BUY":
            return round(fill_price - distance, 5), None
        return round(fill_price + distance, 5), None

    def check_exit(self) -> str | None:
        return None  # hold to close — trail + time-exit govern the exit

    def notify_entry(self, direction: str) -> None:
        pass

    def notify_exit(self) -> None:
        pass

    @property
    def atr(self) -> float | None:
        """Wilder ATR(14) over closed bars — used by both entry band and trail."""
        return self._adx_state.atr


# ---------------------------------------------------------------------------
# Chandelier trail — canonical pure function shared by engine + monitor
# ---------------------------------------------------------------------------

def chandelier_stop(
    direction: str, extreme: float, current_atr: float, k_trail: float
) -> float:
    """Dynamic Chandelier stop level — peak-anchored, ATR-recomputed.

      LONG  stop = extreme − k_trail · current_atr   (extreme = max_high_since_entry)
      SHORT stop = extreme + k_trail · current_atr   (extreme = min_low_since_entry)

    "Dynamic" = `current_atr` is recomputed from closed bars every evaluation,
    so the stop level may move away from price when volatility expands. This is
    the literature-faithful (Zarattini/BR4) trail, distinct from the
    ratchet-only fixed-at-entry ATR trail used by momentum (mode: fixed_atr).

    Backtest engine and live monitor BOTH call this function — one source of
    truth for the dynamic_chandelier trail mode. See SYSTEM_DESIGN §3.7.1.
    """
    distance = k_trail * current_atr
    if direction == "BUY":
        return round(extreme - distance, 5)
    return round(extreme + distance, 5)


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
