"""Unit tests for backtest/run.py — no I/O, no real SQLite file required."""

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import cfd_trading.backtest.run as run_mod
from cfd_trading.backtest.engine import BacktestResult
from cfd_trading.storage.repository import OHLCBar


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOMENTUM_CFG = {
    "risk": {
        "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
        "trailing_stop": {"enabled": False, "min_distance_pct": 0.5, "max_distance_pct": 3.0},
        "take_profit": {"dynamic": True, "min_rr_ratio": 1.5},
        "time_exit": {"enabled": False},
        "target_risk_pct": 1.0,
    }
}

RISK_CFG = {
    "global": {
        "max_loss_pct_per_trade": 5.0,
        "margin_floor_pct": 20.0,
        "max_open_positions": 3,
        "session_end_close": True,
    }
}

SAMPLE_RESULT = BacktestResult(
    epic="EURUSD",
    strategy="momentum",
    total_trades=10,
    winning_trades=6,
    win_rate=0.6,
    profit_factor=1.8,
    max_drawdown_pct=3.5,
    stop_out_rate=0.2,
    signal_frequency=2.5,
    net_pnl_pts=0.1234,
    avg_r=0.16,
    trades=[],
)


def _make_bars(n: int = 50) -> list[OHLCBar]:
    return [
        OHLCBar(epic="EURUSD", resolution="M1", ts=1_700_000_000 + i * 60,
                open=1.10 + i * 0.0001, high=1.10 + i * 0.0001 + 0.0005,
                low=1.10 + i * 0.0001 - 0.0005, close=1.10 + i * 0.0001 + 0.0001,
                volume=100)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _resolve_strategies / _resolve_epics
# ---------------------------------------------------------------------------

def test_resolve_strategies_single():
    args = argparse.Namespace(strategy="momentum", all_strategies=False)
    result = run_mod._resolve_strategies(args, Path("/fake/config"))
    assert result == ["momentum"]


def test_resolve_strategies_all(tmp_path):
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    for name in ("momentum.yaml", "mean_reversion.yaml", "_base.yaml", "scan.yaml"):
        (strat_dir / name).touch()
        (strat_dir / name.replace(".yaml", ".md")).touch()
    args = argparse.Namespace(all_strategies=True)
    result = run_mod._resolve_strategies(args, tmp_path)
    assert set(result) == {"momentum", "mean_reversion"}


def test_resolve_epics_single():
    args = argparse.Namespace(epic="EURUSD", all_epics=False)
    result = run_mod._resolve_epics(args, Path("/fake/config"))
    assert result == ["EURUSD"]


def test_resolve_epics_all(tmp_path):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("forex:\n  - EURUSD\n  - GBPUSD\ncrypto:\n  - BTCUSD\n")
    args = argparse.Namespace(all_epics=True, instruments=None)
    result = run_mod._resolve_epics(args, tmp_path)
    assert result == ["EURUSD", "GBPUSD", "BTCUSD"]


def test_resolve_epics_instruments_subset(tmp_path):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("forex:\n  - EURUSD\n  - GBPUSD\nindices:\n  - US500\n  - DE40\n")
    args = argparse.Namespace(all_epics=False, instruments="GBPUSD,US500")
    result = run_mod._resolve_epics(args, tmp_path)
    assert result == ["GBPUSD", "US500"]


def test_resolve_epics_instruments_rejects_unknown(tmp_path):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("forex:\n  - EURUSD\n")
    args = argparse.Namespace(all_epics=False, instruments="EURUSD,FAKEEPIC")
    with pytest.raises(ValueError, match="Unknown instruments"):
        run_mod._resolve_epics(args, tmp_path)


def test_resolve_epics_instruments_trims_whitespace(tmp_path):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text("forex:\n  - EURUSD\n  - GBPUSD\n")
    args = argparse.Namespace(all_epics=False, instruments=" EURUSD , GBPUSD ")
    result = run_mod._resolve_epics(args, tmp_path)
    assert result == ["EURUSD", "GBPUSD"]


# ---------------------------------------------------------------------------
# _build_signal_kwargs
# ---------------------------------------------------------------------------

def test_build_signal_kwargs_momentum_default():
    args = argparse.Namespace(momentum_relaxed=False)
    kw = run_mod._build_signal_kwargs("momentum", args, "EURUSD")
    assert kw == {"m30_gate": False}


def test_build_signal_kwargs_momentum_relaxed():
    args = argparse.Namespace(momentum_relaxed=True)
    kw = run_mod._build_signal_kwargs("momentum", args, "EURUSD")
    assert kw["m30_gate"] is False
    assert kw["adx_threshold"] == 20.0
    assert kw["min_ema_gap_pct"] == 0.0002


def test_build_signal_kwargs_orb_passes_session_open():
    args = argparse.Namespace(momentum_relaxed=False)
    kw = run_mod._build_signal_kwargs("orb", args, "US500")
    # US500 RTH open = 14:30 UTC (per sessions.py)
    assert kw["session_open_hour"] == 14
    assert kw["session_open_minute"] == 30


def test_build_signal_kwargs_mean_reversion_empty():
    args = argparse.Namespace(momentum_relaxed=False)
    kw = run_mod._build_signal_kwargs("mean_reversion", args, "EURUSD")
    assert kw == {}


# ---------------------------------------------------------------------------
# _load_risk
# ---------------------------------------------------------------------------

def test_load_risk(tmp_path):
    (tmp_path / "risk.yaml").write_text("global:\n  max_loss_pct_per_trade: 5.0\n")
    cfg = run_mod._load_risk(tmp_path)
    assert cfg["global"]["max_loss_pct_per_trade"] == 5.0


# ---------------------------------------------------------------------------
# _print_table
# ---------------------------------------------------------------------------

def test_print_table_no_crash(capsys):
    run_mod._print_table([SAMPLE_RESULT])
    out = capsys.readouterr().out
    assert "EURUSD" in out
    assert "momentum" in out
    assert "60.0%" in out      # win_rate formatted
    assert "1.80" in out       # profit_factor formatted
    assert "+0.16R" in out     # avg_r with sign and R suffix


def test_print_table_avg_r_negative(capsys):
    r = BacktestResult(
        epic="EURUSD", strategy="momentum",
        total_trades=5, winning_trades=1,
        win_rate=0.2, profit_factor=0.5,
        max_drawdown_pct=4.0, stop_out_rate=0.8,
        signal_frequency=3.0, net_pnl_pts=-0.0567, avg_r=-0.04,
        trades=[],
    )
    run_mod._print_table([r])
    out = capsys.readouterr().out
    assert "-0.04R" in out


def test_print_table_inf_profit_factor(capsys):
    r = BacktestResult(
        epic="GOLD", strategy="mean_reversion",
        total_trades=5, winning_trades=5,
        win_rate=1.0, profit_factor=float("inf"),
        max_drawdown_pct=0.0, stop_out_rate=0.0,
        signal_frequency=1.0, trades=[],
    )
    run_mod._print_table([r])
    out = capsys.readouterr().out
    assert "inf" in out


def test_print_table_zero_trades(capsys):
    r = BacktestResult(
        epic="EURUSD", strategy="momentum",
        total_trades=0, winning_trades=0,
        win_rate=0.0, profit_factor=0.0,
        max_drawdown_pct=0.0, stop_out_rate=0.0,
        signal_frequency=0.0, trades=[],
    )
    run_mod._print_table([r])
    out = capsys.readouterr().out
    assert "EURUSD" in out


# ---------------------------------------------------------------------------
# main() integration — mocked DB + engine
# ---------------------------------------------------------------------------

def test_main_single_epic_strategy(tmp_path, monkeypatch):
    """main() with a real in-memory DB populated with bars."""
    # Build a minimal config dir
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (tmp_path / "risk.yaml").write_text(
        "global:\n  max_loss_pct_per_trade: 5.0\n  margin_floor_pct: 20.0\n"
        "  max_open_positions: 3\n  session_end_close: true\n"
    )
    mom_yaml = (
        "entry:\n  min_size: 0.1\n  max_size: 10.0\n"
        "risk:\n"
        "  target_risk_pct: 1.0\n"
        "  stop_loss:\n    type: HARD\n    default_pct: 2.0\n    max_pct: 5.0\n"
        "  trailing_stop:\n    enabled: false\n    min_distance_pct: 0.5\n    max_distance_pct: 3.0\n"
        "  take_profit:\n    dynamic: true\n    min_rr_ratio: 1.5\n"
        "  time_exit:\n    enabled: false\n"
    )
    (strat_dir / "momentum.yaml").write_text(mom_yaml)
    (strat_dir / "momentum.md").write_text("Momentum strategy.")
    (tmp_path / "watchlist.yaml").write_text("forex:\n  - EURUSD\n")

    # In-memory SQLite with ohlc_bars
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE ohlc_bars "
        "(epic TEXT, resolution TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER, "
        "PRIMARY KEY(epic, resolution, ts))"
    )
    bars = _make_bars(50)
    conn.executemany(
        "INSERT INTO ohlc_bars VALUES (?,?,?,?,?,?,?,?)",
        [(b.epic, b.resolution, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("BACKTEST_DB_PATH", db_path)
    monkeypatch.setattr(run_mod, "_CONFIG_DIR", tmp_path)

    with patch("sys.argv", ["run", "--strategy", "momentum", "--epic", "EURUSD"]):
        run_mod.main()  # should not raise


def test_main_missing_db_exits(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BACKTEST_DB_PATH", str(tmp_path / "nonexistent.db"))
    monkeypatch.setattr(run_mod, "_CONFIG_DIR", tmp_path)

    with patch("sys.argv", ["run", "--strategy", "momentum", "--epic", "EURUSD"]):
        with pytest.raises(SystemExit) as exc_info:
            run_mod.main()
    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_no_bars_skips_gracefully(tmp_path, monkeypatch, capsys):
    """When DB exists but has no bars for the requested epic, runner skips and prints message."""
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (tmp_path / "risk.yaml").write_text(
        "global:\n  max_loss_pct_per_trade: 5.0\n  margin_floor_pct: 20.0\n"
        "  max_open_positions: 3\n  session_end_close: true\n"
    )
    mom_yaml = (
        "entry:\n  min_size: 0.1\n  max_size: 10.0\n"
        "risk:\n"
        "  target_risk_pct: 1.0\n"
        "  stop_loss:\n    type: HARD\n    default_pct: 2.0\n    max_pct: 5.0\n"
        "  trailing_stop:\n    enabled: false\n    min_distance_pct: 0.5\n    max_distance_pct: 3.0\n"
        "  take_profit:\n    dynamic: true\n    min_rr_ratio: 1.5\n"
        "  time_exit:\n    enabled: false\n"
    )
    (strat_dir / "momentum.yaml").write_text(mom_yaml)
    (strat_dir / "momentum.md").write_text("Momentum strategy.")
    (tmp_path / "watchlist.yaml").write_text("forex:\n  - EURUSD\n")

    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE ohlc_bars "
        "(epic TEXT, resolution TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume INTEGER, "
        "PRIMARY KEY(epic, resolution, ts))"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("BACKTEST_DB_PATH", db_path)
    monkeypatch.setattr(run_mod, "_CONFIG_DIR", tmp_path)

    with patch("sys.argv", ["run", "--strategy", "momentum", "--epic", "EURUSD"]):
        run_mod.main()

    out = capsys.readouterr().out
    assert "No results" in out or "skip" in out


# ---------------------------------------------------------------------------
# Instrument filter
# ---------------------------------------------------------------------------

def test_instrument_allowed_no_filter():
    assert run_mod._instrument_allowed("EURUSD", {}) is True
    assert run_mod._instrument_allowed("ANYTHING", {"risk": {}}) is True


def test_instrument_allowed_with_list():
    cfg = {"instruments": ["DE40", "UK100", "USDJPY"]}
    assert run_mod._instrument_allowed("DE40", cfg) is True
    assert run_mod._instrument_allowed("EURUSD", cfg) is False
    assert run_mod._instrument_allowed("BTCUSD", cfg) is False


def test_instrument_allowed_empty_list():
    cfg = {"instruments": []}
    assert run_mod._instrument_allowed("EURUSD", cfg) is False


# ---------------------------------------------------------------------------
# Trade-log Parquet output
# ---------------------------------------------------------------------------

def _make_trade(epic="EURUSD", strategy="momentum", entry_ts=1_700_000_000, **overrides):
    from cfd_trading.backtest.engine import Trade
    base = dict(
        epic=epic,
        strategy=strategy,
        direction="BUY",
        entry_ts=entry_ts,
        entry_price=1.10,
        stop_loss=1.078,
        take_profit=1.133,
        exit_ts=entry_ts + 600,
        exit_price=1.12,
        exit_reason="Take profit",
        pnl_points=0.02,
        risk_pts=0.022,
        entry_mid=1.099,
        exit_mid=1.1205,
        spread_at_entry=0.002,
        resolution="M15",
    )
    base.update(overrides)
    return Trade(**base)


def test_write_trade_log_creates_parquet(tmp_path):
    import pyarrow.parquet as pq

    trades = [_make_trade(), _make_trade(strategy="mean_reversion", entry_ts=1_700_001_000)]
    result = BacktestResult(
        epic="EURUSD", strategy="momentum",
        total_trades=2, winning_trades=2,
        win_rate=1.0, profit_factor=2.0,
        max_drawdown_pct=0.0, stop_out_rate=0.0,
        signal_frequency=1.0, trades=trades,
    )
    out = tmp_path / "trades.parquet"
    run_mod._write_trade_log([result], str(out))

    assert out.exists()
    table = pq.read_table(out)
    assert table.num_rows == 2
    expected_cols = {"epic", "strategy", "direction", "entry_ts", "entry_price",
                     "stop_loss", "take_profit", "exit_ts", "exit_price",
                     "exit_reason", "pnl_points", "risk_pts",
                     "entry_mid", "exit_mid", "spread_at_entry", "resolution"}
    assert expected_cols.issubset(set(table.column_names))


def test_write_trade_log_creates_parent_dirs(tmp_path):
    trades = [_make_trade()]
    result = BacktestResult(
        epic="EURUSD", strategy="momentum",
        total_trades=1, winning_trades=1,
        win_rate=1.0, profit_factor=2.0,
        max_drawdown_pct=0.0, stop_out_rate=0.0,
        signal_frequency=1.0, trades=trades,
    )
    nested = tmp_path / "a" / "b" / "trades.parquet"
    run_mod._write_trade_log([result], str(nested))
    assert nested.exists()


def test_write_trade_log_empty_results(tmp_path, capsys):
    empty_result = BacktestResult(
        epic="EURUSD", strategy="momentum",
        total_trades=0, winning_trades=0,
        win_rate=0.0, profit_factor=0.0,
        max_drawdown_pct=0.0, stop_out_rate=0.0,
        signal_frequency=0.0, trades=[],
    )
    out = tmp_path / "trades.parquet"
    run_mod._write_trade_log([empty_result], str(out))

    assert not out.exists()
    assert "No trades to write" in capsys.readouterr().out
