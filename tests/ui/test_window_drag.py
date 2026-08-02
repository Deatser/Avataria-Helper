# tests/ui/test_window_drag.py
"""Dragging arithmetic — the part that made the windows shake."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.ui.module_window import ModuleWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _AttachedWM:
    """A game window whose client area starts well away from (0, 0).

    That offset is the whole point: it is what tells a correct
    implementation apart from one that mixes screen and client coordinates.
    """
    CLIENT_OFFSET = QPoint(500, 300)

    def __init__(self):
        self.moves = []
        self.origin = QPoint(40, 60)   # window position, in client coordinates

    def get_game_hwnd(self): return 4242
    def window_origin(self, hwnd): return (self.origin.x(), self.origin.y())

    def move_window(self, hwnd, x, y, w, h):
        self.moves.append((x, y))
        self.origin = QPoint(x, y)


class _Window(ModuleWindow):
    def __init__(self, config, save_fn, wm):
        super().__init__("Тест", config, save_fn)
        self._wm = wm
        self.resize(200, 150)


def _press(pos):
    point = QPointF(QPoint(*pos))
    return QMouseEvent(QEvent.MouseButtonPress, point, point,
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def _move(pos):
    point = QPointF(QPoint(*pos))
    return QMouseEvent(QEvent.MouseMove, point, point,
                       Qt.NoButton, Qt.LeftButton, Qt.NoModifier)


def _window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    wm = _AttachedWM()
    return _Window(config.data.ava_dancers, config.save, wm), wm, config


def test_the_window_follows_the_mouse_exactly(tmp_path, monkeypatch, app):
    window, wm, _config = _window(tmp_path, monkeypatch)
    start = wm.origin

    window.start_drag(_press((900, 700)))
    window.do_drag(_move((940, 760)), wm)

    assert wm.moves[-1] == (start.x() + 40, start.y() + 60)
    window.close()


def test_a_drag_does_not_drift_over_many_small_steps(tmp_path, monkeypatch, app):
    """Each move is measured from where the drag began, not from the last
    one, so rounding cannot accumulate into a wobble."""
    window, wm, _config = _window(tmp_path, monkeypatch)
    start = wm.origin

    window.start_drag(_press((900, 700)))
    for step in range(1, 21):
        window.do_drag(_move((900 + step, 700 + step)), wm)

    assert wm.moves[-1] == (start.x() + 20, start.y() + 20)
    window.close()


def test_the_grab_point_is_kept_under_the_cursor(tmp_path, monkeypatch, app):
    """Grabbing a corner and moving nowhere must not shift the window."""
    window, wm, _config = _window(tmp_path, monkeypatch)
    start = wm.origin

    window.start_drag(_press((1234, 567)))
    window.do_drag(_move((1234, 567)), wm)

    assert wm.moves[-1] == (start.x(), start.y())
    window.close()


def test_the_config_is_not_written_on_every_mouse_move(tmp_path, monkeypatch, app):
    """A disk write per pixel is felt as stutter."""
    window, wm, config = _window(tmp_path, monkeypatch)
    writes = []
    monkeypatch.setattr(config, "save", lambda: writes.append(1))
    window.save_fn = config.save

    window.start_drag(_press((900, 700)))
    for step in range(30):
        window.do_drag(_move((900 + step, 700)), wm)

    assert writes == []                    # nothing yet…
    assert window._save_later.isActive()    # …but it is coming
    assert window.config.x == wm.moves[-1][0]   # and the value is already right
    window.close()
