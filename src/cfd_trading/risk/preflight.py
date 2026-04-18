"""Validates Claude proposal JSON against strategy YAML risk bounds."""

from dataclasses import dataclass, field


@dataclass
class PreflightResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def validate_entry_proposal(
    proposal: dict,
    strategy_config: dict,
    global_config: dict,
    open_positions_count: int = 0,
    margin_pct: float = 100.0,
) -> PreflightResult:
    """
    Validate a Claude entry proposal against strategy and global risk bounds.

    proposal       — full Claude JSON output (entry schema)
    strategy_config — loaded strategy YAML (e.g. momentum.yaml)
    global_config  — loaded risk.yaml global section
    open_positions_count — current number of open positions
    margin_pct     — current account margin as a percentage
    """
    violations: list[str] = []
    decision = proposal.get("decision", {})
    reasoning = proposal.get("reasoning", {})
    action = decision.get("action", "")

    _check_margin_floor(margin_pct, global_config, violations)
    _check_contra_indicators(reasoning, violations)

    if action not in ("OPEN", "CLOSE", "MODIFY", "NONE"):
        violations.append(f"Invalid action '{action}'. Must be OPEN, CLOSE, MODIFY, or NONE.")

    if action == "OPEN":
        _check_max_positions(open_positions_count, global_config, violations)
        _check_size(decision, strategy_config, violations)
        _check_stop_loss_entry(decision, strategy_config, global_config, violations)
        _check_trailing_stop(decision, strategy_config, violations)
        _check_rr_ratio(decision, strategy_config, violations)

    return PreflightResult(passed=len(violations) == 0, violations=violations)


def validate_monitor_decision(
    decision: dict,
    position: dict,
    strategy_config: dict,
    global_config: dict,
    margin_pct: float = 100.0,
) -> PreflightResult:
    """
    Validate a monitor cycle decision against risk bounds.

    decision       — Claude monitor JSON (action + optional stop_loss adjustment)
    position       — current position dict from Capital.com get_positions()
    strategy_config — loaded strategy YAML
    global_config  — loaded risk.yaml global section
    margin_pct     — current account margin as a percentage
    """
    violations: list[str] = []
    action = decision.get("action", "")

    _check_margin_floor(margin_pct, global_config, violations)

    valid_actions = ("HOLD", "ADJUST", "CLOSE")
    if action not in valid_actions:
        violations.append(f"Invalid monitor action '{action}'. Must be one of {valid_actions}.")
        return PreflightResult(passed=False, violations=violations)

    if action == "ADJUST":
        _check_stop_loss_monitor(decision, strategy_config, global_config, violations)
        _check_trailing_stop_ratchet(decision, position, violations)

    return PreflightResult(passed=len(violations) == 0, violations=violations)


# ---------------------------------------------------------------------------
# Internal check helpers
# ---------------------------------------------------------------------------

def _check_margin_floor(margin_pct: float, global_config: dict, violations: list) -> None:
    floor = global_config.get("margin_floor_pct", 20.0)
    if margin_pct < floor:
        violations.append(
            f"Account margin {margin_pct:.1f}% is below the floor of {floor:.1f}%. "
            "All trading halted."
        )


def _check_contra_indicators(reasoning: dict, violations: list) -> None:
    contra = reasoning.get("contra_indicators")
    if not contra or not str(contra).strip():
        violations.append(
            "Missing 'contra_indicators' in reasoning. Required field — must acknowledge opposing signals."
        )


def _check_max_positions(open_positions_count: int, global_config: dict, violations: list) -> None:
    max_pos = global_config.get("max_open_positions", 3)
    if open_positions_count >= max_pos:
        violations.append(
            f"Cannot open new position: already at maximum of {max_pos} open positions "
            f"({open_positions_count} currently open)."
        )


def _check_size(decision: dict, strategy_config: dict, violations: list) -> None:
    size = decision.get("size")
    if size is None:
        violations.append("Missing 'size' in decision.")
        return
    entry = strategy_config.get("entry", {})
    min_size = entry.get("min_size", 0.0)
    max_size = entry.get("max_size", float("inf"))
    if size < min_size:
        violations.append(f"Size {size} is below strategy minimum of {min_size}.")
    if size > max_size:
        violations.append(f"Size {size} exceeds strategy maximum of {max_size}.")


def _check_stop_loss_entry(
    decision: dict, strategy_config: dict, global_config: dict, violations: list
) -> None:
    sl = decision.get("stop_loss")
    if not sl:
        violations.append("Missing 'stop_loss' in decision. Required for all OPEN actions.")
        return

    pct = sl.get("pct_from_entry")
    if pct is None:
        violations.append("Missing 'stop_loss.pct_from_entry'. Cannot verify risk bounds.")
        return

    strategy_max = strategy_config.get("risk", {}).get("stop_loss", {}).get("max_pct", float("inf"))
    global_max = global_config.get("max_loss_pct_per_trade", float("inf"))

    if pct > strategy_max:
        violations.append(
            f"Stop loss {pct:.2f}% exceeds strategy maximum of {strategy_max:.2f}%."
        )
    if pct > global_max:
        violations.append(
            f"Stop loss {pct:.2f}% exceeds global hard ceiling of {global_max:.2f}%."
        )


def _check_stop_loss_monitor(
    decision: dict, strategy_config: dict, global_config: dict, violations: list
) -> None:
    sl = decision.get("stop_loss")
    if not sl:
        violations.append("ADJUST action requires 'stop_loss' in decision.")
        return

    pct = sl.get("pct_from_entry")
    if pct is not None:
        strategy_max = strategy_config.get("risk", {}).get("stop_loss", {}).get("max_pct", float("inf"))
        global_max = global_config.get("max_loss_pct_per_trade", float("inf"))
        if pct > strategy_max:
            violations.append(
                f"Adjusted stop loss {pct:.2f}% exceeds strategy maximum of {strategy_max:.2f}%."
            )
        if pct > global_max:
            violations.append(
                f"Adjusted stop loss {pct:.2f}% exceeds global hard ceiling of {global_max:.2f}%."
            )


def _check_trailing_stop(decision: dict, strategy_config: dict, violations: list) -> None:
    ts_decision = decision.get("trailing_stop", {})
    if not ts_decision or not ts_decision.get("enabled"):
        return

    ts_config = strategy_config.get("risk", {}).get("trailing_stop", {})
    if not ts_config.get("enabled", False):
        violations.append("Trailing stop requested but not enabled in strategy config.")
        return

    distance_pct = ts_decision.get("initial_distance_pct")
    if distance_pct is None:
        violations.append("Trailing stop enabled but 'initial_distance_pct' not specified.")
        return

    min_dist = ts_config.get("min_distance_pct", 0.0)
    max_dist = ts_config.get("max_distance_pct", float("inf"))
    if distance_pct < min_dist:
        violations.append(
            f"Trailing stop distance {distance_pct:.2f}% is below strategy minimum of {min_dist:.2f}%."
        )
    if distance_pct > max_dist:
        violations.append(
            f"Trailing stop distance {distance_pct:.2f}% exceeds strategy maximum of {max_dist:.2f}%."
        )


def _check_rr_ratio(decision: dict, strategy_config: dict, violations: list) -> None:
    """Check R:R ratio when both entry_level and take_profit.initial_value are present."""
    entry_level = decision.get("entry_level")
    tp = decision.get("take_profit", {})
    sl = decision.get("stop_loss", {})
    direction = decision.get("direction", "")

    if not entry_level or not tp or not sl:
        return  # not enough data to compute — skip
    tp_value = tp.get("initial_value")
    sl_value = sl.get("value")
    if tp_value is None or sl_value is None or entry_level is None:
        return

    if direction == "LONG":
        risk = entry_level - sl_value
        reward = tp_value - entry_level
    elif direction == "SHORT":
        risk = sl_value - entry_level
        reward = entry_level - tp_value
    else:
        return

    if risk <= 0:
        violations.append("Stop loss is on the wrong side of the entry price for the given direction.")
        return
    if reward <= 0:
        violations.append("Take profit is on the wrong side of the entry price for the given direction.")
        return

    rr = reward / risk
    min_rr = strategy_config.get("risk", {}).get("take_profit", {}).get("min_rr_ratio", 0.0)
    if rr < min_rr - 1e-9:  # epsilon for floating point equality at boundary
        violations.append(
            f"R:R ratio {rr:.2f} is below the strategy minimum of {min_rr:.2f}."
        )


def _check_trailing_stop_ratchet(decision: dict, position: dict, violations: list) -> None:
    """Trailing stop can only move in the profitable direction (ratchet rule)."""
    new_sl = decision.get("stop_loss", {}).get("value")
    if new_sl is None:
        return  # no SL adjustment proposed, skip

    pos_data = position.get("position", position)  # tolerate both raw and nested form
    current_sl = pos_data.get("stopLevel")
    direction = pos_data.get("direction", "")

    if current_sl is None:
        return  # no existing stop to compare against

    if direction == "BUY":  # LONG — stop must move up only
        if new_sl < current_sl:
            violations.append(
                f"Trailing stop ratchet violation: new stop {new_sl} would move below "
                f"current stop {current_sl} on a LONG position."
            )
    elif direction == "SELL":  # SHORT — stop must move down only
        if new_sl > current_sl:
            violations.append(
                f"Trailing stop ratchet violation: new stop {new_sl} would move above "
                f"current stop {current_sl} on a SHORT position."
            )
