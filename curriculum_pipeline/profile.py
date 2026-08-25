"""Load and save Profile from JSON or YAML files."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .schemas import Profile


def load_profile(path: str | Path) -> Profile:
    """Load a Profile from .json, .yaml, or .yml."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Profile not found: {p}")

    suffix = p.suffix.lower()
    text = p.read_text(encoding="utf-8")

    if suffix == ".json":
        data = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        # Try JSON first, then YAML
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ValueError(f"Profile root must be a mapping, got {type(data).__name__}")

    return Profile.model_validate(data)


def save_profile(profile: Profile, path: str | Path) -> None:
    """Save a Profile to .json or .yaml based on file extension."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(exclude_none=True)

    suffix = p.suffix.lower()
    if suffix == ".json":
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    elif suffix in {".yaml", ".yml"}:
        p.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    else:
        raise ValueError(f"Unsupported profile extension: {suffix}")
