# tests/core/test_game_geometry.py
"""Перевод координат между эталонным размером игры и нынешним."""
import numpy as np

import app.core.game_geometry as gg
from app.core.game_geometry import (Frame, GameGeometry, _trim_bars,
                                    picture_frame)

# Эталон: игра развёрнута на весь экран, картинка занимает всё окно.
REF = Frame(left=0, top=32, width=2560, height=1368)


def _geom(current: Frame | None, reference: Frame | None = REF) -> GameGeometry:
    geom = GameGeometry()
    geom.set_reference(reference)
    geom.set_current(current)
    return geom


# ── Без эталона ─────────────────────────────────────────────────────────────

def test_without_a_reference_nothing_is_translated():
    """Пока эталон не записан, помощник обязан вести себя как раньше."""
    geom = _geom(Frame(100, 100, 640, 360), reference=None)

    assert geom.point(1280, 741) == (1280, 741)
    assert geom.scale == 1.0
    assert geom.identity is True


def test_without_a_live_frame_nothing_is_translated():
    geom = _geom(None)

    assert geom.point(1280, 741) == (1280, 741)
    assert geom.identity is True


def test_the_game_sitting_exactly_on_its_reference_is_identity():
    geom = _geom(REF)

    assert geom.identity is True
    assert geom.point(1280, 741) == (1280, 741)
    assert geom.scale == 1.0


# ── Масштаб ─────────────────────────────────────────────────────────────────

def test_half_size_halves_every_distance_from_the_corner():
    geom = _geom(Frame(left=0, top=32, width=1280, height=684))

    assert geom.scale_x == 0.5
    assert geom.scale_y == 0.5
    assert geom.point(REF.left, REF.top) == (0, 32)          # угол на месте
    assert geom.point(2560, 1400) == (1280, 716)             # дальний угол
    assert geom.length(100) == 50


def test_a_moved_window_shifts_without_scaling():
    """Игру просто перетащили — размер тот же, значит и масштаб единичный."""
    geom = _geom(Frame(left=300, top=132, width=2560, height=1368))

    assert geom.scale == 1.0
    assert geom.point(1280, 741) == (1580, 841)


def test_a_stretched_window_scales_each_axis_on_its_own():
    """Игра, растянутая под другое соотношение сторон: по X ужалась вдвое,
    по Y осталась как была."""
    geom = _geom(Frame(left=0, top=32, width=1280, height=1368))

    assert geom.scale_x == 0.5
    assert geom.scale_y == 1.0
    assert geom.point(2560, 1400) == (1280, 1400)
    # Один масштаб на обе оси — всегда меньший из двух: окно помощника,
    # ужатое по большему, вылезло бы за край игры.
    assert geom.scale == 0.5


def test_letterbox_bars_leave_both_axes_equal():
    """Игра держит свои пропорции в окне другой формы: полосы уже срезаны
    измерением, и по обеим осям выходит один и тот же масштаб."""
    geom = _geom(Frame(left=200, top=132, width=1280, height=684))

    assert geom.scale_x == geom.scale_y == 0.5


# ── Туда и обратно ──────────────────────────────────────────────────────────

def test_a_point_survives_the_round_trip():
    """Обратный перевод возвращает ту же точку — с точностью до пикселя,
    который съедает округление на дробном масштабе."""
    geom = _geom(Frame(left=137, top=90, width=1707, height=912))

    for x, y in ((0, 32), (1280, 741), (2559, 1399)):
        back_x, back_y = geom.to_reference(*geom.point(x, y))
        assert abs(back_x - x) <= 2 and abs(back_y - y) <= 2


def test_a_rectangle_keeps_its_corners():
    geom = _geom(Frame(left=0, top=32, width=1280, height=684))

    live = geom.frame(Frame(left=780, top=345, width=1000, height=797))

    # y считается от верха эталонного кадра (32), а не от нуля экрана:
    # 32 + (345 - 32) / 2 = 188, 32 + (1142 - 32) / 2 = 587.
    assert (live.left, live.top) == (390, 188)
    assert (live.right, live.bottom) == (890, 587)


def test_a_region_dict_goes_through_unchanged_in_shape():
    geom = _geom(Frame(left=0, top=32, width=1280, height=684))

    live = geom.region({"left": 780, "top": 345, "width": 1000, "height": 797})

    assert set(live) == {"left", "top", "width", "height"}
    assert live["left"] == 390


def test_a_rectangle_never_collapses_to_nothing():
    """Крошечная область на сильно ужатой игре всё равно остаётся областью —
    матчер, которому подсунули нулевой прямоугольник, падает."""
    geom = _geom(Frame(left=0, top=32, width=128, height=68))

    live = geom.frame(Frame(left=100, top=100, width=2, height=2))

    assert live.width >= 1 and live.height >= 1


# ── Измерение кадра ─────────────────────────────────────────────────────────

def _scene(width: int, height: int, bar_top: int = 0, bar_left: int = 0,
           bar_bottom: int = 0, bar_right: int = 0) -> np.ndarray:
    """Кадр с шумной «игрой» посередине и чёрными полями по краям."""
    image = np.zeros((height, width, 3), dtype=np.uint8)
    inner = image[bar_top:height - bar_bottom or height,
                  bar_left:width - bar_right or width]
    rng = np.random.default_rng(7)
    inner[:] = rng.integers(40, 255, inner.shape, dtype=np.uint8)
    return image


def test_bars_top_and_bottom_are_trimmed_off():
    client = Frame(left=0, top=0, width=400, height=400)
    image  = _scene(400, 400, bar_top=50, bar_bottom=50)

    box = _trim_bars(image, (0, 0), client)

    assert (box.left, box.width) == (0, 400)
    assert (box.top, box.height) == (50, 300)


def test_bars_left_and_right_are_trimmed_off():
    client = Frame(left=0, top=0, width=400, height=400)
    image  = _scene(400, 400, bar_left=80, bar_right=80)

    box = _trim_bars(image, (0, 0), client)

    assert (box.left, box.width) == (80, 240)
    assert (box.top, box.height) == (0, 400)


def test_a_picture_that_fills_the_window_is_left_alone():
    client = Frame(left=0, top=0, width=400, height=400)
    image  = _scene(400, 400)

    box = _trim_bars(image, (0, 0), client)

    assert box == client


def test_one_flat_row_at_the_edge_is_not_a_bar():
    """Ровная строка у края самой игры — обычное дело. Приняв её за поле,
    измерение отгрызало бы от кадра случайный пиксель, и эталон с текущим
    кадром переставали бы сходиться."""
    client = Frame(left=0, top=0, width=400, height=400)
    image  = _scene(400, 400)
    image[0, :] = 17          # одна строка одного цвета
    image[:, 0] = 17

    box = _trim_bars(image, (0, 0), client)

    assert box == client


def test_trimming_reads_the_client_area_out_of_a_bigger_frame():
    """Кадр — всё окно вместе с заголовком, а мерить надо клиентскую часть."""
    client = Frame(left=8, top=40, width=400, height=400)
    image  = np.zeros((448, 416, 3), dtype=np.uint8)
    image[0:40, :] = 90                      # «заголовок» окна
    rng = np.random.default_rng(3)
    image[40 + 50:40 + 350, 8:408] = rng.integers(
        40, 255, (300, 400, 3), dtype=np.uint8)

    box = _trim_bars(image, (0, 0), client)

    # Ответ в экранных координатах: верх клиентской области плюс полоса.
    assert (box.top, box.height) == (90, 300)
    assert (box.left, box.width) == (8, 400)


def test_a_window_that_is_gone_measures_to_nothing():
    assert picture_frame(0) is None


# ── Свёрнутая игра ──────────────────────────────────────────────────────────

def test_a_minimised_game_has_no_frame_at_all(monkeypatch):
    """Свёрнутая в панель задач игра не должна отдавать координаты.

    Клиентская область у неё либо нулевая, либо уехавшая в -32000, и
    пересчитать по ней значит разослать всем окнам помощника мусорные
    места — вместо того чтобы дождаться, пока игру развернут обратно.
    """
    monkeypatch.setattr(gg.win32gui, "IsIconic", lambda hwnd: True)
    monkeypatch.setattr(gg.win32gui, "GetClientRect",
                        lambda hwnd: (0, 0, 800, 600))
    monkeypatch.setattr(gg.win32gui, "ClientToScreen",
                        lambda hwnd, point: (-32000, -32000))

    assert gg.client_frame(4242) is None


def test_a_game_on_screen_reports_its_client_area(monkeypatch):
    monkeypatch.setattr(gg.win32gui, "IsIconic", lambda hwnd: False)
    monkeypatch.setattr(gg.win32gui, "GetClientRect",
                        lambda hwnd: (0, 0, 800, 600))
    monkeypatch.setattr(gg.win32gui, "ClientToScreen",
                        lambda hwnd, point: (100, 50))

    assert gg.client_frame(4242) == Frame(100, 50, 800, 600)


# ── Кадр не от этой рамки ───────────────────────────────────────────────────

def test_a_frame_that_does_not_cover_the_client_area_is_refused():
    """Кадр, снятый до того, как окно подвинули, начинается не там, где
    думает измерение. Раньше он молча обрезался по краям и промерялся как
    ни в чём не бывало — а меряется по нему масштаб всей игры: одного
    такого кадра хватало, чтобы объявить картинкой игры случайный его кусок
    и сволочь все окна помощника в угол."""
    client = Frame(left=700, top=400, width=400, height=400)
    image  = _scene(400, 400)          # кадр от окна, стоявшего в (0, 0)

    assert _trim_bars(image, (0, 0), client) is None


def test_a_refused_frame_leaves_the_client_area_as_the_answer():
    """Клиентская область известна точно и без кадра."""
    client = Frame(left=700, top=400, width=400, height=400)
    image  = _scene(400, 400)

    box = picture_frame(0, image, (0, 0))

    assert box is None          # без окна отвечать нечем…


def test_a_frame_starting_at_the_window_is_still_measured():
    """…а кадр, который покрывает клиентскую область, меряется как всегда."""
    client = Frame(left=700, top=400, width=400, height=400)
    image  = _scene(400, 400, bar_top=50, bar_bottom=50)

    box = _trim_bars(image, (700, 400), client)

    assert (box.top, box.height) == (450, 300)


# ── Картинка и клиентская область — пара из одного замера ───────────────────

def test_the_offset_of_the_picture_inside_the_client_area():
    """Окна помощника живут в координатах клиентской области, а меряются по
    картинке: разница между ними — то самое слагаемое, на которое окно
    отступает от угла."""
    geom = GameGeometry()
    geom.set_current(Frame(140, 90, 800, 450),      # картинка с полями
                     Frame(100, 50, 880, 530))      # клиентская область

    assert geom.offset_in_client == (40, 40)


def test_a_picture_that_fills_the_client_area_has_no_offset():
    geom = GameGeometry()
    frame = Frame(100, 50, 880, 530)
    geom.set_current(frame, frame)

    assert geom.offset_in_client == (0, 0)


def test_a_measurement_without_a_client_area_answers_by_the_picture():
    """Старые вызывающие передают только картинку — отступа у них нет."""
    geom = GameGeometry()
    geom.set_current(Frame(100, 50, 880, 530))

    assert geom.offset_in_client == (0, 0)


def test_a_game_that_went_away_leaves_no_offset_behind():
    geom = GameGeometry()
    geom.set_current(Frame(140, 90, 800, 450), Frame(100, 50, 880, 530))
    geom.set_current(None)

    assert geom.offset_in_client == (0, 0)
