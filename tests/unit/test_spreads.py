"""Unit tests for backtest/spreads.py."""

import pytest
from cfd_trading.backtest.spreads import spread_points


class TestSpreadPoints:

    def test_fx_major_returns_one_pip(self):
        assert spread_points("EURUSD", 1.08) == pytest.approx(0.00010)
        assert spread_points("GBPUSD", 1.28) == pytest.approx(0.00010)
        assert spread_points("EURGBP", 0.86) == pytest.approx(0.00010)

    def test_usdjpy_returns_one_pip_in_yen(self):
        # 1 pip for JPY pair = 0.01
        assert spread_points("USDJPY", 155.0) == pytest.approx(0.01)

    def test_index_returns_absolute_points(self):
        assert spread_points("US500",  5200.0) == pytest.approx(0.5)
        assert spread_points("DE40",   18000.0) == pytest.approx(1.0)
        assert spread_points("UK100",  8000.0)  == pytest.approx(1.0)

    def test_gold_returns_absolute_usd(self):
        assert spread_points("GOLD", 2300.0) == pytest.approx(0.35)

    def test_oil_returns_absolute_usd(self):
        assert spread_points("XBRUSD", 80.0) == pytest.approx(0.04)

    def test_crypto_scales_with_price(self):
        # 0.07% of price
        assert spread_points("BTCUSD", 60000.0) == pytest.approx(60000.0 * 0.0007)
        assert spread_points("ETHUSD",  3000.0) == pytest.approx(3000.0 * 0.0007)

    def test_unknown_epic_returns_zero(self):
        assert spread_points("UNKNWN", 100.0) == 0.0

    def test_spread_positive_for_all_watchlist_epics(self):
        watchlist = [
            ("EURUSD", 1.08), ("GBPUSD", 1.28), ("USDJPY", 155.0), ("EURGBP", 0.86),
            ("US500", 5200.0), ("DE40", 18000.0), ("UK100", 8000.0),
            ("GOLD", 2300.0), ("XBRUSD", 80.0),
            ("BTCUSD", 60000.0), ("ETHUSD", 3000.0),
        ]
        for epic, price in watchlist:
            assert spread_points(epic, price) > 0, f"{epic} returned non-positive spread"
