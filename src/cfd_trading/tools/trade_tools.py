"""MCP tools: validate_proposal, execute_trade."""

import json
import uuid
from datetime import datetime, timezone

import yaml

from cfd_trading.risk.preflight import validate_entry_proposal
from cfd_trading.strategy.loader import load_strategy
from cfd_trading.storage import repository as repo
from cfd_trading.tools._state import require_state


def validate_proposal(proposal_json: str) -> str:
    """
    Run preflight checks on a trade proposal against strategy and global risk bounds.

    proposal_json — JSON string matching the entry proposal schema from _base.md.

    Returns pass/fail result with specific violation messages.
    """
    state = require_state()

    try:
        proposal = json.loads(proposal_json)
    except json.JSONDecodeError as e:
        return json.dumps({"passed": False, "violations": [f"Invalid JSON: {e}"]})

    strategy_name = proposal.get("strategy", "")
    if not strategy_name:
        return json.dumps({"passed": False, "violations": ["Missing 'strategy' field in proposal."]})

    try:
        strat = load_strategy(strategy_name, state.config_dir)
    except (FileNotFoundError, ValueError) as e:
        return json.dumps({"passed": False, "violations": [f"Strategy load failed: {e}"]})

    global_config = _load_global_config(state.config_dir)

    positions_resp = state.client.get_positions()
    open_positions = positions_resp.get("positions", [])
    open_count = len(open_positions)

    account_info = state.client.get_account_info()
    margin_pct = _extract_margin_pct(account_info)

    result = validate_entry_proposal(
        proposal=proposal,
        strategy_config=strat.config,
        global_config=global_config,
        open_positions_count=open_count,
        margin_pct=margin_pct,
    )

    return json.dumps({
        "passed": result.passed,
        "violations": result.violations,
        "open_positions_count": open_count,
        "margin_pct": margin_pct,
    })


def execute_trade(proposal_json: str) -> str:
    """
    Execute a trade proposal: preflight → create position → confirm → log to DB.

    proposal_json — JSON string of an approved trade proposal.

    Returns deal details on success, or rejection details if preflight fails.
    The proposal must have already been shown to the human and approved before calling this.
    """
    state = require_state()

    try:
        proposal = json.loads(proposal_json)
    except json.JSONDecodeError as e:
        return json.dumps({"status": "error", "message": f"Invalid JSON: {e}"})

    # Belt-and-suspenders preflight — always re-run before execution
    strategy_name = proposal.get("strategy", "")
    try:
        strat = load_strategy(strategy_name, state.config_dir)
    except (FileNotFoundError, ValueError) as e:
        return json.dumps({"status": "error", "message": f"Strategy load failed: {e}"})

    global_config = _load_global_config(state.config_dir)
    positions_resp = state.client.get_positions()
    open_count = len(positions_resp.get("positions", []))
    margin_pct = _extract_margin_pct(state.client.get_account_info())

    preflight = validate_entry_proposal(
        proposal=proposal,
        strategy_config=strat.config,
        global_config=global_config,
        open_positions_count=open_count,
        margin_pct=margin_pct,
    )
    if not preflight.passed:
        return json.dumps({
            "status": "rejected",
            "message": "Preflight failed — trade not submitted to broker.",
            "violations": preflight.violations,
        })

    decision = proposal.get("decision", {})
    asset = proposal.get("asset", "")
    direction_map = {"LONG": "BUY", "SHORT": "SELL"}
    direction = direction_map.get(decision.get("direction", ""), "")
    size = decision.get("size")
    sl = decision.get("stop_loss", {})
    tp = decision.get("take_profit", {})
    ts = decision.get("trailing_stop", {})

    stop_level = sl.get("value") if sl else None
    profit_level = tp.get("initial_value") if tp else None
    trailing_enabled = ts.get("enabled", False) if ts else False
    trailing_distance_pct = ts.get("initial_distance_pct") if ts else None

    create_kwargs: dict = {"epic": asset, "direction": direction, "size": size}
    if trailing_enabled and trailing_distance_pct and stop_level:
        # Capital.com trailing stop requires stop_distance (in price points), not stop_level
        create_kwargs["trailing_stop"] = True
        create_kwargs["stop_distance"] = round(abs(stop_level), 5) if stop_level else None
    elif stop_level is not None:
        create_kwargs["stop_level"] = stop_level

    if profit_level is not None:
        create_kwargs["profit_level"] = profit_level

    create_result = state.client.create_position(**create_kwargs)
    if "error" in create_result:
        return json.dumps({
            "status": "error",
            "message": f"Broker rejected order: {create_result['error']}",
            "details": create_result.get("details"),
        })

    deal_ref = create_result.get("dealReference")
    confirm = state.client.confirm_deal(deal_ref) if deal_ref else {}
    deal_id = confirm.get("dealId")

    cycle_id = proposal.get("cycle_id") or str(uuid.uuid4())
    trade_id = repo.save_trade(
        conn=state.conn,
        session_id=state.session_id,
        cycle_id=cycle_id,
        asset=asset,
        strategy=strategy_name,
        direction=decision.get("direction", ""),
        size=size,
        entry_price=confirm.get("level"),
        stop_loss=stop_level,
        take_profit=profit_level,
        broker_ref=deal_id,
    )
    repo.update_trade_status(state.conn, trade_id, "EXECUTED")

    repo.save_reasoning_trace(
        conn=state.conn,
        session_id=state.session_id,
        cycle_id=cycle_id,
        prompt_tokens=0,
        output_tokens=0,
        reasoning=json.dumps(proposal.get("reasoning", {})),
        tool_calls=[{"tool": "execute_trade", "proposal": proposal}],
    )

    return json.dumps({
        "status": "executed",
        "trade_id": trade_id,
        "deal_reference": deal_ref,
        "deal_id": deal_id,
        "asset": asset,
        "direction": decision.get("direction"),
        "size": size,
        "entry_price": confirm.get("level"),
        "stop_level": stop_level,
        "profit_level": profit_level,
        "confirm_status": confirm.get("dealStatus"),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_global_config(config_dir) -> dict:
    path = config_dir / "risk.yaml"
    with open(path) as f:
        return yaml.safe_load(f).get("global", {})


def _extract_margin_pct(account_info: dict) -> float:
    accounts = account_info.get("accounts", [])
    if accounts:
        bal = accounts[0].get("balance", {})
        deposit = bal.get("deposit", 0)
        available = bal.get("available", 0)
        total = deposit + available
        if total > 0:
            return round(available / total * 100, 1)
    return 100.0
