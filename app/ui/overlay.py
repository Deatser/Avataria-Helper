# app/ui/overlay.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication
from PySide6.QtCore import Qt, QPoint, QTimer

from app.ui import theme
from app.ui.resize_mixin import ResizeMixin
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.log_panel import LogPanel
from app.module_registry import MODULES


class Overlay(ResizeMixin, QWidget):
    _RESIZE_MIN_W = 240
    _RESIZE_MIN_H = 300

    def __init__(self, config, window_manager, parent=None):
        super().__init__(parent)
        self.config = config
        self.wm = window_manager
        self._drag_pos = QPoint()
        self._open_windows: dict[str, QWidget] = {}
        self._startup_shown = False
        self._panel: NtPanel | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(config.data.overlay.width, config.data.overlay.height)
        self._build_ui()
        self.move(config.data.overlay.x, config.data.overlay.y)
        self._init_resize()

    def _build_ui(self):
        self._panel = NtPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("AVATARIA HELPER")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self._drag_press
        title.mouseMoveEvent  = self._drag_move
        close_btn = NtButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Separator ───────────────────────────────────────────────────────
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # ── Module buttons ──────────────────────────────────────────────────
        self._module_buttons: dict[str, NtButton] = {}
        for module_cls in MODULES:
            btn = NtButton(f"{module_cls.icon}  {module_cls.name}")
            btn.clicked.connect(lambda _, m=module_cls: self._toggle_module(m))
            self._module_buttons[module_cls.name] = btn
            layout.addWidget(btn)

        layout.addSpacing(8)

        # ── Log section ─────────────────────────────────────────────────────
        log_label = QLabel("⊞ LOG")
        log_label.setFont(theme.get_serif_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        layout.addWidget(log_label)

        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(80)
        layout.addWidget(self.log_panel, stretch=1)

        clear_btn = NtButton("CLEAR")
        clear_btn.setMinimumHeight(26)
        clear_btn.clicked.connect(self.log_panel.clear_logs)
        layout.addWidget(clear_btn)

        # ── Drag bar ────────────────────────────────────────────────────────
        drag = QLabel("⠿  ⠿  ⠿")
        drag.setAlignment(Qt.AlignCenter)
        drag.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        drag.setFixedHeight(16)
        drag.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
        drag.setCursor(Qt.SizeAllCursor)
        drag.mousePressEvent = self._drag_press
        drag.mouseMoveEvent  = self._drag_move
        layout.addWidget(drag)

    # ── ResizeMixin hooks ────────────────────────────────────────────────────

    def _on_resize_panel(self):
        if self._panel is not None:
            self._panel.setGeometry(0, 0, self.width(), self.height())

    def _on_resize_done(self):
        self.config.data.overlay.width  = self.width()
        self.config.data.overlay.height = self.height()
        self.config.save()

    # ── Module management ────────────────────────────────────────────────────

    def _toggle_module(self, module_cls):
        name = module_cls.name
        if name in self._open_windows and self._open_windows[name].isVisible():
            self._open_windows[name].close()
            return

        module      = module_cls()
        config_sect = getattr(self.config.data, module_cls.config_key, None)
        window      = module.create_window(
            config         = config_sect,
            save_fn        = self.config.save,
            window_manager = self.wm,
            parent_overlay = self,
        )
        self._open_windows[name] = window
        window.show()

        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))

        self.add_log(f"Запуск мода {name}")
        if name in self._module_buttons:
            self._module_buttons[name].set_active(True)

    def on_module_closed(self, module_name: str):
        self._open_windows.pop(module_name, None)
        if module_name in self._module_buttons:
            self._module_buttons[module_name].set_active(False)
        self.add_log(f"Закрытие мода {module_name}")

    def add_log(self, message: str, level: str = "info"):
        self.log_panel.add_log(message, level)

    def restore_favorite_windows(self):
        for module_cls in MODULES:
            config_sect = getattr(self.config.data, module_cls.config_key, None)
            if config_sect and getattr(config_sect, "favorite", False):
                self._toggle_module(module_cls)

    # ── Drag ────────────────────────────────────────────────────────────────

    def _drag_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def _drag_move(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        pos = event.globalPosition().toPoint() - self._drag_pos
        if self.wm.get_game_hwnd():
            self.wm.move_window(int(self.winId()), pos.x(), pos.y(),
                                self.width(), self.height())
        else:
            self.move(pos.x(), pos.y())
        self.config.data.overlay.x = pos.x()
        self.config.data.overlay.y = pos.y()
        self.config.save()

    # ── Startup sequence ────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._startup_shown:
            self._startup_shown = True
            QTimer.singleShot(200, self._startup_sequence)

    def _startup_sequence(self):
        from app.ui.widgets.log_panel import CLAUDE_ORANGE

        def step2():
            self.log_panel.animate_log(
                segments=[("made by Deatser", CLAUDE_ORANGE)],
                include_ts=False,
                delay_ms=120,
            )

        self.log_panel.animate_log(
            segments=[
                ("Запуск Avataria Helper — ", theme.TEXT_SECONDARY),
                ("Успешно", theme.ACCENT_GREEN),
            ],
            on_done=step2,
        )

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        for w in list(self._open_windows.values()):
            if w.isVisible():
                w.close()
        QApplication.quit()
        event.accept()
