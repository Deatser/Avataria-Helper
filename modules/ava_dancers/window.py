# modules/ava_dancers/window.py
from __future__ import annotations
from pathlib import Path

import cv2
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor, QFontMetricsF
from PySide6.QtCore import Qt, QRect, QTimer, QThread

from app.core.capture import grab_window
from app.core.input_sender import press_key
from app.core.template_match import (FINISH_GOLD, FINISH_SILVER, best_match,
                                     load_template, primary_monitor_region)
from app.ui.calibration_overlay import CalibrationOverlay
from app.ui.module_window import ModuleWindow
from app.ui.settings_panel import SettingsPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui import theme
from modules.ava_dancers.bot import (AvaBot, Thresholds, KEY_MAP,
                                     NICE, BONUS, BAD, DISLIKE, BOMB)
from modules.ava_dancers.entry_flow import EntryFlow
from modules.ava_dancers.exit_flow import ExitFlow
from modules.ava_dancers.game_over_watch import GameOverWatch
from modules.ava_dancers.leave_watch import (LEAVE_REGIONS, LEAVE_TEMPLATES,
                                             MATCH_THRESHOLD, LeaveWatch)
from modules.ava_dancers.speedup_watch import SpeedupWatch

_DEFAULT_W = 420
_DEFAULT_H = 570
_MIN_H     = 510   # header + controls + tiles + log + actions + switch
_MIN_W     = 300

_SPEEDUP_BOOST_MS = 15_000   # how long to keep max poll rate / priority after a timer mark

# Gap between clicking Старт and putting the detector back to work, so it
# does not spend its first seconds polling a round that is still loading.
_RESTART_BOT_DELAY_MS = 2_000

# The reward line's rise means "stop playing well" — mash every lane until
# the game drops the run on its own. The exact sequence that follows is not
# ours to control, so this stays deliberately blunt and short: it only has
# to end a round that has already, in effect, ended.
_FINISH_SPAM_MS       = 4_000   # total mashing time
_FINISH_SPAM_INTERVAL = 60      # ms between bursts of all four keys

# Where the calibration box first appears — centred on the game window,
# seeded as a plain rectangle; drag its edges or middle from there.
_CALIB_DEFAULT_W = 300
_CALIB_DEFAULT_H = 120

_CALIB_START_TEXT = "📐  Область награды"
_CALIB_STOP_TEXT  = "📐  Записать область"

# Tile index → on-screen arrow (tiles are ordered A, S, W, D)
_KEY_LABELS = ["←", "↓", "↑", "→"]
_KEY_ARROWS = {"a": "←", "s": "↓", "w": "↑", "d": "→"}

_START_TEXT = "▶  Запустить бота"
_STOP_TEXT  = "■  Выключить бота"

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

    def __init__(self, config, save_fn, window_manager, parent_overlay=None,
                stats=None):
        super().__init__("Ava Dancers", config, save_fn, parent_overlay)
        self._wm  = window_manager
        self._stats = stats
        self._bot_active = False   # intent, not thread liveness — see _running
        self._bot: AvaBot | None = None
        self._speedup_watch: SpeedupWatch | None = None
        self._leave_watch: LeaveWatch | None = None
        self._gameover_watch: GameOverWatch | None = None
        self._settings: SettingsPanel | None = None
        self._exit_flow: ExitFlow | None = None
        self._entry_flow: EntryFlow | None = None
        self._finish_timer: QTimer | None = None
        self._finish_ticks = 0
        self._calib = CalibrationOverlay(window_manager, reference=self)
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

        # Calibration tools, not day-to-day controls — kept wired up for
        # when the reward templates need retuning, just hidden from the
        # normal view.
        detect_silver_btn = NtButton("🥈  Детект серебра", accent=theme.VW_PURPLE,
                                     upper=False)
        detect_silver_btn.clicked.connect(
            lambda: self._detect_reward(FINISH_SILVER, "Серебро"))
        layout.addWidget(detect_silver_btn)
        detect_silver_btn.setVisible(False)

        detect_gold_btn = NtButton("🥇  Детект золота", accent=theme.VW_PURPLE,
                                   upper=False)
        detect_gold_btn.clicked.connect(
            lambda: self._detect_reward(FINISH_GOLD, "Золото"))
        layout.addWidget(detect_gold_btn)
        detect_gold_btn.setVisible(False)

        self._calib_btn = NtButton(_CALIB_START_TEXT, accent=theme.VW_PURPLE,
                                   upper=False)
        self._calib_btn.clicked.connect(self._toggle_calib)
        layout.addWidget(self._calib_btn)
        self._calib_btn.setVisible(False)

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
        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)
        self._log.setMaximumHeight(_LOG_H)

        log_head = QHBoxLayout()
        log_label = QLabel("Ava Dancers Log:")
        log_label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; background:transparent;"
        )
        log_head.addWidget(log_label)
        log_head.addStretch()
        log_head.addLayout(build_log_actions(self._log, theme.BORDER_BRIGHT))
        layout.addLayout(log_head)

        layout.addWidget(self._log)

        guide_btn = NtButton("Гайд", upper=False,
                             accent=theme.VW_PURPLE, filled=True)
        guide_btn.setMinimumHeight(30)
        layout.addWidget(guide_btn)

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
        """Started, from the button's point of view — tracks intent, not
        thread liveness.

        Walking the menus, playing a round, and clicking through to the next
        one are all part of one continuous "on" state (see _bot_active) —
        between rounds the detector and its watchers are briefly torn down
        and rebuilt (_begin_round / _stop_round_threads) without the bot
        itself ever reading as stopped, so a click during that gap still has
        to route to _stop_bot rather than starting a second flow on top.
        """
        return self._bot_active

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
        self._bot_active = True
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._start_entry_flow(hwnd)

    def _start_detection(self):
        """The round is up — put the detector and its watchers to work."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd or (self._bot and self._bot.isRunning()):
            return
        self._begin_round(hwnd, announce=True)

    def _begin_round(self, hwnd: int, announce: bool):
        """Start the detector and its watchers against one round.

        Shared by the first round of a session (announce=True, from
        _start_detection) and every round auto-restart picks up after
        (announce=False, from _resume_after_restart) — the bot never reads
        as "stopped" in between, so only the first one is worth a log line.
        """
        self._bot = AvaBot(hwnd, self._thresholds())
        self._bot.tiles_seen.connect(self._on_tiles_seen)
        self._bot.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._bot.start()
        self._start_speedup_watch(hwnd)
        self._start_leave_watch(hwnd)
        self._start_gameover_watch(hwnd)

        self._status_dot.set_running()
        if announce:
            self._log.add_log("Бот запущен", level="success")
            self._log_to_helper("бот запущен")

    def _stop_playing_threads(self):
        """Stop the detector and the reward watch — everything that plays
        the round and decides when to leave it.

        GameOverWatch is deliberately left running: _finish_run calls this
        before it starts mashing keys to force the round to end, and the
        banner that mashing causes is exactly what GameOverWatch is still up
        to catch.
        """
        if self._bot:
            self._bot.stop_bot()
            if not self._bot.wait(600):
                self._bot.terminate()
            self._bot = None
        self._stop_speedup_watch()
        self._stop_leave_watch()

    def _stop_round_threads(self):
        """Tear down everything about the current round, GameOverWatch and
        the finish mash included.

        Split out from _stop_bot so _on_game_over can clear it all before
        the exit click-chain without the button, status dot or log reading
        as "stopped" — the whole point of not switching the bot off between
        rounds.
        """
        self._stop_playing_threads()
        self._stop_finish_spam()
        self._stop_gameover_watch()

    def _stop_bot(self):
        self._bot_active = False   # also stops a pending auto-restart, if any
        self._stop_round_threads()
        # Stopping by hand mid-exit means "stop everything" — the click
        # chain is part of the run, not separate machinery.
        self._stop_exit_flow()
        self._stop_entry_flow()

        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_stopped()
        for dot in self._tile_dots:
            dot.set_offline()
        self._log.add_log("Бот остановлен")
        self._log_to_helper("бот остановлен")

    def _log_to_helper(self, message: str):
        """Only session-level events go to the helper's own log — [Ava
        Dancers] marking whose line it is, the same convention Садовник и
        Уборщик use. Callers pass the message lowercase; capitalised here
        so every line reads the same regardless of how it was written."""
        if not self.parent_overlay:
            return
        self.parent_overlay.add_log_segments(
            [("[Ava Dancers] - ", theme.VW_MAGENTA),
             (message[:1].upper() + message[1:], theme.TEXT_SECONDARY)],
            level="plain")

    # ── Speed-up warnings (04:00 / 06:00 / 07:30 / 09:30 / 11:00) ────────────
    # This message lives only in the main Avataria Helper log, not this
    # module's own — Ava Dancers' log is for tile-by-tile play-by-play, this
    # is a session-level heads-up.

    def _start_speedup_watch(self, hwnd: int):
        self._stop_speedup_watch()   # defensive: never leave a prior watcher
                                      # orphaned and still running underneath
                                      # a new one — that alone doubles every
                                      # message it emits.
        self._speedup_watch = SpeedupWatch(hwnd)
        self._speedup_watch.detected.connect(self._on_speedup_detected)
        self._speedup_watch.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._speedup_watch.start()

    def _stop_speedup_watch(self):
        if self._speedup_watch:
            self._speedup_watch.stop_watch()
            if not self._speedup_watch.wait(600):
                self._speedup_watch.terminate()
            self._speedup_watch = None

    def _on_speedup_detected(self, label: str, score: float):
        self._log_to_helper(
            f"нашёл отметку {label} (похожесть {score:.0%}): скоро волна ускорения"
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
        self._bot_active = False
        self._log.add_log(message, level="error")
        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_stopped()

    # ── End of run ───────────────────────────────────────────────────────────
    # Two watchers, one after the other: LeaveWatch says when the chosen
    # reward has grown and it is time to force the round to end (_finish_run
    # mashes the lanes), then GameOverWatch says when that has actually
    # happened (the ИГРА ОКОНЧЕНА banner) and _on_game_over banks the run.
    # Both run only while the bot does — neither can appear otherwise, and a
    # once-a-second screen grab has no reason to keep running idle.

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
        # This one stays local to the module's own log — tile-by-tile
        # play-by-play, not a helper-log event. The helper only hears about
        # a run once it is actually over — see _on_game_over.
        message = f"{label} набрало ({score:.0%}) — заканчиваю забег"
        self._log.add_log(message, level="success")
        self._finish_run()

    def _finish_run(self):
        """Stop playing well, then mash every lane until the game drops the
        run — GameOverWatch (left running, see _stop_playing_threads) picks
        up from there once the banner it causes appears."""
        if self._finish_timer:
            return   # already finishing
        hwnd = self._wm.get_game_hwnd()
        self._stop_playing_threads()
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

    def _stop_finish_spam(self):
        if self._finish_timer:
            self._finish_timer.stop()
            self._finish_timer.deleteLater()
            self._finish_timer = None
        self._finish_ticks = 0

    def _start_gameover_watch(self, hwnd: int):
        self._stop_gameover_watch()
        self._gameover_watch = GameOverWatch(hwnd)
        self._gameover_watch.game_over.connect(self._on_game_over)
        self._gameover_watch.error.connect(
            lambda e: self._log.add_log(e, level="error"))
        self._gameover_watch.start()

    def _stop_gameover_watch(self):
        if self._gameover_watch:
            self._gameover_watch.stop_watch()
            if not self._gameover_watch.wait(1500):
                self._gameover_watch.terminate()
            self._gameover_watch = None

    def _on_game_over(self, score: float):
        """The ИГРА ОКОНЧЕНА banner is up — bank the run, then move on.

        The reward is not read off the screen: the round always pays what
        the chosen mode promises (2750 silver, plus 30 gold if the mode is
        gold), so the settings value is enough on its own.
        """
        hwnd = self._wm.get_game_hwnd()
        self._stop_round_threads()   # no more tiles to read on this screen

        gold_mode = self.finish_target() == FINISH_GOLD
        silver, gold = 2750, (30 if gold_mode else 0)
        if self._stats is not None:
            self._stats.record_ava_dancers_run(gold=gold, silver=silver)
            games_played = self._stats.data.ava_dancers.games_played
        else:
            games_played = 0

        rewards = []
        if silver:
            rewards.append(f"{silver} серебра")
        if gold:
            rewards.append(f"{gold} золота")
        message = f"закончили забег №{games_played} — заработали {' и '.join(rewards)}"
        self._log.add_log(message, level="success")
        self._log_to_helper(message)

        if not hwnd:
            self._log.add_log("Игровое окно не найдено — выход не нажать",
                              level="error")
            self._stop_bot()
            return

        if bool(getattr(self.config, "auto_restart", True)):
            self._start_exit_flow(hwnd)
        else:
            self._stop_bot()

    # ── Exit flow (ОК → Повтор → Старт) ───────────────────────────────────────
    # Only ever started when a new round is about to be picked up — see
    # _on_game_over. The bot is deliberately left reading as "running" the
    # whole time it clicks through (see _stop_round_threads), so the button
    # and status dot never flicker off between rounds.

    def _start_exit_flow(self, hwnd: int):
        self._stop_exit_flow()
        # Silent like the entry flow — errors only. See _start_entry_flow.
        self._exit_flow = ExitFlow(hwnd, restart=True)
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
        QTimer.singleShot(_RESTART_BOT_DELAY_MS, self._resume_after_restart)

    def _resume_after_restart(self):
        """Pick the new round up where the last one left off.

        Deliberately delayed rather than fired the instant Старт is clicked
        (see _RESTART_BOT_DELAY_MS): the round takes a moment to load, and a
        detector started against the loading screen is just burning polls on
        tiles that are not there yet. Goes straight to _begin_round rather
        than back through _start_bot/_start_entry_flow — the exit flow just
        clicked Начать itself, so there is nothing left to resume from.
        """
        if not self._bot_active:
            return   # stopped by hand while this was waiting to fire
        hwnd = self._wm.get_game_hwnd()
        if not hwnd or (self._bot and self._bot.isRunning()):
            return   # game window gone, or already running somehow
        self._begin_round(hwnd, announce=False)

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

    # ── Reward detection (calibration) ────────────────────────────────────────
    # One-shot match-percentage readout for the reward-line templates — click
    # to see how well leave_gold.png / leave_silver.png match the current
    # screen, no action taken beyond logging the number. Searched inside
    # LeaveWatch's own calibrated region for that currency (see
    # leave_watch.LEAVE_REGIONS), not the whole monitor — this is meant to
    # check the same spot the real watcher will actually use.

    def _detect_reward(self, key: str, label: str):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        filename = LEAVE_TEMPLATES[key]
        template = load_template(filename)
        if template is None:
            self._log.add_log(f"Не найден шаблон: {filename}", level="error")
            return
        region = LEAVE_REGIONS.get(key) or primary_monitor_region()
        try:
            gray = cv2.cvtColor(grab_window(hwnd, region),
                                cv2.COLOR_BGR2GRAY)
        except Exception as exc:
            self._log.add_log(f"Скрин не удался: {exc}", level="error")
            return
        score, _ = best_match(gray, template)
        hit = score >= MATCH_THRESHOLD
        tag = "✅ найдено" if hit else "— не найдено"
        self._log.add_log(f"{label}: {score:.1%} {tag}",
                          level="success" if hit else "plain")

    # ── Rectangle calibration ───────────────────────────────────────────────
    # Marks out where the reward line actually sits, so the watchers can be
    # pointed at that one spot instead of scanning the whole screen for it.

    def _toggle_calib(self):
        """First press: a plain box appears over the game — drag its
        middle to move it, an edge or corner to resize it. Second press:
        its bounds go to the log, and it hides."""
        if self._calib.isVisible():
            self._log_calib_bounds()
            self._calib.clear()
            self._calib_btn.setText(_CALIB_START_TEXT)
            self._calib_btn.set_active(False)
            return

        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        rect = self._wm.window_rect_screen(hwnd)
        if rect is None:
            self._log.add_log("Не удалось определить положение окна игры",
                              level="error")
            return

        ox, oy, w, h = rect
        box = QRect(ox + (w - _CALIB_DEFAULT_W) // 2,
                   oy + (h - _CALIB_DEFAULT_H) // 2,
                   _CALIB_DEFAULT_W, _CALIB_DEFAULT_H)
        self._calib.show_at(box)
        self._calib_btn.setText(_CALIB_STOP_TEXT)
        self._calib_btn.set_active(True)
        self._log.add_log(
            "Область — тяните за середину, чтобы подвинуть, за край или "
            "угол — чтобы изменить размер. Нажмите кнопку ещё раз, чтобы "
            "записать.",
            level="plain")

    def _log_calib_bounds(self):
        box = self._calib.bounds()
        cx, cy = box.center().x(), box.center().y()
        self._log.add_log_segments(
            [("Область — ", theme.TEXT_SECONDARY),
             ("x", theme.VW_CYAN), (f" {box.left()}", theme.VW_MAGENTA),
             ("  y", theme.VW_CYAN), (f" {box.top()}", theme.VW_MAGENTA),
             ("  w", theme.VW_CYAN), (f" {box.width()}", theme.VW_MAGENTA),
             ("  h", theme.VW_CYAN), (f" {box.height()}", theme.VW_MAGENTA),
             ("  центр (", theme.TEXT_SECONDARY),
             (f"{cx}, {cy}", theme.VW_MAGENTA),
             (")", theme.TEXT_SECONDARY)],
            level="plain")

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
        self._stop_exit_flow()
        self._stop_round_threads()
        self._stop_entry_flow()
        self._calib.clear()
