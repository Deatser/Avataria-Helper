# app/ui/node_links.py
"""Blender-style wires between the helper and the windows it opened.

A window cannot paint outside itself, so the wires live on their own: one
transparent, click-through layer floating over everything, spanning just the
area the wires need and with the windows themselves cut out of it, so a wire
only ever shows in the gap between them. It follows the windows rather than
being told about them — dragging, resizing and collapsing all move the
sockets, and watching the geometry catches every one of those without a hook
in each.

The animation matches the CRT effect the windows themselves use: the same
cubic easing, the same cold-white core inside a coloured glow, and a wire
that draws itself out of the parent socket instead of just appearing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import (Qt, QEasingCurve, QPointF, QRect, QTimer,
                            QVariantAnimation)
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen,
                           QRegion)
from PySide6.QtWidgets import QWidget

from app.ui import theme
from app.ui.game_layer import GameLayer

GROW_MS   = 380    # matches the window switch-on
RETRACT_MS = 320

# 240 Hz. Affordable only because a tick that finds nothing moved does
# nothing: reading two window rectangles costs a couple of microseconds,
# while repainting a full-screen translucent layer does not, so the repaint
# is asked for only when the geometry actually changed.
_FOLLOW_MS   = 4
_ADOPT_EVERY = 40     # ticks between ownership checks, ~160 ms
_PAD         = 40     # room around the windows for the wire's own curvature
_SOCKET_R    = 5.0
_WIRE_W      = 2.0
_CURVE       = 0.55   # control-point reach, as a share of the gap
_MIN_CURVE   = 45.0
_CORE        = QColor(255, 255, 255)

# Sockets sit at this share of a window's height — a little above the middle,
# where a node's first output row would be, and still on the visible bar when
# the window is collapsed to its header.
_SOCKET_Y = 0.5


@dataclass
class _Link:
    parent: QWidget
    child: QWidget
    accent: str
    progress: float = 0.0
    dying: bool = False
    anim: QVariantAnimation = field(default=None, repr=False)


class NodeLinkCanvas(GameLayer):
    """The layer every wire is drawn on. See GameLayer for the window itself."""

    def __init__(self, window_manager=None):
        super().__init__(window_manager)
        self._links: list[_Link] = []
        self._enabled  = True   # SettingsWindow's own "Линии между окнами"
        self._rect     = QRect()

        self._geom: dict[int, QRect] = {}   # window id → last seen rectangle
        self._ticks = 0

        self._follow = QTimer(self)
        self._follow.setTimerType(Qt.PreciseTimer)   # coarse timers round to ~20 ms
        self._follow.setInterval(_FOLLOW_MS)
        self._follow.timeout.connect(self._sync)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        """Purely cosmetic, so turning it off just means no new wire ever
        gets drawn — whatever is already on screen is dropped immediately,
        same as clear(), rather than left to retract on its own."""
        self._enabled = enabled
        if not enabled:
            self.clear()

    def connect_windows(self, parent: QWidget, child: QWidget, accent: str):
        """Draw a wire out to a window that has just opened."""
        if not self._enabled:
            return
        self.disconnect_window(child, animate=False)
        link = _Link(parent, child, accent or theme.ACCENT)
        link.anim = self._animation(link, 0.0, 1.0, GROW_MS)
        self._links.append(link)
        # Showing is left to _sync: with a starred window the overlay may not
        # be on screen yet, and putting up an empty canvas then would only
        # mean a transparent window sitting over the game with nothing in it.
        self._sync()
        link.anim.start()

    def disconnect_window(self, child: QWidget, animate: bool = True):
        """Pull the wire back in; the link is dropped when it has retracted."""
        for link in [l for l in self._links if l.child is child]:
            if link.anim is not None:
                link.anim.stop()
            if not animate:
                self._links.remove(link)
                continue
            if link.dying:
                continue
            link.dying = True
            link.anim  = self._animation(link, link.progress, 0.0, RETRACT_MS)
            link.anim.finished.connect(lambda l=link: self._drop(l))
            link.anim.start()
        self._sync()

    def clear(self):
        for link in list(self._links):
            if link.anim is not None:
                link.anim.stop()
        self._links.clear()
        self._follow.stop()
        self.hide()

    # ── Animation plumbing ───────────────────────────────────────────────────

    def _animation(self, link: _Link, start: float, end: float,
                   duration: int) -> QVariantAnimation:
        anim = QVariantAnimation(self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(lambda v, l=link: self._on_progress(l, v))
        return anim

    def _on_progress(self, link: _Link, value):
        link.progress = float(value)
        self.update()

    def _drop(self, link: _Link):
        if link in self._links:
            self._links.remove(link)
        if not self._links:
            self._follow.stop()
            self.hide()

    # ── Geometry ─────────────────────────────────────────────────────────────

    def _live_links(self) -> list[_Link]:
        return [l for l in self._links
                if l.parent.isVisible() and (l.child.isVisible() or l.dying)]


    def _sync(self):
        """Re-read where the windows are; hide when the helper is not in view."""
        if not self._links:
            if self.isVisible():
                self.hide()
            self._follow.stop()
            return

        links = self._live_links()
        if not links:
            # A wire whose windows are not on screen *yet* — the starred ones
            # are opened while the overlay is still coming up. Keep watching:
            # giving up here is what left those wires missing until the window
            # was closed and opened again by hand.
            if self.isVisible():
                self.hide()
            self._follow.start()
            return

        if not self._follow.isActive():
            self._follow.start()

        moved = self._read_geometry(links)
        area  = self._canvas_area(links)
        if area != self._rect:
            self._rect = area
            self.setGeometry(area)
        if not self.isVisible():
            self.show()
            self.raise_()
            moved = True

        # After show(), never before: Qt hands the window an owner of its own
        # when it puts it on screen, wiping ours. Re-checked periodically for
        # the same reason, but not every tick — nothing takes the ownership
        # away on its own, and at 240 Hz that would be pure noise.
        self._ticks += 1
        if self._ticks % _ADOPT_EVERY == 1:
            self.adopt(links[0].parent)

        if moved:
            self.update()

    def _read_geometry(self, links: list[_Link]) -> bool:
        """Refresh the cached window rectangles; True if any of them moved.

        Read once per tick and reused by everything that paints — sockets,
        cut-outs, canvas size — instead of each asking Windows again.
        """
        geometry = {}
        for window in {w for link in links for w in (link.parent, link.child)}:
            geometry[int(window.winId())] = self.rect_of(window)
        moved = geometry != self._geom
        self._geom = geometry
        return moved

    def _cached_rect(self, window: QWidget) -> QRect:
        rect = self._geom.get(int(window.winId()))
        return rect if rect is not None else self.rect_of(window)
        if not self._follow.isActive():
            self._follow.start()
        self.update()

    def _canvas_area(self, links: list[_Link]) -> QRect:
        """The game's whole box, or just around the windows when standalone.

        Deliberately something that hardly ever changes: sizing the canvas to
        fit the wires meant moving a window on every frame of every drag, and
        that is what made the lines jitter. Now the canvas stands still and
        only what is painted inside it moves.
        """
        if self._wm is not None and self._wm.get_game_hwnd():
            rect = self._wm.window_rect_screen(self._wm.get_game_hwnd())
            if rect is not None:
                return QRect(*rect)

        area = QRect()
        for link in links:
            area = area.united(self._cached_rect(link.parent))
            area = area.united(self._cached_rect(link.child))
        return area.adjusted(-_PAD, -_PAD, _PAD, _PAD)

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        offset = self._rect.topLeft()
        links  = self._live_links()

        # The canvas floats above everything, so the windows are cut out of it
        # instead of it being ordered behind them. Same result as a node
        # editor's links passing under the nodes, and it does not depend on
        # winning a z-order fight with the game.
        painter.setClipRegion(self._gap_region(links, offset))

        for link in links:
            self._draw_link(painter, link, *self._sockets(link, offset))
        painter.end()

    def _gap_region(self, links: list[_Link], offset) -> QRegion:
        """Everything the canvas covers, minus the windows themselves.

        The sockets sit right on the window edges, so each cut-out is pulled
        in by a socket's radius — otherwise half of every socket would be
        clipped away with the window it belongs to.
        """
        region = QRegion(self.rect())
        inset  = int(_SOCKET_R * 2.4)
        for window in {w for link in links for w in (link.parent, link.child)}:
            rect = self._cached_rect(window).translated(-offset.x(), -offset.y())
            region -= QRegion(rect.adjusted(inset, inset, -inset, -inset))
        return region

    def _sockets(self, link: _Link, offset):
        """Where each wire meets each window, and which way it leaves.

        The socket is the point on the window's border facing the other
        window — so it slides along the edge as the windows move, rounds the
        corner onto the top or the bottom when one sits above the other, and
        never jumps from one side to the opposite the way a fixed left/right
        pair does.
        """
        pr = self._cached_rect(link.parent).translated(-offset.x(), -offset.y())
        cr = self._cached_rect(link.child).translated(-offset.x(), -offset.y())

        start, start_dir = _border_point(pr, _centre(cr))
        end,   end_dir   = _border_point(cr, _centre(pr))
        return start, start_dir, end, end_dir

    def _draw_link(self, painter: QPainter, link: _Link, start: QPointF,
                   start_dir: QPointF, end: QPointF, end_dir: QPointF):
        t = max(0.0, min(1.0, link.progress))
        if t <= 0.0:
            return
        accent = QColor(link.accent)

        # The socket on the open window shows first, then the wire reaches out
        self._draw_socket(painter, start, accent, min(1.0, t / 0.3))

        path = self._curve(start, start_dir, end, end_dir, t)
        for width, colour, alpha in ((_WIRE_W * 3.2, accent, 40),
                                     (_WIRE_W * 1.8, accent, 110),
                                     (_WIRE_W, _CORE, 230)):
            pen_colour = QColor(colour)
            pen_colour.setAlpha(int(alpha * t))
            painter.setPen(QPen(pen_colour, width, Qt.SolidLine,
                                Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)

        if t > 0.75:   # the far socket lands once the wire has arrived
            self._draw_socket(painter, end, accent, (t - 0.75) / 0.25)

    def _curve(self, start: QPointF, start_dir: QPointF, end: QPointF,
               end_dir: QPointF, t: float) -> QPainterPath:
        """A node-editor bezier, drawn only as far as it has grown.

        Each end leaves its socket along the window's outward normal, so a
        wire between windows stacked vertically bows out of the bottom and
        into the top instead of being forced sideways.
        """
        span  = ((end.x() - start.x()) ** 2 + (end.y() - start.y()) ** 2) ** 0.5
        reach = max(_MIN_CURVE, span * _CURVE)
        c1 = QPointF(start.x() + start_dir.x() * reach,
                     start.y() + start_dir.y() * reach)
        c2 = QPointF(end.x() + end_dir.x() * reach,
                     end.y() + end_dir.y() * reach)

        path  = QPainterPath(start)
        steps = 48
        for i in range(1, int(steps * t) + 1):
            path.lineTo(_bezier(start, c1, c2, end, i / steps))
        return path

    def _draw_socket(self, painter: QPainter, centre: QPointF,
                     accent: QColor, t: float):
        t = max(0.0, min(1.0, t))
        if t <= 0.0:
            return
        radius = _SOCKET_R * t

        halo = QColor(accent)
        halo.setAlpha(int(70 * t))
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(centre, radius * 2.2, radius * 2.2)

        body = QColor(accent)
        body.setAlpha(int(255 * t))
        painter.setBrush(body)
        painter.drawEllipse(centre, radius, radius)

        ring = QColor(_CORE)
        ring.setAlpha(int(200 * t))
        painter.setPen(QPen(ring, 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(centre, radius, radius)


def _centre(rect: QRect) -> QPointF:
    """True middle of a rectangle.

    Not QRect.center(): that one works in whole pixels on an inclusive
    rectangle, and rounding there walks the socket a pixel off the edge it
    is supposed to be sitting on.
    """
    return QPointF(rect.x() + rect.width() / 2.0,
                   rect.y() + rect.height() / 2.0)


def _border_point(rect: QRect, target: QPointF) -> tuple[QPointF, QPointF]:
    """Point on rect's border facing `target`, and the outward normal there.

    Found by walking from the middle of the window toward the other one and
    stopping at whichever edge is reached first, which is what makes the
    socket travel smoothly along the border and turn corners instead of
    snapping between fixed positions.
    """
    centre = _centre(rect)
    dx = target.x() - centre.x()
    dy = target.y() - centre.y()
    half_w = max(1.0, rect.width() / 2)
    half_h = max(1.0, rect.height() / 2)

    if dx == 0 and dy == 0:
        return QPointF(centre.x(), centre.y() + half_h), QPointF(0.0, 1.0)

    # How far along the direction each pair of edges lies; the nearer wins.
    to_side = half_w / abs(dx) if dx else float("inf")
    to_cap  = half_h / abs(dy) if dy else float("inf")
    scale   = min(to_side, to_cap)
    point   = QPointF(centre.x() + dx * scale, centre.y() + dy * scale)

    if to_side <= to_cap:
        normal = QPointF(1.0 if dx > 0 else -1.0, 0.0)
    else:
        normal = QPointF(0.0, 1.0 if dy > 0 else -1.0)
    return point, normal


def _bezier(p0: QPointF, p1: QPointF, p2: QPointF, p3: QPointF,
            t: float) -> QPointF:
    u = 1.0 - t
    return QPointF(
        u * u * u * p0.x() + 3 * u * u * t * p1.x()
        + 3 * u * t * t * p2.x() + t * t * t * p3.x(),
        u * u * u * p0.y() + 3 * u * u * t * p1.y()
        + 3 * u * t * t * p2.y() + t * t * t * p3.y(),
    )
