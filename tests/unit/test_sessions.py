"""Unit tests for backtest/sessions.py."""

from cfd_trading.backtest.sessions import session_open_utc


class TestSessionOpenUtc:

    def test_us500_nyse_open(self):
        assert session_open_utc("US500") == (14, 30)

    def test_de40_xetra_open(self):
        assert session_open_utc("DE40") == (8, 0)

    def test_uk100_lse_open(self):
        assert session_open_utc("UK100") == (8, 0)

    def test_fx_london_open(self):
        for epic in ("EURUSD", "GBPUSD", "USDJPY", "EURGBP"):
            assert session_open_utc(epic) == (8, 0), f"{epic} should use London open"

    def test_commodities_london_open(self):
        assert session_open_utc("GOLD")   == (8, 0)
        assert session_open_utc("XBRUSD") == (8, 0)

    def test_crypto_midnight_utc(self):
        assert session_open_utc("BTCUSD") == (0, 0)
        assert session_open_utc("ETHUSD") == (0, 0)

    def test_unknown_epic_defaults_to_london_open(self):
        assert session_open_utc("UNKNOWN") == (8, 0)

    def test_returns_tuple_of_two_ints(self):
        hour, minute = session_open_utc("US500")
        assert isinstance(hour, int)
        assert isinstance(minute, int)
