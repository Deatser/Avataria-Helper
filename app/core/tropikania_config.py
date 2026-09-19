# app/core/tropikania_config.py
"""Tropikania's own settings file, kept apart from config.json the same way
its stats live apart from stats.json — a separate app, a separate record.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from app.core.json_store import load_dataclass, save_dataclass
from app.core.paths import data_path


@dataclass
class TropikaniaOverlayConfig:
    x: int = 10
    y: int = 10
    width: int = 330
    height: int = 480
    skip_close_confirm: bool = False
    background: str = ""            # explicit backdrop path; empty → auto-pick
    show_links: bool = True         # wires between this window and Статистика
    auto_continue: bool = False     # "Автопродолжение" — restart the farm
                                    # cycle instead of stopping after confirm


@dataclass
class TropikaniaStatsWindowConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 420
    y: int = 60
    width: int = 460
    height: int = 560
    background: str = ""


@dataclass
class TropikaniaAppConfig:
    overlay: TropikaniaOverlayConfig = field(default_factory=TropikaniaOverlayConfig)
    stats_window: TropikaniaStatsWindowConfig = field(default_factory=TropikaniaStatsWindowConfig)


class TropikaniaConfigManager:
    CONFIG_FILE = data_path("tropikania_config.json")

    def __init__(self):
        self.data = load_dataclass(self.CONFIG_FILE, TropikaniaAppConfig)

    def save(self):
        save_dataclass(self.CONFIG_FILE, self.data)
