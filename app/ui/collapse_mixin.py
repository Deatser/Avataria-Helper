# app/ui/collapse_mixin.py
"""Mixin that rolls a frameless window up into its header strip."""
from PySide6.QtCore import (QVariantAnimation, QEasingCurve, QAbstractAnimation,
                            QTimer)

from app.ui import theme


class CollapseMixin:
    """
    Inherit before QWidget. Call _init_collapse(panel) after the UI is built;
    `panel` is the background widget that carries the window's layout, and its
    first layout item must be the header row.

    While collapsing, the panel's layout is switched off so the widgets below
    the header keep their geometry and get clipped by the shrinking window —
    that is what makes the content roll up instead of squeezing together.
    """
    _COLLAPSE_MS = 190

    def _init_collapse(self, panel, window_manager=None):
        self._c_panel      = panel
        self._c_wm         = window_manager
        self._collapsed    = False
        self._c_anim       = None
        self._c_origin     = None
        self._c_native_pin = False
        self._c_last_h     = None
        self._c_expanded_h = self.height()

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def _toggle_collapse(self):
        """Slot for the header button: toggles and flips the arrow."""
        self.toggle_collapse()
        button = getattr(self, "_collapse_btn", None)
        if button is not None:
            button.setText("▼" if self._collapsed else "▲")

    def toggle_collapse(self):
        if self._c_anim and self._c_anim.state() == QAbstractAnimation.State.Running:
            return

        layout = self._c_panel.layout()
        if self._collapsed:
            target = self._c_expanded_h
        else:
            self._c_expanded_h = self.height()
            target = self._collapsed_height()
            layout.setEnabled(False)
            # A window minimum (module windows set one) would clamp the roll-up
            self._c_min_h = self.minimumHeight()
            self.setMinimumHeight(0)

        expanding = self._collapsed
        self._collapsed = not self._collapsed
        self._c_capture_origin()

        anim = QVariantAnimation(self)
        anim.setDuration(self._COLLAPSE_MS)
        anim.setStartValue(self.height())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.valueChanged.connect(self._c_set_height)
        anim.finished.connect(lambda: self._c_finish(expanding))
        self._c_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    # ── Internals ────────────────────────────────────────────────────────────

    def _collapsed_height(self) -> int:
        """Header row plus the panel's own padding."""
        header = self._c_panel.layout().itemAt(0)
        return header.geometry().bottom() + theme.PADDING + 1

    def _c_attached(self) -> bool:
        return self._c_wm is not None and bool(self._c_wm.get_game_hwnd())

    def _c_capture_origin(self):
        """Freeze the top-left corner for the whole animation."""
        origin = None
        if self._c_attached():
            origin = self._c_wm.window_origin(int(self.winId()))
        self._c_native_pin = origin is not None
        self._c_origin = origin if origin is not None else (self.x(), self.y())

    def _c_set_height(self, value):
        self._c_pin(int(value))
        self._on_resize_panel()

    def _c_pin(self, height: int = None):
        """Re-apply the frozen position with the given height.

        Once the window is a WS_CHILD of the game, Qt's own resize path
        re-applies its cached position in the wrong coordinate space, so the
        window jumps. Setting x/y explicitly every frame overrides that.
        """
        if self._c_origin is None:
            return
        if height is not None:
            self._c_last_h = height
        x, y = self._c_origin
        h = self._c_last_h or self.height()
        if self._c_native_pin and self._c_wm.set_window_rect(
                int(self.winId()), x, y, self.width(), h):
            return
        self.setGeometry(x, y, self.width(), h)

    def _c_finish(self, expanding: bool):
        self._c_anim = None
        if expanding:
            self.setMinimumHeight(getattr(self, "_c_min_h", 0))
            layout = self._c_panel.layout()
            layout.setEnabled(True)
            layout.activate()
            self._on_resize_panel()
        # Re-pin after the layout settles, then once more after Qt has flushed
        # whatever geometry it queued behind us.
        self._c_pin()
        QTimer.singleShot(0, self._c_pin)
