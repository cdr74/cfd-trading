"""Per-instrument session open times for ORB backtesting.

Returns the UTC (hour, minute) at which the primary trading session opens
for each instrument.  Used by ORBSignalState to identify the opening range bar.

DST caveat: times are fixed UTC values.  European instruments shift by 1 hour
around DST changes (last Sunday March / last Sunday October).  For a 4-month
backtest the misalignment affects ~1–2 weeks of sessions and is acceptable.
"""

_SESSION_UTC: dict[str, tuple[int, int]] = {
    # US indices — NYSE open 09:30 ET (winter EST = UTC-5)
    "US500":  (14, 30),

    # European indices — primary exchange open (winter UTC)
    "DE40":   ( 8,  0),   # Xetra 09:00 CET = 08:00 UTC
    "UK100":  ( 8,  0),   # LSE 08:00 UTC

    # FX — London open
    "EURUSD": ( 8,  0),
    "GBPUSD": ( 8,  0),
    "USDJPY": ( 8,  0),
    "EURGBP": ( 8,  0),

    # Commodities — London/ICE open
    "GOLD":   ( 8,  0),
    "XBRUSD": ( 8,  0),

    # Crypto — UTC midnight (no traditional session)
    "BTCUSD": ( 0,  0),
    "ETHUSD": ( 0,  0),
}


def session_open_utc(epic: str) -> tuple[int, int]:
    """Return (hour, minute) UTC for the primary session open of this epic.

    Defaults to (8, 0) for unknown epics.
    """
    return _SESSION_UTC.get(epic, (8, 0))
