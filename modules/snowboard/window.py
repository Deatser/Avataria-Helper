# modules/snowboard/window.py
"""Сноуборд — the shell only, mirrored down from Ava Dancers' own window.py.

The run itself controls with just A and D across seven lanes, not four keys
each with a direction of its own, so the tile row here is seven identical
lane markers rather than four arrows — everything else about the shell
(header, start button, settings, log, guide, drag handle) follows Ava
Dancers' layout. No lane detection, no key presses, no bot loop yet — the
one real thing running is the "Играть ещё" watch, so a finished run does
not just sit there. The slope's own outer rectangle was already worked out
by hand — see modules/snowboard/slope_area.py — but the lanes inside it run
at an angle, not straight down the screen, so a second calibration tool is
still here: a four-cornered box, each corner dragged on its own, for
marking out a lane's own slanted edges.
"""
from __future__ import annotations


import cv2
import numpy as np

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QRect, QTimer

from app.core.capture import grab_window
from app.core.input_sender import click_at, press_key
from app.core.paths import TEMPLATES
from app.core.template_match import best_match, load_template
from app.ui import theme
from app.ui.multi_polygon_overlay import MultiPolygonOverlay
from app.ui.module_window import ModuleWindow
from app.ui.quad_calibration_overlay import QuadCalibrationOverlay
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.vw_panel import VwPanel
from modules.snowboard.player_lanes import LANE_COLOURS, LANE_QUADS, LANE_REGIONS
from modules.snowboard.settings_panel import SnowboardSettingsPanel
from modules.snowboard.track_rows import ROW_COLOURS, ROW_QUADS, ROW_REGIONS

_BACKDROP_STEM = "snowboard_sinthwawe"
_TEMPLATES     = TEMPLATES
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _default_backdrop() -> str:
    """First existing templates/snowboard_sinthwawe.* file — the same rule
    Ava Dancers' own backdrop picker uses."""
    for suffix in _STILL_SUFFIXES:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""

_DEFAULT_W = 420
_DEFAULT_H = 570
_MIN_W     = 300
_MIN_H     = 510

_LOG_H = 160   # a floor now, not a fixed height — the log takes the slack
              # that used to just be empty space above/below the lane row

_START_TEXT = "▶  Запустить бота для фарма золота"
_STOP_TEXT  = "■  Выключить бота для фарма золота"

# Seven lanes, not four keyed directions — the same glyph for every one of
# them, since a lane is just a position, not its own command.
_LANE_COUNT = 7
_LANE_GLYPH = "\\"
_LANE_RATIO = 0.12
_LANE_MIN   = 26
_LANE_MAX   = 110
_DOT_SIZE   = 22

# "Играть ещё" — the end-of-run replay button. Checked across the whole
# game window once a second, the whole time the bot is "running" — the
# run has no detection of its own yet, but this alone is worth having:
# it means a run does not just sit there once the previous one has ended.
_PLAY_AGAIN_TEMPLATE  = "snowboard_play_again.png"
_PLAY_AGAIN_THRESHOLD = 0.85
_PLAY_AGAIN_CHECK_MS  = 1000

# Moving to the rightmost lane the instant a run starts — confirmed live
# (2026-08-03): pressing A and targeting lane 1 lands exactly on lane 1 as
# coded, but that is the wrong edge; the right one is what the run
# actually needs, so this presses D and targets lane 7. Detected, not
# mashed blind: reads the lane back after every press and stops the
# moment it actually reads as lane 7, rather than pressing a fixed number
# of times and hoping that was enough from wherever the run actually
# started. _MAX_EDGE_ATTEMPTS is just a safety cap — one more than the six
# steps edge-to-edge could ever take — so a detection that never lines up
# cannot mash D forever.
_MAX_EDGE_ATTEMPTS    = 9
_MAX_EDGE_INTERVAL_MS = 200

# Watching each of the seven rows for something passing through it — a
# screen-diff against a couple of ticks back, the same idea Садовник's own
# click-confirm watch used, just aimed at "did this patch of screen
# change" rather than "has the character arrived".
#
# Lowered again and sped up (2026-08-03): at 500ms/65%, the run drove
# straight into a real obstacle without even trying to dodge — the check
# either never landed on a frame where it was actually in the row, or 65%
# turned out to be above what even a real hit reads as. Checking several
# times a second gives a crossing obstacle more chances to be caught
# inside its own brief window on screen; both numbers are still rough
# guesses, not yet checked against a marked, deliberate collision.
_ROW_WATCH_MS   = 150
_ROW_HISTORY    = 2     # how many past frames a row keeps, to diff against
_ROW_CHANGE_PCT = 35.0

# Twice a second, matching the row watch above — corrects which lane the
# run actually thinks it is on, quietly; only a real change moves the
# tracked lane, and only the avoidance move below is worth a log line.
_LANE_WATCH_MS = 500

# "Определить полосу" — matches the snowboarder's own sprite against each
# of the seven lane crops in turn; whichever scores highest is the one
# actually holding it right now.
_LANE_TEMPLATE = "snowboard_basic.png"
# A lane's own box (LANE_REGIONS) is barely larger than the sprite's own
# template — some lanes are narrower than it — so the capture is padded
# out on every side to give matchTemplate room to actually search.
_LANE_CAPTURE_PAD = 25

# "Сравнить с линией" — a plain, obstacle-free row texture, matched against
# all seven ROW_REGIONS in turn. Purely a calibration aid: it gives the
# user a real % reading for "empty" so _ROW_CHANGE_PCT can be picked
# against actual numbers instead of guessed.
_LINE_TEMPLATE = "snowboard_line.png"

# Where the quad calibration box first appears — centred on the game
# window, seeded as a plain rectangle; drag its corners from there.
_QUAD_DEFAULT_W = 300
_QUAD_DEFAULT_H = 400

_QUAD_START_TEXT = "📐  Область по углам"
_QUAD_STOP_TEXT  = "📐  Записать область"


class SnowboardWindow(ModuleWindow):
    """Сноуборд — header, frame and the usual module-window habits, plus a
    start button and settings sheet that do not yet do anything. Filled in
    once the lane-detection side of the module exists.
    """

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Сноуборд", config, save_fn, parent_overlay)
        self._wm = window_manager
        self._running = False
        self._settings: SnowboardSettingsPanel | None = None
        self._play_again_timer: QTimer | None = None
        self._row_watch_timer: QTimer | None = None
        self._row_frames: list[list] = [[] for _ in ROW_REGIONS]
        self._lane_watch_timer: QTimer | None = None
        self._current_lane = _LANE_COUNT - 1   # assumed — see _move_to_start_edge
        self._quad = QuadCalibrationOverlay(window_manager, reference=self)
        self._rows_overlay = MultiPolygonOverlay(window_manager, reference=self)
        self._player_lanes_overlay = MultiPolygonOverlay(window_manager, reference=self)

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
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.SB_BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        self._start_btn = NtButton(_START_TEXT, accent=theme.SB_STEEL,
                                   upper=False)
        self._start_btn.clicked.connect(self._toggle_running)
        layout.addWidget(self._start_btn)

        settings_btn = NtButton("⚙  Настройки", accent=theme.SB_STEEL_SOFT,
                                upper=False)
        settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(settings_btn)

        self._quad_btn = NtButton(_QUAD_START_TEXT, accent=theme.SB_STEEL_SOFT,
                                  upper=False)
        self._quad_btn.clicked.connect(self._toggle_quad)
        layout.addWidget(self._quad_btn)

        detect_lane_btn = NtButton("🔍  Определить полосу",
                                   accent=theme.SB_STEEL_SOFT, upper=False)
        detect_lane_btn.clicked.connect(self._detect_lane)
        layout.addWidget(detect_lane_btn)

        compare_line_btn = NtButton("📏  Сравнить с линией",
                                    accent=theme.SB_STEEL_SOFT, upper=False)
        compare_line_btn.clicked.connect(self._compare_line)
        layout.addWidget(compare_line_btn)

        layout.addLayout(self._build_lanes())

        layout.addLayout(self._build_log(), stretch=1)

        guide_btn = NtButton("Гайд", upper=False,
                             accent=theme.SB_STEEL_SOFT, filled=True)
        guide_btn.setMinimumHeight(30)
        layout.addWidget(guide_btn)

        # Backdrop: templates/snowboard_sinthwawe.* by default —
        # configured here, still inside _build_ui and so still before the
        # window has ever been shown.
        self._panel.background_failed.connect(
            lambda msg: self._log.add_log(msg, level="error"))
        self._apply_backdrop(fade=False)

        drag = NtDragHandle(dot_color=theme.SB_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _apply_backdrop(self, fade: bool = True):
        backdrop = getattr(self.config, "background", "") or _default_backdrop()
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self._log.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _crt_open_ready(self) -> bool:
        """Hold the switch-on until the video backdrop has a frame to show."""
        return self._panel.backdrop_ready

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        self._status_dot = NtStatusDot(accent=theme.SB_STEEL)
        self._status_dot.set_stopped()

        title = QLabel("СНОУБОРД")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.SB_STEEL}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.SB_STEEL)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.SB_ICE)
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

    def _build_lanes(self) -> QHBoxLayout:
        """Five lane markers — position readouts, not per-key arrows, since
        the run steers only with A/D across five lanes rather than one key
        each way. Filled in once the lane-detection side exists."""
        row = QHBoxLayout()
        self._lanes_row = row
        row.addStretch()
        self._lane_dots:   list[NtStatusDot] = []
        self._lane_glyphs: list[QLabel]      = []
        for _ in range(_LANE_COUNT):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addStretch()
            dot = NtStatusDot(size=_DOT_SIZE, accent=theme.SB_ICE,
                              idle=theme.SB_TEXT)
            lbl = QLabel(_LANE_GLYPH)
            lbl.setAlignment(Qt.AlignCenter)
            self._set_lane_lit(lbl, False)
            for widget in (dot, lbl):
                shadow = QGraphicsDropShadowEffect(widget)
                shadow.setBlurRadius(14)
                shadow.setOffset(0, 1)
                shadow.setColor(QColor(0, 0, 0, 200))
                widget.setGraphicsEffect(shadow)
            col.addWidget(dot, 0, Qt.AlignCenter)
            col.addWidget(lbl, 0, Qt.AlignCenter)
            col.addStretch()
            self._lane_dots.append(dot)
            self._lane_glyphs.append(lbl)
            row.addLayout(col)
        row.addStretch()
        self._scale_lanes()
        return row

    def _set_lane_lit(self, label: QLabel, lit: bool):
        colour = theme.SB_ICE if lit else theme.SB_TEXT
        label.setStyleSheet(f"color:{colour}; background:transparent;")

    def _scale_lanes(self):
        """Lanes are the readout you watch — they grow with the window,
        the same way Ava Dancers' arrows do."""
        glyphs = getattr(self, "_lane_glyphs", None)
        if not glyphs:
            return
        point = int(min(self.height() * _LANE_RATIO, self.width() * 0.11))
        point = max(_LANE_MIN, min(_LANE_MAX, point))
        for label in glyphs:
            label.setFont(theme.get_mono_font(point, bold=True))
            label.setFixedHeight(int(point * 1.5))
        for dot in self._lane_dots:
            dot.set_size(max(12, int(point * 0.4)))
        self._lanes_row.setSpacing(int(point * 0.7))

    def _build_log(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)

        head = QHBoxLayout()
        title = QLabel("Snowboard Log:")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.SB_TEXT}; background:transparent;")
        head.addWidget(title)
        head.addStretch()
        head.addLayout(build_log_actions(self._log, theme.SB_BORDER))
        block.addLayout(head)

        block.addWidget(self._log, stretch=1)
        return block

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scale_lanes()
        if self._settings is not None:
            self._settings.keep_inside_host()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    # ── Bot toggle — placeholder until the lane side of the module exists ───

    def _toggle_running(self):
        if self._running:
            self._running = False
            if self._play_again_timer is not None:
                self._play_again_timer.stop()
                self._play_again_timer = None
            if self._row_watch_timer is not None:
                self._row_watch_timer.stop()
                self._row_watch_timer = None
            if self._lane_watch_timer is not None:
                self._lane_watch_timer.stop()
                self._lane_watch_timer = None
            self._row_frames = [[] for _ in ROW_REGIONS]
            self._rows_overlay.clear()
            self._player_lanes_overlay.clear()
            self._start_btn.setText(_START_TEXT)
            self._start_btn.set_active(False)
            self._status_dot.set_stopped()
            for dot in self._lane_dots:
                dot.set_offline()
            self._log.add_log("Бот остановлен", level="plain")
            return

        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        self._running = True
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._status_dot.set_running()
        self._log.add_log_segments(
            [("Бот запущен — ", theme.TEXT_SECONDARY),
             ("логика спуска ещё не реализована", theme.ACCENT_AMBER)],
            level="plain")

        self._rows_overlay.show_shapes(list(zip(ROW_QUADS, ROW_COLOURS)))
        self._player_lanes_overlay.show_shapes(list(zip(LANE_QUADS, LANE_COLOURS)))

        self._play_again_timer = QTimer(self)
        self._play_again_timer.timeout.connect(
            lambda: self._check_play_again(hwnd))
        self._play_again_timer.start(_PLAY_AGAIN_CHECK_MS)

        self._row_watch_timer = QTimer(self)
        self._row_watch_timer.timeout.connect(lambda: self._check_rows(hwnd))
        self._row_watch_timer.start(_ROW_WATCH_MS)

        self._lane_watch_timer = QTimer(self)
        self._lane_watch_timer.timeout.connect(lambda: self._correct_lane(hwnd))
        self._lane_watch_timer.start(_LANE_WATCH_MS)

        self._log.add_log("Съезжаем в крайнюю правую полосу", level="plain")
        self._move_to_start_edge(hwnd, _MAX_EDGE_ATTEMPTS)

    def _check_rows(self, hwnd: int):
        """One row at a time: grab just its own patch of screen, and
        compare it to a couple of ticks back — a big enough change means
        something just appeared on it. Quiet otherwise; only a real change
        is worth a log line, not every tick's own reading. Whatever is
        flagged this tick is handed straight to _avoid_threats — ROW_QUADS
        and LANE_QUADS are both sorted left-to-right on screen, so row
        index i and lane index i are the same physical lane directly, no
        conversion needed.
        """
        if not self._running:
            return
        alerted: set[int] = set()
        for i, region in enumerate(ROW_REGIONS):
            try:
                frame = grab_window(hwnd, region)
            except Exception:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            history = self._row_frames[i]
            if history and history[0].shape == gray.shape:
                changed = float(np.abs(
                    gray.astype(np.int16) - history[0].astype(np.int16)
                ).mean()) / 255 * 100
                if changed >= _ROW_CHANGE_PCT:
                    lane = i
                    alerted.add(lane)
                    self._log.add_log_segments(
                        [(f"Полоса {lane + 1} — ", LANE_COLOURS[lane]),
                         ("похоже, кто-то появился", LANE_COLOURS[lane])],
                        level="plain")

            history.append(gray)
            if len(history) > _ROW_HISTORY:
                history.pop(0)

        self._avoid_threats(hwnd, alerted)

    def _correct_lane(self, hwnd: int):
        """Re-reads which lane the sprite is actually on, quietly — see
        _detect_lane for the same match done by hand, with the full
        per-lane readout."""
        if not self._running:
            return
        scores = self._lane_scores(hwnd)
        if scores is None:
            return
        self._current_lane = max(range(len(scores)), key=lambda i: scores[i])

    def _avoid_threats(self, hwnd: int, alerted: set[int]):
        """Only acts if the lane actually under us is one of the flagged
        ones — the nearest lane that is not gets the move, so the run
        drifts as little as possible rather than always bolting for an
        edge."""
        if self._current_lane not in alerted:
            return
        free = [lane for lane in range(_LANE_COUNT) if lane not in alerted]
        if not free:
            return   # every lane flagged at once — nowhere safer to go
        target = min(free, key=lambda lane: abs(lane - self._current_lane))
        self._move_to_lane(hwnd, target)

    def _move_to_lane(self, hwnd: int, target_lane: int):
        diff = target_lane - self._current_lane
        if diff == 0:
            return
        self._log.add_log_segments(
            [("Угроза на полосе ", theme.ACCENT_RED),
             (str(self._current_lane + 1), LANE_COLOURS[self._current_lane]),
             (" — уходим на ", theme.TEXT_SECONDARY),
             (str(target_lane + 1), LANE_COLOURS[target_lane])],
            level="plain")
        key = "d" if diff > 0 else "a"
        self._current_lane = target_lane   # optimistic — _correct_lane re-checks
        self._press_lane_steps(hwnd, key, abs(diff))

    def _press_lane_steps(self, hwnd: int, key: str, presses_left: int):
        if not self._running or presses_left <= 0:
            return
        press_key(hwnd, key)
        QTimer.singleShot(
            _MAX_EDGE_INTERVAL_MS,
            lambda: self._press_lane_steps(hwnd, key, presses_left - 1))

    def _lane_scores(self, hwnd: int) -> list[float] | None:
        """The snowboarder's own sprite matched against each of the seven
        lane crops in turn — None only if the template itself or a grab
        failed, not for a plain low score."""
        template = load_template(_LANE_TEMPLATE)
        if template is None:
            return None
        scores: list[float] = []
        for region in LANE_REGIONS:
            # Padded on every side: a lane's own box is barely bigger than
            # the sprite's own template (some are narrower than it), so
            # matched against the bare box every score came back 0 — there
            # was nowhere for the template to even sit inside it.
            padded = {
                "left": region["left"] - _LANE_CAPTURE_PAD,
                "top": region["top"] - _LANE_CAPTURE_PAD,
                "width": region["width"] + 2 * _LANE_CAPTURE_PAD,
                "height": region["height"] + 2 * _LANE_CAPTURE_PAD,
            }
            try:
                frame = grab_window(hwnd, padded)
            except Exception:
                return None
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            score, _loc = best_match(gray, template)
            scores.append(score)
        return scores

    def _move_to_start_edge(self, hwnd: int, attempts_left: int):
        """Reads the lane back after every press and stops the moment it
        actually reads as lane 7 — see _MAX_EDGE_ATTEMPTS for why this
        is detected rather than mashed blind."""
        if not self._running:
            return

        scores = self._lane_scores(hwnd)
        detected = (max(range(len(scores)), key=lambda i: scores[i])
                   if scores else None)
        if detected is not None:
            self._current_lane = detected
        if detected == _LANE_COUNT - 1:
            self._log.add_log("В крайней правой полосе", level="plain")
            return

        if attempts_left <= 0:
            self._log.add_log(
                "Не удалось точно определить крайнюю правую полосу — "
                "останавливаемся здесь", level="error")
            return

        press_key(hwnd, "d")
        QTimer.singleShot(
            _MAX_EDGE_INTERVAL_MS,
            lambda: self._move_to_start_edge(hwnd, attempts_left - 1))

    def _check_play_again(self, hwnd: int):
        """"Играть ещё" — checked once a second the whole time the bot is
        running, so a finished run does not just sit there."""
        if not self._running:
            return
        template = load_template(_PLAY_AGAIN_TEMPLATE)
        if template is None:
            return
        try:
            gray = cv2.cvtColor(grab_window(hwnd), cv2.COLOR_BGR2GRAY)
        except Exception:
            return   # a dropped frame here is not worth stopping the run over
        score, (x, y) = best_match(gray, template)
        if score < _PLAY_AGAIN_THRESHOLD:
            return
        rect = self._wm.window_rect_screen(hwnd)
        if rect is None:
            return
        ox, oy, _w, _h = rect
        th, tw = template.shape[:2]
        click_at(hwnd, ox + x + tw // 2, oy + y + th // 2)
        self._log.add_log_segments(
            [("Нажали «Играть ещё» — ", theme.TEXT_SECONDARY),
             (f"{score * 100:.0f}%", theme.SB_ICE)],
            level="plain")

    # ── Quad calibration ─────────────────────────────────────────────────────

    def _toggle_quad(self):
        """First press: a four-cornered box appears over the game, each
        corner draggable on its own. Second press: its corners and centre
        go to the log, and it hides."""
        if self._quad.isVisible():
            self._log_quad_bounds()
            self._quad.clear()
            self._quad_btn.setText(_QUAD_START_TEXT)
            self._quad_btn.set_active(False)
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
        box = QRect(ox + (w - _QUAD_DEFAULT_W) // 2,
                   oy + (h - _QUAD_DEFAULT_H) // 2,
                   _QUAD_DEFAULT_W, _QUAD_DEFAULT_H)
        self._quad.show_at(box)
        self._quad_btn.setText(_QUAD_STOP_TEXT)
        self._quad_btn.set_active(True)
        self._log.add_log(
            "Область — тяните за угол, чтобы наклонить его отдельно, за "
            "середину — чтобы подвинуть всю фигуру. Нажмите кнопку ещё "
            "раз, чтобы записать.",
            level="plain")

    def _log_quad_bounds(self):
        corners = self._quad.corners()
        box = self._quad.bounds()
        cx, cy = box.center().x(), box.center().y()
        labels = ["верх-лево", "верх-право", "низ-право", "низ-лево"]
        segments = [("Область по углам — ", theme.TEXT_SECONDARY)]
        for label, point in zip(labels, corners):
            segments += [
                (f"{label} ", theme.TEXT_DIM),
                (f"({point.x()}, {point.y()})", theme.SB_ICE),
                ("  ", theme.TEXT_DIM),
            ]
        segments += [
            ("центр (", theme.TEXT_SECONDARY),
            (f"{cx}, {cy}", theme.SB_ICE),
            (")", theme.TEXT_SECONDARY),
        ]
        self._log.add_log_segments(segments, level="plain")

    # ── Lane detection ───────────────────────────────────────────────────────

    def _detect_lane(self):
        """The full, logged version of what _correct_lane does quietly
        every tick while the bot runs — one crop per lane, the
        snowboarder's own sprite matched against each in turn."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        scores = self._lane_scores(hwnd)
        if scores is None:
            self._log.add_log("Шаблон не найден", level="error")
            return

        for i, score in enumerate(scores):
            self._log.add_log_segments(
                [(f"Полоса {i + 1} — ", LANE_COLOURS[i]),
                 (f"{score * 100:.1f}%", LANE_COLOURS[i])],
                level="plain")

        best_i = max(range(len(scores)), key=lambda i: scores[i])
        self._current_lane = best_i
        self._log.add_log_segments(
            [("Стоим на полосе ", theme.TEXT_SECONDARY),
             (str(best_i + 1), LANE_COLOURS[best_i]),
             (f" ({scores[best_i] * 100:.1f}%)", LANE_COLOURS[best_i])],
            level="plain")

    def _compare_line(self):
        """A plain empty-row template matched against all seven
        ROW_REGIONS — a calibration reading, not a decision: gives the
        user a real % for "nothing here" to weigh _ROW_CHANGE_PCT
        against, one log line per row."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        template = load_template(_LINE_TEMPLATE)
        if template is None:
            self._log.add_log("Шаблон snowboard_line.png не найден",
                              level="error")
            return

        for i, region in enumerate(ROW_REGIONS):
            try:
                frame = grab_window(hwnd, region)
            except Exception:
                self._log.add_log_segments(
                    [(f"Полоса {i + 1} — ", ROW_COLOURS[i]),
                     ("не удалось снять кадр", theme.ACCENT_RED)],
                    level="plain")
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            score, _loc = best_match(gray, template)
            self._log.add_log_segments(
                [(f"Полоса {i + 1} — ", ROW_COLOURS[i]),
                 (f"{score * 100:.1f}%", ROW_COLOURS[i])],
                level="plain")

    # ── Settings ─────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = SnowboardSettingsPanel(self)
        self._settings.toggle()

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
        """Nothing of ours should outlive the window — the "Играть ещё"
        watcher, the quad calibration box and the track overlay included."""
        if self._play_again_timer is not None:
            self._play_again_timer.stop()
            self._play_again_timer = None
        if self._row_watch_timer is not None:
            self._row_watch_timer.stop()
            self._row_watch_timer = None
        if self._lane_watch_timer is not None:
            self._lane_watch_timer.stop()
            self._lane_watch_timer = None
        self._quad.clear()
        self._rows_overlay.clear()
        self._player_lanes_overlay.clear()
        self._panel.stop_background()
