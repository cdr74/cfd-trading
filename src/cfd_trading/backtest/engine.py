"""Backtest engine — walks bars chronologically, applies entry signals and exit rules.

Entry:  deterministic signal rules (signals.py)
Exit:   same evaluate_position() rule engine used by the live monitor
Prices: local SQLite ohlc_bars — no Capital.com API calls
"""

from dataclasses import dataclass, field

from cfd_trading.monitor.monitor import evaluate_position
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.signals import MomentumSignalState, MeanReversionSignalState

# Maps strategy name → state class.  A fresh instance is created per run.
_SIGNAL_STATES: dict[str, type] = {
    "momentum": MomentumSignalState,
    "mean_reversion": MeanReversionSignalState,
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
    trades: list[Trade] = field(default_factory=list)


def run_backtest(
    epic: str,
    strategy: str,
    bars: list[OHLCBar],
    strategy_config: dict,
    risk_config: dict,
) -> BacktestResult:
    """Walk bars chronologically; fire entry signals; manage exits with evaluate_position.

    strategy_config — loaded strategy YAML dict (e.g. momentum.yaml)
    risk_config     — global section of risk.yaml (unused by evaluate_position directly,
                      kept for future preflight integration)
    """
    if strategy not in _SIGNAL_STATES:
        raise ValueError(f"Unknown strategy '{strategy}'. Available: {list(_SIGNAL_STATES)}")

    signal_state = _SIGNAL_STATES[strategy]()
    stop_pct = strategy_config["risk"]["stop_loss"]["default_pct"] / 100
    rr_ratio = strategy_config["risk"]["take_profit"]["min_rr_ratio"]

    completed: list[Trade] = []
    open_trade: Trade | None = None
    current_stop: float | None = None

    for i, bar in enumerate(bars):
        # Always update signal state so EMAs stay current even while in a position
        signal = signal_state.update(bar)

        if open_trade is not None:
            # --- Manage open position ---
            price_data = {"bid": bar.close, "offer": bar.close}
            position_dict = {
                "direction": open_trade.direction,
                "stopLevel": current_stop,
                "profitLevel": open_trade.take_profit,
            }
            action, reason, new_stop = evaluate_position(
                position_dict, price_data, strategy_config
            )

            if action == "CLOSE":
                open_trade.exit_ts = bar.ts
                open_trade.exit_price = bar.close
                open_trade.exit_reason = reason
                open_trade.pnl_points = _pnl(open_trade.direction, open_trade.entry_price, bar.close)
                completed.append(open_trade)
                open_trade = None
                current_stop = None

            elif action == "ADJUST" and new_stop is not None:
                current_stop = new_stop

        else:
            # --- Check entry signal ---
            if signal is not None and i + 1 < len(bars):
                next_bar = bars[i + 1]
                entry_price = next_bar.open
                direction = "BUY" if signal == "LONG" else "SELL"
                stop_distance = entry_price * stop_pct

                if direction == "BUY":
                    stop_level = round(entry_price - stop_distance, 5)
                    profit_level = round(entry_price + stop_distance * rr_ratio, 5)
                else:
                    stop_level = round(entry_price + stop_distance, 5)
                    profit_level = round(entry_price - stop_distance * rr_ratio, 5)

                open_trade = Trade(
                    epic=epic,
                    strategy=strategy,
                    direction=direction,
                    entry_ts=next_bar.ts,
                    entry_price=entry_price,
                    stop_loss=stop_level,
                    take_profit=profit_level,
                )
                current_stop = stop_level

    # Close any position still open at end of data
    if open_trade is not None and bars:
        last = bars[-1]
        open_trade.exit_ts = last.ts
        open_trade.exit_price = last.close
        open_trade.exit_reason = "End of data"
        open_trade.pnl_points = _pnl(open_trade.direction, open_trade.entry_price, last.close)
        completed.append(open_trade)

    return _summarise(epic, strategy, completed, bars)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _pnl(direction: str, entry: float, exit_price: float) -> float:
    return round(exit_price - entry if direction == "BUY" else entry - exit_price, 6)


def _summarise(
    epic: str, strategy: str, trades: list[Trade], bars: list[OHLCBar]
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
        trades=trades,
    )
