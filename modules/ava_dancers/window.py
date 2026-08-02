# modules/ava_dancers/window.py
from __future__ import annotations
from pathlib import Path

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor, QFontMetricsF
from PySide6.QtCore import Qt, QTimer, QThread

from app.ui.module_window import ModuleWindow
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.nt_switch import NtSwitch
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.log_panel import LogPanel
from app.ui import theme
from modules.ava_dancers.bot import (AvaBot, Thresholds, split_tiles,
                                     classify_tile, TILE_REGION,
                                     NICE, BONUS, BAD, DISLIKE, BOMB)
from modules.ava_dancers.speedup_watch import SpeedupWatch

_DEFAULT_W = 420
_DEFAULT_H = 570
_MIN_H     = 510   # header + controls + tiles + log + actions + switch
_MIN_W     = 300

_SPEEDUP_BOOST_MS = 15_000   # how long to keep max poll rate / priority after a timer mark

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
_KIND_LABEL = {
    NICE:    "стрелка",
    BONUS:   "бонус",
    BOMB:    "бомба",
    BAD:     "красная",
    DISLIKE: "дизлайк",
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

        test_btn = NtButton("◎  Тест детекции", accent=theme.VW_PURPLE,
                            upper=False)
        test_btn.clicked.connect(self._test_detection)
        layout.addWidget(test_btn)

        debug_btn = NtButton("🎥  Сохранить последние кадры", accent=theme.VW_PURPLE,
                             upper=False)
        debug_btn.clicked.connect(self._dump_debug_frames)
        layout.addWidget(debug_btn)

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

        # ── Log (stretchy) ───────────────────────────────────────────────────
        log_label = QLabel("Ava Dance Log:")
        log_label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; background:transparent;"
        )
        layout.addWidget(log_label)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)
        self._log.setMaximumHeight(_LOG_H)
        layout.addWidget(self._log)

        # ── Log actions ──────────────────────────────────────────────────────
        actions = QHBoxLayout()
        actions.setSpacing(theme.SPACING)
        clear_btn = NtButton("Очистить логи", upper=False,
                             accent=theme.BORDER_BRIGHT)
        clear_btn.clicked.connect(self._log.clear_logs)
        guide_btn = NtButton("Гайд", upper=False,
                             accent=theme.VW_PURPLE, filled=True)
        for btn in (clear_btn, guide_btn):
            btn.setMinimumHeight(30)
        actions.addWidget(clear_btn)
        actions.addWidget(guide_btn)
        layout.addLayout(actions)

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

    # ── Bot control ──────────────────────────────────────────────────────────

    def _toggle_bot(self):
        if self._bot and self._bot.isRunning():
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        self._bot = AvaBot(hwnd, self._thresholds())
        self._bot.tiles_seen.connect(self._on_tiles_seen)
        self._bot.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._bot.start()
        self._start_speedup_watch()

        # setText first: it only schedules an update(), set_active() repaints
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
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

    def _start_speedup_watch(self):
        self._stop_speedup_watch()   # defensive: never leave a prior watcher
                                      # orphaned and still running underneath
                                      # a new one — that alone doubles every
                                      # message it emits.
        self._speedup_watch = SpeedupWatch()
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
        self._log_tiles(by_tile)

    def _log_tiles(self, by_tile: dict[int, str]):
        """One line: all four arrows, each active one in its own kind's colour."""
        segments = [
            (f"{arrow}   ",
             _KIND_COLOUR.get(by_tile[i], theme.VW_TEXT) if i in by_tile else theme.TEXT_DIM)
            for i, arrow in enumerate(_KEY_LABELS)
        ]
        self._log.add_log_segments(segments, level="plain")

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

    # ── Test detection ────────────────────────────────────────────────────────

    def _test_detection(self):
        from app.core.capture import ScreenCapture
        try:
            img        = ScreenCapture.get().grab(TILE_REGION)
            tiles      = split_tiles(img)
            thresholds = self._thresholds()
            readings   = [classify_tile(t, thresholds) for t in tiles]
            for i, reading in enumerate(readings):
                colour  = _KIND_COLOUR.get(reading.kind, theme.TEXT_DIM)
                verdict = _KIND_LABEL.get(reading.kind, "пусто")
                self._log.add_log_segments(
                    [(f"{_KEY_LABELS[i]}  свет {100 * reading.lit:4.1f}%  "
                      f"красный {100 * reading.red:4.1f}%  "
                      f"цвет {100 * reading.colour:4.1f}%  ",
                      theme.TEXT_SECONDARY),
                     (verdict + ("  ЖМЁМ" if reading.pressable else ""), colour)],
                    level="plain",
                )
            self._dump_hsv(tiles, readings)
            self._log.blank_line()
        except Exception as e:
            self._log.add_log(str(e), level="error")

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

    def _dump_hsv(self, tiles, readings):
        """Write each tile as raw HSV, for eyeballing what the detector sees.

        The channels are saved unconverted, so the files look acid-coloured —
        hue lands in the blue channel and so on. That is the point: it makes
        colour differences between tiles obvious.

        The verdict goes in the filename, so a wrong call is visible in the
        directory listing and the file can be dropped straight into
        tests/fixtures/ as a regression case.
        """
        import cv2
        for i, (tile, reading) in enumerate(zip(tiles, readings)):
            path = _PROJECT_ROOT / f"hsv_{i}_{reading.kind}.png"
            cv2.imwrite(str(path), cv2.cvtColor(tile, cv2.COLOR_BGR2HSV))
        self._log.add_log(f"Снимки HSV сохранены в {_PROJECT_ROOT}",
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

    def closeEvent(self, event):
        self._panel.stop_background()
        if self._bot and self._bot.isRunning():
            self._bot.stop_bot()
            if not self._bot.wait(600):
                self._bot.terminate()
        self._stop_speedup_watch()
        super().closeEvent(event)
