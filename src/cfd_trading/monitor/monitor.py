"""Rule-based position monitor — runs as a subprocess during an active session."""

import argparse
import json
import logging
import math
import os
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cfd_trading.storage.repository import OHLCBar
from cfd_trading.strategy.signal_engine import (
    IntradayContinuationSignalState, MeanReversionSignalState,
    MomentumSignalState, chandelier_stop,
)

logger = logging.getLogger("monitor")

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("SIGTERM received — finishing current cycle then exiting.")
    _shutdown = True


# ---------------------------------------------------------------------------
# Pure rule evaluation — no I/O, fully unit testable
# ---------------------------------------------------------------------------

def evaluate_position(
    position: dict,
    price_data: dict,
    strategy_config: dict,
    session_end_time: Optional[datetime] = None,
    *,
    now: Optional[datetime] = None,
    signal_state=None,
    entry_atr: Optional[float] = None,
    peak_price: Optional[float] = None,
    current_atr: Optional[float] = None,
) -> tuple[str, str, Optional[float]]:
    """
    Evaluate exit conditions for a single open position.

    Shared by the live monitor and the backtest engine — one ordered ruleset so
    the two cannot drift (SYSTEM_DESIGN §3.7 / §3.10).

    position        — one entry from Capital.com get_positions() response
    price_data      — one entry from get_prices() (latest bar or snapshot)
    strategy_config — loaded strategy YAML dict
    session_end_time — aware datetime for time-exit; None disables time exit
    now             — injected "current" time for the time-exit check; None →
                      datetime.now(UTC). Live passes nothing (real clock); the
                      backtest injects the bar timestamp. Live behaviour with
                      now=None is byte-for-byte unchanged.
    signal_state    — shared signal_engine state for rule 4 (signal-exit). None
                      → rule 4 skipped (price/time-only behaviour preserved).
    entry_atr       — ATR(14) captured at entry; used by the `fixed_atr` trail
                      mode (ratchet-only, fixed distance for the trade).
    peak_price      — best favourable price since entry (caller-tracked);
                      required for ATR trailing.
    current_atr     — ATR(14) recomputed for the current bar; used by the
                      `dynamic_chandelier` trail mode (may loosen on vol
                      expansion).

    Trail mode dispatch (`risk.trailing_stop.mode` in the strategy YAML):
      • `fixed_atr`        — distance = atr_multiplier × entry_atr, ratchet-only
      • `dynamic_chandelier` — distance = atr_multiplier × current_atr from
                                running extreme; MAY loosen (literature-faithful,
                                SYSTEM_DESIGN §3.7.1)
      • `fixed_pct`        — distance = min_distance_pct × bid/ask, ratchet-only
      • absent             — defaults to `fixed_atr` if atr_multiplier is set,
                                else `fixed_pct` (back-compat)

    Priority: 1 hard stop → 2 trailing → 3 take-profit → 4 signal-exit
    → 5 time-exit → HOLD.

    Returns (action, reason, new_stop_level):
        action         — "HOLD", "ADJUST", or "CLOSE"
        reason         — human-readable explanation logged to DB and audit file
        new_stop_level — new stop price for ADJUST actions, None otherwise
    """
    pos = position.get("position", position)
    direction = pos.get("direction", "")    # "BUY" or "SELL"
    stop_level = pos.get("stopLevel")
    profit_level = pos.get("profitLevel")

    # Capital.com uses "offer" for ask price; accept both for testability
    bid = price_data.get("bid")
    ask = price_data.get("offer", price_data.get("ask"))

    # 1. Hard stop — safety check first
    if stop_level is not None and bid is not None and ask is not None:
        if direction == "BUY" and bid <= stop_level:
            return "CLOSE", f"Hard stop: bid {bid} <= stop {stop_level}", None
        if direction == "SELL" and ask >= stop_level:
            return "CLOSE", f"Hard stop: ask {ask} >= stop {stop_level}", None

    # 2. Trailing stop — mode-dispatched (fixed_atr | dynamic_chandelier | fixed_pct)
    ts_config = strategy_config.get("risk", {}).get("trailing_stop", {})
    if ts_config.get("enabled") and stop_level is not None:
        atr_mult = ts_config.get("atr_multiplier")
        # Mode default: fixed_atr if atr_multiplier is set, else fixed_pct (back-compat)
        mode = ts_config.get("mode") or ("fixed_atr" if atr_mult is not None else "fixed_pct")

        if mode == "dynamic_chandelier":
            # Per-bar Chandelier — peak-anchored, current_atr recomputed each
            # bar. MAY loosen on vol expansion (SYSTEM_DESIGN §3.7.1). Uses
            # the shared chandelier_stop() pure function — engine + monitor
            # parity by construction.
            if (atr_mult is not None and current_atr is not None
                    and peak_price is not None):
                candidate = chandelier_stop(direction, peak_price, current_atr, atr_mult)
                if abs(candidate - stop_level) > 1e-9:
                    arrow = "↑" if candidate > stop_level else "↓"
                    return (
                        "ADJUST",
                        f"Chandelier {direction} {arrow}: {stop_level} → {candidate} "
                        f"(peak={peak_price}, atr={current_atr:.5f}×{atr_mult})",
                        candidate,
                    )

        elif mode == "fixed_atr":
            # ATR trailing fixed-at-entry — ratchet-only (legacy momentum path).
            # If entry_atr / peak are unavailable this cycle, simply do not trail
            # (never silently fall back to fixed-% — that would be live↔backtest drift).
            if entry_atr is not None and peak_price is not None and atr_mult is not None:
                distance = atr_mult * entry_atr
                if direction == "BUY":
                    candidate = round(peak_price - distance, 5)
                    if candidate > stop_level:
                        return (
                            "ADJUST",
                            f"Trailing stop ratchet LONG (ATR): {stop_level} → {candidate} "
                            f"(peak={peak_price}, dist={distance:.5f}=ATR{entry_atr:.5f}×{atr_mult})",
                            candidate,
                        )
                elif direction == "SELL":
                    candidate = round(peak_price + distance, 5)
                    if candidate < stop_level:
                        return (
                            "ADJUST",
                            f"Trailing stop ratchet SHORT (ATR): {stop_level} → {candidate} "
                            f"(peak={peak_price}, dist={distance:.5f}=ATR{entry_atr:.5f}×{atr_mult})",
                            candidate,
                        )

        elif mode == "fixed_pct" and bid is not None and ask is not None:
            distance_pct = ts_config.get("min_distance_pct", 1.0)
            if direction == "BUY":
                candidate = round(bid * (1 - distance_pct / 100), 5)
                if candidate > stop_level:
                    return (
                        "ADJUST",
                        f"Trailing stop ratchet LONG: {stop_level} → {candidate} "
                        f"(bid={bid}, dist={distance_pct}%)",
                        candidate,
                    )
            elif direction == "SELL":
                candidate = round(ask * (1 + distance_pct / 100), 5)
                if candidate < stop_level:
                    return (
                        "ADJUST",
                        f"Trailing stop ratchet SHORT: {stop_level} → {candidate} "
                        f"(ask={ask}, dist={distance_pct}%)",
                        candidate,
                    )

    # 3. Take profit
    if profit_level is not None and bid is not None and ask is not None:
        if direction == "BUY" and bid >= profit_level:
            return "CLOSE", f"Take profit: bid {bid} >= target {profit_level}", None
        if direction == "SELL" and ask <= profit_level:
            return "CLOSE", f"Take profit: ask {ask} <= target {profit_level}", None

    # 4. Signal-exit — deterministic per-strategy signal reversal (SYSTEM_DESIGN §3.7)
    if signal_state is not None:
        sig_reason = signal_state.check_exit()
        if sig_reason:
            return "CLOSE", sig_reason, None

    # 5. Time exit
    if session_end_time is not None:
        te_config = strategy_config.get("risk", {}).get("time_exit", {})
        if te_config.get("enabled"):
            close_min = te_config.get("close_minutes_before_session_end", 30)
            _now = now if now is not None else datetime.now(timezone.utc)
            minutes_left = (session_end_time - _now).total_seconds() / 60
            if minutes_left <= close_min:
                return (
                    "CLOSE",
                    f"Time exit: {minutes_left:.1f} min remaining <= {close_min} min threshold",
                    None,
                )

    return "HOLD", "No exit conditions met", None


# ---------------------------------------------------------------------------
# I/O layer — network + DB calls
# ---------------------------------------------------------------------------

def _load_strategy_for_position(deal_id: str, conn, config_dir: Path):
    """Look up the strategy name from trades DB and load the YAML config."""
    from cfd_trading.storage.repository import get_trade_by_broker_ref
    from cfd_trading.strategy.loader import load_strategy

    trade = get_trade_by_broker_ref(conn, deal_id)
    if trade is None or not trade["strategy"]:
        logger.warning(f"No trade record found for deal_id={deal_id} — skipping position.")
        return None
    try:
        return load_strategy(trade["strategy"], config_dir)
    except Exception as e:
        logger.error(f"Failed to load strategy '{trade['strategy']}' for {deal_id}: {e}")
        return None


def _write_audit(audit_path: Path, entry: dict) -> None:
    with audit_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Per-position signal-engine state — strategies whose exit path needs streaming
# state in the monitor. ORB has no state (check_exit→None, trailing disabled).
# intraday_continuation needs state for the dynamic Chandelier trail's
# current_atr (recomputed from closed bars each cycle).
# ---------------------------------------------------------------------------

_SIGNAL_CLS = {
    "momentum": MomentumSignalState,
    "mean_reversion": MeanReversionSignalState,
    "intraday_continuation": IntradayContinuationSignalState,
}


def _signal_kwargs_for(strategy_name: str, epic: str) -> dict:
    """Per-strategy/per-epic signal_state ctor kwargs.

    `intraday_continuation` needs the per-epic session_open (UTC) to identify
    the session-open bar — pooled US500+DE40+UK100 evaluates on each instrument
    with its own session open. Defaults to (8, 0) for unknown epics, matching
    `backtest/sessions.session_open_utc`.
    """
    if strategy_name == "intraday_continuation":
        from cfd_trading.backtest.sessions import session_open_utc
        h, m = session_open_utc(epic)
        return {"session_open_hour": h, "session_open_minute": m}
    return {}
_RES_CODE = {"M1": "MINUTE", "M5": "MINUTE_5", "M15": "MINUTE_15",
             "M30": "MINUTE_30", "M60": "HOUR", "H1": "HOUR"}
_RES_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
                "M60": 3600, "H1": 3600}
_WARMUP_BARS = 80   # ≥ momentum _MIN_BARS(22)+ADX(~28)+M30(30); MR needs ~48


@dataclass
class _PosState:
    signal_state: object
    entry_atr: Optional[float]
    peak_price: Optional[float]
    direction: str
    last_bar_ts: int


# dealId → _PosState, kept for the monitor process lifetime; deterministically
# re-seeded from a warm-up backfill on first sighting or after a restart.
_POSITION_STATE: dict[str, _PosState] = {}


def _parse_snapshot_ts(p: dict) -> int:
    s = (p.get("snapshotTimeUTC") or p.get("snapshotTime") or "").replace("/", "-")
    s = s[:19]  # trim millis/offset
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


def _bars_from_prices(epic: str, resolution: str, prices: list) -> list[OHLCBar]:
    """Capital.com price records → bid-side OHLCBars (matches the backtest DB)."""
    bars: list[OHLCBar] = []
    for p in prices:
        try:
            o = p["openPrice"]["bid"]; h = p["highPrice"]["bid"]
            lo = p["lowPrice"]["bid"]; c = p["closePrice"]["bid"]
        except (KeyError, TypeError):
            continue
        if None in (o, h, lo, c):
            continue
        bars.append(OHLCBar(epic=epic, resolution=resolution,
                            ts=_parse_snapshot_ts(p), open=o, high=h, low=lo,
                            close=c, volume=p.get("lastTradedVolume", 0) or 0))
    bars.sort(key=lambda b: b.ts)
    return bars


def _warmup_state(client, epic: str, strategy, direction: str,
                  entry_ts: int, entry_price: float) -> Optional[_PosState]:
    """Replay a warm-up window through the SHARED signal_engine to rebuild the
    streaming state, snapshotting entry_atr at the entry bar (== backtest)."""
    cls = _SIGNAL_CLS.get(strategy.name)
    if cls is None:
        return None
    res = strategy.resolution
    code, secs = _RES_CODE.get(res), _RES_SECONDS.get(res)
    if code is None or secs is None:
        logger.error(f"Unknown resolution {res!r} for {strategy.name}; no signal state")
        return None
    now = int(datetime.now(timezone.utc).timestamp())
    span = max(0, math.ceil((now - entry_ts) / secs))
    need = min(1000, _WARMUP_BARS + span + 5)
    resp = client.get_prices(epic, resolution=code, max=need)
    if "error" in resp:
        logger.error(f"warmup get_prices failed {epic}: {resp['error']}")
        return None
    bars = _bars_from_prices(epic, res, resp.get("prices", []))
    if len(bars) < 5:
        logger.warning(f"warmup: too few bars for {epic} ({len(bars)})")
        return None
    st = cls(**_signal_kwargs_for(strategy.name, epic))
    entry_atr: Optional[float] = None
    peak = entry_price
    entered = False
    for b in bars:
        st.update(b)
        if not entered and b.ts >= entry_ts:
            entry_atr = st.atr
            st.notify_entry(direction)
            peak = entry_price
            entered = True
        elif entered:
            peak = max(peak, b.high) if direction == "BUY" else min(peak, b.low)
    if not entered:                       # position opened after the last bar
        entry_atr = st.atr
        st.notify_entry(direction)
        peak = entry_price
    return _PosState(signal_state=st, entry_atr=entry_atr, peak_price=peak,
                     direction=direction, last_bar_ts=bars[-1].ts)


def run_cycle(
    client,
    conn,
    config_dir: Path,
    session_id: str,
    session_end_time: Optional[datetime],
    audit_path: Path,
) -> None:
    """Run one monitor cycle: fetch all open positions and evaluate each."""
    from cfd_trading.storage import repository as repo

    positions_response = client.get_positions()
    if "error" in positions_response:
        logger.error(f"get_positions failed: {positions_response['error']}")
        return

    positions = positions_response.get("positions", [])
    if not positions:
        logger.debug("No open positions — nothing to evaluate.")
        return

    for position in positions:
        pos_data = position.get("position", {})
        market_data = position.get("market", {})
        deal_id = pos_data.get("dealId", "")
        epic = market_data.get("epic", "")

        strategy = _load_strategy_for_position(deal_id, conn, config_dir)
        if strategy is None:
            continue

        res = strategy.resolution
        code = _RES_CODE.get(res, "MINUTE")
        sig_state = entry_atr = peak_price = None

        # First sighting / restart of a momentum|MR position → warm-up backfill
        if strategy.name in _SIGNAL_CLS and deal_id not in _POSITION_STATE:
            trade = repo.get_trade_by_broker_ref(conn, deal_id)
            if trade is None:
                logger.warning(f"No trade record for {deal_id}; price/time rules only.")
            else:
                try:
                    entry_ts = int(datetime.fromisoformat(trade["ts"]).timestamp())
                except (ValueError, TypeError):
                    entry_ts = int(datetime.now(timezone.utc).timestamp())
                ps0 = _warmup_state(client, epic, strategy,
                                    pos_data.get("direction", ""),
                                    entry_ts, trade["entry_price"])
                if ps0 is not None:
                    _POSITION_STATE[deal_id] = ps0

        prices_response = client.get_prices(epic, resolution=code, max=3)
        if "error" in prices_response:
            logger.error(f"get_prices failed for {epic}: {prices_response['error']}")
            continue
        raw = prices_response.get("prices", [])
        if not raw:
            logger.warning(f"No price data returned for {epic}.")
            continue

        # Advance the streaming state with any bars newer than last seen
        ps = _POSITION_STATE.get(deal_id)
        if ps is not None:
            for b in _bars_from_prices(epic, res, raw):
                if b.ts > ps.last_bar_ts:
                    ps.signal_state.update(b)
                    ps.peak_price = (max(ps.peak_price, b.high)
                                     if ps.direction == "BUY"
                                     else min(ps.peak_price, b.low))
                    ps.last_bar_ts = b.ts
            sig_state, entry_atr, peak_price = (
                ps.signal_state, ps.entry_atr, ps.peak_price)
        # current_atr — recomputed from closed bars each cycle; powers the
        # dynamic_chandelier trail mode. Strategies without an .atr property
        # (e.g. ORB) yield None, leaving the dispatch a no-op.
        current_atr = getattr(sig_state, "atr", None) if sig_state is not None else None

        # Current bid/ask from the latest raw bar's close
        last_raw = raw[-1]
        price_data = {
            "bid": last_raw.get("closePrice", {}).get("bid"),
            "offer": last_raw.get("closePrice", {}).get("ask"),
        }

        action, reason, new_stop = evaluate_position(
            position, price_data, strategy.config, session_end_time,
            now=datetime.now(timezone.utc),
            signal_state=sig_state,
            entry_atr=entry_atr,
            peak_price=peak_price,
            current_atr=current_atr,
        )

        logger.info(f"{epic} ({deal_id[:8]}…) → {action}: {reason}")

        # Write cycle snapshot for every position on every cycle
        repo.save_cycle_snapshot(
            conn,
            session_id=session_id,
            asset=epic,
            strategy=strategy.name,
            account_bal=None,
            positions=[pos_data],
            market_data={"price": price_data, "action": action, "reason": reason},
        )

        if action == "ADJUST" and new_stop is not None:
            result = client.update_position(deal_id, stop_level=new_stop)
            if "error" not in result:
                trade = repo.get_trade_by_broker_ref(conn, deal_id)
                if trade:
                    repo.update_trade_stop_loss(conn, trade["id"], new_stop)
                _write_audit(audit_path, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "session_id": session_id,
                    "deal_id": deal_id,
                    "asset": epic,
                    "action": "ADJUST",
                    "reason": reason,
                    "new_stop": new_stop,
                })
            else:
                logger.error(f"update_position failed for {deal_id}: {result['error']}")

        elif action == "CLOSE":
            result = client.close_position(deal_id)
            if "error" not in result:
                trade = repo.get_trade_by_broker_ref(conn, deal_id)
                if trade:
                    repo.update_trade_status(conn, trade["id"], "EXECUTED")
                _ps = _POSITION_STATE.pop(deal_id, None)
                if _ps is not None:
                    _ps.signal_state.notify_exit()
                _write_audit(audit_path, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "session_id": session_id,
                    "deal_id": deal_id,
                    "asset": epic,
                    "action": "CLOSE",
                    "reason": reason,
                    "new_stop": None,
                })
            else:
                logger.error(f"close_position failed for {deal_id}: {result['error']}")


def run_loop(
    client,
    conn,
    config_dir: Path,
    session_id: str,
    session_end_time: Optional[datetime],
    audit_path: Path,
    interval_seconds: int,
) -> None:
    """Main monitor loop — runs until SIGTERM or session end."""
    global _shutdown
    logger.info(
        f"Monitor started. Session={session_id}, interval={interval_seconds}s, "
        f"session_end={session_end_time}"
    )
    while not _shutdown:
        try:
            run_cycle(client, conn, config_dir, session_id, session_end_time, audit_path)
        except Exception as e:
            logger.error(f"Unhandled error in monitor cycle: {e}", exc_info=True)

        # Sleep in small increments so SIGTERM is handled promptly
        for _ in range(interval_seconds):
            if _shutdown:
                break
            import time
            time.sleep(1)

    logger.info("Monitor shut down cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="CFD Trading rule-based monitor")
    p.add_argument("--session-id", required=True)
    p.add_argument("--db-path", required=True)
    p.add_argument("--config-dir", required=True)
    p.add_argument("--audit-log", required=True)
    p.add_argument("--session-end", default=None,
                   help="ISO8601 UTC datetime for time-exit rule, e.g. 2026-04-18T17:00:00+00:00")
    p.add_argument("--interval", type=int,
                   default=int(os.getenv("MONITOR_INTERVAL_SECONDS", "60")))
    return p.parse_args(argv)


def main(argv=None):
    from cfd_trading.storage.db import get_connection, init_db
    from cfd_trading.broker.capital_client import CapitalClient

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_sigterm)

    args = _parse_args(argv)

    session_end_time = None
    if args.session_end:
        session_end_time = datetime.fromisoformat(args.session_end)

    init_db(args.db_path)
    conn = get_connection(args.db_path)

    client = CapitalClient()
    if not client.authenticate():
        logger.error("Monitor: authentication with Capital.com failed. Exiting.")
        sys.exit(1)

    run_loop(
        client=client,
        conn=conn,
        config_dir=Path(args.config_dir),
        session_id=args.session_id,
        session_end_time=session_end_time,
        audit_path=Path(args.audit_log),
        interval_seconds=args.interval,
    )


if __name__ == "__main__":
    main()
