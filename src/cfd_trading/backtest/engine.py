"""Backtest engine — walks bars chronologically, applies entry signals and exit rules.

Entry:  deterministic signal rules (signals.py)
Exit:   same evaluate_position() rule engine used by the live monitor
Prices: local SQLite ohlc_bars — no Capital.com API calls
"""

import datetime as _dt
from dataclasses import dataclass, field

from cfd_trading.backtest.sessions import session_open_utc
from cfd_trading.monitor.monitor import evaluate_position
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.strategy.signal_engine import (
    IntradayContinuationSignalState, MeanReversionSignalState,
    MomentumSignalState, ORBSignalState,
)

# Maps strategy name → state class.  A fresh instance is created per run.
_SIGNAL_STATES: dict[str, type] = {
    "momentum": MomentumSignalState,
    "mean_reversion": MeanReversionSignalState,
    "orb": ORBSignalState,
    "intraday_continuation": IntradayContinuationSignalState,
}


@dataclass
class Trade:
    epic: str
    strategy: str
    direction: str          # "BUY" or "SELL"
    entry_ts: int
    entry_price: float      # fill price — includes half-spread
    stop_loss: float
    take_profit: float
    exit_ts: int | None = None
    exit_price: float | None = None   # fill price — includes half-spread
    exit_reason: str | None = None
    pnl_points: float | None = None   # net of spread (derived from fills)
    risk_pts: float | None = None     # actual stop distance in price units; used for per-trade AvgR
    # Audit fields — used by trade-log persistence and Phase A audit slicing.
    # Allow gross-vs-net cost decomposition: pnl_points uses fills,
    # (exit_mid − entry_mid) gives the spread-free move.
    entry_mid: float | None = None    # next_bar.open before _entry_fill applies half-spread
    exit_mid: float | None = None     # bar.close before _exit_fill applies half-spread
    spread_at_entry: float | None = None  # spread_pts in effect when the entry was filled
    resolution: str | None = None     # bar resolution this trade was generated at, e.g. "M1", "M15"
    # Per-bar trail-stop trace: appended on every in-trade bar AFTER the entry
    # bar (the entry bar is skipped — matches live monitor semantics). Each
    # entry is (bar_ts, stop_level_after_this_bar). Used by parity tests to
    # assert engine ≡ live monitor across the full trail series, not just at
    # the exit. Also useful for forensic analysis of trail behaviour.
    stop_history: list[tuple[int, float]] = field(default_factory=list)


@dataclass
class BacktestResult:
    epic: str
    strategy: str
    total_trades: int
    winning_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    stop_out_rate: float
    signal_frequency: float     # trades per week
    net_pnl_pts: float = 0.0   # sum of all pnl_points; sign indicates profit/loss
    avg_r: float = 0.0          # expectancy per trade in R-multiples
    # Directional split — distinguishes genuine edge from trend-riding bias
    long_trades: int = 0
    long_win_rate: float = 0.0
    long_profit_factor: float = 0.0
    short_trades: int = 0
    short_win_rate: float = 0.0
    short_profit_factor: float = 0.0
    trades: list[Trade] = field(default_factory=list)


def run_backtest(
    epic: str,
    strategy: str,
    bars: list[OHLCBar],
    strategy_config: dict,
    risk_config: dict,
    signal_kwargs: dict | None = None,
    spread_pts: float = 0.0,
    session_close_utc: str = "21:00",
) -> BacktestResult:
    """Walk bars chronologically; fire entry signals; manage exits via the SHARED
    `evaluate_position` (same code path as the live monitor — no drift).

    strategy_config — loaded strategy YAML dict (e.g. momentum.yaml)
    risk_config     — global section of risk.yaml (unused here; kept for parity)
    signal_kwargs   — optional kwargs forwarded to the signal state constructor
    spread_pts      — typical spread in price units; adjusts entry/exit fills and
                      feeds the MeanReversion ATR viability gate
    session_close_utc — "HH:MM" UTC daily close. Each bar gets that day's
                      session_end; the per-strategy `time_exit` flattens
                      `close_minutes_before_session_end` before it. No position is
                      held overnight or over a weekend; no new entry is opened
                      inside the no-trade window. See SYSTEM_DESIGN §3.10 /
                      BACKTESTING §5.6. Set time_exit.enabled=false to disable.
    """
    if strategy not in _SIGNAL_STATES:
        raise ValueError(f"Unknown strategy '{strategy}'. Available: {list(_SIGNAL_STATES)}")

    kwargs = dict(signal_kwargs or {})
    if strategy == "mean_reversion":
        kwargs.setdefault("spread_pts", spread_pts)
    if strategy == "intraday_continuation":
        # Per-epic session-open UTC; pooled US500+DE40+UK100 each get their own.
        h, m = session_open_utc(epic)
        kwargs.setdefault("session_open_hour", h)
        kwargs.setdefault("session_open_minute", m)
    signal_state = _SIGNAL_STATES[strategy](**kwargs)

    stop_pct = strategy_config["risk"]["stop_loss"]["default_pct"] / 100
    rr_ratio = strategy_config["risk"]["take_profit"]["min_rr_ratio"]
    half = spread_pts / 2

    te_config = strategy_config.get("risk", {}).get("time_exit", {})
    te_enabled = bool(te_config.get("enabled"))
    close_min = te_config.get("close_minutes_before_session_end", 30)

    completed: list[Trade] = []
    open_trade: Trade | None = None
    current_stop: float | None = None
    entry_atr: float | None = None      # ATR(14) at entry — fixed-distance ATR trailing
    peak_price: float | None = None     # best favourable price since entry
    last_in_trade_bar: OHLCBar | None = None  # last bar the open position was managed on

    def _close(trade: Trade, ts: int, mid: float, reason: str) -> None:
        fill = _exit_fill(trade.direction, mid, half)
        trade.exit_ts = ts
        trade.exit_mid = mid
        trade.exit_price = fill
        trade.exit_reason = reason
        trade.pnl_points = _pnl(trade.direction, trade.entry_price, fill)
        completed.append(trade)
        signal_state.notify_exit()

    for i, bar in enumerate(bars):
        signal = signal_state.update(bar)

        # No overnight / weekend holds: if the UTC day rolled over with a
        # position still open (no bar fell in that day's close window — early
        # close, data gap, or weekend), flatten at the prior day's last bar.
        # Normal days are closed earlier by the time-exit rule (rule 5).
        if (open_trade is not None and te_enabled and last_in_trade_bar is not None
                and _utc_date(bar.ts) != _utc_date(last_in_trade_bar.ts)):
            _close(open_trade, last_in_trade_bar.ts, last_in_trade_bar.close,
                   "Session close (no bar at threshold)")
            open_trade = None
            current_stop = entry_atr = peak_price = last_in_trade_bar = None

        if open_trade is not None:
            # Skip evaluate_position on the entry bar — matches live monitor
            # semantics (a live cycle sees a position only on the cycle AFTER
            # its creation). Positional flag (last_in_trade_bar is None at
            # entry; set on this bar so the *next* bar evaluates normally).
            # Without this, the engine could fire ADJUST/CLOSE on bars the
            # live monitor would never see, breaking per-bar parity.
            if last_in_trade_bar is None:
                last_in_trade_bar = bar
                continue

            # Track best-favourable price for ATR trailing
            if open_trade.direction == "BUY":
                peak_price = bar.high if peak_price is None else max(peak_price, bar.high)
            else:
                peak_price = bar.low if peak_price is None else min(peak_price, bar.low)

            price_data = {"bid": bar.close - half, "offer": bar.close + half}
            position_dict = {
                "direction": open_trade.direction,
                "stopLevel": current_stop,
                "profitLevel": open_trade.take_profit,
            }
            session_end = _session_end_for(bar.ts, session_close_utc) if te_enabled else None
            action, reason, new_stop = evaluate_position(
                position_dict, price_data, strategy_config,
                session_end_time=session_end,
                now=_utc_dt(bar.ts),
                signal_state=signal_state,
                entry_atr=entry_atr,
                peak_price=peak_price,
                current_atr=getattr(signal_state, "atr", None),
            )
            if action == "CLOSE":
                _close(open_trade, bar.ts, bar.close, reason)
                open_trade = None
                current_stop = entry_atr = peak_price = last_in_trade_bar = None
            else:
                if action == "ADJUST" and new_stop is not None:
                    current_stop = new_stop
                last_in_trade_bar = bar
                open_trade.stop_history.append((bar.ts, current_stop))

        elif signal is not None and i + 1 < len(bars):
            # --- Entry ---
            next_bar = bars[i + 1]
            # No new entry inside the no-trade window before the daily close
            if te_enabled:
                ne = _session_end_for(next_bar.ts, session_close_utc)
                if _utc_dt(next_bar.ts) >= ne - _dt.timedelta(minutes=close_min):
                    continue
            direction = "BUY" if signal == "LONG" else "SELL"
            entry_mid = next_bar.open
            fill_price = _entry_fill(direction, entry_mid, half)

            # OR-width-based levels when the signal state provides them
            if hasattr(signal_state, "get_entry_levels"):
                stop_level, profit_level = signal_state.get_entry_levels(
                    direction, fill_price, rr_ratio
                )
            else:
                stop_distance = fill_price * stop_pct
                if direction == "BUY":
                    stop_level = round(fill_price - stop_distance, 5)
                    profit_level = round(fill_price + stop_distance * rr_ratio, 5)
                else:
                    stop_level = round(fill_price + stop_distance, 5)
                    profit_level = round(fill_price - stop_distance * rr_ratio, 5)

            open_trade = Trade(
                epic=epic, strategy=strategy, direction=direction,
                entry_ts=next_bar.ts, entry_price=fill_price,
                stop_loss=stop_level, take_profit=profit_level,
                risk_pts=abs(fill_price - stop_level),
                entry_mid=entry_mid, spread_at_entry=spread_pts,
            )
            current_stop = stop_level
            entry_atr = getattr(signal_state, "atr", None)
            peak_price = fill_price
            signal_state.notify_entry(direction)

    # Final partial day: close any position still open at end of data
    if open_trade is not None and bars:
        _close(open_trade, bars[-1].ts, bars[-1].close, "End of data")

    return _summarise(epic, strategy, completed, bars, stop_pct)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _entry_fill(direction: str, bar_open: float, half_spread: float) -> float:
    """Fill price at entry: BUY at ask (open + half), SELL at bid (open - half)."""
    return bar_open + half_spread if direction == "BUY" else bar_open - half_spread


def _exit_fill(direction: str, bar_close: float, half_spread: float) -> float:
    """Fill price at exit: closing BUY sells at bid (close - half), closing SELL buys at ask."""
    return bar_close - half_spread if direction == "BUY" else bar_close + half_spread


def _pnl(direction: str, entry: float, exit_price: float) -> float:
    return round(exit_price - entry if direction == "BUY" else entry - exit_price, 6)


def _utc_dt(ts: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)


def _utc_date(ts: int):
    return _utc_dt(ts).date()


def _session_end_for(ts: int, close_hhmm: str) -> _dt.datetime:
    """That bar's UTC-day session end = the date of `ts` at close_hhmm UTC."""
    h, m = (int(x) for x in close_hhmm.split(":"))
    return _utc_dt(ts).replace(hour=h, minute=m, second=0, microsecond=0)


def _dir_profit_factor(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    gross_profit = sum(t.pnl_points for t in trades if (t.pnl_points or 0) > 0)
    gross_loss   = abs(sum(t.pnl_points for t in trades if (t.pnl_points or 0) <= 0))
    return round(gross_profit / gross_loss, 3) if gross_loss > 0 else float("inf")


def _dir_win_rate(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    return round(sum(1 for t in trades if (t.pnl_points or 0) > 0) / len(trades), 3)


def _summarise(
    epic: str, strategy: str, trades: list[Trade], bars: list[OHLCBar], stop_pct: float = 0.0
) -> BacktestResult:
    n = len(trades)
    if n == 0:
        return BacktestResult(
            epic=epic, strategy=strategy,
            total_trades=0, winning_trades=0,
            win_rate=0.0, profit_factor=0.0,
            max_drawdown_pct=0.0, stop_out_rate=0.0,
            signal_frequency=0.0, trades=[],
        )

    winning = [t for t in trades if (t.pnl_points or 0) > 0]
    gross_profit = sum(t.pnl_points for t in winning)
    gross_loss = abs(sum(t.pnl_points for t in trades if (t.pnl_points or 0) <= 0))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else float("inf")

    stopped = [t for t in trades if "Hard stop" in (t.exit_reason or "")]
    avg_entry = sum(t.entry_price for t in trades) / n

    # Peak-to-trough drawdown on cumulative P&L (in points)
    cum = 0.0
    peak = 0.0
    max_dd_points = 0.0
    for t in trades:
        cum += t.pnl_points or 0
        peak = max(peak, cum)
        max_dd_points = max(max_dd_points, peak - cum)
    max_drawdown_pct = round(max_dd_points / avg_entry * 100, 3) if avg_entry else 0.0

    # Signal frequency: trades per week over the bar span
    if len(bars) >= 2:
        span_seconds = bars[-1].ts - bars[0].ts
        span_weeks = span_seconds / (7 * 24 * 3600)
        signal_frequency = round(n / span_weeks, 2) if span_weeks > 0 else 0.0
    else:
        signal_frequency = 0.0

    net_pnl_pts = round(sum(t.pnl_points or 0 for t in trades), 4)

    # AvgR: average P&L per trade in R-multiples.
    # Use per-trade risk_pts when available (e.g. OR-width-based stops); fall back to config %.
    if all(t.risk_pts is not None and t.risk_pts > 0 for t in trades):
        avg_r = round(sum((t.pnl_points or 0) / t.risk_pts for t in trades) / n, 4)
    else:
        r_per_trade = avg_entry * stop_pct
        avg_r = round(net_pnl_pts / (n * r_per_trade), 4) if r_per_trade > 0 else 0.0

    long_t  = [t for t in trades if t.direction == "BUY"]
    short_t = [t for t in trades if t.direction == "SELL"]
    long_pf  = _dir_profit_factor(long_t)
    short_pf = _dir_profit_factor(short_t)

    return BacktestResult(
        epic=epic,
        strategy=strategy,
        total_trades=n,
        winning_trades=len(winning),
        win_rate=round(len(winning) / n, 3),
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
        stop_out_rate=round(len(stopped) / n, 3),
        signal_frequency=signal_frequency,
        net_pnl_pts=net_pnl_pts,
        avg_r=avg_r,
        long_trades=len(long_t),
        long_win_rate=_dir_win_rate(long_t),
        long_profit_factor=long_pf,
        short_trades=len(short_t),
        short_win_rate=_dir_win_rate(short_t),
        short_profit_factor=short_pf,
        trades=trades,
    )
