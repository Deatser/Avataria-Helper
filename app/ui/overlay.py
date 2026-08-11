# app/ui/overlay.py
from __future__ import annotations
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QApplication, QDialog, QSizePolicy)
from PySide6.QtCore import Qt, QPoint, QTimer

from app.core.promo_activate import PromoAutoLoop
from app.core.stats import StatsManager
from app.ui.promo_window import PromoWindow
from app.ui import theme
from app.ui.crt_power_mixin import CrtPowerMixin
from app.ui.helper_settings_panel import HelperSettingsPanel
from app.ui.module_window import _SAVE_DELAY_MS
from app.ui.drag_mixin import BackgroundDragMixin
from app.ui.node_links import NodeLinkCanvas
from app.ui.resize_mixin import ResizeMixin
from app.ui.stats_window import StatsWindow
from app.ui.collapse_mixin import CollapseMixin
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets import log_panel as log_panel_mod
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_confirm_dialog import NtConfirmDialog
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES
from app.module_registry import MODULES

# Same auto-pick rule every module's own backdrop uses — see
# modules.ava_dancers.window._default_backdrop.
_BACKDROP_STEM  = "AvaHelper"
_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_TEMPLATES      = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _default_backdrop(video: bool = True) -> str:
    """First existing templates/AvaHelper.* file."""
    moving = VIDEO_SUFFIXES + (".gif",)
    order  = moving + _STILL_SUFFIXES if video else _STILL_SUFFIXES + moving
    for suffix in order:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


# The board of tiles owns the window and the log gets what is left — 70/30
# of the height below the header, held by the layout's own stretch factors.
_BOARD_SHARE = 7
_LOG_SHARE   = 3

# A tile is a button, not a panel: it keeps its own size and the three
# columns are spread across the window — first flush left, last flush right,
# the slack shared out between them — so the backdrop breathes around them
# instead of being papered over edge to edge.
_TILE_W     = 210   # fits the longest face, "PROMO CODES", without clipping
_TILE_H     = 52    # a floor and a ceiling, so a tall window gives air, not slabs
_TILE_MAX_H = 78

_BOARD_TOP_GAP = 22   # separator → column captions
_TILE_GAP      = 14   # between tiles inside a column
_ICON_BTN   = 26    # < NtButton._SMALL_W → centred glyph, no accent bar

# Button faces are English; module_cls.name stays Russian — it is the key
# for open windows, logs and config, and none of that is on screen here.
_DISPLAY_NAMES = {
    "Ava Dancers": "Ava Dancers",
    "Сноуборд":    "Snowboard",
    "Хоккей":      "Hockey",
    "Садовник":    "Садовник",
    "Уборщик":     "Уборщик",
}


class Overlay(CollapseMixin, CrtPowerMixin, BackgroundDragMixin,
              ResizeMixin, QWidget):
    _RESIZE_MIN_W = 3 * _TILE_W + 2 * theme.SPACING + theme.PADDING * 2
    _RESIZE_MIN_H = 430   # header + three tile rows + log + handle

    def __init__(self, config, window_manager, stats=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.wm = window_manager
        self.stats = stats if stats is not None else StatsManager()
        self._stats_window: StatsWindow | None = None
        self._settings_panel: HelperSettingsPanel | None = None
        self._links = NodeLinkCanvas(window_manager)
        self._links.set_enabled(getattr(config.data.overlay, "show_links", True))
        # Before any LogPanel is built, so the very first startup line
        # already obeys the saved choice.
        log_panel_mod.set_animation_enabled(
            getattr(config.data.overlay, "log_animation", True))
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
        self._panel: VwPanel | None = None
        self._promo_auto: PromoAutoLoop | None = None
        self._promo_window: PromoWindow | None = None

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

        self.set_promo_watch_enabled(
            getattr(config.data.promo, "detect_enabled", False))

    def _build_ui(self):
        self._panel = VwPanel(self)
        # Same tall-and-narrow crop StatsWindow deals with, biased the other
        # way — just a slight nudge left, not the full swing StatsWindow's
        # own backdrop needed.
        self._panel.focus_x = -0.2
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
        # Settings is a gear up in the title bar: pressed once in a while, and
        # it has no business taking a slab on the board.
        settings_btn = NtButton("⚙", accent=theme.ACCENT, filled=True)
        settings_btn.setFixedSize(_ICON_BTN, _ICON_BTN)
        settings_btn.setToolTip("Настройки")
        settings_btn.clicked.connect(self._toggle_settings)
        self._collapse_btn = NtButton("▲", accent=theme.ACCENT)
        self._collapse_btn.setFixedSize(_ICON_BTN, _ICON_BTN)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(_ICON_BTN, _ICON_BTN)
        close_btn.clicked.connect(self.close)
        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(settings_btn)
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Separator ───────────────────────────────────────────────────────
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER_DIM};")
        layout.addWidget(sep)
        # Air between the title bar's rule and the GAMES / JOBS / PANEL
        # captions — only above them; the gap to their own tiles stays tight.
        layout.addSpacing(_BOARD_TOP_GAP)

        # ── Three columns of big tiles ──────────────────────────────────────
        # GAMES — the modules that play something, one under another.
        # JOBS  — the two that grind a profession.
        # PANEL — the helper's own windows.
        # Every tile stretches to fill the board, so the buttons are the
        # window's main surface and the log takes the rest.
        self._module_buttons: dict[str, NtButton] = {}
        board = QHBoxLayout()
        board.setSpacing(theme.SPACING)

        games = [m for m in MODULES if getattr(m, "panel_slot", "top") == "top"]
        jobs  = [m for m in MODULES if getattr(m, "panel_slot", "top") == "bottom"]

        def module_tile(module_cls) -> NtButton:
            btn = self._tile(self._module_label(module_cls.name, False),
                             getattr(module_cls, "color", None))
            btn.clicked.connect(lambda _, m=module_cls: self._toggle_module(m))
            self._module_buttons[module_cls.name] = btn
            return btn

        columns = [
            ("GAMES", [module_tile(m) for m in games]),
            ("JOBS",  [module_tile(m) for m in jobs]),
            ("PANEL", [self._tile("Stats", theme.ACCENT_CYAN,
                                  self._toggle_stats),
                       self._tile("Promo codes", theme.ACCENT_CYAN,
                                  self._toggle_promo_window)]),
        ]
        # A column of its own per group; the tiles keep their own height, so a
        # short column simply ends earlier instead of stretching its buttons.
        for index, (caption, tiles) in enumerate(columns):
            if index:
                board.addStretch(1)   # slack shared evenly between columns
            strip = QVBoxLayout()
            strip.setSpacing(6)          # caption sits close to its column
            strip.addWidget(self._caption(caption))
            stack = QVBoxLayout()        # …the tiles themselves breathe more
            stack.setSpacing(_TILE_GAP)
            for tile in tiles:
                stack.addWidget(tile)
            strip.addLayout(stack)
            strip.addStretch()
            board.addLayout(strip, 0)
        layout.addLayout(board, stretch=_BOARD_SHARE)

        # ── Log section ─────────────────────────────────────────────────────
        # The bottom third: clearing and copying stay as small buttons on the
        # heading row.
        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(90)
        self.log_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        log_head = QHBoxLayout()
        log_head.addWidget(self._caption("LOG"))
        log_head.addStretch()
        log_head.addLayout(build_log_actions(self.log_panel, theme.BORDER_BRIGHT))
        layout.addLayout(log_head)

        layout.addWidget(self.log_panel, stretch=_LOG_SHARE)

        # ── Backdrop: templates/AvaHelper.* by default, video first ──────────
        self._panel.background_failed.connect(
            lambda msg: self.add_log(msg, level="error")
        )
        self._apply_backdrop(fade=False)

        # ── Drag handle ──────────────────────────────────────────────────────
        drag = NtDragHandle()
        drag.mousePressEvent = self._drag_press
        drag.mouseMoveEvent  = self._drag_move
        layout.addWidget(drag)

    # ── Layout helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _caption(text: str) -> QLabel:
        """Small dim heading over a group — МОДЫ, LOG."""
        label = QLabel(text)
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        return label

    @staticmethod
    def _tile(text: str, accent: str, on_click=None) -> NtButton:
        """One slab on the board — the launcher's main surface."""
        btn = NtButton(text, accent=accent)
        btn.setFixedWidth(_TILE_W)
        btn.setMinimumHeight(_TILE_H)
        btn.setMaximumHeight(_TILE_MAX_H)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        btn.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        if on_click is not None:
            btn.clicked.connect(on_click)
        return btn

    # ── Backdrop ─────────────────────────────────────────────────────────────

    def _apply_backdrop(self, fade: bool = True):
        cfg      = self.config.data.overlay
        video    = getattr(cfg, "video_background", True)
        backdrop = getattr(cfg, "background", "") or _default_backdrop(video)
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _crt_open_ready(self) -> bool:
        """Hold the switch-on until the video backdrop has a frame to show."""
        return self._panel.backdrop_ready

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
        if self._promo_window is not None:
            windows.append(self._promo_window)
        return windows

    def on_module_closed(self, module_name: str):
        self._open_windows.pop(module_name, None)
        self._set_module_active(module_name, False)
        self.add_log(f"Закрытие мода {module_name}")

    @staticmethod
    def _module_label(name: str, active: bool) -> str:
        # The tile says what the module is, in English; whether it is running
        # is the accent's job (set_active), not the label's.
        return _DISPLAY_NAMES.get(name, name)

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

    # ── Settings ─────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings_panel is None:
            self._settings_panel = HelperSettingsPanel(self)
        self._settings_panel.toggle()

    # ── Promo codes ──────────────────────────────────────────────────────────

    def _toggle_promo_window(self):
        if self._promo_window and self._promo_window.isVisible():
            self._promo_window.close()
            return

        window = PromoWindow(
            config          = self.config.data.promo,
            save_fn         = self.config.save,
            window_manager  = self.wm,
            stats           = self.stats,
            overlay         = self,
        )
        window.closed.connect(self._on_promo_window_closed)
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window, theme.ACCENT_CYAN)
        self._promo_window = window
        self.add_log("Открыты промокоды")

    def _on_promo_window_closed(self):
        self._promo_window = None

    def set_promo_watch_enabled(self, enabled: bool, announce: bool = False):
        """Starts/stops PromoAutoLoop — the once-a-minute autonomous
        check-and-activate cycle behind "Запустить автоматический детект
        промокодов". Called both from PromoWindow's own button (announce=True,
        the user just turned it on) and once at startup, from whatever
        config.data.promo.detect_enabled was last left at (announce=False —
        resuming, not a fresh "on" the user should be told about)."""
        running = self._promo_auto is not None
        if enabled == running:
            return
        if enabled:
            loop = PromoAutoLoop(get_hwnd=self.wm.get_game_hwnd)
            loop.starting.connect(self._on_promo_starting)
            loop.submitted.connect(self._on_promo_submitted)
            loop.confirmed.connect(self._on_promo_confirmed)
            loop.failed.connect(self._on_promo_failed)
            loop.unknown_error.connect(self._on_promo_unknown_error)
            loop.error.connect(self._on_promo_error)
            loop.recovered.connect(self._on_promo_recovered)
            self._promo_auto = loop
            loop.start()
            if announce:
                self.add_log(
                    "[Промокоды] Активирован автодетект промокодов - Успешно",
                    level="plain")
        else:
            self._promo_auto.stop_loop()
            self._promo_auto.wait()
            self._promo_auto = None

    # These mirror PromoWindow's own handlers (same signal shapes, since
    # both PromoActivateFlow and PromoAutoLoop share promo_activate.run_entry)
    # — logged here too, in the helper's main log, not only the promo
    # window's own one, since this loop runs whether or not that window
    # is even open. Expired/already-activated entries are not logged here
    # at all — that noise belongs only to the "Вывести данные" button's own
    # one-off listing, not to a check running every minute in the
    # background; PromoAutoLoop simply skips them without a signal.

    def _on_promo_starting(self, entry):
        self.add_log(
            f"[Промокоды] Обнаружен новый промокод {entry.code} - Активируем...",
            level="plain")

    def _on_promo_submitted(self, entry):
        title = entry.title or "без названия"
        self.add_log_segments(
            [("[Промокоды] ", theme.TEXT_SECONDARY),
             ("Активирован", theme.ACCENT_GREEN),
             (f" промокод {entry.code} — {title}", theme.TEXT_SECONDARY)],
            level="plain")

    def _on_promo_confirmed(self, entry):
        self.stats.record_promo_activation()

    def _on_promo_failed(self, entry):
        self.add_log(f"[Промокоды] Не удалось активировать промокод {entry.code}",
                     level="error")

    def _on_promo_unknown_error(self, entry):
        self.add_log(
            "[Промокоды] Произошла неизвестная ошибка при активации промокода.",
            level="plain")

    def _on_promo_error(self, message: str):
        self.add_log(f"[Промокоды] {message}", level="error")

    def _on_promo_recovered(self):
        self.add_log("[Промокоды] База промокодов снова доступна", level="plain")

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
            self._panel.stop_background()
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
        self.set_promo_watch_enabled(False)
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
