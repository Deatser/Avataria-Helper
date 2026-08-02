# app/ui/drag_mixin.py
"""Drag a frameless window by any empty part of it, not just its title."""
from PySide6.QtCore import Qt


class BackgroundDragMixin:
    """Turns the window's own background into a drag handle.

    Nothing has to be excluded by hand: a press that lands on a real control
    is delivered to that control and never reaches the window, so buttons,
    switches, the log and the settings sheet keep behaving normally. What is
    left over — the panel itself, headings, captions, the space around the
    tiles — is exactly the area with nothing better to do than move the
    window.

    Inherit before ResizeMixin so the edges still win: a press within the
    resize margin is claimed there first, and this only picks up what falls
    through. Implement _drag_begin / _drag_to to say how the window moves —
    attached windows go through the window manager, standalone ones do not.
    """

    def _init_background_drag(self):
        self._bg_dragging = False

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and not getattr(self, "_rsz_dir", 0):
            self._bg_dragging = True
            self._drag_begin(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if getattr(self, "_bg_dragging", False) and (event.buttons() & Qt.LeftButton):
            self._drag_to(event)

    def mouseReleaseEvent(self, event):
        self._bg_dragging = False
        super().mouseReleaseEvent(event)

    def _idle_cursor(self):
        """The whole background drags, so say so wherever it does.

        Only the window's own surface is affected: buttons, switches and
        checkboxes carry a pointing hand of their own, and a widget with a
        cursor set keeps it, so nothing that is not draggable claims to be.
        """
        return Qt.SizeAllCursor

    # ── Subclass hooks ───────────────────────────────────────────────────────

    def _drag_begin(self, event):
        """Remember where inside the window the drag started."""

    def _drag_to(self, event):
        """Move the window so that point follows the cursor."""
