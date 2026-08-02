# tests/ui/test_marker_overlay.py
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from app.ui.marker_overlay import Marker, MarkerOverlay


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _WM:
    """A game window at a known place on screen."""
    GAME = (100, 50, 1200, 800)

    def __init__(self):
        self.owned = []

    def get_game_hwnd(self): return 4242
    def root_of(self, hwnd): return 4242
    def owner_of(self, hwnd): return self.owned[-1][1] if self.owned else 0
    def set_owner(self, hwnd, owner): self.owned.append((hwnd, owner))

    def window_rect_screen(self, hwnd):
        return self.GAME if hwnd == 4242 else None


# ── The overlay itself ──────────────────────────────────────────────────────

def test_markers_put_the_layer_over_the_game(app):
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())

    overlay.show_markers([Marker(300, 200, "#ff0000")])

    assert overlay.isVisible()
    assert overlay.geometry().getRect() == _WM.GAME   # exactly over the game
    overlay.clear()


def test_clearing_takes_the_dots_off_the_screen(app):
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())
    overlay.show_markers([Marker(300, 200, "#ff0000")])

    overlay.clear()

    assert overlay._markers == []
    assert not overlay.isVisible()
    assert not overlay._follow.isActive()   # and stops watching the game


def test_showing_nothing_is_the_same_as_clearing(app):
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())
    overlay.show_markers([Marker(1, 1, "#ff0000")])

    overlay.show_markers([])

    assert not overlay.isVisible()


def test_a_new_scan_replaces_the_previous_dots(app):
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())
    overlay.show_markers([Marker(300, 200, "#ff0000"),
                          Marker(400, 200, "#00ff00")])

    overlay.show_markers([Marker(500, 200, "#0000ff")])

    assert [m.x for m in overlay._markers] == [500]
    overlay.clear()


def test_dots_outside_the_game_window_are_dropped(app):
    """The dots belong to Avataria's window; a find beyond its edges is not
    in the garden at all, and drawing it would put a dot on the desktop."""
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())
    left, top, width, height = _WM.GAME

    overlay.show_markers([Marker(left + 10, top + 10, "#ff0000"),      # inside
                          Marker(left + width + 50, top + 10, "#ff0000"),
                          Marker(left + 10, top + height + 40, "#ff0000")])

    assert len(overlay._markers) == 1
    assert overlay.geometry().getRect() == _WM.GAME   # and never grows past it
    overlay.clear()


def test_the_layer_is_owned_by_the_game(app):
    """Ownership is what keeps the dots over Avataria and nowhere else."""
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())

    overlay.show_markers([Marker(300, 200, "#ff0000")])

    assert wm.owned and wm.owned[-1][1] == 4242
    overlay.clear()


def test_without_a_game_there_is_nothing_to_draw_on(app):
    class _NoGame:
        def get_game_hwnd(self): return 0

    overlay = MarkerOverlay(_NoGame(), reference=QWidget())

    overlay.show_markers([Marker(10, 10, "#ff0000")])

    assert not overlay.isVisible()


def test_it_paints_every_phase_without_error(app):
    wm = _WM()
    overlay = MarkerOverlay(wm, reference=QWidget())
    overlay.show_markers([Marker(300, 200, "#ff0000"),
                          Marker(800, 600, "#00ffaa", solid=False)])

    for _ in range(5):
        overlay._tick()
        overlay.repaint()

    overlay.clear()
