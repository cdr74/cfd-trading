"""Unit tests for backtest/engine.py — no I/O, synthetic bar sequences."""

import pytest
from cfd_trading.storage.repository import OHLCBar
from cfd_trading.backtest.engine import run_backtest, Trade, BacktestResult


# ---------------------------------------------------------------------------
# Fixtures — strategy configs matching the real YAML files
# ---------------------------------------------------------------------------

@pytest.fixture
def momentum_cfg():
    return {
        "risk": {
            "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
            "trailing_stop": {
                "enabled": True,
                "min_distance_pct": 0.5,
                "max_distance_pct": 3.0,
            },
            "take_profit": {"dynamic": True, "min_rr_ratio": 1.5},
            "time_exit": {"enabled": False},
        }
    }


@pytest.fixture
def mean_rev_cfg():
    return {
        "risk": {
            "stop_loss": {"type": "HARD", "default_pct": 1.5, "max_pct": 3.0},
            "trailing_stop": {"enabled": False},
            "take_profit": {"dynamic": False, "min_rr_ratio": 2.0},
            "time_exit": {"enabled": False},
        }
    }


RISK_CFG = {"global": {"max_loss_pct_per_trade": 5.0, "margin_floor_pct": 20.0}}


# ---------------------------------------------------------------------------
# Bar-building helpers
# ---------------------------------------------------------------------------

def _bar(ts: int, price: float, open_price: float | None = None) -> OHLCBar:
    o = open_price if open_price is not None else price
    return OHLCBar(epic="EURUSD", resolution="M1", ts=ts,
                   open=o, high=price, low=price, close=price, volume=100)


def _bars(prices: list[float]) -> list[OHLCBar]:
    """All OHLC fields equal to close price. ts = index * 60."""
    return [_bar(i * 60, p) for i, p in enumerate(prices)]


def _momentum_long_entry_bars(entry_open: float = 1.10) -> list[OHLCBar]:
    """21 flat + spike (crossover) + 1 confirm bar → signal fires; the next
    bar is the entry bar. Momentum entry is a pending crossover that confirms
    on a later bar, so a confirm bar must sit between the cross and entry."""
    pre = _bars([1.0] * 21 + [1.10, 1.10])          # idx21 cross, idx22 confirm→signal
    return pre + [_bar(len(pre) * 60, entry_open, entry_open)]   # idx23 entry bar


# ---------------------------------------------------------------------------
# Entry signal tests
# ---------------------------------------------------------------------------

class TestEntry:

    def test_momentum_long_opens_trade(self, momentum_cfg):
        bars = _momentum_long_entry_bars()
        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades >= 1
        assert result.trades[0].direction == "BUY"
        assert result.trades[0].entry_price == 1.10

    def test_momentum_short_opens_trade(self, momentum_cfg):
        # 21 flat bars + spike down → SHORT signal
        bars = _bars([1.0] * 21 + [0.90, 0.90, 0.90])
        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades >= 1
        assert result.trades[0].direction == "SELL"

    def test_mean_reversion_long_opens_trade(self, mean_rev_cfg):
        # 19 bars at 1.0, then spike down → LONG mean reversion
        bars = _bars([1.0] * 19 + [0.5, 0.5])
        result = run_backtest("EURUSD", "mean_reversion", bars, mean_rev_cfg, RISK_CFG)
        assert result.total_trades >= 1
        assert result.trades[0].direction == "BUY"

    def test_mean_reversion_short_opens_trade(self, mean_rev_cfg):
        # 19 bars at 1.0, then spike up → SHORT mean reversion
        bars = _bars([1.0] * 19 + [1.5, 1.5])
        result = run_backtest("EURUSD", "mean_reversion", bars, mean_rev_cfg, RISK_CFG)
        assert result.total_trades >= 1
        assert result.trades[0].direction == "SELL"

    def test_no_signal_produces_no_trades(self, momentum_cfg):
        # Flat bars — no crossover, no signal
        result = run_backtest("EURUSD", "momentum", _bars([1.0] * 30), momentum_cfg, RISK_CFG)
        assert result.total_trades == 0

    def test_stop_and_take_profit_set_correctly(self, momentum_cfg):
        # Entry at 1.10, default_pct=2.0, rr=1.5
        # stop = 1.10 * 0.98 = 1.078
        # take_profit = 1.10 + (1.10 * 0.02 * 1.5) = 1.10 + 0.033 = 1.133
        bars = _momentum_long_entry_bars(entry_open=1.10)
        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        trade = result.trades[0]
        assert trade.stop_loss == pytest.approx(1.10 * 0.98, rel=1e-4)
        assert trade.take_profit == pytest.approx(1.10 + 1.10 * 0.02 * 1.5, rel=1e-4)

    def test_unknown_strategy_raises(self, momentum_cfg):
        with pytest.raises(ValueError, match="Unknown strategy"):
            run_backtest("EURUSD", "ghost", _bars([1.0] * 30), momentum_cfg, RISK_CFG)


# ---------------------------------------------------------------------------
# Exit rule tests
# ---------------------------------------------------------------------------

class TestExits:

    def test_hard_stop_closes_trade(self, momentum_cfg):
        # Entry at 1.10, stop ≈ 1.078; bar after entry crashes to 0.50
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])     # signal fires here
        entry_bar = _bar(22 * 60, 1.10)              # entry at 1.10 open
        crash_bar = _bar(23 * 60, 0.50)              # price below stop
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades == 1
        trade = result.trades[0]
        assert "Hard stop" in trade.exit_reason
        assert trade.pnl_points < 0

    def test_take_profit_closes_trade(self):
        # Trailing stop must be disabled so the take-profit check is reached.
        # With trailing stop enabled, evaluate_position returns ADJUST (ratchet)
        # before it reaches the take-profit check, masking the TP exit.
        cfg_no_ts = {
            "risk": {
                "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
                "trailing_stop": {"enabled": False},
                "take_profit": {"dynamic": True, "min_rr_ratio": 1.5},
                "time_exit": {"enabled": False},
            }
        }
        # Entry at 1.10; TP = 1.10 + 1.10*0.02*1.5 = 1.133; 2.0 > 1.133 → CLOSE
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        tp_bar = _bar(23 * 60, 2.0)
        bars = signal_bars + [entry_bar, tp_bar]

        result = run_backtest("EURUSD", "momentum", bars, cfg_no_ts, RISK_CFG)
        assert result.total_trades == 1
        trade = result.trades[0]
        assert "Take profit" in trade.exit_reason
        assert trade.pnl_points > 0

    def test_trailing_stop_ratchets_upward(self, momentum_cfg):
        # Enter BUY at 1.10. Price rises to 2.0 → ratchet should ADJUST stop upward.
        # Then price crashes → closed at the ratcheted stop level.
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        # min_distance_pct=0.5; candidate = 2.0 * 0.995 = 1.99 > initial stop 1.078 → ADJUST
        high_bar = _bar(23 * 60, 2.0)
        # Now stop is ~1.99; crash to 1.50 → below 1.99 → hard stop fires
        crash_bar = _bar(24 * 60, 1.50)
        bars = signal_bars + [entry_bar, high_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades == 1
        trade = result.trades[0]
        # Stop was ratcheted up, so the closing price reflects that
        assert "Hard stop" in trade.exit_reason
        # P&L should be profitable because ratcheted stop is above entry
        assert trade.pnl_points > 0

    def test_end_of_data_closes_open_trade(self, momentum_cfg):
        # Signal fires but no more bars to trigger a rule exit → closed at last bar
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        flat_bars = [_bar((23 + i) * 60, 1.10) for i in range(3)]
        bars = signal_bars + [entry_bar] + flat_bars

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades == 1
        assert result.trades[0].exit_reason == "End of data"


# ---------------------------------------------------------------------------
# BacktestResult metrics
# ---------------------------------------------------------------------------

class TestMetrics:

    def _result_with_trades(self, trades_pnl: list[float], strategy_cfg, exit_reasons=None):
        """Build a BacktestResult by running a sequence that produces known trades."""
        # We test _summarise indirectly by constructing a scenario where trades
        # are deterministic enough to predict the outcome metrics.
        # For metric accuracy, we use the real engine with a crafted bar sequence
        # that produces one predictable trade per run.
        pass  # see individual tests below

    def test_win_rate_computed_correctly(self, momentum_cfg):
        # One winning trade (TP hit)
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        tp_bar = _bar(23 * 60, 2.0)
        bars = signal_bars + [entry_bar, tp_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades == 1
        assert result.winning_trades == 1
        assert result.win_rate == 1.0

    def test_stop_out_rate_computed_correctly(self, momentum_cfg):
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        crash_bar = _bar(23 * 60, 0.50)
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.stop_out_rate == 1.0

    def test_profit_factor_with_winning_trade(self, momentum_cfg):
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        tp_bar = _bar(23 * 60, 2.0)
        bars = signal_bars + [entry_bar, tp_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        # No losing trades → profit_factor = inf
        assert result.profit_factor == float("inf")

    def test_empty_bars_returns_zero_trades(self, momentum_cfg):
        result = run_backtest("EURUSD", "momentum", [], momentum_cfg, RISK_CFG)
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0

    def test_result_fields_populated(self, momentum_cfg):
        result = run_backtest("EURUSD", "momentum", _bars([1.0] * 5), momentum_cfg, RISK_CFG)
        assert result.epic == "EURUSD"
        assert result.strategy == "momentum"
        assert isinstance(result.trades, list)

    def test_net_pnl_pts_is_sum_of_trade_pnl(self, momentum_cfg):
        # One stop-out trade: entry 1.10, crash to 0.50 → pnl = 0.50 - 1.10 = -0.60
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        crash_bar = _bar(23 * 60, 0.50)
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades == 1
        expected_net = result.trades[0].pnl_points
        assert result.net_pnl_pts == pytest.approx(expected_net, abs=1e-4)
        assert result.net_pnl_pts < 0

    def test_avg_r_computed_correctly(self, momentum_cfg):
        # entry=1.10, stop_pct=0.02, net_pnl=-0.60 (crash to 0.50)
        # R = 1.10 * 0.02 = 0.022; avg_r = -0.60 / (1 * 0.022) ≈ -27.27
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        crash_bar = _bar(23 * 60, 0.50)
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.total_trades == 1
        expected_r = result.net_pnl_pts / (1 * 1.10 * 0.02)
        assert result.avg_r == pytest.approx(expected_r, rel=1e-3)
        assert result.avg_r < 0

    def test_avg_r_zero_when_no_trades(self, momentum_cfg):
        result = run_backtest("EURUSD", "momentum", [], momentum_cfg, RISK_CFG)
        assert result.avg_r == 0.0

    def test_mean_reversion_midline_exit(self, mean_rev_cfg):
        # SHORT signal fires at bar 19 (z=4.36).  Entry at bar 20 (open=1.5).
        # Price stays at 1.5; evaluate_position never fires TP because TP=1.455 < 1.5
        # (SELL TP fires when close <= profitLevel).
        # After 16 bars of 1.5 accumulate in the 20-bar window the mean rises to 1.4
        # and z drops to 0.5 → check_exit fires "Z-score midline".
        # window at bar 34: [1.0]*4 + [1.5]*16, mean=1.4, sigma=0.2, z=0.5
        # (Hold-cap removed 2026-05-15 — midline is now the only signal-exit.)
        bars = _bars([1.0] * 19 + [1.5] * 17)   # 36 bars; midline fires at bar 34

        result = run_backtest("EURUSD", "mean_reversion", bars, mean_rev_cfg, RISK_CFG)
        assert result.total_trades == 1
        assert result.trades[0].exit_reason == "Z-score midline"

    def test_spread_adjusts_buy_entry_and_exit(self, momentum_cfg):
        # BUY at next_bar.open=1.10 with spread_pts=0.10:
        #   entry fill = 1.10 + 0.05 = 1.15
        #   exit fill  = close - 0.05
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        flat_bar = _bar(23 * 60, 1.10)
        bars = signal_bars + [entry_bar, flat_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG,
                              spread_pts=0.10)
        trade = result.trades[0]
        assert trade.entry_price == pytest.approx(1.15, rel=1e-6)
        assert trade.exit_price  == pytest.approx(1.10 - 0.05, rel=1e-6)

    def test_spread_adjusts_sell_entry_and_exit(self, momentum_cfg):
        # SELL at next_bar.open=0.90 with spread_pts=0.10:
        #   entry fill = 0.90 - 0.05 = 0.85
        #   exit fill  = close + 0.05
        bars = _bars([1.0] * 21 + [0.90, 0.90, 0.90])

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG,
                              spread_pts=0.10)
        trade = result.trades[0]
        assert trade.entry_price == pytest.approx(0.85, rel=1e-6)
        assert trade.exit_price  == pytest.approx(0.90 + 0.05, rel=1e-6)

    def test_hard_stop_takes_priority_over_midline_exit(self, mean_rev_cfg):
        # Hard stop fires before check_exit is reached (open_trade is set to None first)
        signal_bars = _bars([1.0] * 19 + [1.5])
        entry_bar   = _bar(20 * 60, 1.5)
        crash_bar   = _bar(21 * 60, 5.0)   # SELL stop = 1.5225; 5.0 >> stop → hard stop
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "mean_reversion", bars, mean_rev_cfg, RISK_CFG)
        assert result.total_trades == 1
        assert "Hard stop" in result.trades[0].exit_reason


class TestDirectionalSplit:
    """long_trades/short_trades/long_pf/short_pf fields on BacktestResult."""

    def test_all_long_trades_no_short(self, momentum_cfg):
        # Signal sequence produces only LONG entries; short fields should be zero
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar   = _bar(22 * 60, 1.10)
        exit_bar    = _bar(23 * 60, 2.00)   # take profit
        bars = signal_bars + [entry_bar, exit_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.long_trades + result.short_trades == result.total_trades
        assert result.short_trades == 0
        assert result.short_win_rate == 0.0
        assert result.short_profit_factor == 0.0

    def test_directional_split_sums_to_total(self, momentum_cfg):
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar   = _bar(22 * 60, 1.10)
        crash_bar   = _bar(23 * 60, 0.50)
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.long_trades + result.short_trades == result.total_trades

    def test_long_pf_correct_for_winning_trade(self, momentum_cfg):
        # Disable trailing so TP fires cleanly
        cfg = {
            "risk": {
                "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
                "trailing_stop": {"enabled": False},
                "take_profit": {"dynamic": True, "min_rr_ratio": 1.5},
                "time_exit": {"enabled": False},
            }
        }
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar   = _bar(22 * 60, 1.10)
        tp_bar      = _bar(23 * 60, 2.00)
        bars = signal_bars + [entry_bar, tp_bar]

        result = run_backtest("EURUSD", "momentum", bars, cfg, RISK_CFG)
        assert result.long_trades == 1
        assert result.long_win_rate == 1.0
        assert result.long_profit_factor == float("inf")  # no losses


class TestATRTrailing:
    """ATR-trailing stop: trails at N×ATR(14) from bar high/low peak."""

    @pytest.fixture
    def atr_cfg(self):
        return {
            "risk": {
                "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
                "trailing_stop": {
                    "enabled": True,
                    "atr_multiplier": 1.5,
                    "min_distance_pct": 0.5,
                },
                "take_profit": {"dynamic": False, "min_rr_ratio": 99},
                "time_exit": {"enabled": False},
            }
        }

    def test_atr_trailing_ratchets_above_initial_stop(self, atr_cfg):
        # 21 flat + 1 spike → LONG at 1.10; price rises for 15 bars then gently falls.
        # ATR trailing ratchets the stop well above the initial 2% hard stop (1.078).
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar   = _bar(22 * 60, 1.10)
        rise_bars   = _bars([1.10 + i * 0.01 for i in range(1, 16)])  # 1.11..1.25
        fall_bars   = _bars([1.25 - i * 0.002 for i in range(1, 40)])  # gentle fall
        # Bump timestamps so bars are chronological after signal_bars
        for j, b in enumerate(rise_bars + fall_bars):
            object.__setattr__(b, "ts", (23 + j) * 60)
        bars = signal_bars + [entry_bar] + rise_bars + fall_bars

        result = run_backtest("EURUSD", "momentum", bars, atr_cfg, RISK_CFG)
        # Under the confirm-window entry, the gentle fall can confirm a second
        # crossover; we only care about the first (LONG) trade here.
        assert result.total_trades >= 1
        trade = result.trades[0]
        assert trade.direction == "BUY"
        initial_hard_stop = trade.entry_price * (1 - 0.02)   # 2% below entry
        # ATR trailing should ratchet stop above the initial 2% hard stop
        assert trade.exit_price > initial_hard_stop

    def test_atr_trailing_does_not_fire_before_price_moves(self, atr_cfg):
        # LONG entry, then immediate crash — ATR trailing ratchet should not have moved
        # stop above OR low since price never rose, so trade closes at entry-level stop.
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar   = _bar(22 * 60, 1.10)
        crash_bar   = _bar(23 * 60, 0.50)
        bars = signal_bars + [entry_bar, crash_bar]

        result = run_backtest("EURUSD", "momentum", bars, atr_cfg, RISK_CFG)
        assert result.total_trades == 1
        assert "Hard stop" in result.trades[0].exit_reason
        assert result.trades[0].pnl_points < 0

    def test_no_hard_tp_with_large_rr_ratio(self, atr_cfg):
        # With min_rr_ratio=99, a 2× move should not trigger take-profit
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar   = _bar(22 * 60, 1.10)
        tp_bar      = _bar(23 * 60, 2.20)   # 2× entry — TP at 99× stop won't be reached
        eod_bar     = _bar(24 * 60, 2.20)
        bars = signal_bars + [entry_bar, tp_bar, eod_bar]

        result = run_backtest("EURUSD", "momentum", bars, atr_cfg, RISK_CFG)
        assert result.total_trades == 1
        assert "Take profit" not in result.trades[0].exit_reason


# ---------------------------------------------------------------------------
# Audit fields — entry_mid, exit_mid, spread_at_entry, resolution
# These power the Phase A audit's gross-vs-net cost decomposition.
# ---------------------------------------------------------------------------

class TestAuditFields:

    def test_entry_mid_is_next_bar_open_not_fill(self, momentum_cfg):
        # Verify entry_mid is the un-spread-adjusted price.
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        flat_bar = _bar(23 * 60, 1.10)
        bars = signal_bars + [entry_bar, flat_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG,
                              spread_pts=0.10)
        trade = result.trades[0]
        assert trade.entry_mid == pytest.approx(1.10, rel=1e-6)
        assert trade.entry_price == pytest.approx(1.15, rel=1e-6)  # mid + half-spread
        # Half-spread is recoverable as the difference.
        assert trade.entry_price - trade.entry_mid == pytest.approx(0.05, rel=1e-6)

    def test_exit_mid_is_bar_close_not_fill(self, momentum_cfg):
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        flat_bar = _bar(23 * 60, 1.10)
        bars = signal_bars + [entry_bar, flat_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG,
                              spread_pts=0.10)
        trade = result.trades[0]
        # exit_mid is the bar.close that the engine saw at exit time.
        assert trade.exit_mid is not None
        # exit_price applies half-spread in the closing direction (BUY exits at bid = mid - half).
        assert trade.exit_price == pytest.approx(trade.exit_mid - 0.05, rel=1e-6)

    def test_spread_at_entry_is_recorded(self, momentum_cfg):
        bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG,
                              spread_pts=0.10)
        assert result.trades[0].spread_at_entry == pytest.approx(0.10, rel=1e-6)

    def test_zero_spread_records_zero(self, momentum_cfg):
        bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)  # default spread_pts=0
        trade = result.trades[0]
        assert trade.spread_at_entry == 0.0
        # No spread → entry_mid == entry_price.
        assert trade.entry_mid == pytest.approx(trade.entry_price, rel=1e-6)
        assert trade.exit_mid == pytest.approx(trade.exit_price, rel=1e-6)

    def test_resolution_default_is_none_when_engine_called_directly(self, momentum_cfg):
        # The engine never sets resolution — run.py stamps it after the fact.
        bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG)
        assert result.trades[0].resolution is None

    def test_gross_net_decomposition_recoverable(self, momentum_cfg):
        # With recorded mids and spread, gross P&L is (exit_mid - entry_mid)
        # for BUY trades, and the cost is the full spread paid.
        signal_bars = _bars([1.0] * 21 + [1.10, 1.10, 1.10])
        entry_bar = _bar(22 * 60, 1.10)
        up_bar = _bar(23 * 60, 1.12)
        eod_bar = _bar(24 * 60, 1.12)
        bars = signal_bars + [entry_bar, up_bar, eod_bar]

        result = run_backtest("EURUSD", "momentum", bars, momentum_cfg, RISK_CFG,
                              spread_pts=0.10)
        trade = result.trades[0]
        gross_pnl = trade.exit_mid - trade.entry_mid       # 1.12 - 1.10 = 0.02
        cost = trade.spread_at_entry                        # 0.10 (full spread)
        net_pnl_recovered = gross_pnl - cost
        assert net_pnl_recovered == pytest.approx(trade.pnl_points, rel=1e-6)


# ---------------------------------------------------------------------------
# Phase 4 — session model: time-exit, no overnight/weekend, no-trade window
# ---------------------------------------------------------------------------

import datetime as _dt


def _uts(y, mo, d, h, mi) -> int:
    return int(_dt.datetime(y, mo, d, h, mi, tzinfo=_dt.timezone.utc).timestamp())


def _mom_session_cfg():
    return {
        "risk": {
            "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
            "trailing_stop": {"enabled": False},
            "take_profit": {"dynamic": True, "min_rr_ratio": 1.5},
            "time_exit": {"enabled": True, "close_minutes_before_session_end": 30},
        }
    }


class TestSessionModel:
    _KW = {"adx_threshold": 0.0, "m30_gate": False}

    def _mom_bars(self, prices, start, step_s=900):
        return [
            OHLCBar(epic="EURUSD", resolution="M15",
                    ts=start + i * step_s, open=p, high=p, low=p, close=p, volume=100)
            for i, p in enumerate(prices)
        ]

    def test_time_exit_fires_at_daily_close(self):
        # 21 flat + spike → LONG; entry next bar; then flat through 21:00 UTC.
        start = _uts(2026, 5, 15, 8, 0)
        prices = [1.0] * 21 + [1.10] + [1.10] * 40
        bars = self._mom_bars(prices, start)
        result = run_backtest("EURUSD", "momentum", bars, _mom_session_cfg(),
                              RISK_CFG, signal_kwargs=self._KW,
                              session_close_utc="21:00")
        assert result.total_trades == 1
        t = result.trades[0]
        assert t.exit_reason.startswith("Time exit")
        # Closed at/after 20:30 UTC (session_end 21:00 − 30 min), same day
        assert _dt.datetime.fromtimestamp(t.exit_ts, _dt.timezone.utc) \
            >= _dt.datetime(2026, 5, 15, 20, 30, tzinfo=_dt.timezone.utc)

    def test_no_overnight_hold_when_no_close_window_bar(self):
        # Position opens, one in-trade bar, then the series jumps to the NEXT
        # UTC day with no bar in the close window → forced flat at prior bar.
        start = _uts(2026, 5, 15, 9, 0)
        bars = self._mom_bars([1.0] * 21 + [1.10, 1.10, 1.10, 1.10], start)  # cross,confirm,entry,in-trade — all day 1
        bars.append(OHLCBar(epic="EURUSD", resolution="M15",
                            ts=_uts(2026, 5, 16, 9, 0), open=1.10, high=1.10,
                            low=1.10, close=1.10, volume=100))
        result = run_backtest("EURUSD", "momentum", bars, _mom_session_cfg(),
                              RISK_CFG, signal_kwargs=self._KW,
                              session_close_utc="21:00")
        assert result.total_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "Session close (no bar at threshold)"
        # Flattened on day 1, never carried into 2026-05-16
        assert _dt.datetime.fromtimestamp(t.exit_ts, _dt.timezone.utc).date() \
            == _dt.date(2026, 5, 15)

    def test_no_new_entry_inside_no_trade_window(self):
        # Crossover bar placed so the entry bar (i+1) lands at 20:45 UTC, inside
        # the 30-min no-trade window before the 21:00 close → entry suppressed.
        entry_ts = _uts(2026, 5, 15, 20, 45)
        start = entry_ts - 22 * 900   # 22 bars before the entry bar
        bars = self._mom_bars([1.0] * 21 + [1.10] + [1.10], start)
        result = run_backtest("EURUSD", "momentum", bars, _mom_session_cfg(),
                              RISK_CFG, signal_kwargs=self._KW,
                              session_close_utc="21:00")
        assert result.total_trades == 0
