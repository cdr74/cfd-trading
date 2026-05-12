"""Broker client — raises immediately when BACKTEST_MODE=true."""

import os

from capital_com_client import CapitalClient as _BaseCapitalClient


class CapitalClient(_BaseCapitalClient):
    def __init__(self, *args, **kwargs):
        if os.getenv("BACKTEST_MODE", "").lower() == "true":
            raise RuntimeError("Live API disabled in backtest mode")
        super().__init__(*args, **kwargs)


__all__ = ["CapitalClient"]
