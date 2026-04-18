"""Integration tests for broker/capital_client.py against the Capital.com demo API."""

import pytest
from cfd_trading.broker.capital_client import CapitalClient


@pytest.fixture(scope="module")
def client():
    c = CapitalClient()
    assert c.authenticate(), "Authentication failed — check .env credentials"
    return c


@pytest.mark.integration
def test_authenticate(client):
    assert client.account_id is not None
    assert client.cst is not None
    assert client.x_security_token is not None


@pytest.mark.integration
def test_ping(client):
    result = client.ping()
    assert "error" not in result


@pytest.mark.integration
def test_get_account_info(client):
    result = client.get_account_info()
    assert "error" not in result
    assert "accounts" in result
    assert len(result["accounts"]) > 0


@pytest.mark.integration
def test_get_prices_eurusd(client):
    result = client.get_prices("EURUSD", resolution="MINUTE", max=10)
    assert "error" not in result, f"get_prices failed: {result}"
    prices = result.get("prices", [])
    assert len(prices) > 0, "Expected at least one price bar"
    bar = prices[0]
    assert "openPrice" in bar or "snapshotTime" in bar, f"Unexpected bar structure: {bar}"


@pytest.mark.integration
def test_get_positions(client):
    result = client.get_positions()
    assert "error" not in result, f"get_positions failed: {result}"
    assert "positions" in result


@pytest.mark.integration
def test_get_client_sentiment_eurusd(client):
    result = client.get_client_sentiment("EURUSD")
    assert "error" not in result, f"get_client_sentiment failed: {result}"
    sentiments = result.get("clientSentiments", [])
    assert len(sentiments) > 0
    s = sentiments[0]
    assert "longPositionPercentage" in s
    assert "shortPositionPercentage" in s
    total = s["longPositionPercentage"] + s["shortPositionPercentage"]
    assert abs(total - 100.0) < 1.0, f"Long + short should sum to ~100, got {total}"


@pytest.mark.integration
def test_get_historical_prices(client):
    result = client.get_historical_prices("EURUSD", resolution="MINUTE_5", max_bars=20)
    assert "error" not in result, f"get_historical_prices failed: {result}"
    assert len(result.get("prices", [])) > 0


# ---------------------------------------------------------------------------
# Trade-marked tests — create/close real demo positions. Run manually only.
# ---------------------------------------------------------------------------

@pytest.mark.trade
def test_create_and_close_position(client):
    """Open a minimal EURUSD long, confirm it, then immediately close it."""
    create_result = client.create_position(
        epic="EURUSD",
        direction="BUY",
        size=0.1,
        stop_distance=50,
    )
    assert "error" not in create_result, f"create_position failed: {create_result}"
    deal_ref = create_result.get("dealReference")
    assert deal_ref, "No dealReference in create response"

    confirm = client.confirm_deal(deal_ref)
    assert "error" not in confirm, f"confirm_deal failed: {confirm}"
    deal_id = confirm.get("dealId")
    assert deal_id, f"No dealId in confirm response: {confirm}"

    close_result = client.close_position(deal_id)
    assert "error" not in close_result, f"close_position failed: {close_result}"
    assert "dealReference" in close_result
