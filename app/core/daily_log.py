# app/core/daily_log.py
"""Per-day activity totals, one small JSON file per day in logs/.

Separate from stats.json (the all-time running totals) and separate from
config.json (settings) — this answers "how much did I farm today" on its
own, without diffing anything against a snapshot. Every record_*_run /
record_*_cleanup call in StatsManager bumps today's file by the exact same
delta it just added to the running total, so the two never drift apart.

The date is re-read on every call rather than cached once at construction,
so a session left open across midnight rolls over to a fresh file on its
own instead of quietly writing tonight's runs into yesterday's file until
the app is restarted.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.core.json_store import load_dataclass, save_dataclass
from app.core.paths import data_path

LOGS_DIR  = data_path("logs")
KEEP_DAYS = 30   # older files are pruned away on the next ensure_today()


# Deliberately its own tiny copy of AvaDancersStats/SnowboardStats/
# HockeyStats' shape rather than importing those from stats.py: stats.py
# owns DailyLog (calls into it from every record_*), so importing the
# other way round would be circular.
@dataclass
class DailyRunStats:
    games_played: int = 0
    gold_won:     int = 0
    silver_won:   int = 0


@dataclass
class DailyCleanupStats:
    shifts_finished: int = 0


@dataclass
class DailyPromoStats:
    activated: int = 0


@dataclass
class DailyEnergyStats:
    """Своя копия EnergyStats — по той же причине, что и у остальных здесь."""
    energy_bought:     int = 0
    gold_spent:        int = 0
    silver_spent:      int = 0
    pie_bought:        int = 0
    cheesecake_bought: int = 0
    brownie_bought:    int = 0


@dataclass
class DailyStats:
    date:        str              = ""
    ava_dancers: DailyRunStats    = field(default_factory=DailyRunStats)
    snowboard:   DailyRunStats    = field(default_factory=DailyRunStats)
    hockey:      DailyRunStats    = field(default_factory=DailyRunStats)
    gardener:    DailyCleanupStats = field(default_factory=DailyCleanupStats)
    janitor:     DailyCleanupStats = field(default_factory=DailyCleanupStats)
    promo:       DailyPromoStats  = field(default_factory=DailyPromoStats)
    energy:      DailyEnergyStats = field(default_factory=DailyEnergyStats)


_RUN_KEYS     = ("ava_dancers", "snowboard", "hockey")
_CLEANUP_KEYS = ("gardener", "janitor")
_ENERGY_FIELDS = ("energy_bought", "gold_spent", "silver_spent",
                  "pie_bought", "cheesecake_bought", "brownie_bought")


def _add(a: DailyStats, b: DailyStats) -> DailyStats:
    """a + b, field by field — used to fold many days' files into one
    running total for this_month(); a's own date is kept, b's is not
    meaningful once summed."""
    total = DailyStats(date=a.date)
    for key in _RUN_KEYS:
        ea, eb, er = getattr(a, key), getattr(b, key), getattr(total, key)
        er.games_played = ea.games_played + eb.games_played
        er.gold_won     = ea.gold_won + eb.gold_won
        er.silver_won   = ea.silver_won + eb.silver_won
    for key in _CLEANUP_KEYS:
        ea, eb, er = getattr(a, key), getattr(b, key), getattr(total, key)
        er.shifts_finished = ea.shifts_finished + eb.shifts_finished
    total.promo.activated = a.promo.activated + b.promo.activated
    for name in _ENERGY_FIELDS:
        setattr(total.energy, name,
                getattr(a.energy, name) + getattr(b.energy, name))
    return total


def _today_str() -> str:
    return datetime.now().strftime("%d.%m.%Y")


def _path_for(date_str: str) -> Path:
    return LOGS_DIR / f"Статистика за {date_str}.json"


class DailyLog:
    """Owns no state of its own — every method re-derives today's path and
    reads/writes that one file, so nothing here can go stale."""

    def ensure_today(self):
        """Called once at startup: creates today's file, zeroed, if it does
        not exist yet, and prunes anything past KEEP_DAYS days old."""
        LOGS_DIR.mkdir(exist_ok=True)
        path = _path_for(_today_str())
        if not path.exists():
            save_dataclass(path, DailyStats(date=_today_str()))
        self._prune()

    def bump_run(self, module_key: str, gold: int = 0, silver: int = 0):
        """The same delta a record_*_run call just added to the running
        total in stats.json, added to today's file too."""
        data = self._load()
        entity = getattr(data, module_key)
        entity.games_played += 1
        entity.gold_won     += max(0, int(gold))
        entity.silver_won   += max(0, int(silver))
        save_dataclass(_path_for(_today_str()), data)

    def bump_cleanup(self, module_key: str):
        data = self._load()
        getattr(data, module_key).shifts_finished += 1
        save_dataclass(_path_for(_today_str()), data)

    def bump_promo(self):
        data = self._load()
        data.promo.activated += 1
        save_dataclass(_path_for(_today_str()), data)

    def bump_energy(self, energy: int = 0, gold: int = 0, silver: int = 0,
                    pie: int = 0, cheesecake: int = 0, brownie: int = 0):
        """Тот же итог захода в кафе, что ушёл в общий счёт stats.json."""
        data = self._load()
        entity = data.energy
        entity.energy_bought     += max(0, int(energy))
        entity.gold_spent        += max(0, int(gold))
        entity.silver_spent      += max(0, int(silver))
        entity.pie_bought        += max(0, int(pie))
        entity.cheesecake_bought += max(0, int(cheesecake))
        entity.brownie_bought    += max(0, int(brownie))
        save_dataclass(_path_for(_today_str()), data)

    # ── Reading, aggregated ──────────────────────────────────────────────────

    def today(self) -> DailyStats:
        """Today's own file, as-is — the same data bump_run/bump_cleanup
        write into."""
        return self._load()

    def this_month(self) -> DailyStats:
        """Every kept day whose date falls in the current calendar month,
        summed into one DailyStats. Limited by KEEP_DAYS in practice — a
        month more than 30 days into the past has already lost its early
        days to _prune(), same as everything else here."""
        now = datetime.now()
        total = DailyStats(date=f"{now.month:02d}.{now.year}")
        LOGS_DIR.mkdir(exist_ok=True)
        for path in LOGS_DIR.glob("Статистика за *.json"):
            day = load_dataclass(path, DailyStats)
            try:
                parsed = datetime.strptime(day.date, "%d.%m.%Y")
            except ValueError:
                continue
            if parsed.year == now.year and parsed.month == now.month:
                total = _add(total, day)
        return total

    def _load(self) -> DailyStats:
        LOGS_DIR.mkdir(exist_ok=True)
        date = _today_str()
        return load_dataclass(_path_for(date), lambda: DailyStats(date=date))

    def _prune(self):
        files = sorted(LOGS_DIR.glob("Статистика за *.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[KEEP_DAYS:]:
            stale.unlink(missing_ok=True)
