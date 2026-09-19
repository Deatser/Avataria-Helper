# app/core/tropikania_stats.py
"""Everything the Tropikania helper counts — kept in its own file, apart
from the main app's stats.json, the same way its config is kept apart in
tropikania_config.json.

No player section here: ID/имя/регистрация are the same player in both
games, so TropikaniaStatsWindow reads those three fields straight off the
main app's own StatsManager (app/core/stats.py, stats.json) instead of
keeping — and risking drifting from — a second copy.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from app.core.json_store import load_dataclass, save_dataclass
from app.core.paths import data_path
from app.core.tropikania_daily_log import TropikaniaDailyLog


@dataclass
class TropikaniaFarmStats:
    # +1 every time TropikaniaFarmLoop.confirmed fires — one full farm run,
    # start to finish (see TropikaniaOverlay._toggle_farm's confirmed hookup).
    blueberry_exp: int = 0


@dataclass
class TropikaniaAppStats:
    farm: TropikaniaFarmStats = field(default_factory=TropikaniaFarmStats)


class TropikaniaStatsManager:
    STATS_FILE = data_path("tropikania_stats.json")

    def __init__(self):
        self.data = load_dataclass(self.STATS_FILE, TropikaniaAppStats)
        self.daily = TropikaniaDailyLog()
        self.daily.ensure_today()

    def reload(self):
        self.data = load_dataclass(self.STATS_FILE, TropikaniaAppStats)

    def save(self):
        save_dataclass(self.STATS_FILE, self.data)

    def record_blueberry_farm(self):
        """One confirmed farm-опыта cycle — bumps both the running total
        and today's own file, the same way StatsManager.record_* keeps its
        daily/all-time numbers from drifting apart."""
        self.data.farm.blueberry_exp += 1
        self.save()
        self.daily.bump_exp()
