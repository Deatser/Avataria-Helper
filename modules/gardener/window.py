# modules/gardener/window.py
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer

from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.gd_panel import GdPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot

_DEFAULT_W = 380
_DEFAULT_H = 420
_MIN_W     = 300
_MIN_H     = 260


class GardenerWindow(ModuleWindow):
    """Садовник — the shell only: header, frame and the usual window habits.

    Everything the other module windows do comes from ModuleWindow and the
    overlay: the switch-on and switch-off animations, the wire back to the
    helper, dragging by any empty spot, resizing by the edges, collapsing,
    the star, and the remembered position. What goes inside is still to be
    decided, so the body is deliberately empty rather than filled with
    controls that would have to be thrown away.
    """

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Садовник", config, save_fn, parent_overlay)
        self._wm = window_manager
        self.resize(max(getattr(config, "width",  _DEFAULT_W), _MIN_W),
                    max(getattr(config, "height", _DEFAULT_H), _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = GdPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background:{theme.GD_BORDER};")
        layout.addWidget(separator)

        # The body is empty on purpose — see the class docstring.
        layout.addStretch()

        drag = NtDragHandle(dot_color=theme.GD_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        # Offline, not stopped: there is nothing to run here yet, and a red
        # dot on an empty window reads as something having gone wrong.
        self._status_dot = NtStatusDot(accent=theme.GD_OLIVE)
        self._status_dot.set_offline()

        title = QLabel("САДОВНИК")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.GD_OLIVE}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.GD_OLIVE)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.GD_OLIVE_SOFT)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)

        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._collapse_btn)
        header.addWidget(self._fav_btn)
        header.addWidget(close_btn)
        return header

    # ── Favorite ─────────────────────────────────────────────────────────────

    def _toggle_favorite(self):
        self.config.favorite = not self.config.favorite
        self._fav_btn.setText("★" if self.config.favorite else "☆")
        self.save_fn()

        if self.config.favorite:
            verb, tail, colour = ("добавлено", " в автозагрузку при старте",
                                  theme.ACCENT_GREEN)
        else:
            verb, tail, colour = ("удалено", " из автозагрузки при старте",
                                  theme.ACCENT_AMBER)
        if self.parent_overlay:
            self.parent_overlay.add_log_segments([
                (f"Окно {self.module_name} ", theme.TEXT_SECONDARY),
                (verb, colour),
                (tail, theme.TEXT_SECONDARY),
            ])

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)
