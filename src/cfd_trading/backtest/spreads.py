"""Capital.com typical mid-session spread values per instrument.

Values taken from Capital.com published spread table (2026-05).
Used by:
  - backtest/engine.py  — adjusts entry/exit fill prices by spread/2
  - backtest/signals.py — ATR(14) ≥ 4 × spread gate in MeanReversionSignalState

FX spreads are expressed as decimal price units (1 pip = 0.0001 for EUR/USD,
0.01 for USD/JPY).  Index / commodity spreads are in index points or USD.
Crypto spreads are a fraction of the current price (multiply by bar.close).
"""

from __future__ import annotations

_ABSOLUTE: dict[str, float] = {
    "EURUSD": 0.00010,   # 1.0 pip
    "GBPUSD": 0.00010,   # 1.0 pip
    "USDJPY": 0.01000,   # 1.0 pip (JPY pair: 1 pip = 0.01)
    "EURGBP": 0.00010,   # 1.0 pip
    "US500":  0.5,       # 0.5 index points
    "DE40":   1.0,       # 1.0 index points
    "UK100":  1.0,       # 1.0 index points
    "GOLD":   0.35,      # USD/oz
    "XBRUSD": 0.04,      # USD/bbl
}

_PCT: dict[str, float] = {
    "BTCUSD": 0.0007,    # 0.07% of price
    "ETHUSD": 0.0007,    # 0.07% of price
}


def spread_points(epic: str, price: float) -> float:
    """Return typical spread in price units for *epic* at *price*.

    Returns 0.0 for unknown epics so callers can use it unconditionally.
    Percentage-based epics (crypto) use *price* as the reference mid.
    """
    if epic in _ABSOLUTE:
        return _ABSOLUTE[epic]
    if epic in _PCT:
        return _PCT[epic] * price
    return 0.0
