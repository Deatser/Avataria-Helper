# app/core/tropikania_daily_log.py
"""Per-day farm totals, one small JSON file per day in logs_tropikania/ —
Tropikania's own copy of daily_log.py's pattern, own directory so its files
never collide with the main app's "Статистика за <date>.json" ones.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.json_store import load_dataclass, save_dataclass
from app.core.paths import data_path

LOGS_DIR  = data_path("logs_tropikania")
KEEP_DAYS = 30


@dataclass
class DailyFarmStats:
    date: str = ""
    blueberry_exp: int = 0


def _today_str() -> str:
    return datetime.now().strftime("%d.%m.%Y")


def _path_for(date_str: str) -> Path:
    return LOGS_DIR / f"Статистика за {date_str}.json"


class TropikaniaDailyLog:
    """Owns no state of its own — every method re-derives today's path and
    reads/writes that one file, so nothing here can go stale."""

    def ensure_today(self):
        LOGS_DIR.mkdir(exist_ok=True)
        path = _path_for(_today_str())
        if not path.exists():
            save_dataclass(path, DailyFarmStats(date=_today_str()))
        self._prune()

    def bump_exp(self, amount: int = 1):
        data = self._load()
        data.blueberry_exp += amount
        save_dataclass(_path_for(_today_str()), data)

    # ── Reading, aggregated ──────────────────────────────────────────────────

    def today(self) -> DailyFarmStats:
        return self._load()

    def this_month(self) -> DailyFarmStats:
        now = datetime.now()
        total = DailyFarmStats(date=f"{now.month:02d}.{now.year}")
        LOGS_DIR.mkdir(exist_ok=True)
        for path in LOGS_DIR.glob("Статистика за *.json"):
            day = load_dataclass(path, DailyFarmStats)
            try:
                parsed = datetime.strptime(day.date, "%d.%m.%Y")
            except ValueError:
                continue
            if parsed.year == now.year and parsed.month == now.month:
                total.blueberry_exp += day.blueberry_exp
        return total

    def _load(self) -> DailyFarmStats:
        LOGS_DIR.mkdir(exist_ok=True)
        date = _today_str()
        return load_dataclass(_path_for(date), lambda: DailyFarmStats(date=date))

    def _prune(self):
        files = sorted(LOGS_DIR.glob("Статистика за *.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[KEEP_DAYS:]:
            stale.unlink(missing_ok=True)
