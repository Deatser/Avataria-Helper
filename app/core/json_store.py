# app/core/json_store.py
"""Load and save a nested dataclass as JSON, keeping defaults for the rest.

Shared by the config and the stats files: both are user-editable JSON that
has to survive being hand-edited, half-written by an older version, or
corrupted outright, and in every one of those cases the missing pieces come
from the dataclass defaults rather than blowing up at startup.
"""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path


def merge_into(instance, raw: dict):
    """Recursively merge a raw dict into a dataclass, coercing types.

    Unknown keys are ignored and values that cannot be coerced keep the
    default — a typo in the file costs one field, not the whole load.
    """
    for key, val in raw.items():
        if not hasattr(instance, key):
            continue
        current = getattr(instance, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(val, dict):
            merge_into(current, val)
        else:
            try:
                setattr(instance, key, type(current)(val))
            except (TypeError, ValueError):
                pass  # keep default on type mismatch


def load_dataclass(path: Path, factory):
    """Read `path` into a fresh `factory()`; all defaults if it is unusable."""
    default = factory()
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return factory()
    merge_into(default, raw)
    return default


def save_dataclass(path: Path, data):
    path.write_text(
        json.dumps(asdict(data), indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
