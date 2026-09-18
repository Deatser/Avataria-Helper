# tests/core/test_input_scaling.py
"""Клик по эталонной точке приходит туда, где игра рисует её сейчас."""
import pytest

from app.core import input_sender
from app.core.game_geometry import Frame, geometry, set_primary

REF  = Frame(left=0, top=0, width=800, height=600)
HALF = Frame(left=0, top=0, width=400, height=300)
HWND = 1


@pytest.fixture(autouse=True)
def clean_geometry():
    set_primary(HWND)
    geom = geometry(HWND)
    geom.set_reference(None)
    geom.set_current(None)
    yield geom
    geom.set_reference(None)
    geom.set_current(None)
    set_primary(0)


@pytest.fixture
def posted(monkeypatch):
    """Куда на самом деле ушло сообщение — в клиентских координатах окна."""
    seen = []

    monkeypatch.setattr(input_sender, "_input_target", lambda hwnd: hwnd)
    monkeypatch.setattr(input_sender, "_prime_focus", lambda hwnd, target: None)
    # Клиентские координаты здесь совпадают с экранными: окно в углу экрана,
    # и проверять мы хотим перевод масштаба, а не арифметику Win32.
    monkeypatch.setattr(input_sender.win32gui, "ScreenToClient",
                        lambda hwnd, point: point)
    monkeypatch.setattr(input_sender.win32api, "PostMessage",
                        lambda hwnd, msg, wparam, lparam: seen.append(
                            (msg, lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)))
    monkeypatch.setattr(input_sender._delayed, "call_later",
                        lambda delay, func, *args: None)
    return seen


def _clicked_at(posted) -> tuple[int, int]:
    import win32con
    for msg, x, y in posted:
        if msg == win32con.WM_LBUTTONDOWN:
            return x, y
    raise AssertionError("клика не было")


def test_without_a_reference_the_click_lands_where_it_was_asked(posted):
    """Прежнее поведение слово в слово."""
    input_sender.click_at(HWND, 400, 300)

    assert _clicked_at(posted) == (400, 300)


def test_a_halved_game_halves_the_click(posted, clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)

    input_sender.click_at(HWND, 400, 300)

    assert _clicked_at(posted) == (200, 150)


def test_a_moved_game_shifts_the_click(posted, clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(left=120, top=40, width=800, height=600))

    input_sender.click_at(HWND, 400, 300)

    assert _clicked_at(posted) == (520, 340)


def test_a_stretched_game_moves_each_axis_on_its_own(posted, clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 400, 600))

    input_sender.click_at(HWND, 400, 300)

    assert _clicked_at(posted) == (200, 300)


def test_a_drag_travels_through_the_same_translation(posted, clean_geometry):
    """Ведение мышью — те же координаты, что и у клика; разойдись они, и
    бросок в Хоккее уезжал бы в сторону тем сильнее, чем меньше окно."""
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)

    input_sender.mouse_down_at(HWND, 400, 300)

    assert _clicked_at(posted) == (200, 150)
