# app/ui/raise_on_click.py
"""Click a window, get that window — even where Windows would not do it.

The helper's windows are frameless children of the game window, so none of
the usual click-to-front behaviour applies to them: the OS only reorders
top-level windows, and these are siblings inside someone else's client area.
Without this, a window that ends up underneath can only be recovered by
moving the one on top of it.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent
from PySide6.QtWidgets import QWidget


class RaiseOnClick(QObject):
    """Application-wide filter: any press raises the window it landed in.

    Installed on the application rather than on each window because a press
    is delivered to whatever child widget is under it — a button, a label,
    the log — and a filter on the window itself would never see those.
    Every event in the app passes through here, so the type check comes
    first and nothing else happens until it matches.
    """

    def __init__(self, window_manager=None, parent=None):
        super().__init__(parent)
        self._wm = window_manager

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            self._raise_window_of(obj)
        return False   # never consume: the click still has work to do

    # ── Internals ────────────────────────────────────────────────────────────

    def _raise_window_of(self, obj):
        if not isinstance(obj, QWidget):
            return   # QWindow-level events; the widget press follows anyway
        window = obj.window()
        if window is None or not window.isVisible():
            return
        self._raise(window)

    def _raise(self, window: QWidget):
        """Native raise while attached to the game, Qt's own otherwise."""
        if self._wm is not None and self._wm.get_game_hwnd():
            self._wm.raise_window(int(window.winId()))
        else:
            window.raise_()
