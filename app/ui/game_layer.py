# app/ui/game_layer.py
"""A transparent layer that draws over the game and nowhere else.

Shared by everything the helper paints on top of Avataria — the node wires
between windows, the markers on found litter. All of it needs the same
awkward pieces, and they were awkward enough to get right once:

  * not re-parented into the game. Attaching a window there puts it among
    the game's own child windows, where it is painted over and never seen;
  * made an *owned* window of the game instead, which is what keeps it above
    the game and, just as importantly, takes it away the moment the user
    switches to something else. No polling of the foreground window;
  * the owner is re-applied after every show, because Qt hands the window an
    owner of its own when it puts it on screen;
  * positions asked of Windows rather than Qt: the helper's windows are
    re-parented into the game, and Qt's idea of where they are stops being
    the truth the moment that happens.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect
from PySide6.QtWidgets import QWidget


class GameLayer(QWidget):
    """Base for overlays that live above the game window."""

    def __init__(self, window_manager=None):
        super().__init__()
        self._wm    = window_manager
        self._owner = 0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowTransparentForInput
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    # ── Ownership ────────────────────────────────────────────────────────────

    def adopt(self, reference: QWidget):
        """Hand this layer to the window the reference belongs to.

        Call it *after* show(), and keep calling it: Qt replaces the owner
        whenever it shows the window, and a layer without one sinks behind
        the game where nothing is visible at all.
        """
        if self._wm is None or not hasattr(self._wm, "root_of"):
            return
        owner = self._wm.root_of(int(reference.winId()))
        if not owner or owner == int(self.winId()):
            return
        if self._wm.owner_of(int(self.winId())) != owner:
            self._owner = owner
            self._wm.set_owner(int(self.winId()), owner)
            self.raise_()

    # ── Geometry ─────────────────────────────────────────────────────────────

    def rect_of(self, window: QWidget) -> QRect:
        """A window's box in screen coordinates — the space all of them share."""
        ask = getattr(self._wm, "window_rect_screen", None)
        if ask is not None:
            rect = ask(int(window.winId()))
            if rect is not None:
                return QRect(*rect)
        return QRect(window.pos(), window.size())

    def game_rect(self) -> QRect | None:
        """The game's own box, or None when running without it."""
        if self._wm is None or not self._wm.get_game_hwnd():
            return None
        rect = self._wm.window_rect_screen(self._wm.get_game_hwnd())
        return QRect(*rect) if rect is not None else None
