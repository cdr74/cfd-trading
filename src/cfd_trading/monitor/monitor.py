"""Rule-based position monitor — runs as a subprocess during an active session."""

import argparse
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
) -> tuple[str, str, Optional[float]]:
    """
    Evaluate exit conditions for a single open position.

    position       — one entry from Capital.com get_positions() response
    price_data     — one entry from Capital.com get_prices() response (latest bar or snapshot)
    strategy_config — loaded strategy YAML dict
    session_end_time — aware datetime for time-exit check; None disables time exit

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

    # 2. Trailing stop ratchet
    ts_config = strategy_config.get("risk", {}).get("trailing_stop", {})
    if ts_config.get("enabled") and stop_level is not None and bid is not None and ask is not None:
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

    # 4. Time exit
    if session_end_time is not None:
        te_config = strategy_config.get("risk", {}).get("time_exit", {})
        if te_config.get("enabled"):
            close_min = te_config.get("close_minutes_before_session_end", 30)
            now = datetime.now(timezone.utc)
            minutes_left = (session_end_time - now).total_seconds() / 60
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

        prices_response = client.get_prices(epic, resolution="MINUTE", max=1)
        if "error" in prices_response:
            logger.error(f"get_prices failed for {epic}: {prices_response['error']}")
            continue

        prices = prices_response.get("prices", [])
        if not prices:
            logger.warning(f"No price data returned for {epic}.")
            continue

        # Use latest bar's close prices as current bid/ask approximation
        latest = prices[-1]
        price_data = {
            "bid": latest.get("closePrice", {}).get("bid"),
            "offer": latest.get("closePrice", {}).get("ask"),
        }

        action, reason, new_stop = evaluate_position(
            position, price_data, strategy.config, session_end_time
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
