"""Unit tests for strategy/loader.py."""

import pytest
from pathlib import Path
import yaml

from cfd_trading.strategy.loader import (
    load_strategy,
    list_strategies,
    load_base_prompt,
    load_scan_prompt,
    Strategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).parents[2] / "config"


def _write_strategy(tmp_path: Path, name: str, yaml_content: dict, md_content: str = "# prompt") -> Path:
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir(parents=True, exist_ok=True)
    (strategies_dir / f"{name}.yaml").write_text(yaml.dump(yaml_content))
    (strategies_dir / f"{name}.md").write_text(md_content)
    return tmp_path


def _minimal_yaml(overrides: dict | None = None) -> dict:
    base = {
        "name": "test",
        "description": "Test strategy",
        "entry": {"min_size": 0.1, "max_size": 5.0},
        "risk": {
            "target_risk_pct": 1.0,
            "stop_loss": {"type": "HARD", "default_pct": 2.0, "max_pct": 5.0},
            "trailing_stop": {"enabled": False},
            "take_profit": {"dynamic": False, "min_rr_ratio": 1.5, "max_pct": 10.0},
            "position_scaling": {"enabled": False},
            "time_exit": {"enabled": True, "close_minutes_before_session_end": 30},
        },
    }
    if overrides:
        base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Real strategies load correctly
# ---------------------------------------------------------------------------

def test_momentum_loads(tmp_path):
    result = load_strategy("momentum", CONFIG_DIR)
    assert isinstance(result, Strategy)
    assert result.name == "momentum"
    assert result.config["entry"]["max_size"] == 5.0
    assert len(result.prompt) > 100


def test_mean_reversion_loads(tmp_path):
    result = load_strategy("mean_reversion", CONFIG_DIR)
    assert isinstance(result, Strategy)
    assert result.name == "mean_reversion"
    assert result.config["risk"]["trailing_stop"]["enabled"] is False
    assert len(result.prompt) > 100


def test_both_strategies_have_descriptions():
    for name in ("momentum", "mean_reversion"):
        s = load_strategy(name, CONFIG_DIR)
        assert s.description, f"{name} should have a non-empty description"


def test_list_strategies_returns_both():
    names = list_strategies(CONFIG_DIR)
    assert "momentum" in names
    assert "mean_reversion" in names


def test_list_strategies_excludes_reserved():
    names = list_strategies(CONFIG_DIR)
    assert "_base" not in names
    assert "scan" not in names


# ---------------------------------------------------------------------------
# Missing files raise clear errors
# ---------------------------------------------------------------------------

def test_missing_yaml_raises_file_not_found(tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "ghost.md").write_text("# prompt")
    with pytest.raises(FileNotFoundError, match="Strategy YAML not found"):
        load_strategy("ghost", tmp_path)


def test_missing_md_raises_file_not_found(tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "ghost.yaml").write_text(yaml.dump(_minimal_yaml()))
    with pytest.raises(FileNotFoundError, match="Strategy prompt file not found"):
        load_strategy("ghost", tmp_path)


def test_nonexistent_strategy_raises(tmp_path):
    (tmp_path / "strategies").mkdir()
    with pytest.raises(FileNotFoundError):
        load_strategy("does_not_exist", tmp_path)


# ---------------------------------------------------------------------------
# Invalid YAML raises clear errors
# ---------------------------------------------------------------------------

def test_invalid_yaml_syntax_raises(tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "bad.yaml").write_text("key: [unclosed bracket")
    (strategies_dir / "bad.md").write_text("# prompt")
    with pytest.raises(ValueError, match="Invalid YAML"):
        load_strategy("bad", tmp_path)


def test_yaml_not_a_mapping_raises(tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "bad.yaml").write_text("- item1\n- item2\n")
    (strategies_dir / "bad.md").write_text("# prompt")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_strategy("bad", tmp_path)


# ---------------------------------------------------------------------------
# Missing required YAML fields raise clear errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("remove_field,remove_parent", [
    ("min_size", "entry"),
    ("max_size", "entry"),
    ("max_pct", "stop_loss"),
    ("enabled", "trailing_stop"),
    ("min_rr_ratio", "take_profit"),
    ("enabled", "time_exit"),
])
def test_missing_required_field_raises(tmp_path, remove_field, remove_parent):
    cfg = _minimal_yaml()
    # Remove the field from its parent
    if remove_parent in cfg:
        cfg[remove_parent].pop(remove_field, None)
    elif remove_parent in cfg.get("risk", {}):
        cfg["risk"][remove_parent].pop(remove_field, None)

    _write_strategy(tmp_path, "partial", cfg)
    with pytest.raises(ValueError, match="missing required fields"):
        load_strategy("partial", tmp_path)


# ---------------------------------------------------------------------------
# Base and scan prompts load correctly
# ---------------------------------------------------------------------------

def test_load_base_prompt_returns_content():
    prompt = load_base_prompt(CONFIG_DIR)
    assert len(prompt) > 100
    assert "contra_indicators" in prompt
    assert "stop_loss" in prompt


def test_load_scan_prompt_returns_content():
    prompt = load_scan_prompt(CONFIG_DIR)
    assert len(prompt) > 100
    assert "ATR" in prompt


def test_load_base_prompt_missing_raises(tmp_path):
    (tmp_path / "strategies").mkdir()
    with pytest.raises(FileNotFoundError, match="Base prompt not found"):
        load_base_prompt(tmp_path)


def test_load_scan_prompt_missing_raises(tmp_path):
    (tmp_path / "strategies").mkdir()
    with pytest.raises(FileNotFoundError, match="Scan prompt not found"):
        load_scan_prompt(tmp_path)


# ---------------------------------------------------------------------------
# Strategy dataclass contents
# ---------------------------------------------------------------------------

def test_strategy_prompt_is_non_empty_string(tmp_path):
    _write_strategy(tmp_path, "s", _minimal_yaml(), md_content="# My prompt\nSome content.")
    s = load_strategy("s", tmp_path)
    assert isinstance(s.prompt, str)
    assert len(s.prompt) > 0


def test_strategy_config_accessible_as_dict(tmp_path):
    _write_strategy(tmp_path, "s", _minimal_yaml())
    s = load_strategy("s", tmp_path)
    assert s.config["risk"]["stop_loss"]["max_pct"] == 5.0
