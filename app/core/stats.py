# app/core/stats.py
"""Everything the helper counts about who is using it and what they farmed.

Kept apart from config.json on purpose: config is settings the user changes,
this is a record that accumulates. Mixing them would mean a reset of one
wiping the other.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from app.core.json_store import load_dataclass, save_dataclass


@dataclass
class PlayerStats:
    # Kept as text, like the other two: an ID that has not been issued yet
    # shows as a placeholder, which a numeric 0 could not do.
    player_id: str = ""
    player_name: str = ""
    registration_date: str = ""


@dataclass
class AvaDancersStats:
    games_played: int = 0
    gold_won: int = 0
    silver_won: int = 0


@dataclass
class AppStats:
    player: PlayerStats = field(default_factory=PlayerStats)
    ava_dancers: AvaDancersStats = field(default_factory=AvaDancersStats)


def shown(value, field_name: str) -> str:
    """Value for display, or %field_name% while nothing has been filled in.

    Only blanks get the placeholder treatment. A counter reading 0 is a real
    answer — nothing farmed yet — and dressing it up as "unset" would hide
    the difference between a fresh install and a run that scored nothing.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"%{field_name}%"
    return str(value)


class StatsManager:
    STATS_FILE = Path("stats.json")

    def __init__(self):
        self.data = load_dataclass(self.STATS_FILE, AppStats)

    def reload(self):
        """Pick up hand edits to stats.json without restarting the helper."""
        self.data = load_dataclass(self.STATS_FILE, AppStats)

    def save(self):
        save_dataclass(self.STATS_FILE, self.data)

    # ── Recording ────────────────────────────────────────────────────────────

    def record_ava_dancers_run(self, gold: int = 0, silver: int = 0):
        """One finished Ava Dancers round and what it paid out."""
        stats = self.data.ava_dancers
        stats.games_played += 1
        stats.gold_won     += max(0, int(gold))
        stats.silver_won   += max(0, int(silver))
        self.save()
