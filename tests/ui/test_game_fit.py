# tests/ui/test_game_fit.py
"""Окна помощника, живущие в долях игры.

Игру можно свернуть в окно любого размера; окна мода обязаны уехать вместе с
ней — и местом, и размером, и тем, что в config.json остаётся эталонным.
"""
import pytest
from PySide6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.core.game_geometry import Frame, geometry
from app.core.stats import StatsManager
from app.ui.stats_window import StatsWindow

# Эталон: игра развёрнута, картинка занимает всю клиентскую область.
REF = Frame(left=0, top=0, width=2560, height=1368)
HALF = Frame(left=0, top=0, width=1280, height=684)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_geometry():
    """Геометрия — одна на приложение, и тест не должен её кому-то оставить."""
    geom = geometry()
    geom.set_reference(None)
    geom.set_current(None)
    yield geom
    geom.set_reference(None)
    geom.set_current(None)


class _WM:
    """Игра «есть» ровно настолько, чтобы окно считало себя прицепленным —
    но hwnd нулевой, и до настоящих вызовов Win32 дело не доходит."""

    def get_game_hwnd(self): return 0
    def is_game_alive(self): return False
    def attach_child(self, hwnd): pass
    def move_window(self, *args): pass


def _window(tmp_path, monkeypatch, app):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    window = StatsWindow(config.data.stats_window, config.save,
                         StatsManager(), _WM())
    return window, config


# ── Подгонка под размер игры ────────────────────────────────────────────────

def test_without_a_reference_the_window_is_left_alone(tmp_path, monkeypatch,
                                                      app):
    """Эталон ещё не записан — помощник обязан вести себя как раньше."""
    window, _config = _window(tmp_path, monkeypatch, app)
    before = window.size()

    assert window.fit_to_game() is False
    assert window.size() == before
    assert window.ui_scale == 1.0
    window.close()


def test_a_halved_game_halves_the_window(tmp_path, monkeypatch, app,
                                         clean_geometry):
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 400, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)

    assert window.fit_to_game() is True

    assert (window.width(), window.height()) == (300, 400)
    assert (window.x(), window.y()) == (200, 100)
    window.close()


def test_the_game_back_at_full_size_puts_the_window_back(tmp_path, monkeypatch,
                                                         app, clean_geometry):
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 400, 200, 600, 800

    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)
    window.fit_to_game()
    clean_geometry.set_current(REF)
    window.fit_to_game()

    assert (window.width(), window.height()) == (600, 800)
    assert (window.x(), window.y()) == (400, 200)
    window.close()


def test_a_stretched_game_scales_the_window_by_the_smaller_side(
        tmp_path, monkeypatch, app, clean_geometry):
    """Ужатая по одной оси игра не должна давать окно, вылезающее по другой:
    масштаб окна — всегда меньший из двух."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.width, cfg.height = 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 1280, 1368))   # по X вдвое

    window.fit_to_game()

    assert (window.width(), window.height()) == (300, 400)
    window.close()


# ── Что уходит в config ─────────────────────────────────────────────────────

def test_dragging_on_a_shrunken_game_saves_the_reference_place(
        tmp_path, monkeypatch, app, clean_geometry):
    """Иначе каждый перетаск записывал бы уменьшенные числа, и окно с каждым
    сеансом сползало бы к углу игры."""
    window, config = _window(tmp_path, monkeypatch, app)
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)

    window.save_position(200, 100)          # куда окно легло сейчас

    assert (config.data.stats_window.x, config.data.stats_window.y) == (400, 200)
    window.close()


def test_resizing_on_a_shrunken_game_saves_the_reference_size(
        tmp_path, monkeypatch, app, clean_geometry):
    window, config = _window(tmp_path, monkeypatch, app)
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)
    window.set_ui_scale(0.5)
    window.resize(300, 400)

    window._on_resize_done()

    cfg = config.data.stats_window
    assert (cfg.width, cfg.height) == (600, 800)
    window.close()


def test_a_place_survives_the_trip_through_config(tmp_path, monkeypatch, app,
                                                  clean_geometry):
    window, _config = _window(tmp_path, monkeypatch, app)
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 1707, 912))

    for x, y in ((0, 0), (420, 500), (1200, 900)):
        live_x, live_y = window.to_live_offset(x, y)
        back_x, back_y = window.to_reference_offset(live_x, live_y)
        assert abs(back_x - x) <= 2 and abs(back_y - y) <= 2
    window.close()


# ── Масштаб содержимого ─────────────────────────────────────────────────────

def test_at_full_size_no_scaling_machinery_is_built(tmp_path, monkeypatch, app):
    """Пока игра развёрнута, окно остаётся ровно тем, чем было всегда —
    панель обычным дочерним виджетом, без сцены и вида."""
    window, _config = _window(tmp_path, monkeypatch, app)

    window.set_ui_scale(1.0)

    assert window._fit_view is None
    assert window._panel.parent() is window
    window.close()


def test_shrinking_keeps_the_panel_at_its_own_size(tmp_path, monkeypatch, app):
    """Окно вдвое меньше, а панель — прежняя: мельчит её вид, а не вёрстка,
    поэтому шрифты и отступы уезжают в тех же пропорциях."""
    window, _config = _window(tmp_path, monkeypatch, app)

    window.set_ui_scale(0.5)
    window.resize(300, 400)
    window.layout_panel()

    assert window._fit_view is not None
    assert (window.width(), window.height()) == (300, 400)
    assert (window._panel.width(), window._panel.height()) == (600, 800)
    assert window._fit_view.size() == window.size()
    window.close()


def test_the_panel_follows_the_window_while_it_is_dragged_smaller(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch, app)
    window.set_ui_scale(0.5)

    window.resize(400, 300)
    window.layout_panel()

    assert (window._panel.width(), window._panel.height()) == (800, 600)
    window.close()


def test_the_window_never_shrinks_past_reading(tmp_path, monkeypatch, app,
                                               clean_geometry):
    """Игру можно ужать во что угодно; подписи на плитках после какого-то
    предела уже не буквы, а рябь, и окно перестаёт за ней ужиматься."""
    from app.ui.game_fit import MIN_UI_SCALE

    window, _config = _window(tmp_path, monkeypatch, app)
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 256, 137))   # десятая часть

    window.fit_to_game()

    assert window.ui_scale == MIN_UI_SCALE
    window.close()


def test_the_resize_floor_shrinks_along_with_the_window(tmp_path, monkeypatch,
                                                        app):
    """Иначе на ужатой игре окно нельзя уменьшить вообще: предел записан в
    расчётных пикселях, а тянут его в живых."""
    window, _config = _window(tmp_path, monkeypatch, app)
    full_w, full_h = window._min_size()

    window.set_ui_scale(0.5)

    half_w, half_h = window._min_size()
    assert (half_w, half_h) == (full_w // 2, full_h // 2)
    window.close()


# ── Мышь сквозь уменьшенное окно ────────────────────────────────────────────
# Уменьшенное содержимое показывает вид, накрывающий окно целиком. Он обязан
# отдавать окну всё, что не забрал ни один виджет внутри: перетаскивание за
# фон и ресайз за край живут на mousePressEvent самого окна.

def _scaled(tmp_path, monkeypatch, app):
    """Окно не показываем: событие мыши доходит до виджета и так, а
    видеофон, который заводится при показе, стоит секунд."""
    window, _config = _window(tmp_path, monkeypatch, app)
    window.set_ui_scale(0.5)
    window.resize(300, 400)
    window.layout_panel()
    return window


def test_the_panel_really_moves_into_the_scene(tmp_path, monkeypatch, app):
    """QGraphicsScene.addWidget берёт только виджет без родителя. Панель —
    ребёнок окна, и без снятия родителя сцена оставалась пустой: окно
    выглядело прежним, а поверх него лежал пустой вид, евший мышь."""
    window = _scaled(tmp_path, monkeypatch, app)

    assert window._panel.graphicsProxyWidget() is not None
    assert window._panel.parent() is not window
    window.close()


def test_the_background_still_drags_a_shrunken_window(tmp_path, monkeypatch,
                                                      app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    window = _scaled(tmp_path, monkeypatch, app)
    spot = QPoint(window.width() // 2, window.height() // 2)
    window._bg_dragging = False

    QTest.mousePress(window._fit_view.viewport(), Qt.LeftButton,
                     Qt.NoModifier, spot)

    assert window._bg_dragging is True
    QTest.mouseRelease(window._fit_view.viewport(), Qt.LeftButton,
                       Qt.NoModifier, spot)
    window.close()


def test_the_edges_still_resize_a_shrunken_window(tmp_path, monkeypatch, app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    window = _scaled(tmp_path, monkeypatch, app)
    edge = QPoint(2, window.height() // 2)
    window._rsz_dir = 0

    QTest.mousePress(window._fit_view.viewport(), Qt.LeftButton,
                     Qt.NoModifier, edge)

    assert window._rsz_dir != 0
    QTest.mouseRelease(window._fit_view.viewport(), Qt.LeftButton,
                       Qt.NoModifier, edge)
    window.close()


def test_a_button_inside_still_takes_its_own_click(tmp_path, monkeypatch, app):
    """Пересылка не должна забирать нажатия у тех, кому они предназначены."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    window = _scaled(tmp_path, monkeypatch, app)
    pressed = []
    window._fav_btn.clicked.connect(lambda: pressed.append(True))
    centre = window._fav_btn.geometry().center()

    QTest.mouseClick(window._fit_view.viewport(), Qt.LeftButton, Qt.NoModifier,
                     QPoint(int(centre.x() * 0.5), int(centre.y() * 0.5)))

    assert pressed == [True]
    assert window._bg_dragging is False
    window.close()


# ── Окно, открытое на уже ужатой игре ───────────────────────────────────────

def test_a_window_opened_on_a_shrunken_game_opens_already_fitted(
        tmp_path, monkeypatch, app, clean_geometry):
    """Окно собирается по эталонным числам из config, а открыть его могут
    когда игра уже ужата: без подгонки на показе оно вылезало за её край во
    весь свой развёрнутый размер и оставалось таким до первой смены размера
    игры — которой за весь сеанс могло и не случиться."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 400, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)

    window.show()

    assert (window.width(), window.height()) == (300, 400)
    assert (window.x(), window.y()) == (200, 100)
    assert window.ui_scale == 0.5
    window.close()


def test_a_window_opened_on_a_full_size_game_is_left_alone(
        tmp_path, monkeypatch, app, clean_geometry):
    """Игра эталонного размера — окно обязано открыться ровно таким, каким
    открывалось всегда, и без сжимающего вида внутри."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.width, cfg.height = 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(REF)

    window.show()

    assert (window.width(), window.height()) == (600, 800)
    assert window.ui_scale == 1.0
    assert window._fit_view is None
    window.close()


# ── Сколько места у окна есть ───────────────────────────────────────────────

def test_the_room_a_window_has_is_the_game_and_not_the_monitor(
        tmp_path, monkeypatch, app, clean_geometry):
    """Окно, растущее под своё содержимое, упирается в игру: предел в высоту
    монитора на игре в четверть экрана не ограничивает ровно ничего."""
    window, _config = _window(tmp_path, monkeypatch, app)
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)

    assert window.fit_room_height() == HALF.height
    window.close()


def test_without_a_game_the_room_falls_back_to_the_screen(
        tmp_path, monkeypatch, app, clean_geometry):
    window, _config = _window(tmp_path, monkeypatch, app)

    assert window.fit_room_height() > 0
    window.close()


# ── Игру потянули за один край ──────────────────────────────────────────────

def test_a_game_stretched_along_one_axis_moves_the_window_only_along_it(
        tmp_path, monkeypatch, app, clean_geometry):
    """Место окна — доля ширины и доля высоты, и они разные.

    Общий множитель min(…) уводил окно ещё и вверх, хотя по высоте не
    менялось ничего: потянешь игру за правый край — и окна разъезжаются
    наискось. Размер при этом по-прежнему меряется одним числом на обе оси,
    иначе окно перекашивало бы вслед за игрой.
    """
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 400, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 1280, 1368))   # ужата только по X

    assert window.fit_to_game() is True

    assert (window.x(), window.y()) == (200, 200)
    assert (window.width(), window.height()) == (300, 400)
    window.close()


def test_the_place_saved_on_a_stretched_game_comes_back_unchanged(
        tmp_path, monkeypatch, app, clean_geometry):
    """Туда и обратно одними и теми же двумя множителями: разойдись они, и
    каждый перетаск записывал бы не то место, куда окно положили."""
    window, _config = _window(tmp_path, monkeypatch, app)
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 1280, 1368))

    live = window.to_live_offset(400, 200)

    assert window.to_reference_offset(*live) == (400, 200)
    window.close()


# ── Окно у края ужатой игры ─────────────────────────────────────────────────

# Игра мельче, чем пол MIN_UI_SCALE: окно ужимается уже не в её долю, а
# только до 0.35 — и занимает бОльшую часть игры, чем занимало.
TINY = Frame(left=0, top=0, width=640, height=342)


def test_a_window_near_the_edge_is_pushed_back_inside_the_game(
        tmp_path, monkeypatch, app, clean_geometry):
    """Размер окна снизу упирается в MIN_UI_SCALE, поэтому на маленькой игре
    окно занимает большую её долю, чем занимало, и стоящее у правого края
    вылезает за него."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 1900, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(TINY)

    window.fit_to_game()

    assert window.x() + window.width() <= TINY.width
    assert window.x() == TINY.width - window.width()
    window.close()


def test_being_pushed_to_the_edge_does_not_rewrite_the_saved_place(
        tmp_path, monkeypatch, app, clean_geometry):
    """На развёрнутой игре окно обязано вернуться ровно туда, где стояло."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 1900, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(TINY)
    window.fit_to_game()

    clean_geometry.set_current(REF)
    window.fit_to_game()

    assert (window.x(), window.y()) == (1900, 200)
    assert (cfg.x, cfg.y) == (1900, 200)
    window.close()


# ── Игра меньше окна ────────────────────────────────────────────────────────

def test_a_window_is_never_bigger_than_the_game_it_lives_in(
        tmp_path, monkeypatch, app, clean_geometry):
    """Окно шире или выше игры не помещается в неё ни при каком месте:
    Windows прижимает такое окно к дальнему краю и не даёт сдвинуть ни на
    пиксель — все окна слипаются в углу как примагниченные."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 400, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 200, 120))

    window.fit_to_game()

    assert (window.width(), window.height()) == (200, 120)
    assert window.minimumWidth() <= 200 and window.minimumHeight() <= 120


def test_a_window_still_has_room_to_be_dragged_on_a_tiny_game(
        tmp_path, monkeypatch, app, clean_geometry):
    """Влезло — значит его есть куда двигать, а не только прижать к краю."""
    window, config = _window(tmp_path, monkeypatch, app)
    cfg = config.data.stats_window
    cfg.x, cfg.y, cfg.width, cfg.height = 400, 200, 600, 800
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 400, 300))

    window.fit_to_game()

    assert window.width() <= 400 and window.height() <= 300
    assert window.x() + window.width() <= 400
    assert window.y() + window.height() <= 300
