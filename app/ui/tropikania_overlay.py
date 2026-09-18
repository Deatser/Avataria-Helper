# app/ui/tropikania_overlay.py
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QPoint, QRect, QTimer

from app.core.tropikania_farm import TropikaniaFarmLoop, PLANT_AREA, SEED_CHECK_PAD
from app.core.tropikania_stats import TropikaniaStatsManager
from app.ui import theme
from app.ui.crt_power_mixin import CrtPowerMixin
from app.ui.drag_mixin import BackgroundDragMixin
from app.ui.resize_mixin import ResizeMixin
from app.ui.collapse_mixin import CollapseMixin
from app.ui.node_links import NodeLinkCanvas
from app.ui.overlay import _default_backdrop
from app.ui.tropikania_settings_panel import TropikaniaSettingsPanel
from app.ui.tropikania_stats_window import TropikaniaStatsWindow
from app.ui.zones_overlay import ZonesOverlay
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.nt_confirm_dialog import NtConfirmDialog
from app.ui.widgets.vw_panel import VwPanel

# Same cadence as Overlay's own drag save — see app/ui/module_window.py.
_SAVE_DELAY_MS = 400


class TropikaniaOverlay(CollapseMixin, CrtPowerMixin, BackgroundDragMixin,
                        ResizeMixin, QWidget):
    """Launcher window for Tropikania — same frame as Overlay (header, drag,
    resize, collapse, CRT power, backdrop, log, node-link wires, Статистика,
    Настройки), with a farm-run button standing in for the module bar until
    real modules exist."""

    _RESIZE_MIN_W = 260
    _RESIZE_MIN_H = 400

    def __init__(self, config, window_manager, stats=None, player_stats=None,
                parent=None):
        super().__init__(parent)
        self.config = config
        self.wm = window_manager
        self.stats = stats if stats is not None else TropikaniaStatsManager()
        # The main app's own StatsManager — Статистика reads ID/имя/
        # регистрация from it, see _toggle_stats.
        self.player_stats = player_stats
        self._drag_origin = QPoint()
        self._drag_from   = QPoint()
        self._save_later  = QTimer(self)
        self._save_later.setSingleShot(True)
        self._save_later.setInterval(_SAVE_DELAY_MS)
        self._save_later.timeout.connect(self.config.save)
        self._panel: VwPanel | None = None
        self._settings_panel: TropikaniaSettingsPanel | None = None
        self._stats_window: TropikaniaStatsWindow | None = None
        self._links = NodeLinkCanvas(window_manager)
        self._links.set_enabled(getattr(config.data.overlay, "show_links", True))
        self._game_ok = None   # tri-state: unknown until first check
        self._farm_loop: TropikaniaFarmLoop | None = None
        self._farm_stopping = False          # stop_loop() called, thread not dead yet
        self._farm_restart_pending = False   # a start press landed during that window
        self._farm_start_exp = 0             # exp baseline at the last start, see _start_farm
        # Shown for the whole run, not just a one-off check: the seed wait
        # was silently stalling with no way to see where it was actually
        # looking, so the padded PLANT_AREA box now stays drawn over the
        # game the entire time the farm loop is running.
        self._seed_zone = ZonesOverlay(window_manager, reference=self)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(config.data.overlay.width, config.data.overlay.height)
        self._build_ui()
        self.move(config.data.overlay.x, config.data.overlay.y)
        self._init_resize()
        self._init_background_drag()
        self._init_crt_power()
        self._init_collapse(self._panel, self.wm)

        self._game_watch = QTimer(self)
        self._game_watch.setInterval(2000)
        self._game_watch.timeout.connect(self.refresh_game_status)
        self._game_watch.start()

    def _build_ui(self):
        self._panel = VwPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        self._status_dot.set_stopped()
        title = QLabel("TROPIKANIA HELPER")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self._drag_press
        title.mouseMoveEvent  = self._drag_move
        self._collapse_btn = NtButton("▲", accent=theme.ACCENT_GREEN)
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

        # ── Farm button ──────────────────────────────────────────────────────
        self._farm_btn = NtButton("Запустить фарм опыта", upper=False,
                                  accent=theme.ACCENT_GREEN)
        self._farm_btn.setMinimumHeight(30)
        self._farm_btn.clicked.connect(self._toggle_farm)
        layout.addWidget(self._farm_btn)

        # ── Auto-continue toggle ─────────────────────────────────────────────
        # A trigger, not an action: set_active(True) is what draws the green
        # outline/glow — same visual language NtButton already uses for every
        # other on/off state in this app (module toggles, the farm button
        # itself while running).
        self._auto_continue = bool(
            getattr(self.config.data.overlay, "auto_continue", False))
        self._auto_btn = NtButton("Автопродолжение", upper=False,
                                  accent=theme.ACCENT_GREEN)
        self._auto_btn.setMinimumHeight(26)
        self._auto_btn.set_active(self._auto_continue)
        self._auto_btn.clicked.connect(self._toggle_auto_continue)
        layout.addWidget(self._auto_btn)

        layout.addSpacing(6)

        # ── Log section ──────────────────────────────────────────────────────
        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(80)

        log_head = QHBoxLayout()
        log_label = QLabel("LOG")
        log_label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; background:transparent;"
        )
        log_head.addWidget(log_label)
        log_head.addStretch()
        log_head.addLayout(build_log_actions(self.log_panel, theme.BORDER_BRIGHT))
        layout.addLayout(log_head)

        layout.addWidget(self.log_panel, stretch=1)

        stats_btn = NtButton("Статистика", upper=False,
                             accent=theme.ACCENT_GREEN)
        stats_btn.setMinimumHeight(30)
        stats_btn.clicked.connect(self._toggle_stats)
        layout.addWidget(stats_btn)

        settings_btn = NtButton("Настройки", accent=theme.ACCENT_GREEN,
                                upper=False, filled=True)
        settings_btn.setMinimumHeight(30)
        settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(settings_btn)

        # ── Backdrop: templates/AvaHelper.* — same files as Avataria Helper ──
        self._panel.background_failed.connect(
            lambda msg: self.add_log(msg, level="error")
        )
        self._apply_backdrop(fade=False)

        # ── Drag handle ─────────────────────────────────────────────────────
        drag = NtDragHandle()
        drag.mousePressEvent = self._drag_press
        drag.mouseMoveEvent  = self._drag_move
        layout.addWidget(drag)

    # ── Backdrop ─────────────────────────────────────────────────────────────

    def _apply_backdrop(self, fade: bool = True):
        cfg      = self.config.data.overlay
        backdrop = getattr(cfg, "background", "") or _default_backdrop()
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _crt_open_ready(self) -> bool:
        return self._panel.backdrop_ready

    # ── Settings ─────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings_panel is None:
            self._settings_panel = TropikaniaSettingsPanel(self)
        self._settings_panel.toggle()

    # ── Statistics ───────────────────────────────────────────────────────────

    def _toggle_stats(self):
        if self._stats_window and self._stats_window.isVisible():
            self._stats_window.close()
            return

        window = TropikaniaStatsWindow(
            config          = self.config.data.stats_window,
            save_fn         = self.config.save,
            stats           = self.stats,
            window_manager  = self.wm,
            overlay         = self,
            player_stats    = self.player_stats,
        )
        window.closed.connect(self._on_stats_closed)
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window, theme.ACCENT_GREEN)
        self._stats_window = window
        self.add_log("Открыта статистика")

    def _on_stats_closed(self):
        self._stats_window = None

    # ── Node wires ───────────────────────────────────────────────────────────

    def own_windows(self) -> list[QWidget]:
        windows: list[QWidget] = [self]
        if self._stats_window is not None:
            windows.append(self._stats_window)
        return windows

    def on_window_closing(self, window: QWidget):
        self._links.disconnect_window(window)

    def restore_favorite_windows(self):
        if getattr(self.config.data.stats_window, "favorite", False):
            self._toggle_stats()

    # ── Farm loop ────────────────────────────────────────────────────────────
    # A stopped run is never resumed mid-step: stop_loop() only asks the
    # current step's poll to give up, and the next run always starts a brand
    # new TropikaniaFarmLoop, which always begins at MARKET_TPL — there is no
    # saved step to resume from. The only wrinkle is timing: stopping a QThread
    # is not instant (whatever step it is mid-poll on has to notice
    # stop_event and unwind), so a "Запустить" pressed before that finishes
    # used to be silently swallowed — see _farm_stopping/_farm_restart_pending
    # below, which queue that press instead of dropping it.

    def _toggle_farm(self):
        if self._farm_loop is not None:
            if self._farm_stopping:
                # Already stopping — this second press means "restart once
                # it actually stops", not "stop again".
                self._farm_restart_pending = True
                self.add_log("Фарм опыта — перезапущу после остановки",
                             level="plain")
                return
            self._farm_stopping = True
            self._farm_loop.stop_loop()
            self.add_log("Фарм опыта — останавливаю...", level="plain")
            return

        self._start_farm()

    def _start_farm(self):
        # Only the start/finish lines are logged now — every intermediate
        # step (market/blueberry/plant/sprout/close/search/sell/replant)
        # used to get its own line and that was too much noise for a run
        # that repeats every ~15s. The signals themselves still fire and
        # still drive _on_farm_confirmed's stats recording; they just have
        # no log lines connected to them any more.
        loop = TropikaniaFarmLoop(get_hwnd=self.wm.get_game_hwnd,
                                  auto_continue=self._auto_continue)
        loop.confirmed.connect(self._on_farm_confirmed)
        loop.error.connect(lambda msg: self.add_log(msg, level="error"))
        loop.finished.connect(self._on_farm_thread_finished)
        self._farm_loop = loop
        self._farm_stopping = False
        # Baseline for the "заработано X опыта" line on stop — the
        # all-time total as it stood at this exact start, so the diff at
        # stop time is only what this run itself earned.
        self._farm_start_exp = self.stats.data.farm.blueberry_exp
        loop.start()
        self._farm_btn.setText("Завершить фарм опыта")
        self._farm_btn.set_active(True)
        self._show_seed_zone()
        self.add_log("Фарм опыта — начат", level="plain")

    def _show_seed_zone(self):
        """Draws the exact box the seed-wait step searches — screen
        coordinates, padded PLANT_AREA — over the game for as long as the
        farm loop runs, so it can be checked against where the seedling
        actually appears."""
        left, top, w, h = PLANT_AREA
        box = QRect(left - SEED_CHECK_PAD, top - SEED_CHECK_PAD,
                   w + 2 * SEED_CHECK_PAD, h + 2 * SEED_CHECK_PAD)
        self._seed_zone.show_zones([(box, QColor(theme.ACCENT_AMBER))])

    def _toggle_auto_continue(self):
        self._auto_continue = not self._auto_continue
        self.config.data.overlay.auto_continue = self._auto_continue
        self.config.save()
        self._auto_btn.set_active(self._auto_continue)
        if self._farm_loop is not None:
            # Takes effect at the next cycle boundary — see
            # TropikaniaFarmLoop.run()'s own comment on why that's fine.
            self._farm_loop.auto_continue = self._auto_continue
        self.add_log(
            "Автопродолжение — "
            + ("включено" if self._auto_continue else "выключено"),
            level="plain")

    def _on_farm_confirmed(self):
        # Bumps the stat every cycle (that's the whole point of
        # auto-continue), but no log line here any more — with
        # auto-continue on, this fires once per cycle, and "завершён" is
        # meant to mean the run actually stopped, not "one more harvest
        # done". See _on_farm_thread_finished for the real "завершён" line.
        self.stats.record_blueberry_farm()

    def _on_farm_thread_finished(self):
        # "завершён" only reports an actual Завершить press — not a cycle
        # ending naturally (auto-continue starts the next one anyway) and
        # not a thread that died on its own (a missing hwnd/template
        # already logs its own error).
        user_stopped = self._farm_stopping
        self._farm_loop = None
        self._farm_stopping = False
        if self._farm_restart_pending:
            self._farm_restart_pending = False
            self._start_farm()
            return
        self._seed_zone.clear()
        self._farm_btn.setText("Запустить фарм опыта")
        self._farm_btn.set_active(False)
        if user_stopped:
            earned = self.stats.data.farm.blueberry_exp - self._farm_start_exp
            self.add_log(f"Фарм опыта — завершён, заработано {earned} опыта",
                        level="plain")

    # ── Log ──────────────────────────────────────────────────────────────────

    def add_log(self, message: str, level: str = "info"):
        self.add_log_segments([(message, theme.TEXT_SECONDARY)], level)

    def add_log_segments(self, segments: list, level: str = "info"):
        self.log_panel.add_log_segments(segments, level)

    # ── ResizeMixin hooks ────────────────────────────────────────────────────

    def _on_resize_panel(self):
        if self._panel is not None:
            self._panel.setGeometry(0, 0, self.width(), self.height())
        if self._settings_panel is not None:
            self._settings_panel.keep_inside_host()

    def _on_resize_done(self):
        self.config.data.overlay.width  = self.width()
        self.config.data.overlay.height = self.height()
        self.config.save()

    # ── Drag ────────────────────────────────────────────────────────────────

    def _drag_begin(self, event):
        self._drag_press(event)

    def _drag_to(self, event):
        self._drag_move(event)

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
        self._save_later.start()

    # ── Status ───────────────────────────────────────────────────────────────

    def refresh_game_status(self):
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

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if getattr(self, "_crt_started", False):
            event.accept()
            return

        if not self.config.data.overlay.skip_close_confirm \
                and not self._confirm_close():
            event.ignore()
            return

        # The drag-position save is debounced (_SAVE_DELAY_MS) so dragging
        # doesn't hit disk on every mouse-move tick — but that means a drag
        # immediately followed by closing the app could quit before the
        # timer ever fires, silently dropping the final position. Flushing
        # here guarantees whatever's in memory right now reaches disk.
        if self._save_later.isActive():
            self._save_later.stop()
        self.config.save()

        self._farm_restart_pending = False
        if self._farm_loop is not None:
            self._farm_loop.stop_loop()
            self._farm_loop.wait()
            self._farm_loop = None
        self._seed_zone.clear()
        if self._stats_window:
            self._stats_window.close()
        self._links.clear()
        self.crt_close_started()
        event.ignore()

    def _confirm_close(self) -> bool:
        dialog = NtConfirmDialog(
            title         = "Закрыть Tropikania Helper",
            message       = "Вы уверены, что хотите закрыть помощник?",
            confirm_text  = "Закрыть",
            remember_text = "Не спрашивать снова",
            parent        = self,
        )
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(dialog.winId()))
            self._center_dialog_in_game(dialog)
            QTimer.singleShot(0, lambda: self._center_dialog_in_game(dialog))

        if dialog.exec() != dialog.DialogCode.Accepted:
            return False

        if dialog.remembered:
            self.config.data.overlay.skip_close_confirm = True
            self.config.save()
        return True

    def _center_dialog_in_game(self, dialog):
        ov = self.config.data.overlay
        self.wm.move_window(
            int(dialog.winId()),
            ov.x + (ov.width  - dialog.width())  // 2,
            ov.y + (ov.height - dialog.height()) // 2,
            dialog.width(), dialog.height(),
        )
