# app/core/stats.py
"""Everything the helper counts about who is using it and what they farmed.

Kept apart from config.json on purpose: config is settings the user changes,
this is a record that accumulates. Mixing them would mean a reset of one
wiping the other.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.duration import from_seconds, to_seconds
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
class GardenerStats:
    shifts_finished: int = 0
    # When the garden next becomes cleanable — personal history, not a
    # setting, so it lives here rather than in config. clean_next_time is
    # the "H:MM:SS" duration read off the badge; clean_was_time is when
    # that reading was actually true, in wall-clock time. Together they let
    # the remaining time be recomputed correctly no matter how long the mod
    # was closed for — see StatsManager.gardener_remaining_seconds.
    clean_next_time: str = ""
    clean_was_time: str = ""


@dataclass
class JanitorStats:
    # Same shape as GardenerStats, and for the same reason — Уборщик gets
    # its own independent cooldown, not a shared one with Садовник.
    shifts_finished: int = 0
    clean_next_time: str = ""
    clean_was_time: str = ""


@dataclass
class AppStats:
    player: PlayerStats = field(default_factory=PlayerStats)
    ava_dancers: AvaDancersStats = field(default_factory=AvaDancersStats)
    gardener: GardenerStats = field(default_factory=GardenerStats)
    janitor: JanitorStats = field(default_factory=JanitorStats)


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

    def record_gardener_cleanup(self):
        """One finished garden — the count Statistics shows next to
        Садовник's own countdown."""
        self.data.gardener.shifts_finished += 1
        self.save()

    def record_janitor_cleanup(self):
        """The same, for Уборщик's own count."""
        self.data.janitor.shifts_finished += 1
        self.save()

    def set_gardener_next_time(self, clean_next_time: str):
        """What the timer badge itself reads — overwrites whatever was
        read last, and stamps the moment it was true so the countdown can
        be recomputed correctly however much later it is actually looked
        at (see gardener_remaining_seconds)."""
        self._set_next_time(self.data.gardener, clean_next_time)

    def set_janitor_next_time(self, clean_next_time: str):
        self._set_next_time(self.data.janitor, clean_next_time)

    def gardener_remaining_seconds(self) -> int:
        """How much of clean_next_time is actually left right now — the
        mod does not run while it is closed, so this is worked out from
        wall-clock time elapsed since clean_was_time rather than trusting
        clean_next_time to have kept ticking down on its own."""
        return self._remaining_seconds(self.data.gardener)

    def janitor_remaining_seconds(self) -> int:
        return self._remaining_seconds(self.data.janitor)

    def sync_gardener_countdown(self) -> int:
        """gardener_remaining_seconds, but also writes that live value
        back to clean_next_time and re-anchors clean_was_time to now.

        Meant to be called from exactly one place, once a second — the
        stats window's own redraw timer — so stats.json shows the same
        number the window does instead of only being correct if worked
        out by hand from clean_was_time. Idempotent at any call rate:
        each call re-anchors from true elapsed wall time, so calling it
        twice in the same second just re-derives the same answer rather
        than double-counting.
        """
        return self._sync_countdown(self.data.gardener)

    def sync_janitor_countdown(self) -> int:
        """The same, for Уборщик's own — independent — cooldown."""
        return self._sync_countdown(self.data.janitor)

    # ── Cooldown math, shared by Садовник and Уборщик ───────────────────────
    # Both GardenerStats and JanitorStats carry the same clean_next_time /
    # clean_was_time pair, so the anchor-and-recompute logic is written once
    # and each module's public method just points it at its own dataclass.

    def _set_next_time(self, entity, clean_next_time: str):
        entity.clean_next_time = clean_next_time
        entity.clean_was_time = datetime.now().isoformat()
        self.save()

    def _remaining_seconds(self, entity) -> int:
        seconds = to_seconds(entity.clean_next_time)
        if seconds is None:
            return 0
        if not entity.clean_was_time:
            return seconds
        try:
            was = datetime.fromisoformat(entity.clean_was_time)
        except ValueError:
            return seconds
        elapsed = int((datetime.now() - was).total_seconds())
        return max(0, seconds - elapsed)

    def _sync_countdown(self, entity) -> int:
        remaining = self._remaining_seconds(entity)
        entity.clean_next_time = from_seconds(remaining)
        entity.clean_was_time = datetime.now().isoformat()
        self.save()
        return remaining
