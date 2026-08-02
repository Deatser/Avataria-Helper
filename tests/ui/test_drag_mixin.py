# tests/ui/test_drag_mixin.py
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.ui.drag_mixin import BackgroundDragMixin
from app.ui.resize_mixin import ResizeMixin


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Window(BackgroundDragMixin, ResizeMixin, QWidget):
    """The real stack a module window uses, minus the decoration."""

    def __init__(self):
        super().__init__()
        self.resize(300, 200)
        self._init_resize()
        self._init_background_drag()
        self.begun = 0
        self.moves = 0
        self.button = QPushButton("x", self)
        self.button.move(100, 100)

    def _drag_begin(self, event):
        self.begun += 1

    def _drag_to(self, event):
        self.moves += 1


def _mouse(kind, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
    point = QPointF(QPoint(*pos))
    return QMouseEvent(kind, point, point, button, buttons, Qt.NoModifier)


_MIDDLE = (150, 100)   # well clear of the 8px resize margin
_EDGE   = (2, 100)     # inside it


def test_pressing_the_background_starts_a_drag(app):
    window = _Window()

    window.mousePressEvent(_mouse(QEvent.MouseButtonPress, _MIDDLE))
    window.mouseMoveEvent(_mouse(QEvent.MouseMove, (170, 120), Qt.NoButton))

    assert window.begun == 1
    assert window.moves == 1


def test_the_drag_stops_at_release(app):
    window = _Window()
    window.mousePressEvent(_mouse(QEvent.MouseButtonPress, _MIDDLE))
    window.mouseReleaseEvent(
        _mouse(QEvent.MouseButtonRelease, _MIDDLE, buttons=Qt.NoButton))

    window.mouseMoveEvent(_mouse(QEvent.MouseMove, (200, 150), Qt.NoButton))

    assert window.moves == 0


def test_a_resize_edge_still_wins_over_dragging(app):
    """Otherwise the window would move instead of resizing at every border."""
    window = _Window()

    window.mousePressEvent(_mouse(QEvent.MouseButtonPress, _EDGE))
    window.mouseMoveEvent(_mouse(QEvent.MouseMove, (2, 130), Qt.NoButton))

    assert window.begun == 0
    assert window.moves == 0


def test_moving_without_a_press_moves_nothing(app):
    window = _Window()

    window.mouseMoveEvent(_mouse(QEvent.MouseMove, _MIDDLE, Qt.NoButton))

    assert window.moves == 0


def test_a_press_on_a_button_never_reaches_the_window(app):
    """Qt delivers it to the button, which is what keeps controls usable —
    the window only ever sees what nothing else wanted."""
    window = _Window()
    window.show()

    target = window.childAt(QPoint(105, 105))

    assert target is window.button
    window.close()


def test_the_background_advertises_that_it_drags(app):
    """Everything that can be dragged should look like it can."""
    window = _Window()

    window.mouseMoveEvent(_mouse(QEvent.MouseMove, _MIDDLE, Qt.NoButton,
                                 Qt.NoButton))

    assert window.cursor().shape() == Qt.SizeAllCursor


def test_the_resize_edges_still_get_their_own_cursor(app):
    window = _Window()

    window.mouseMoveEvent(_mouse(QEvent.MouseMove, _EDGE, Qt.NoButton,
                                 Qt.NoButton))

    assert window.cursor().shape() == Qt.SizeHorCursor


def test_controls_keep_their_own_cursor(app):
    """A widget with a cursor of its own is untouched by the window's."""
    window = _Window()

    assert window.button.cursor().shape() != Qt.SizeAllCursor


def test_the_right_button_does_not_drag(app):
    window = _Window()

    window.mousePressEvent(
        _mouse(QEvent.MouseButtonPress, _MIDDLE, Qt.RightButton, Qt.RightButton))

    assert window.begun == 0
