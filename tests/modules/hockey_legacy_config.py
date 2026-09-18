# tests/modules/hockey_legacy_config.py
"""Поля, которые старый хоккей держал в app.core.config.HockeyConfig.

Мод переписывается с нуля, и из общего конфига хоккейная часть убрана — там
осталась только рамка окна. Но алгоритмические модули (rink_area, detect,
trajectory, planner) с диска не удалены и лежат как справочник, а их тесты
собирают конфиг именно этой формы.

Поэтому форма переехала сюда: она нужна только тестам легаси-кода и умрёт
вместе с ним, а общий конфиг приложения при этом остаётся чистым. Значения —
те же умолчания, что были в HockeyConfig на момент коммита
«LEGACY: хоккей — рабочая, но не идеальная версия»; каждый тест всё равно
переписывает почти все из них под свою сцену.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HockeyConfig:
    # ── Геометрия катка, абсолютные экранные координаты ──────────────────
    rink_left:   int = 891
    rink_top:    int = 385
    rink_width:  int = 780
    rink_height: int = 666

    # Ряды, сверху вниз: {"y", "height", "wall_left", "wall_right",
    # "body_w", "body_h", "body_dy"}. Борта у каждого ряда свои — вратарь
    # каждого разворачивается в своём месте; габарит body_* — всё тело, а не
    # шлем, потому что бросок блокирует именно оно.
    lanes: list = field(default_factory=list)

    # Запасные борта для ряда, у которого своих нет.
    wall_left:  int = 941
    wall_right: int = 1621

    # Откуда летит шайба и куда.
    shooter_x: int = 1281
    shooter_y: int = 984
    goal_y:     int = 452
    goal_left:  int = 1141
    goal_right: int = 1421

    goalie_half_w: int = 50
    puck_radius:   int = 10

    # ── Детекция шлема ───────────────────────────────────────────────────
    red_sat_min:   int = 120
    red_val_min:   int = 80
    blob_area_min: int = 150
    player_match_min: float = 0.30
    lane_height: int = 56
    static_mask: bool = False
