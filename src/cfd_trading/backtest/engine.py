"""Backtest engine — walks bars chronologically, applies entry signals and exit rules.

Entry:  deterministic signal rules (signals.py)
Exit:   same evaluate_position() rule engine used by the live monitor
Prices: local SQLite ohlc_bars — no Capital.com API calls
"""

import copy
from dataclasses import dataclass, field

from cfd_trading.monitor.monitor import evaluate_position
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.signals import (
    MomentumSignalState, MeanReversionSignalState, ORBSignalState, _ADXState,
)

# Maps strategy name → state class.  A fresh instance is created per run.
_SIGNAL_STATES: dict[str, type] = {
    "momentum": MomentumSignalState,
    "mean_reversion": MeanReversionSignalState,
    "orb": ORBSignalState,
}


@dataclass
class Trade:
    epic: str
    strategy: str
    direction: str          # "BUY" or "SELL"
    entry_ts: int
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_ts: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl_points: float | None = None
    risk_pts: float | None = None   # actual stop distance in price units; used for per-trade AvgR


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
    trades: list[Trade] = field(default_factory=list)


def run_backtest(
    epic: str,
    strategy: str,
    bars: list[OHLCBar],
    strategy_config: dict,
    risk_config: dict,
    signal_kwargs: dict | None = None,
    spread_pts: float = 0.0,
) -> BacktestResult:
    """Walk bars chronologically; fire entry signals; manage exits with evaluate_position.

    strategy_config — loaded strategy YAML dict (e.g. momentum.yaml)
    risk_config     — global section of risk.yaml (unused by evaluate_position directly,
                      kept for future preflight integration)
    signal_kwargs   — optional keyword args forwarded to the signal state constructor
                      (e.g. {"min_ema_gap_pct": 0.002} for tuning the momentum filter)
    spread_pts      — typical spread in price units for this instrument; used to
                      adjust entry/exit fill prices (buy at ask = mid + spread/2,
                      sell at bid = mid - spread/2) and passed to MeanReversionSignalState
                      for the ATR viability gate
    """
    if strategy not in _SIGNAL_STATES:
        raise ValueError(f"Unknown strategy '{strategy}'. Available: {list(_SIGNAL_STATES)}")

    kwargs = dict(signal_kwargs or {})
    if strategy == "mean_reversion":
        kwargs.setdefault("spread_pts", spread_pts)
    signal_state = _SIGNAL_STATES[strategy](**kwargs)

    stop_pct = strategy_config["risk"]["stop_loss"]["default_pct"] / 100
    rr_ratio = strategy_config["risk"]["take_profit"]["min_rr_ratio"]
    half = spread_pts / 2

    # ATR-trailing setup: when atr_multiplier is configured, trail at N×ATR(14) from
    # bar high/low peak.  evaluate_position's own trailing stop is disabled in this mode
    # to avoid double-ratcheting; hard-stop and TP checks are preserved.
    ts_config = strategy_config.get("risk", {}).get("trailing_stop", {})
    atr_multiplier: float | None = ts_config.get("atr_multiplier")
    _atr_tracker = _ADXState(period=14) if atr_multiplier else None
    _peak_price: float | None = None

    if atr_multiplier:
        eval_config = copy.deepcopy(strategy_config)
        eval_config["risk"]["trailing_stop"]["enabled"] = False
    else:
        eval_config = strategy_config

    completed: list[Trade] = []
    open_trade: Trade | None = None
    current_stop: float | None = None

    for i, bar in enumerate(bars):
        # Always update signal state and ATR tracker so indicators stay current
        signal = signal_state.update(bar)
        if _atr_tracker:
            _atr_tracker.update(bar)

        if open_trade is not None:
            # Track bar high/low peak for ATR trailing
            if _atr_tracker:
                if open_trade.direction == "BUY":
                    _peak_price = max(_peak_price if _peak_price is not None else bar.high, bar.high)
                else:
                    _peak_price = min(_peak_price if _peak_price is not None else bar.low, bar.low)

            # --- Manage open position ---
            price_data = {
                "bid": bar.close - half,
                "offer": bar.close + half,
            }
            position_dict = {
                "direction": open_trade.direction,
                "stopLevel": current_stop,
                "profitLevel": open_trade.take_profit,
            }
            action, reason, new_stop = evaluate_position(
                position_dict, price_data, eval_config
            )

            if action == "CLOSE":
                exit_fill = _exit_fill(open_trade.direction, bar.close, half)
                open_trade.exit_ts = bar.ts
                open_trade.exit_price = exit_fill
                open_trade.exit_reason = reason
                open_trade.pnl_points = _pnl(open_trade.direction, open_trade.entry_price, exit_fill)
                completed.append(open_trade)
                open_trade = None
                current_stop = None
                _peak_price = None
                signal_state.notify_exit()

            elif action == "ADJUST" and new_stop is not None:
                current_stop = new_stop

            # ATR-trailing ratchet — applied after evaluate_position; only ratchets in
            # the profitable direction, never widens the stop
            if open_trade is not None and _atr_tracker and _peak_price is not None:
                current_atr = _atr_tracker.atr
                if current_atr:
                    if open_trade.direction == "BUY":
                        atr_candidate = round(_peak_price - atr_multiplier * current_atr, 5)
                        if atr_candidate > current_stop:
                            current_stop = atr_candidate
                    else:
                        atr_candidate = round(_peak_price + atr_multiplier * current_atr, 5)
                        if atr_candidate < current_stop:
                            current_stop = atr_candidate

            # Signal-based exit: check after evaluate_position so hard stop takes priority
            if open_trade is not None:
                exit_reason = signal_state.check_exit()
                if exit_reason:
                    exit_fill = _exit_fill(open_trade.direction, bar.close, half)
                    open_trade.exit_ts = bar.ts
                    open_trade.exit_price = exit_fill
                    open_trade.exit_reason = exit_reason
                    open_trade.pnl_points = _pnl(open_trade.direction, open_trade.entry_price, exit_fill)
                    completed.append(open_trade)
                    open_trade = None
                    current_stop = None
                    _peak_price = None
                    signal_state.notify_exit()

        else:
            # --- Check entry signal ---
            if signal is not None and i + 1 < len(bars):
                next_bar = bars[i + 1]
                direction = "BUY" if signal == "LONG" else "SELL"
                fill_price = _entry_fill(direction, next_bar.open, half)

                # Use OR-width-based levels when the signal state provides them
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

                risk_pts = abs(fill_price - stop_level)
                open_trade = Trade(
                    epic=epic,
                    strategy=strategy,
                    direction=direction,
                    entry_ts=next_bar.ts,
                    entry_price=fill_price,
                    stop_loss=stop_level,
                    take_profit=profit_level,
                    risk_pts=risk_pts,
                )
                current_stop = stop_level
                signal_state.notify_entry()

    # Close any position still open at end of data
    if open_trade is not None and bars:
        last = bars[-1]
        exit_fill = _exit_fill(open_trade.direction, last.close, half)
        open_trade.exit_ts = last.ts
        open_trade.exit_price = exit_fill
        open_trade.exit_reason = "End of data"
        open_trade.pnl_points = _pnl(open_trade.direction, open_trade.entry_price, exit_fill)
        completed.append(open_trade)
        _peak_price = None
        signal_state.notify_exit()

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
        trades=trades,
    )
