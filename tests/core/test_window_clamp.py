# tests/core/test_window_clamp.py
"""Окно помощника, прижимаемое к клиентской области игры.

Здесь стояло max(0, min(x, game_w - w)), и на игре меньше окна обе границы
схлопывались в ноль: окно прилипало к углу и переставало таскаться вовсе.
Именно это и ловилось как «свернул игру — окна больше не двигаются».
"""
from app.core.window_manager import clamp_into

WINDOW = (713, 828)     # реальный размер окна помощника из config.json


def _clamp(x, y, game_w, game_h):
    return clamp_into(x, y, WINDOW[0], WINDOW[1], game_w, game_h)


# ── Игра больше окна: как было ──────────────────────────────────────────────

def test_a_window_that_fits_stays_inside_the_game():
    assert _clamp(300, 400, 2560, 1368) == (300, 400)


def test_a_window_pushed_past_the_far_edge_is_pulled_back():
    assert _clamp(9000, 9000, 2560, 1368) == (2560 - 713, 1368 - 828)


def test_a_window_pushed_past_the_near_edge_is_pulled_back():
    assert _clamp(-500, -500, 2560, 1368) == (0, 0)


# ── Игра меньше окна: то, что не работало ───────────────────────────────────

def test_a_window_taller_than_the_game_can_still_be_moved_up():
    """Игра 1000×600, окно 828 в высоту: запас по вертикали −228, и раньше
    любая координата y превращалась в ноль."""
    assert _clamp(0, -100, 1000, 600) == (0, -100)


def test_a_window_bigger_than_the_game_can_be_moved_on_both_axes():
    # Игра 700×400: по X окно может уехать на 13 пикселей, по Y — на 428.
    assert _clamp(-10, -300, 700, 400) == (-10, -300)


def test_it_can_be_slid_far_enough_to_reach_its_own_far_corner():
    """Дальше края — нельзя: какая-то часть окна остаётся на виду всегда."""
    assert _clamp(-9000, -9000, 700, 400) == (700 - 713, 400 - 828)


def test_it_does_not_slide_off_the_near_corner():
    assert _clamp(500, 500, 700, 400) == (0, 0)


def test_each_axis_is_decided_on_its_own():
    """Игра шире окна, но ниже его — по X прижимаем как всегда, по Y
    разрешаем уехать."""
    assert _clamp(400, -200, 2560, 600) == (400, -200)
