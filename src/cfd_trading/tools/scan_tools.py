"""MCP tools: scan_markets, analyze_instrument."""

import json
import math
import os
from pathlib import Path

import yaml

from cfd_trading.strategy.loader import load_strategy, load_base_prompt, load_scan_prompt
from cfd_trading.tools._state import require_state

_CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(Path(__file__).parents[3] / "config")))


def scan_markets(watchlist: str | None = None) -> str:
    """
    Fetch ATR, trend, spread, and sentiment for each instrument in the watchlist.
    Returns structured data + the scan prompt for Claude Code to rank and present.

    watchlist — optional comma-separated list of epics to override config/watchlist.yaml.
    """
    state = require_state()

    epics = _resolve_watchlist(watchlist, state.config_dir)
    scan_prompt = load_scan_prompt(state.config_dir)

    instruments = []
    for epic in epics:
        result = _analyse_epic(state.client, epic)
        if result:
            instruments.append(result)

    return json.dumps({
        "scan_prompt": scan_prompt,
        "instruments": instruments,
    })


def analyze_instrument(epic: str, strategy: str) -> str:
    """
    Fetch 60x1min bars, sentiment, and open positions for one instrument.
    Returns structured context + base and strategy prompts for Claude Code to reason over.

    epic     — Capital.com epic identifier, e.g. "EURUSD"
    strategy — strategy name, e.g. "momentum" or "mean_reversion"
    """
    state = require_state()

    strat = load_strategy(strategy, state.config_dir)
    base_prompt = load_base_prompt(state.config_dir)

    prices_resp = state.client.get_prices(epic, resolution="MINUTE", max=60)
    bars = prices_resp.get("prices", []) if "error" not in prices_resp else []

    sentiment_resp = state.client.get_client_sentiment(epic)
    sentiments = sentiment_resp.get("clientSentiments", [])
    sentiment = sentiments[0] if sentiments else {}

    positions_resp = state.client.get_positions()
    all_positions = positions_resp.get("positions", [])
    instrument_positions = [
        p for p in all_positions
        if p.get("market", {}).get("epic") == epic
    ]

    atr = _compute_atr(bars)
    trend_slope = _compute_trend_slope(bars)
    spread, spread_pct = _compute_spread(bars, atr)
    candle_summary = _summarise_candles(bars, n=20)
    high_low = _recent_high_low(bars)
    ema_9 = _compute_ema(bars, 9)
    ema_21 = _compute_ema(bars, 21)
    zscore = _compute_zscore(bars)

    account_info = state.client.get_account_info()
    accounts = account_info.get("accounts", []) if "error" not in account_info else []
    account_balance = accounts[0]["balance"]["available"] if accounts else None
    target_risk_pct = strat.config.get("risk", {}).get("target_risk_pct")
    suggested_size = None
    if account_balance and atr and atr > 0 and target_risk_pct:
        suggested_size = round((target_risk_pct / 100) * account_balance / atr, 2)

    return json.dumps({
        "base_prompt": base_prompt,
        "strategy_prompt": strat.prompt,
        "strategy_config": strat.config,
        "market": {
            "epic": epic,
            "current_bid": candle_summary[-1]["close_bid"] if candle_summary else None,
            "spread": spread,
            "spread_pct_of_atr": spread_pct,
        },
        "analysis": {
            "atr": atr,
            "trend_slope": trend_slope,
            "trend_direction": "UP" if trend_slope > 0 else "DOWN" if trend_slope < 0 else "FLAT",
            "recent_high": high_low["high"],
            "recent_low": high_low["low"],
            "bars_available": len(bars),
            "ema_9": ema_9,
            "ema_21": ema_21,
            "zscore": zscore,
        },
        "account": {
            "available_balance": account_balance,
            "suggested_size": suggested_size,
        },
        "candles": candle_summary,
        "sentiment": {
            "long_pct": sentiment.get("longPositionPercentage"),
            "short_pct": sentiment.get("shortPositionPercentage"),
        },
        "open_positions_in_instrument": instrument_positions,
    })


# ---------------------------------------------------------------------------
# Market analysis helpers — pure functions, no I/O
# ---------------------------------------------------------------------------

def _compute_atr(bars: list, period: int = 14) -> float | None:
    """Average True Range using bid-side OHLC."""
    if len(bars) < 2:
        return None
    trs = []
    for i in range(1, len(bars)):
        try:
            h = bars[i]["highPrice"]["bid"]
            l = bars[i]["lowPrice"]["bid"]
            prev_c = bars[i - 1]["closePrice"]["bid"]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        except (KeyError, TypeError):
            continue
    if not trs:
        return None
    recent = trs[-period:]
    return round(sum(recent) / len(recent), 6)


def _compute_trend_slope(bars: list) -> float:
    """
    Linear regression slope over close prices (bid side).
    Positive = uptrend, negative = downtrend.
    """
    closes = []
    for b in bars:
        try:
            closes.append(b["closePrice"]["bid"])
        except (KeyError, TypeError):
            continue
    n = len(closes)
    if n < 4:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(closes) / n
    num = sum((xs[i] - mean_x) * (closes[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs)
    return round(num / den, 8) if den else 0.0


def _compute_spread(bars: list, atr: float | None) -> tuple[float | None, float | None]:
    """Current spread from latest bar's bid/ask close, and spread as % of ATR."""
    if not bars:
        return None, None
    try:
        last = bars[-1]
        bid = last["closePrice"]["bid"]
        ask = last["closePrice"]["ask"]
        spread = round(ask - bid, 6)
        pct = round(spread / atr * 100, 1) if atr and atr > 0 else None
        return spread, pct
    except (KeyError, TypeError):
        return None, None


def _summarise_candles(bars: list, n: int = 20) -> list[dict]:
    """Return the last n bars in compact form for Claude Code's context window."""
    recent = bars[-n:] if len(bars) >= n else bars
    result = []
    for b in recent:
        try:
            result.append({
                "time": b.get("snapshotTime", ""),
                "open_bid": b["openPrice"]["bid"],
                "high_bid": b["highPrice"]["bid"],
                "low_bid": b["lowPrice"]["bid"],
                "close_bid": b["closePrice"]["bid"],
                "close_ask": b["closePrice"]["ask"],
                "volume": b.get("lastTradedVolume"),
            })
        except (KeyError, TypeError):
            continue
    return result


def _recent_high_low(bars: list) -> dict:
    highs = []
    lows = []
    for b in bars:
        try:
            highs.append(b["highPrice"]["bid"])
            lows.append(b["lowPrice"]["bid"])
        except (KeyError, TypeError):
            continue
    return {
        "high": round(max(highs), 6) if highs else None,
        "low": round(min(lows), 6) if lows else None,
    }


def _analyse_epic(client, epic: str) -> dict | None:
    """Fetch and compute metrics for one instrument. Returns None on API error."""
    prices_resp = client.get_prices(epic, resolution="MINUTE", max=30)
    if "error" in prices_resp:
        return None
    bars = prices_resp.get("prices", [])
    if not bars:
        return None

    atr = _compute_atr(bars)
    trend_slope = _compute_trend_slope(bars)
    spread, spread_pct = _compute_spread(bars, atr)

    sentiment_resp = client.get_client_sentiment(epic)
    sentiments = sentiment_resp.get("clientSentiments", [])
    sentiment = sentiments[0] if sentiments else {}

    try:
        current_bid = bars[-1]["closePrice"]["bid"]
        current_ask = bars[-1]["closePrice"]["ask"]
    except (KeyError, IndexError, TypeError):
        current_bid, current_ask = None, None

    return {
        "epic": epic,
        "current_bid": current_bid,
        "current_ask": current_ask,
        "spread": spread,
        "spread_pct_of_atr": spread_pct,
        "atr": atr,
        "trend_slope": trend_slope,
        "trend_direction": "UP" if trend_slope > 0 else "DOWN" if trend_slope < 0 else "FLAT",
        "sentiment_long_pct": sentiment.get("longPositionPercentage"),
        "sentiment_short_pct": sentiment.get("shortPositionPercentage"),
    }


def _compute_ema(bars: list, period: int) -> float | None:
    """Exponential moving average of close (bid) seeded with a simple average."""
    closes = []
    for b in bars:
        try:
            closes.append(b["closePrice"]["bid"])
        except (KeyError, TypeError):
            continue
    if len(closes) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = alpha * c + (1 - alpha) * ema
    return round(ema, 6)


def _compute_zscore(bars: list, period: int = 20) -> dict:
    """
    Z-score of the latest close relative to the last `period` closes.
    Returns {"mu", "sigma", "z"} — all None if there is insufficient data.
    """
    closes = []
    for b in bars:
        try:
            closes.append(b["closePrice"]["bid"])
        except (KeyError, TypeError):
            continue
    window = closes[-period:] if len(closes) >= period else closes
    if len(window) < 4:
        return {"mu": None, "sigma": None, "z": None}
    mu = sum(window) / len(window)
    sigma = (sum((c - mu) ** 2 for c in window) / len(window)) ** 0.5
    if sigma == 0:
        return {"mu": round(mu, 6), "sigma": 0.0, "z": None}
    return {
        "mu": round(mu, 6),
        "sigma": round(sigma, 6),
        "z": round((window[-1] - mu) / sigma, 3),
    }


def _resolve_watchlist(watchlist_param: str | None, config_dir: Path) -> list[str]:
    if watchlist_param:
        return [e.strip() for e in watchlist_param.split(",") if e.strip()]
    wl_path = config_dir / "watchlist.yaml"
    with wl_path.open() as f:
        wl = yaml.safe_load(f)
    epics = []
    for group in wl.values():
        if isinstance(group, list):
            epics.extend(group)
    return epics
