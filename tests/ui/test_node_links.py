# tests/ui/test_node_links.py
import time

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

from app.ui.node_links import GROW_MS, RETRACT_MS, NodeLinkCanvas


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


def _windows():
    parent, child = QWidget(), QWidget()
    parent.setGeometry(100, 100, 300, 400)
    child.setGeometry(600, 200, 300, 400)
    parent.show()
    child.show()
    return parent, child


def test_connecting_adds_a_wire_and_shows_the_canvas(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()

    canvas.connect_windows(parent, child, "#ff00dd")

    assert len(canvas._links) == 1
    assert canvas.isVisible()
    canvas.clear()
    parent.close(); child.close()


def test_the_wire_grows_in_rather_than_appearing(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()

    canvas.connect_windows(parent, child, "#ff00dd")
    partway = canvas._links[0].progress
    _pump(app, GROW_MS / 1000 + 0.2)

    assert partway < 1.0
    assert canvas._links[0].progress == pytest.approx(1.0)
    canvas.clear()
    parent.close(); child.close()


def test_disconnecting_retracts_then_drops_the_wire(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    _pump(app, GROW_MS / 1000 + 0.2)

    canvas.disconnect_window(child)
    assert len(canvas._links) == 1        # still there, reeling in
    assert canvas._links[0].dying is True

    _pump(app, RETRACT_MS / 1000 + 0.3)

    assert canvas._links == []
    assert not canvas.isVisible()          # nothing left to draw
    parent.close(); child.close()


def test_a_second_disconnect_does_not_restart_the_retraction(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")

    canvas.disconnect_window(child)
    first = canvas._links[0].anim
    canvas.disconnect_window(child)

    assert canvas._links[0].anim is first
    canvas.clear()
    parent.close(); child.close()


def test_the_canvas_spans_both_windows(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()

    canvas.connect_windows(parent, child, "#ff00dd")

    area = canvas._rect
    assert area.left() < parent.x()          # padded out on every side
    assert area.right() > child.x() + child.width()
    canvas.clear()
    parent.close(); child.close()


def test_the_sockets_sit_on_the_facing_edges(app):
    parent, child = _windows()          # child sits to the right
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    link = canvas._links[0]

    start, start_dir, end, end_dir = canvas._sockets(link,
                                                    canvas._rect.topLeft())

    assert start.x() == pytest.approx(parent.x() + parent.width()
                                      - canvas._rect.left())
    assert end.x() == pytest.approx(child.x() - canvas._rect.left())
    assert (start_dir.x(), start_dir.y()) == (1.0, 0.0)   # wire leaves right
    assert (end_dir.x(), end_dir.y()) == (-1.0, 0.0)      # and arrives left
    canvas.clear()
    parent.close(); child.close()


def test_the_sockets_round_the_corner_onto_the_bottom_edge(app):
    """A window straight below should be met underneath, not off the side."""
    parent, child = _windows()
    parent.setGeometry(400, 100, 300, 200)
    child.setGeometry(430, 600, 300, 200)
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    link = canvas._links[0]

    start, start_dir, end, end_dir = canvas._sockets(link,
                                                    canvas._rect.topLeft())

    assert (start_dir.x(), start_dir.y()) == (0.0, 1.0)   # out of the bottom
    assert (end_dir.x(), end_dir.y()) == (0.0, -1.0)      # into the top
    assert start.y() == pytest.approx(parent.y() + parent.height()
                                      - canvas._rect.top())
    canvas.clear()
    parent.close(); child.close()


def test_the_socket_slides_along_the_edge_as_a_window_moves(app):
    """No jumping between fixed positions — it travels."""
    parent, child = _windows()
    parent.setGeometry(300, 300, 300, 300)
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    link = canvas._links[0]

    seen = []
    for y in (320, 380, 440, 500):
        child.move(900, y)
        canvas._sync()          # what the follow timer does, 240 times a second
        start, _sd, _e, _ed = canvas._sockets(link, canvas._rect.topLeft())
        seen.append(start.y())

    assert seen == sorted(seen)                    # moves one way, smoothly
    assert max(seen) - min(seen) > 5               # and actually moves
    canvas.clear()
    parent.close(); child.close()


def test_a_wire_drawn_before_its_windows_are_shown_still_arrives(app):
    """Starred windows are opened while the overlay is still coming up, so
    the wire is asked for before either end is on screen."""
    parent, child = QWidget(), QWidget()      # deliberately not shown yet
    parent.setGeometry(100, 100, 300, 400)
    child.setGeometry(600, 200, 300, 400)
    canvas = NodeLinkCanvas()

    canvas.connect_windows(parent, child, "#ff00dd")

    assert not canvas.isVisible()             # nothing to connect yet
    assert canvas._follow.isActive()          # but it keeps watching

    parent.show()
    child.show()
    canvas._sync()

    assert canvas.isVisible()
    canvas.clear()
    parent.close(); child.close()


def test_the_canvas_keeps_watching_once_a_wire_is_up(app):
    """Without this the wire is only ever right for as long as its own
    appear animation runs, and then stops following the windows entirely."""
    parent, child = _windows()
    canvas = NodeLinkCanvas()

    canvas.connect_windows(parent, child, "#ff00dd")
    _pump(app, GROW_MS / 1000 + 0.2)      # let the animation finish

    assert canvas._follow.isActive()
    canvas.clear()
    parent.close(); child.close()


def test_the_wire_actually_moves_with_a_dragged_window(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    canvas._sync()
    before = canvas._sockets(canvas._links[0], canvas._rect.topLeft())[0]

    child.move(child.x(), child.y() + 120)
    canvas._sync()
    after = canvas._sockets(canvas._links[0], canvas._rect.topLeft())[0]

    assert after.y() != before.y()
    canvas.clear()
    parent.close(); child.close()


def test_a_tick_with_nothing_moving_asks_for_no_repaint(app):
    """What makes 240 Hz affordable: the expensive half only runs on change."""
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    canvas._sync()

    assert canvas._read_geometry(canvas._live_links()) is False

    child.move(child.x() + 30, child.y())

    assert canvas._read_geometry(canvas._live_links()) is True
    canvas.clear()
    parent.close(); child.close()


def test_the_wire_follows_a_window_that_moves(app):
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    before = canvas._rect

    child.move(1200, 700)
    canvas._sync()

    assert canvas._rect != before
    canvas.clear()
    parent.close(); child.close()


class _WM:
    """A window manager that reports screen rectangles of its own."""

    def __init__(self, rects):
        self.rects = rects
        self.asked = []
        self.owned = []

    def get_game_hwnd(self): return 4242
    def root_of(self, hwnd): return 4242
    def owner_of(self, hwnd): return self.owned[-1][1] if self.owned else 0
    def set_owner(self, hwnd, owner): self.owned.append((hwnd, owner))

    def window_rect_screen(self, hwnd):
        self.asked.append(hwnd)
        return self.rects.get(hwnd)


def test_positions_come_from_windows_not_from_qt(app):
    """The helper's windows are re-parented into the game, and from then on
    Qt's idea of where they are is not the truth."""
    parent, child = _windows()
    moved = {int(parent.winId()): (1000, 1000, 300, 400),
             int(child.winId()):  (1600, 1100, 300, 400)}
    canvas = NodeLinkCanvas(_WM(moved))

    canvas.connect_windows(parent, child, "#ff00dd")

    assert canvas.rect_of(parent) == QRect(1000, 1000, 300, 400)
    assert canvas._rect.contains(QRect(1000, 1000, 300, 400))
    canvas.clear()
    parent.close(); child.close()


def test_the_canvas_is_owned_by_the_window_the_wires_belong_to(app):
    """Ownership is what keeps it over the game and only over the game —
    Windows hides an owned window whenever its owner is not in front."""
    parent, child = _windows()
    wm = _WM({})
    canvas = NodeLinkCanvas(wm)

    canvas.connect_windows(parent, child, "#ff00dd")

    assert wm.owned == [(int(canvas.winId()), 4242)]
    canvas._sync()
    assert wm.owned == [(int(canvas.winId()), 4242)]   # set once, not per tick
    canvas.clear()
    parent.close(); child.close()


def test_the_windows_are_cut_out_of_the_canvas(app):
    """The wire floats above everything, so it is clipped rather than
    ordered behind — a z-order fight with the game is not winnable."""
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")
    offset = canvas._rect.topLeft()

    region = canvas._gap_region(canvas._live_links(), offset)

    inside_parent = parent.geometry().center() - offset
    between = QPoint((parent.x() + parent.width() + child.x()) // 2,
                     parent.y() + 200) - offset
    assert not region.contains(inside_parent)   # never drawn over a window
    assert region.contains(between)             # only in the gap
    canvas.clear()
    parent.close(); child.close()


def test_a_hidden_window_takes_its_wire_off_the_screen(app):
    """Collapsing or hiding a window should not leave a wire in mid-air."""
    parent, child = _windows()
    canvas = NodeLinkCanvas()
    canvas.connect_windows(parent, child, "#ff00dd")

    child.hide()
    canvas._sync()

    assert canvas._live_links() == []
    assert not canvas.isVisible()
    canvas.clear()
    parent.close(); child.close()
