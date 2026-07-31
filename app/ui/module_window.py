# app/ui/module_window.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPoint


class ModuleWindow(QWidget):
    """Base class for all module windows.
    Subclasses get: drag behavior, position persistence, close notification.
    """

    def __init__(self, module_name: str, config, save_fn, parent_overlay=None):
        super().__init__()
        self.module_name    = module_name
        self.config         = config
        self.save_fn        = save_fn
        self.parent_overlay = parent_overlay
        self._drag_pos      = QPoint()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def restore_position(self):
        """Move window to saved position if available."""
        if getattr(self.config, "position_saved", False):
            self.move(self.config.x, self.config.y)

    def save_position(self, x: int, y: int):
        self.config.x = x
        self.config.y = y
        self.config.position_saved = True
        self.save_fn()

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
            window_manager.move_window(int(self.winId()), x, y, self.width(), self.height())
        else:
            self.move(x, y)
        self.save_position(x, y)

    def closeEvent(self, event):
        if self.parent_overlay:
            self.parent_overlay.on_module_closed(self.module_name)
        event.accept()
