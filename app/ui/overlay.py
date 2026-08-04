# app/ui/overlay.py
from __future__ import annotations
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QApplication, QDialog)
from PySide6.QtCore import Qt, QPoint, QTimer

from app.core.stats import StatsManager
from app.ui import theme
from app.ui.crt_power_mixin import CrtPowerMixin
from app.ui.module_window import _SAVE_DELAY_MS
from app.ui.drag_mixin import BackgroundDragMixin
from app.ui.node_links import NodeLinkCanvas
from app.ui.resize_mixin import ResizeMixin
from app.ui.stats_window import StatsWindow
from app.ui.collapse_mixin import CollapseMixin
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_confirm_dialog import NtConfirmDialog
from app.module_registry import MODULES


class Overlay(CollapseMixin, CrtPowerMixin, BackgroundDragMixin,
              ResizeMixin, QWidget):
    _RESIZE_MIN_W = 240
    _RESIZE_MIN_H = 466   # header + 4 module buttons + log + 2 actions

    def __init__(self, config, window_manager, stats=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.wm = window_manager
        self.stats = stats if stats is not None else StatsManager()
        self._stats_window: StatsWindow | None = None
        self._links = NodeLinkCanvas(window_manager)
        self._drag_origin = QPoint()
        self._drag_from   = QPoint()
        self._save_later  = QTimer(self)
        self._save_later.setSingleShot(True)
        self._save_later.setInterval(_SAVE_DELAY_MS)
        self._save_later.timeout.connect(self.config.save)
        self._open_windows: dict[str, QWidget] = {}
        self._startup_shown = False
        self._startup_done  = False
        self._game_ok       = None   # tri-state: unknown until first check
        self._pending_logs: list[tuple[list, str]] = []
        self._panel: NtPanel | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(config.data.overlay.width, config.data.overlay.height)
        self._build_ui()
        self.move(config.data.overlay.x, config.data.overlay.y)
        self._init_resize()
        self._init_background_drag()
        self._init_crt_power()
        self._init_collapse(self._panel, self.wm)

        # Keep the header dot honest if the game window disappears
        self._game_watch = QTimer(self)
        self._game_watch.setInterval(2000)
        self._game_watch.timeout.connect(self.refresh_game_status)
        self._game_watch.start()

    def _build_ui(self):
        self._panel = NtPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        self._status_dot.set_stopped()   # red until the game window is found
        title = QLabel("AVATARIA HELPER")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self._drag_press
        title.mouseMoveEvent  = self._drag_move
        self._collapse_btn = NtButton("▲", accent=theme.ACCENT)
        self._collapse_btn.setFixedSize(26, 26)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Separator ───────────────────────────────────────────────────────
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER_DIM};")
        layout.addWidget(sep)
        layout.addSpacing(2)

        # ── Module buttons ──────────────────────────────────────────────────
        self._module_buttons: dict[str, NtButton] = {}

        def add_modules(slot: str):
            for module_cls in MODULES:
                if getattr(module_cls, "panel_slot", "top") != slot:
                    continue
                btn = NtButton(self._module_label(module_cls.name, False),
                               accent=getattr(module_cls, "color", None),
                               upper=False)
                btn.clicked.connect(lambda _, m=module_cls: self._toggle_module(m))
                self._module_buttons[module_cls.name] = btn
                layout.addWidget(btn)

        add_modules("top")
        # Хоккей graduated to a real, registered module too (see MODULES)
        # — no placeholders left here.
        add_modules("bottom")

        layout.addSpacing(6)

        # ── Log section ─────────────────────────────────────────────────────
        # Clearing moved up here as a small button on the heading row: it is
        # an action on the log itself, and it frees the full-width slot below
        # for something worth pressing often.
        log_head = QHBoxLayout()
        log_label = QLabel("LOG")
        log_label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; background:transparent;"
        )
        clear_btn = NtButton("⌫", accent=theme.BORDER_BRIGHT)
        clear_btn.setFixedSize(22, 20)
        clear_btn.setToolTip("Очистить логи")
        clear_btn.clicked.connect(lambda: self.log_panel.clear_logs())
        log_head.addWidget(log_label)
        log_head.addStretch()
        log_head.addWidget(clear_btn)
        layout.addLayout(log_head)

        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(80)
        layout.addWidget(self.log_panel, stretch=1)

        stats_btn = NtButton("Статистика", upper=False,
                             accent=theme.ACCENT_CYAN)
        stats_btn.setMinimumHeight(30)
        stats_btn.clicked.connect(self._toggle_stats)
        layout.addWidget(stats_btn)

        settings_btn = NtButton("Настройки", accent=theme.ACCENT,
                                upper=False, filled=True)
        settings_btn.setMinimumHeight(30)
        layout.addWidget(settings_btn)

        # ── Drag handle ──────────────────────────────────────────────────────
        drag = NtDragHandle()
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
            stats          = self.stats,
        )
        self._open_windows[name] = window

        # Attach BEFORE the first show. Re-parenting an already-shown window
        # leaves Qt's deferred update() path dead: repaint() still draws, but
        # update() never flushes — hover sweeps and text changes freeze.
        # main.py attaches the overlay the same way, which is why it stays live.
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window,
                                    getattr(module_cls, "color", None))

        self.add_log(f"Запуск мода {name}")
        self._set_module_active(name, True)

    def own_windows(self) -> list[QWidget]:
        """Every window the helper owns — this launcher bar, every open
        module window, and Statistics if it is up. Anything wanting to grab
        a clean frame of the game needs to know what of ours could be
        sitting over it, and that is every one of these, not just itself.
        """
        windows: list[QWidget] = [self, *self._open_windows.values()]
        if self._stats_window is not None:
            windows.append(self._stats_window)
        return windows

    def on_module_closed(self, module_name: str):
        self._open_windows.pop(module_name, None)
        self._set_module_active(module_name, False)
        self.add_log(f"Закрытие мода {module_name}")

    @staticmethod
    def _module_label(name: str, active: bool) -> str:
        return f"{'Выключить' if active else 'Включить'} мод {name}"

    def _set_module_active(self, name: str, active: bool):
        btn = self._module_buttons.get(name)
        if not btn:
            return
        btn.setText(self._module_label(name, active))
        btn.set_active(active)

    def add_log(self, message: str, level: str = "info"):
        self.add_log_segments([(message, theme.TEXT_SECONDARY)], level)

    def add_log_segments(self, segments: list, level: str = "info"):
        # Hold logs until the startup banner is written, so "made by Deatser"
        # always stays the very first line (favorite windows restore early).
        if not self._startup_done:
            self._pending_logs.append((segments, level))
            return
        self.log_panel.add_log_segments(segments, level)

    # ── Statistics ───────────────────────────────────────────────────────────

    def _toggle_stats(self):
        if self._stats_window and self._stats_window.isVisible():
            self._stats_window.close()
            return

        window = StatsWindow(
            config          = self.config.data.stats_window,
            save_fn         = self.config.save,
            stats           = self.stats,
            window_manager  = self.wm,
            overlay         = self,
        )
        window.closed.connect(self._on_stats_closed)
        # Attach before the first show, same rule as the module windows —
        # re-parenting an already-shown window kills its deferred repaints.
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window, theme.ACCENT_CYAN)
        self._stats_window = window
        self.add_log("Открыта статистика")

    def _on_stats_closed(self):
        self._stats_window = None

    # ── Node wires ───────────────────────────────────────────────────────────

    def on_window_closing(self, window: QWidget):
        """A window has begun switching off — reel its wire back in."""
        self._links.disconnect_window(window)

    def restore_favorite_windows(self):
        for module_cls in MODULES:
            config_sect = getattr(self.config.data, module_cls.config_key, None)
            if config_sect and getattr(config_sect, "favorite", False):
                self._toggle_module(module_cls)
        # Not a module, but starred the same way and restored the same way
        if getattr(self.config.data.stats_window, "favorite", False):
            self._toggle_stats()

    # ── Drag ────────────────────────────────────────────────────────────────
    # The title and the handle keep their own bindings; these two let the
    # whole background do the same job. See BackgroundDragMixin.

    def _drag_begin(self, event):
        self._drag_press(event)

    def _drag_to(self, event):
        self._drag_move(event)

    # Only the mouse's travel is used, and the window's own starting point is
    # read in the space it is actually positioned in — see ModuleWindow for
    # why measuring the grab against Qt's geometry made the window shake.

    def _window_origin(self) -> QPoint:
        if self.wm.get_game_hwnd():
            origin = self.wm.window_origin(int(self.winId()))
            if origin is not None:
                return QPoint(*origin)
        return self.pos()

    def _drag_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_from   = self._window_origin()

    def _drag_move(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        target = self._drag_from + (event.globalPosition().toPoint()
                                    - self._drag_origin)
        if self.wm.get_game_hwnd():
            self.wm.move_window(int(self.winId()), target.x(), target.y(),
                                self.width(), self.height())
        else:
            self.move(target.x(), target.y())
        self.config.data.overlay.x = target.x()
        self.config.data.overlay.y = target.y()
        self._save_later.start()   # not once per mouse event — see the module window

    # ── Startup sequence ────────────────────────────────────────────────────

    def refresh_game_status(self):
        """Green while the game window is alive, red otherwise."""
        alive = self.wm.is_game_alive()
        if alive == self._game_ok:
            return
        self._game_ok = alive
        if alive:
            self._status_dot.set_running()
        else:
            self._status_dot.set_stopped()
        self._status_dot.setToolTip(
            "Игровое окно найдено" if alive else "Игровое окно не найдено"
        )

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_game_status()
        if not self._startup_shown:
            self._startup_shown = True
            QTimer.singleShot(200, self._startup_sequence)

    def _startup_sequence(self):
        from app.ui.widgets.log_panel import CLAUDE_ORANGE

        def step2():
            # Blank spacer line between the signature and the real log
            self.log_panel.append("")
            self.log_panel.animate_log(
                segments=[
                    ("Запуск Avataria Helper — ", theme.TEXT_SECONDARY),
                    ("Успешно", theme.ACCENT_GREEN),
                ],
                delay_ms=120,
                on_done=self._flush_pending_logs,
            )

        self.log_panel.animate_log(
            segments=[("made by Deatser", CLAUDE_ORANGE)],
            include_ts=False,
            on_done=step2,
        )

    def _flush_pending_logs(self):
        self._startup_done = True
        pending, self._pending_logs = self._pending_logs, []
        for segments, level in pending:
            self.log_panel.add_log_segments(segments, level)

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if getattr(self, "_crt_started", False):
            QApplication.quit()   # the animation has played; really go now
            event.accept()
            return

        if not self.config.data.overlay.skip_close_confirm and not self._confirm_close():
            event.ignore()
            return

        # Every window switches off at once, all on the same clock, so the
        # helper goes dark like one screen rather than several.
        for w in list(self._open_windows.values()):
            if w.isVisible():
                w.close()
        if self._stats_window:
            self._stats_window.close()

        self._links.clear()   # nothing left to connect to
        self.crt_close_started()
        event.ignore()

    def _confirm_close(self) -> bool:
        dialog = NtConfirmDialog(
            title         = "Закрыть помощник",
            message       = "Вы уверены, что хотите закрыть помощник "
                            "и все включённые моды?",
            confirm_text  = "Закрыть",
            remember_text = "Не спрашивать снова",
            parent        = self,
        )
        if self.wm.get_game_hwnd():
            # Same rule as module windows: attach before the first show, or the
            # dialog's hover and checkbox repaints never reach the screen.
            self.wm.attach_child(int(dialog.winId()))
            self._center_dialog_in_game(dialog)
            # And again once the modal loop runs — Qt re-applies its own
            # screen-space geometry over ours when it shows the window.
            QTimer.singleShot(0, lambda: self._center_dialog_in_game(dialog))

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        if dialog.remembered:
            self.config.data.overlay.skip_close_confirm = True
            self.config.save()
        return True

    def _center_dialog_in_game(self, dialog):
        """Attached windows use game-client coordinates, not screen ones."""
        ov = self.config.data.overlay
        self.wm.move_window(
            int(dialog.winId()),
            ov.x + (ov.width  - dialog.width())  // 2,
            ov.y + (ov.height - dialog.height()) // 2,
            dialog.width(), dialog.height(),
        )
