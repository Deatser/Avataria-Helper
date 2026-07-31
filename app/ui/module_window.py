# app/ui/module_window.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPoint

from app.ui.resize_mixin import ResizeMixin


class ModuleWindow(ResizeMixin, QWidget):
    """Base for all module windows: drag, position/size persistence, resize."""

    def __init__(self, module_name: str, config, save_fn, parent_overlay=None):
        super().__init__()
        self.module_name    = module_name
        self.config         = config
        self.save_fn        = save_fn
        self.parent_overlay = parent_overlay
        self._drag_pos      = QPoint()
        self._panel         = None   # subclass assigns in _build_ui

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._init_resize()

    # ── Position / size ──────────────────────────────────────────────────────

    def restore_position(self):
        if getattr(self.config, "position_saved", False):
            self.move(self.config.x, self.config.y)

    def save_position(self, x: int, y: int):
        self.config.x = x
        self.config.y = y
        self.config.position_saved = True
        self.save_fn()

    # ── Drag ────────────────────────────────────────────────────────────────

    def start_drag(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def do_drag(self, event, window_manager=None):
        if not (event.buttons() & Qt.LeftButton):
            return
        new_pos = event.globalPosition().toPoint() - self._drag_pos
        x, y = new_pos.x(), new_pos.y()
        if window_manager and window_manager.get_game_hwnd():
            window_manager.move_window(int(self.winId()), x, y,
                                       self.width(), self.height())
        else:
            self.move(x, y)
        self.save_position(x, y)

    # ── ResizeMixin hooks ────────────────────────────────────────────────────

    def _on_resize_panel(self):
        if self._panel is not None:
            self._panel.setGeometry(0, 0, self.width(), self.height())

    def _on_resize_done(self):
        if hasattr(self.config, "width"):
            self.config.width  = self.width()
            self.config.height = self.height()
            self.save_fn()

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.parent_overlay:
            self.parent_overlay.on_module_closed(self.module_name)
        event.accept()
