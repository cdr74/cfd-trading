"""Discovers and validates strategy YAML + MD pairs from config/strategies/."""

from dataclasses import dataclass
from pathlib import Path

import yaml


REQUIRED_YAML_FIELDS = [
    ("entry", "min_size"),
    ("entry", "max_size"),
    ("risk", "target_risk_pct"),
    ("risk", "stop_loss", "max_pct"),
    ("risk", "trailing_stop", "enabled"),
    ("risk", "take_profit", "min_rr_ratio"),
    ("risk", "time_exit", "enabled"),
]

# Names that exist in config/strategies/ but are not tradeable strategies
_RESERVED_NAMES = {"_base", "scan"}


@dataclass
class Strategy:
    name: str
    description: str
    config: dict        # parsed YAML contents
    prompt: str         # contents of <name>.md


def load_strategy(name: str, config_dir: Path) -> Strategy:
    """Load and validate a strategy YAML + MD pair by name."""
    yaml_path = config_dir / "strategies" / f"{name}.yaml"
    md_path = config_dir / "strategies" / f"{name}.md"

    if not yaml_path.exists():
        raise FileNotFoundError(f"Strategy YAML not found: {yaml_path}")
    if not md_path.exists():
        raise FileNotFoundError(f"Strategy prompt file not found: {md_path}")

    with yaml_path.open() as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {yaml_path}: {e}") from e

    if not isinstance(config, dict):
        raise ValueError(f"Strategy YAML must be a mapping, got {type(config).__name__}: {yaml_path}")

    _validate_schema(config, yaml_path)

    prompt = md_path.read_text()

    return Strategy(
        name=name,
        description=config.get("description", ""),
        config=config,
        prompt=prompt,
    )


def list_strategies(config_dir: Path) -> list[str]:
    """Return names of all valid strategies, excluding reserved files (_base, scan)."""
    strategies_dir = config_dir / "strategies"
    return sorted(
        p.stem
        for p in strategies_dir.glob("*.yaml")
        if p.stem not in _RESERVED_NAMES
    )


def load_base_prompt(config_dir: Path) -> str:
    """Load _base.md — injected into every Claude Code context."""
    path = config_dir / "strategies" / "_base.md"
    if not path.exists():
        raise FileNotFoundError(f"Base prompt not found: {path}")
    return path.read_text()


def load_scan_prompt(config_dir: Path) -> str:
    """Load scan.md — injected into Claude Code context for market scan."""
    path = config_dir / "strategies" / "scan.md"
    if not path.exists():
        raise FileNotFoundError(f"Scan prompt not found: {path}")
    return path.read_text()


def _validate_schema(config: dict, path: Path) -> None:
    missing = []
    for field_path in REQUIRED_YAML_FIELDS:
        node = config
        for key in field_path:
            if not isinstance(node, dict) or key not in node:
                missing.append(".".join(field_path))
                break
            node = node[key]
    if missing:
        raise ValueError(
            f"Strategy YAML {path} is missing required fields: {', '.join(missing)}"
        )
