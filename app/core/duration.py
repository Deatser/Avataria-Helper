# app/core/duration.py
"""Parsing and formatting "H:MM:SS" — the shape the gardener's timer badge
reads in, and the shape clean_next_time is stored in."""
from __future__ import annotations


def to_seconds(text: str) -> int | None:
    """"H:MM:SS" -> total seconds, or None if it isn't that shape."""
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def from_seconds(total: int) -> str:
    """Total seconds -> "H:MM:SS", the same shape to_seconds parses."""
    total = max(0, total)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"
