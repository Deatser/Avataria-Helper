# tests/ui/test_raise_on_click.py
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.ui.raise_on_click import RaiseOnClick


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Window(QWidget):
    """Top-level window that records being raised."""

    def __init__(self):
        super().__init__()
        self.raises = 0
        self.child = QLabel("x", self)   # what a click actually lands on

    def raise_(self):
        self.raises += 1
        super().raise_()


def _press(widget):
    return QMouseEvent(QEvent.MouseButtonPress, QPointF(QPoint(1, 1)),
                       QPointF(QPoint(1, 1)), Qt.LeftButton, Qt.LeftButton,
                       Qt.NoModifier)


def test_a_press_on_a_child_raises_its_window(app):
    window = _Window()
    window.show()
    filt = RaiseOnClick()

    filt.eventFilter(window.child, _press(window.child))

    assert window.raises == 1
    window.close()


def test_other_events_are_ignored(app):
    window = _Window()
    window.show()
    filt = RaiseOnClick()

    filt.eventFilter(window.child, QEvent(QEvent.Type.Paint))

    assert window.raises == 0
    window.close()


def test_the_filter_never_swallows_the_click(app):
    window = _Window()
    window.show()

    assert RaiseOnClick().eventFilter(window.child, _press(window.child)) is False
    window.close()


def test_the_native_path_is_used_while_attached_to_the_game(app):
    raised = []

    class WM:
        def get_game_hwnd(self): return 4242
        def raise_window(self, hwnd): raised.append(hwnd)

    window = _Window()
    window.show()

    RaiseOnClick(WM()).eventFilter(window.child, _press(window.child))

    assert raised == [int(window.winId())]
    assert window.raises == 0   # Qt's own raise is not used when attached
    window.close()
