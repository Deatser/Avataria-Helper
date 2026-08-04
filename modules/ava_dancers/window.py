# modules/ava_dancers/window.py
from __future__ import annotations
from pathlib import Path

import cv2
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor, QFontMetricsF
from PySide6.QtCore import Qt, QTimer, QThread

from app.core.capture import grab_window
from app.core.input_sender import press_key
from app.core.template_match import FINISH_GOLD, FINISH_SILVER
from app.ui.module_window import ModuleWindow
from app.ui.settings_panel import SettingsPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.nt_switch import NtSwitch
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.log_panel import LogPanel
from app.ui import theme
from modules.ava_dancers.bot import (AvaBot, Thresholds, KEY_MAP,
                                     NICE, BONUS, BAD, DISLIKE, BOMB)
from modules.ava_dancers.entry_flow import EntryFlow
from modules.ava_dancers.exit_flow import ExitFlow
from modules.ava_dancers.leave_watch import LeaveWatch
from modules.ava_dancers.speedup_watch import SpeedupWatch

_DEFAULT_W = 420
_DEFAULT_H = 570
_MIN_H     = 510   # header + controls + tiles + log + actions + switch
_MIN_W     = 300

_SPEEDUP_BOOST_MS = 15_000   # how long to keep max poll rate / priority after a timer mark

# ── End of run ───────────────────────────────────────────────────────────────
# Placeholder routine for "the chosen reward is on screen, get out": stop
# playing well, then mash all four lanes so the game drops the run. The real
# sequence is still to be specified, so this stays deliberately blunt and
# short — it only has to end a round that is already over.
_FINISH_SPAM_MS       = 4_000   # total mashing time
_FINISH_SPAM_INTERVAL = 60      # ms between bursts of all four keys

# Gap between clicking Старт and putting the detector back to work, so it
# does not spend its first seconds polling a round that is still loading.
_RESTART_BOT_DELAY_MS = 2_000

# Time before the test shot is actually taken — long enough to alt-tab away
# from the game or minimise it by hand, to see whether the capture still
# gets the game and not whatever is on top of it.
_SCREENSHOT_DELAY_MS = 3_000

# Tile index → on-screen arrow (tiles are ordered A, S, W, D)
_KEY_LABELS = ["←", "↓", "↑", "→"]
_KEY_ARROWS = {"a": "←", "s": "↓", "w": "↑", "d": "→"}

_START_TEXT = "▶  Запустить бота для фарма золота"
_STOP_TEXT  = "■  Выключить бота для фарма золота"

# Tile readout scales with the window: point size ≈ 12% of its height
_ARROW_RATIO = 0.12
_ARROW_MIN   = 30
_ARROW_MAX   = 130
_ARROW_PT    = 56   # starting point size, replaced on the first resize
_DOT_SIZE    = 26   # starting dot size, replaced on the first resize
_LOG_H       = 120  # the log is a side channel here, keep it short
_LIT_MS      = 220  # how long a hit stays lit

# What each tile kind looks like in the UI
_KIND_COLOUR = {
    NICE:    theme.VW_CYAN,
    BONUS:   theme.ACCENT_GREEN,
    BOMB:    theme.VW_PURPLE,
    BAD:     theme.ACCENT_RED,
    DISLIKE: theme.ACCENT_RED,
}


_BACKDROP_STEM = "vaporwawe"
_PROJECT_ROOT  = Path(__file__).resolve().parents[2]
_TEMPLATES     = _PROJECT_ROOT / "templates"


_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_PROBE_PT = 100                    # reference size for measuring glyphs
_ARROW_SCALE: dict[str, float] = {}


def _arrow_scales() -> dict[str, float]:
    """Per-glyph size correction so all four arrows read the same length.

    In a monospace cell ↑ and ↓ run the full cell height while ← and → only
    span its width — about a third longer. Measure both spans once and shrink
    the tall ones to match the short ones.
    """
    if not _ARROW_SCALE:
        metrics = QFontMetricsF(theme.get_mono_font(_PROBE_PT, bold=True))
        spans = {}
        for arrow in _KEY_LABELS:
            rect = metrics.tightBoundingRect(arrow)
            spans[arrow] = max(rect.width(), rect.height())
        shortest = min(spans.values())
        _ARROW_SCALE.update(
            {a: (shortest / span if span else 1.0) for a, span in spans.items()}
        )
    return _ARROW_SCALE


def _default_backdrop(video: bool = True) -> str:
    """First existing templates/vaporwawe.* file.

    With video on, moving formats come first; with it off, stills do — but
    either way anything that exists is better than an empty panel.
    """
    moving = VIDEO_SUFFIXES + (".gif",)
    order  = moving + _STILL_SUFFIXES if video else _STILL_SUFFIXES + moving
    for suffix in order:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


class AvaDancersWindow(ModuleWindow):
    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Ava Dancers", config, save_fn, parent_overlay)
        self._wm  = window_manager
        self._bot: AvaBot | None = None
        self._speedup_watch: SpeedupWatch | None = None
        self._leave_watch: LeaveWatch | None = None
        self._settings: SettingsPanel | None = None
        self._exit_flow: ExitFlow | None = None
        self._entry_flow: EntryFlow | None = None
        self._finish_timer: QTimer | None = None
        self._finish_ticks = 0
        w = getattr(config, "width",  _DEFAULT_W)
        h = getattr(config, "height", _DEFAULT_H)
        self.resize(max(w, _MIN_W), max(h, _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = VwPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ───────────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        self._status_dot.set_stopped()   # red until the bot is running
        title = QLabel("AVA DANCERS")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.VW_CYAN}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.VW_CYAN)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.VW_MAGENTA)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)

        close_btn = NtButton("×", accent=theme.VW_MAGENTA)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._collapse_btn)
        header.addWidget(self._fav_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.VW_BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # ── Controls ─────────────────────────────────────────────────────────
        self._start_btn = NtButton(_START_TEXT, accent=theme.VW_CYAN, upper=False)
        self._start_btn.clicked.connect(self._toggle_bot)
        layout.addWidget(self._start_btn)

        settings_btn = NtButton("⚙  Настройки", accent=theme.VW_PURPLE,
                                upper=False)
        settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(settings_btn)

        debug_btn = NtButton("🎥  Сохранить последние кадры", accent=theme.VW_PURPLE,
                             upper=False)
        debug_btn.clicked.connect(self._dump_debug_frames)
        layout.addWidget(debug_btn)

        screenshot_btn = NtButton("📷  Скрин поля (через 3 сек)", accent=theme.VW_PURPLE,
                                  upper=False)
        screenshot_btn.clicked.connect(self._start_test_screenshot)
        layout.addWidget(screenshot_btn)

        # ── Tile status row — the main readout, so it gets the room ───────────
        tiles_row = QHBoxLayout()
        self._tiles_row = tiles_row
        tiles_row.addStretch()
        self._tile_dots:   list[NtStatusDot] = []
        self._tile_arrows: list[QLabel]      = []
        for label in _KEY_LABELS:
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addStretch()   # keep dot and arrow together, centred as a pair
            dot = NtStatusDot(size=_DOT_SIZE, accent=theme.VW_CYAN,
                              idle=theme.VW_TEXT)
            lbl = QLabel(label)
            # Mono, not the display face: arrows are guaranteed glyphs there
            lbl.setAlignment(Qt.AlignCenter)
            self._set_arrow_lit(lbl, False)
            # The backdrop runs from near-black grid to a bright sun, so every
            # glyph carries its own shadow instead of relying on contrast
            for widget in (dot, lbl):
                shadow = QGraphicsDropShadowEffect(widget)
                shadow.setBlurRadius(14)
                shadow.setOffset(0, 1)
                shadow.setColor(QColor(0, 0, 0, 200))
                widget.setGraphicsEffect(shadow)
            col.addWidget(dot, 0, Qt.AlignCenter)
            col.addWidget(lbl, 0, Qt.AlignCenter)
            col.addStretch()
            self._tile_dots.append(dot)
            self._tile_arrows.append(lbl)
            tiles_row.addLayout(col)
        tiles_row.addStretch()
        layout.addLayout(tiles_row, stretch=1)
        self._scale_tiles()

        # ── Log ──────────────────────────────────────────────────────────────
        # Clearing lives on the heading row as a small button, the same as in
        # the overlay and the garden: it is an action on the log itself, and
        # the full-width slot below is worth more to the guide.
        log_head = QHBoxLayout()
        log_label = QLabel("Ava Dance Log:")
        log_label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; background:transparent;"
        )
        clear_btn = NtButton("⌫", accent=theme.BORDER_BRIGHT)
        clear_btn.setFixedSize(22, 20)
        clear_btn.setToolTip("Очистить логи")
        log_head.addWidget(log_label)
        log_head.addStretch()
        log_head.addWidget(clear_btn)
        layout.addLayout(log_head)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)
        self._log.setMaximumHeight(_LOG_H)
        layout.addWidget(self._log)
        clear_btn.clicked.connect(lambda: self._log.clear_logs())

        guide_btn = NtButton("Гайд", upper=False,
                             accent=theme.VW_PURPLE, filled=True)
        guide_btn.setMinimumHeight(30)
        layout.addWidget(guide_btn)

        self._video_switch = NtSwitch("Видеофон  ·  выключите на слабом ПК",
                                      accent=theme.VW_CYAN)
        self._video_switch.set_checked_silently(
            getattr(self.config, "video_background", True)
        )
        self._video_switch.toggled.connect(self._toggle_video_background)
        layout.addWidget(self._video_switch)

        # ── Backdrop: templates/vaporwawe.* by default, video first ──────────
        self._panel.background_failed.connect(
            lambda msg: self._log.add_log(msg, level="error")
        )
        self._apply_backdrop(fade=False)

        # ── Drag handle ──────────────────────────────────────────────────────
        drag = NtDragHandle(dot_color=theme.VW_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    # ── Backdrop ─────────────────────────────────────────────────────────────

    def _apply_backdrop(self, fade: bool = True):
        video    = getattr(self.config, "video_background", True)
        backdrop = getattr(self.config, "background", "") or _default_backdrop(video)
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self._log.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _toggle_video_background(self, enabled: bool):
        self.config.video_background = enabled
        self.save_fn()
        self._apply_backdrop()
        self._log.add_log_segments(
            [("Фон окна: ", theme.TEXT_SECONDARY),
             ("видео" if enabled else "фото",
              theme.VW_CYAN if enabled else theme.TEXT_PRIMARY)],
            level="plain",
        )

    # ── showEvent: force repaint to fix visual freeze on first show ──────────

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    def _crt_open_ready(self) -> bool:
        """Hold the switch-on until the video backdrop has a frame to show."""
        return self._panel.backdrop_ready

    # ── Bot control ──────────────────────────────────────────────────────────

    def _toggle_bot(self):
        if self._running:
            self._stop_bot()
        else:
            self._start_bot()

    @property
    def _running(self) -> bool:
        """Started, from the button's point of view — the entry flow counts.

        Walking the menus can take several seconds, and during them the
        button has to read as "on" so a second press cancels instead of
        kicking off a second flow.
        """
        return bool((self._bot and self._bot.isRunning())
                    or (self._entry_flow and self._entry_flow.isRunning()))

    def _start_bot(self):
        """Make sure a round is actually running, then switch the bot on.

        The screen is only sampled from here on down — nothing looks at it
        while the module merely sits open.
        """
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        # Read as started right away: the entry flow is part of starting.
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._start_entry_flow(hwnd)

    def _start_detection(self):
        """The round is up — put the detector and its watchers to work."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd or (self._bot and self._bot.isRunning()):
            return

        self._bot = AvaBot(hwnd, self._thresholds())
        self._bot.tiles_seen.connect(self._on_tiles_seen)
        self._bot.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._bot.start()
        self._start_speedup_watch(hwnd)
        self._start_leave_watch(hwnd)

        self._status_dot.set_running()
        self._log.add_log("Бот запущен", level="success")
        if self.parent_overlay:
            self.parent_overlay.add_log("Ava Dancers: бот запущен")

    def _stop_bot(self):
        if self._bot:
            self._bot.stop_bot()
            if not self._bot.wait(600):
                self._bot.terminate()
            self._bot = None
        self._stop_speedup_watch()
        self._stop_leave_watch()
        # Stopping by hand mid-exit means "stop everything" — the click chain
        # and the mash are both part of the run, not separate machinery.
        # (_finish_run stops the bot before it arms either, so this cannot
        # cancel the very sequence that triggered it.)
        self._stop_finish_spam()
        self._stop_exit_flow()
        self._stop_entry_flow()

        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_stopped()
        for dot in self._tile_dots:
            dot.set_offline()
        self._log.add_log("Бот остановлен")
        if self.parent_overlay:
            self.parent_overlay.add_log("Ava Dancers: бот остановлен")

    # ── Speed-up warnings (04:00 / 06:00 / 07:30 / 09:30 / 11:00) ────────────
    # These messages live only in the main Avataria Helper log, not this
    # module's own — Ava Dancers' log is for tile-by-tile play-by-play, this
    # is a session-level heads-up, and the two don't need to say it twice.

    def _start_speedup_watch(self, hwnd: int):
        self._stop_speedup_watch()   # defensive: never leave a prior watcher
                                      # orphaned and still running underneath
                                      # a new one — that alone doubles every
                                      # message it emits.
        self._speedup_watch = SpeedupWatch(hwnd)
        self._speedup_watch.watch_started.connect(self._on_speedup_watch_started)
        self._speedup_watch.detected.connect(self._on_speedup_detected)
        self._speedup_watch.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._speedup_watch.start()

    def _on_speedup_watch_started(self):
        if self.parent_overlay:
            self.parent_overlay.add_log(
                "Ava Dance — запуск поиска по фото таймера (4:00, 6:00, 7:30, 9:30, 11:00)"
            )

    def _stop_speedup_watch(self):
        if self._speedup_watch:
            self._speedup_watch.stop_watch()
            if not self._speedup_watch.wait(600):
                self._speedup_watch.terminate()
            self._speedup_watch = None

    def _on_speedup_detected(self, label: str, score: float):
        if self.parent_overlay:
            self.parent_overlay.add_log(
                f"Ava Dance — нашёл отметку {label} (похожесть {score:.0%}): "
                f"скоро волна ускорения"
            )
        if self._bot and self._bot.isRunning():
            self._bot.setPriority(QThread.TimeCriticalPriority)
            self._bot.boost_poll_rate()
            QTimer.singleShot(_SPEEDUP_BOOST_MS, self._end_speedup_boost)

    def _end_speedup_boost(self):
        if self._bot and self._bot.isRunning():
            self._bot.setPriority(QThread.NormalPriority)
            self._bot.reset_poll_rate()

    # ── Entry flow (menus → running round) ────────────────────────────────────

    def _start_entry_flow(self, hwnd: int):
        self._stop_entry_flow()
        # The walk through the menus is not logged: it works, and narrating
        # every click buried the lines that matter. Errors still speak up —
        # a chain stuck on a button it cannot find has to be visible.
        self._entry_flow = EntryFlow(hwnd)
        self._entry_flow.flow_done.connect(self._start_detection)
        self._entry_flow.error.connect(self._on_entry_error)
        self._entry_flow.start()

    def _stop_entry_flow(self):
        if self._entry_flow:
            self._entry_flow.stop_flow()
            if not self._entry_flow.wait(1500):
                self._entry_flow.terminate()
            self._entry_flow = None

    def _on_entry_error(self, message: str):
        """A step that never found its button leaves the bot switched off."""
        self._log.add_log(message, level="error")
        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_stopped()

    # ── End-of-run watch (leave_gold / leave_silver) ──────────────────────────
    # Runs only while the bot does — the reward line can't appear otherwise,
    # and a once-a-second screen grab has no reason to keep running idle.

    def finish_target(self) -> str:
        stored = getattr(self.config, "finish_on", FINISH_GOLD)
        return FINISH_SILVER if stored == FINISH_SILVER else FINISH_GOLD

    def set_finish_target(self, target: str):
        """Settings switch flipped — a running watcher switches with it."""
        if self._leave_watch:
            self._leave_watch.set_target(target)
        silver = target == FINISH_SILVER
        self._log.add_log_segments(
            [("Завершение забега: ", theme.TEXT_SECONDARY),
             ("серебро" if silver else "золото",
              theme.TEXT_PRIMARY if silver else theme.ACCENT_AMBER)],
            level="plain",   # a setting's new value, not an action's outcome
        )

    def _start_leave_watch(self, hwnd: int):
        self._stop_leave_watch()
        self._leave_watch = LeaveWatch(hwnd, self.finish_target())
        self._leave_watch.leave_ready.connect(self._on_leave_ready)
        self._leave_watch.error.connect(
            lambda e: self._log.add_log(e, level="error"))
        self._leave_watch.start()

    def _stop_leave_watch(self):
        if self._leave_watch:
            self._leave_watch.stop_watch()
            if not self._leave_watch.wait(1500):
                self._leave_watch.terminate()
            self._leave_watch = None

    def _on_leave_ready(self, label: str, score: float):
        message = f"{label} набрано ({score:.0%}) — заканчиваю забег"
        self._log.add_log(message, level="success")
        if self.parent_overlay:
            self.parent_overlay.add_log(f"Ava Dance — {message}")
        self._finish_run()

    def _finish_run(self):
        """Stop playing, then mash every lane until the game drops the run."""
        if self._finish_timer:
            return   # already finishing
        hwnd = self._wm.get_game_hwnd()
        self._stop_bot()   # stop playing well first, or the detector keeps
                            # hitting real notes and holds the round open
        if not hwnd:
            return
        self._finish_ticks = max(1, _FINISH_SPAM_MS // _FINISH_SPAM_INTERVAL)
        self._finish_timer = QTimer(self)
        self._finish_timer.timeout.connect(self._finish_tick)
        self._finish_timer.start(_FINISH_SPAM_INTERVAL)

    def _finish_tick(self):
        hwnd = self._wm.get_game_hwnd()
        if hwnd:
            for key in KEY_MAP.values():
                press_key(hwnd, key)
        self._finish_ticks -= 1
        if self._finish_ticks <= 0:
            self._stop_finish_spam()
            self._log.add_log("Забег завершён", level="success")
            self._start_exit_flow()

    def _stop_finish_spam(self):
        if self._finish_timer:
            self._finish_timer.stop()
            self._finish_timer.deleteLater()
            self._finish_timer = None
        self._finish_ticks = 0

    # ── Exit flow (ОК → Повтор → Старт) ───────────────────────────────────────

    def _start_exit_flow(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено — выход не нажать",
                              level="error")
            return
        self._stop_exit_flow()
        restart = bool(getattr(self.config, "auto_restart", True))
        # Silent like the entry flow — errors only. See _start_entry_flow.
        self._exit_flow = ExitFlow(hwnd, restart)
        self._exit_flow.flow_done.connect(self._on_exit_flow_done)
        self._exit_flow.error.connect(
            lambda e: self._log.add_log(e, level="error"))
        self._exit_flow.start()

    def _stop_exit_flow(self):
        if self._exit_flow:
            self._exit_flow.stop_flow()
            if not self._exit_flow.wait(1500):
                self._exit_flow.terminate()
            self._exit_flow = None

    def _on_exit_flow_done(self):
        if not bool(getattr(self.config, "auto_restart", True)):
            self._log.add_log("Вышел из забега", level="success")
            return
        self._log.add_log("Новая игра запущена", level="success")
        QTimer.singleShot(_RESTART_BOT_DELAY_MS, self._restart_bot)

    def _restart_bot(self):
        """Pick the new round up where the last one left off.

        Deliberately delayed rather than fired the instant Старт is clicked:
        the round takes a moment to load, and a detector started against the
        loading screen is just burning polls on tiles that are not there yet.
        """
        if self._running:
            return   # started by hand while the round was loading
        self._start_bot()

    def _thresholds(self) -> Thresholds:
        defaults = Thresholds()
        return Thresholds(
            lit_share = getattr(self.config, "lit_share", defaults.lit_share),
            red_share = getattr(self.config, "red_share", defaults.red_share),
            hue_share = getattr(self.config, "hue_share", defaults.hue_share),
        )

    def _on_tiles_seen(self, events: list[tuple[int, str]]):
        """One or more tiles changed in the same poll — flash and log together.

        Two arrows can be pressable at once, or one pressable and one bad —
        that's one moment in the game, so it's one flash and one log line,
        not several arriving back to back.
        """
        by_tile = {i: kind for i, kind in events if 0 <= i < 4}
        if not by_tile:
            return
        for tile_id, kind in by_tile.items():
            self._flash_tile(tile_id, _KIND_COLOUR.get(kind, theme.VW_TEXT))

    def _set_arrow_colour(self, label: QLabel, colour: str):
        label.setStyleSheet(f"color:{colour}; background:transparent;")
        if label.isVisible():
            label.repaint()

    def _set_arrow_lit(self, label: QLabel, lit: bool):
        self._set_arrow_colour(label, theme.VW_CYAN if lit else theme.VW_TEXT)

    def _flash_tile(self, tile_id: int, colour: str):
        """Light dot and arrow in the tile's colour, then fade back to idle."""
        dot   = self._tile_dots[tile_id]
        arrow = self._tile_arrows[tile_id]
        dot.flash(colour)
        self._set_arrow_colour(arrow, colour)
        QTimer.singleShot(_LIT_MS, dot.set_offline)
        QTimer.singleShot(_LIT_MS,
                          lambda: self._set_arrow_colour(arrow, theme.VW_TEXT))

    def _scale_tiles(self):
        """Tiles are the readout you watch — they grow with the window."""
        arrows = getattr(self, "_tile_arrows", None)
        if not arrows:
            return
        point = int(min(self.height() * _ARROW_RATIO, self.width() * 0.11))
        point = max(_ARROW_MIN, min(_ARROW_MAX, point))
        scales = _arrow_scales()
        for label in arrows:
            glyph_pt = max(8, int(round(point * scales.get(label.text(), 1.0))))
            label.setFont(theme.get_mono_font(glyph_pt, bold=True))
            label.setFixedHeight(int(point * 1.5))   # boxes stay aligned
        for dot in self._tile_dots:
            dot.set_size(max(12, int(point * 0.45)))
        self._tiles_row.setSpacing(int(point * 0.95))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale_tiles()
        if self._settings is not None:
            self._settings.keep_inside_host()

    # ── Settings ──────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        """Show or hide the settings sheet in the middle of this window.

        Built once and kept — it is a child widget, so it costs nothing while
        hidden and holds the switch's state without re-reading the config.
        """
        if self._settings is None:
            self._settings = SettingsPanel(
                config  = self.config,
                save_fn = self.save_fn,
                host    = self,
            )
            self._settings.finish_target_changed.connect(self.set_finish_target)
            self._settings.auto_restart_changed.connect(self._on_auto_restart)
        self._settings.toggle()

    def _on_auto_restart(self, enabled: bool):
        self._log.add_log_segments(
            [("Новая игра автоматически: ", theme.TEXT_SECONDARY),
             ("да" if enabled else "нет",
              theme.ACCENT_GREEN if enabled else theme.TEXT_PRIMARY)],
            level="plain")

    def _dump_debug_frames(self):
        """Flush the bot's rolling capture buffer to disk for review.

        Click this right after a miss: the bot is always recording its last
        few seconds of raw tile frames while it runs, so the moment just
        happened is still in the buffer.
        """
        if not self._bot or not self._bot.isRunning():
            self._log.add_log("Бот не запущен — нечего сохранять", level="error")
            return
        out_dir = self._bot.dump_debug_frames(_PROJECT_ROOT / "debug_frames")
        if out_dir is None:
            self._log.add_log("Буфер пуст, кадров нет", level="error")
            return
        self._log.add_log(f"Кадры сохранены в {out_dir}", level="success")

    # ── Test screenshot (window-capture check) ────────────────────────────────
    # Verifies grab_window() actually reads the game's own render output —
    # not whatever the desktop happens to show — while it is minimised or
    # covered by another window. See app/core/capture.py.

    def _start_test_screenshot(self):
        if not self._wm.get_game_hwnd():
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        self._log.add_log(
            f"Скрин поля через {_SCREENSHOT_DELAY_MS // 1000} сек — "
            f"можно свернуть игру или переключиться на другое окно"
        )
        QTimer.singleShot(_SCREENSHOT_DELAY_MS, self._take_test_screenshot)

    def _take_test_screenshot(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        try:
            frame = grab_window(hwnd)
            out_path = _TEMPLATES / "screenshot.png"
            if not cv2.imwrite(str(out_path), frame):
                raise RuntimeError("cv2.imwrite вернул False")
        except Exception as exc:
            self._log.add_log(f"Скрин не удался: {exc}", level="error")
            return
        self._log.add_log(f"Скрин сохранён: {out_path}", level="success")

    # ── Favorite ─────────────────────────────────────────────────────────────

    def _toggle_favorite(self):
        self.config.favorite = not self.config.favorite
        self._fav_btn.setText("★" if self.config.favorite else "☆")
        self.save_fn()

        if self.config.favorite:
            verb, tail, colour = "добавлено", " в автозагрузку при старте", theme.ACCENT_GREEN
        else:
            verb, tail, colour = "удалено", " из автозагрузки при старте", theme.ACCENT_AMBER
        segments = [
            (f"Окно {self.module_name} ", theme.TEXT_SECONDARY),
            (verb, colour),
            (tail, theme.TEXT_SECONDARY),
        ]
        self._log.add_log_segments(segments)
        if self.parent_overlay:
            self.parent_overlay.add_log_segments(segments)

    # ── Close ────────────────────────────────────────────────────────────────

    def _teardown(self):
        """Runs on the real close, after the switch-off animation."""
        self._panel.stop_background()
        self._stop_finish_spam()
        self._stop_exit_flow()
        if self._bot and self._bot.isRunning():
            self._bot.stop_bot()
            if not self._bot.wait(600):
                self._bot.terminate()
        self._stop_speedup_watch()
        self._stop_leave_watch()
        self._stop_entry_flow()
