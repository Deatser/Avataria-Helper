# app/ui/sheet_panel.py
"""A sheet that floats in the middle of the window that owns it.

Not a window of its own: an earlier version was, re-parented onto the host
with SetParent, and it flashed and vanished. Qt does not know about a native
re-parent, so the first time it re-applied its own geometry it used screen
coordinates on a window whose coordinates had become parent-relative —
several hundred pixels outside a 438x535 parent, i.e. clipped away. As a
plain child widget it simply moves with the host, and nothing has to track
anything.

Not modal either: whatever the module is doing carries on while the sheet is
open, and changes take effect as they are made rather than on an OK button.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QPoint

from app.ui import theme
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_button import NtButton


class SheetPanel(QWidget):
    """Header, frame, dragging and placement. Subclasses fill the body."""

    def __init__(self, host: QWidget, title: str, size: tuple[int, int],
                 accent: str = None, close_accent: str = None):
        super().__init__(host)
        self._accent   = accent or theme.ACCENT
        self._drag_at: QPoint | None = None   # grab point, panel-local
        self._moved    = False   # dragged at least once → stop re-centring it

        self.setFixedSize(*size)
        # The sheet drags by its background too, so it says so everywhere its
        # own surface shows; its buttons and switches override this.
        self.setCursor(Qt.SizeAllCursor)
        self._build(title, close_accent or theme.VW_MAGENTA)
        self.hide()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build(self, title: str, close_accent: str):
        self._panel = NtPanel(self, traces=False)
        self._panel.setGeometry(0, 0, self.width(), self.height())

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING + 2, theme.PADDING,
                                  theme.PADDING + 2, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        heading.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        close_btn = NtButton("×", accent=close_accent)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.hide)
        header.addWidget(heading)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        rule = QLabel()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background:{theme.BORDER_DIM};")
        layout.addWidget(rule)

        self._build_body(layout)
        layout.addStretch()

    def _build_body(self, layout: QVBoxLayout):
        """Everything under the heading. Implemented by the subclass."""

    def caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        return label

    def hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        label.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
        return label

    # ── Placement ────────────────────────────────────────────────────────────

    def centre_on_host(self):
        host = self.parentWidget()
        if host is None:
            return
        self.move(max(0, (host.width()  - self.width())  // 2),
                  max(0, (host.height() - self.height()) // 2))

    def keep_inside_host(self):
        """Called when the host resizes: re-centre, or re-clamp if dragged.

        Snapping a hand-placed sheet back to the middle on every resize step
        would fight the user, so once it has been moved it only gets pulled
        back far enough to stay fully inside.
        """
        if self._moved:
            self.move(*self._clamped(self.x(), self.y()))
        else:
            self.centre_on_host()

    def _clamped(self, x: int, y: int) -> tuple[int, int]:
        """Position kept fully inside the host window.

        A host smaller than the sheet clamps to 0 rather than to a negative
        limit — better pinned to the top-left corner than parked off-screen.
        """
        host = self.parentWidget()
        if host is None:
            return x, y
        return (max(0, min(x, host.width()  - self.width())),
                max(0, min(y, host.height() - self.height())))

    def toggle(self):
        if self.isVisible():
            self.hide()
            return
        self.keep_inside_host()
        self.raise_()   # above the module's backdrop panel, which is a sibling
        self.show()

    # ── Drag ─────────────────────────────────────────────────────────────────
    # A child widget, so dragging is plain arithmetic in the host's
    # coordinates — no win32, and the clamp is what keeps it from being
    # dragged out from under its own window.

    def _drag_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_at = event.position().toPoint()

    def _drag_move(self, event):
        if self._drag_at is None or not (event.buttons() & Qt.LeftButton):
            return
        target = (self.pos() + event.position().toPoint() - self._drag_at)
        self.move(*self._clamped(target.x(), target.y()))
        self._moved = True

    def _drag_release(self, event):
        self._drag_at = None

    # Bare panel background: NtPanel does not take mouse events, so they fall
    # through to here and the whole sheet drags, not just its title.
    def mousePressEvent(self, event):
        self._drag_press(event)

    def mouseMoveEvent(self, event):
        self._drag_move(event)

    def mouseReleaseEvent(self, event):
        self._drag_release(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._panel.setGeometry(0, 0, self.width(), self.height())
