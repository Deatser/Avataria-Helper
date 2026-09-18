# tests/core/test_capture_scaling.py
"""Кадр игры любого размера приводится к эталонному.

В этом весь смысл: шаблоны нарезаны с развёрнутой игры, и совпадать они
будут только с картинкой того же масштаба. Приводится кадр — значит, ни один
шаблон, порог и забитый прямоугольник трогать не пришлось.
"""
import numpy as np
import pytest

from app.core import capture
from app.core.game_geometry import Frame, geometry, set_primary

REF  = Frame(left=0, top=0, width=800, height=600)
HALF = Frame(left=0, top=0, width=400, height=300)


# Окно игры в этих тестах — hwnd 1; объявляем его основным, чтобы
# geometry() без аргумента отвечала про него же (так делает main.py).
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


def _frame(width: int, height: int) -> np.ndarray:
    """Кадр, по которому видно, какой кусок из него вырезали."""
    rng = np.random.default_rng(11)
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


def _scripted(monkeypatch, image, origin=(0, 0)):
    monkeypatch.setattr(capture, "_grab_frame",
                        lambda hwnd, fresh: (image, origin))


# ── Без эталона ─────────────────────────────────────────────────────────────

def test_without_a_reference_the_frame_comes_back_untouched(monkeypatch):
    """Прежнее поведение слово в слово — ровно то, на что опирается вся
    работающая сейчас калибровка."""
    image = _frame(800, 600)
    _scripted(monkeypatch, image)

    out = capture.grab_window(1)

    assert out.shape == image.shape
    assert np.array_equal(out, image)


def test_a_region_is_cut_exactly_where_it_was_asked_for(monkeypatch):
    image = _frame(800, 600)
    _scripted(monkeypatch, image)

    out = capture.grab_window(1, {"left": 100, "top": 50,
                                  "width": 200, "height": 150})

    assert out.shape == (150, 200, 3)
    assert np.array_equal(out, image[50:200, 100:300])


# ── С эталоном ──────────────────────────────────────────────────────────────

def test_a_halved_game_comes_back_at_reference_size(monkeypatch,
                                                    clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)
    _scripted(monkeypatch, _frame(400, 300))

    out = capture.grab_window(1)

    assert out.shape == (600, 800, 3)


def test_a_region_asked_in_reference_pixels_comes_back_that_size(
        monkeypatch, clean_geometry):
    """Мод просит ту же область, что и всегда, — и получает картинку того же
    размера, что и всегда. Про масштаб игры он не знает ничего."""
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)
    _scripted(monkeypatch, _frame(400, 300))

    out = capture.grab_window(1, {"left": 100, "top": 50,
                                  "width": 200, "height": 150})

    assert out.shape == (150, 200, 3)


def test_the_region_is_taken_from_where_the_game_actually_draws_it(
        monkeypatch, clean_geometry):
    """Игра вдвое меньше — значит, и область лежит вдвое ближе к её углу.
    Метку кладём именно туда и проверяем, что вырезали её."""
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)
    image = np.zeros((300, 400, 3), dtype=np.uint8)
    image[25:100, 50:150] = 200          # половина от (50, 100)-(300, 200)
    _scripted(monkeypatch, image)

    out = capture.grab_window(1, {"left": 100, "top": 50,
                                  "width": 200, "height": 150})

    assert out.shape == (150, 200, 3)
    assert out.min() == 200 and out.max() == 200


def test_a_moved_game_shifts_the_cut_without_resizing_it(monkeypatch,
                                                         clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(left=300, top=100, width=800, height=600))
    # Кадр уже в координатах окна: его пиксель (0,0) — это экранное
    # (300,100), поэтому эталонная точка (100,50) лежит в кадре на (100,50).
    image = np.zeros((600, 800, 3), dtype=np.uint8)
    image[50:200, 100:300] = 70
    _scripted(monkeypatch, image, origin=(300, 100))

    out = capture.grab_window(1, {"left": 100, "top": 50,
                                  "width": 200, "height": 150})

    assert out.shape == (150, 200, 3)
    assert out.min() == 70 and out.max() == 70


def test_a_stretched_game_is_squeezed_back_on_each_axis(monkeypatch,
                                                        clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(0, 0, 400, 600))    # только по X вдвое
    _scripted(monkeypatch, _frame(400, 600))

    out = capture.grab_window(1)

    assert out.shape == (600, 800, 3)


# ── Куда складывать найденное ───────────────────────────────────────────────

def test_the_frame_origin_is_answered_in_reference_pixels(monkeypatch,
                                                          clean_geometry):
    """Точку, найденную в приведённом кадре, складывают с этим началом —
    значит, и оно обязано быть эталонным."""
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(Frame(left=300, top=100, width=400, height=300))
    monkeypatch.setattr(capture, "_live_frame_origin", lambda hwnd: (300, 100))

    assert capture.window_frame_origin(1) == (0, 0)


# ── Снимок экрана мимо окна игры ────────────────────────────────────────────

def test_a_screen_region_goes_through_the_same_translation(monkeypatch,
                                                           clean_geometry):
    clean_geometry.set_reference(REF)
    clean_geometry.set_current(HALF)
    asked = {}

    class _Sct:
        def grab(self, region):
            asked.update(region)
            return _frame(region["width"], region["height"])

    monkeypatch.setattr(capture.ScreenCapture, "get", staticmethod(lambda: _Sct()))

    out = capture.grab_screen_region({"left": 100, "top": 50,
                                      "width": 200, "height": 150})

    assert asked == {"left": 50, "top": 25, "width": 100, "height": 75}
    assert out.shape == (150, 200, 3)
