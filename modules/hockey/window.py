# modules/hockey/window.py
"""Хоккей — the shell (mirrored down from Ava Dancers' and Snowboard's own
window.py: header, start button, settings sheet, video/photo backdrop,
guide button, drag handle) plus a first real attempt at the bot itself.

The real input is one knob, not two: hold, drag left/right by *some
amount*, release — the further the drag, the sharper the curve, in
whichever direction it went (confirmed live 2026-08-04: direction was
right both ways, strength was there but too weak). There is no separate
"aim point" the player picks independently of how hard they pull; how
far left/right the shot lands is however much curving that drag amount
buys. _pull_point is that one knob, expressed as curve_frac in [-1, 1].

For scoring, that same single knob is modelled as a quadratic Bezier
curve (start = shooter, end = a point in the goal mouth *derived from*
curve_frac, control = the midpoint pushed sideways by curve_frac too) —
see _attempt_shot, which sweeps curve_frac, evaluates each resulting
curve against every tracked defender's predicted position at the moment
the puck would cross their own depth (players.Tracker — see there for
how a defender's own bounce between its patrol bounds gets predicted
forward across the ~1.5s a shot takes), and takes whichever curve_frac
scores safest. The control point's y is always exactly the midpoint of
start/end y, which makes the curve's own y(t) come out perfectly linear
in t — no quadratic to invert — so "where will the puck be, in x, at the
depth a given defender sits at" is one division and one Bezier
evaluation, not a numerical solve.

If nothing safe enough turns up, the shot is not forced right away —
_attempt_shot waits (re-evaluating on every scan tick, not spending the
cooldown) for a clearer moment, up to _MAX_WAIT_S before firing the
least-bad option anyway rather than stalling forever.

The physical drag itself (_execute_shot) is a short, local pull near the
shooter, not a trace of the puck's own predicted flight path — the first
version dragged the mouse the whole way to the goal (~500px) and the
game's own drag-tracking never resolved (no shot fired, the aim line
just kept following the cursor until the bot was stopped, live-tested
2026-08-04).

What still cannot be gotten right without more live checks, flagged
where they are set: exactly how curve_frac maps to real screen pixels of
pull (_PULL_HORIZONTAL_MAX_PX — the one live data point so far said
"weak" at 110px, raised, not yet reconfirmed) and _PUCK_TRAVEL_MS, now a
real measurement (~1.5s) rather than a guess.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel

from app.core.input_sender import mouse_down_at, mouse_move_to, mouse_up_at
from app.ui import theme
from app.ui.calibration_overlay import CalibrationOverlay
from app.ui.module_window import ModuleWindow
from app.ui.tracking_overlay import TrackingOverlay
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES
from modules.hockey.players import Tracker, scan
from modules.hockey.rink_area import (FIXED_AREA, GOAL_LEFT_FRAC,
                                      GOAL_RIGHT_FRAC, GOAL_Y_FRAC,
                                      SHOOTER_X_FRAC, SHOOTER_Y_FRAC)
from modules.hockey.settings_panel import HockeySettingsPanel

_BACKDROP_STEM = "hockey_frost"
_PROJECT_ROOT  = Path(__file__).resolve().parents[2]
_TEMPLATES     = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _bezier_x(x0: float, x1: float, x2: float, t: float) -> float:
    return (1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t ** 2 * x2


def _bezier_y(y0: float, y1: float, y2: float, t: float) -> float:
    return (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t ** 2 * y2


def _default_backdrop(video: bool = True) -> str:
    """First existing templates/hockey_frost.* file — same rule Ava
    Dancers' and Snowboard's own backdrop pickers use: with video on,
    moving formats come first; with it off, stills do."""
    moving = VIDEO_SUFFIXES + (".gif",)
    order  = moving + _STILL_SUFFIXES if video else _STILL_SUFFIXES + moving
    for suffix in order:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""

_DEFAULT_W = 420
_DEFAULT_H = 480
_MIN_W     = 300
_MIN_H     = 420

_LOG_H = 160

_START_TEXT = "▶  Запустить бота для заброса шайбы"
_STOP_TEXT  = "■  Выключить бота для заброса шайбы"

# Where the calibration box first appears — centred on the game window,
# seeded as a plain rectangle; drag its edges or middle from there.
_CALIB_DEFAULT_W = 300
_CALIB_DEFAULT_H = 200

_CALIB_START_TEXT = "📐  Область экрана"
_CALIB_STOP_TEXT  = "📐  Записать область"

_DETECT_TEXT = "🔍  Определить игроков"

# "Тестовый бросок" — a fixed, known pull with nothing predicted or
# scored, at one of a few fixed strengths each press (not just three fixed
# directions — 2026-08-04: "мы просто тянем курсор влево право с разной
# силой и чем сильнее тянем тем сильнее искривление"). Purely a
# calibration aid: the fastest way to find out how a pull distance
# actually turns into a curve is to fire a known one by hand and watch
# what happens, not to guess again.
_TEST_TEXT = "🎯  Тестовый бросок"
_TEST_FRACS = [0.0, -0.5, 0.5, -1.0, 1.0]   # curve_frac values, cycled
                                            # through one per press

# ── Bot loop ─────────────────────────────────────────────────────────────────

# Defender scan/track cadence. Raised 2026-08-04: at 150ms, the tracking
# boxes visibly snapped to each new position and then sat still for the
# rest of the interval, rather than reading as continuous motion — the
# overlay's own easing (see tracking_overlay._EASE) converges in ~90ms,
# so anything slower than that leaves a dead gap between arriving and the
# next update. 70ms keeps every update inside that ~90ms window instead,
# so the box is always still catching up to somewhere new when the next
# one lands.
_SCAN_MS = 70

# How long one shot's own cooldown is — rough guess at "puck lands or
# misses, the next one spawns, worth trying again" — not yet checked
# against a real round's own pacing.
_SHOT_COOLDOWN_MS = 2600

# Real time (ms) for a shot to travel start-to-goal at the game's own
# constant puck speed — measured, not guessed, 2026-08-04 ("шайба летит
# где-то 1.5 секунды"). Needed to turn a point on the Bezier curve (a
# fraction 0..1 of the way along it) into "what time will the puck
# actually be there", which is what predicting a defender's position at
# that moment needs.
_PUCK_TRAVEL_MS = 1500

_CURVE_STEPS  = 9     # candidate curve_frac values swept per shot, hard
                     # left through to hard right
_CURVE_MAX_PX = 140   # how far the scored Bezier's control point leans
                     # off the straight line at curve_frac = ±1 — a
                     # scoring-model shape, not the real drag distance
_SAFETY_PX    = 46    # how far clear of a defender's predicted position
                     # the puck's own path has to pass to call it safe

# How long to wait for a clearer moment before shooting the least-bad
# candidate anyway, rather than either forcing every shot on cooldown
# regardless of safety or stalling forever if nothing ever clears.
_MAX_WAIT_S = 4.0

# The physical gesture — corrected 2026-08-04 after a live run: dragging
# the mouse along the puck's own predicted flight path (start clear
# across to the goal, ~500px) made the game's own drag-tracking never
# resolve at all — no shot fired, aim line just followed the cursor
# indefinitely until the bot was stopped. Replaced with a short, local
# pull instead — a real player pulls the mouse a little near the shooter
# and lets go, the puck's own flight afterwards is the game's business,
# not something the cursor has to trace.
#
# Second live check the same day: pull direction was confirmed correct
# (left curves left, right curves right) at a flat 110px either way, but
# the curve itself read as weak both directions — so unlike the first
# fix, this is a magnitude problem, not a mechanic problem. The pull is
# no longer a fixed length regardless of how much curve was chosen —
# _PULL_HORIZONTAL_PX now scales with curve_frac itself (0 at dead
# straight, full length at curve_frac = ±1), stacked onto a constant
# vertical pull that just performs the drag gesture at all.
_PULL_VERTICAL_PX     = 90    # constant upward pull — "cocks" the shot
_PULL_HORIZONTAL_PX   = 220   # sideways pull at curve_frac = ±1; scales
                              # down linearly toward 0 at curve_frac = 0
_DRAG_STEPS   = 5      # steps across the pull
_DRAG_STEP_MS = 30     # spacing between them — ~150ms to pull, then a
                      # short hold before releasing
_HOLD_MS      = 90     # pause at the pulled point before letting go — a
                      # human's own release is not the same instant as
                      # their last bit of motion


class HockeyWindow(ModuleWindow):
    """Хоккей — header, frame and the usual module-window habits, a
    rectangle calibration tool for marking out screen regions by hand, and
    a first working bot loop: track defenders, predict a clear arc, shoot.
    """

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Хоккей", config, save_fn, parent_overlay)
        self._wm = window_manager
        self._running = False
        self._settings: HockeySettingsPanel | None = None
        self._calib = CalibrationOverlay(window_manager, reference=self)
        self._tracking = TrackingOverlay(window_manager, reference=self)
        self._tracker = Tracker()
        self._scan_timer: QTimer | None = None
        self._next_shot_at = 0.0   # time.monotonic() — 0 means "shoot now"
        self._shot_count = 0
        self._dragging = False     # a shot's own drag is in flight; the
                                   # scan loop keeps tracking but will not
                                   # start a second shot over it
        self._waiting_since: float | None = None   # time.monotonic() a
                                   # clear-enough window started being
                                   # waited for; None when not waiting
        self._last_wait_log = 0.0  # throttles the "waiting" log line
        self._test_index = 0       # which of _TEST_FRACS "Тестовый бросок"
                                   # fires next

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
        sep.setStyleSheet(f"background:{theme.HK_BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        self._start_btn = NtButton(_START_TEXT, accent=theme.HK_ICE,
                                   upper=False)
        self._start_btn.clicked.connect(self._toggle_running)
        layout.addWidget(self._start_btn)

        settings_btn = NtButton("⚙  Настройки", accent=theme.HK_ICE_SOFT,
                                upper=False)
        settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(settings_btn)

        self._calib_btn = NtButton(_CALIB_START_TEXT, accent=theme.HK_ICE_SOFT,
                                   upper=False)
        self._calib_btn.clicked.connect(self._toggle_calib)
        layout.addWidget(self._calib_btn)

        detect_btn = NtButton(_DETECT_TEXT, accent=theme.HK_ICE_SOFT,
                              upper=False)
        detect_btn.clicked.connect(self._detect_players)
        layout.addWidget(detect_btn)

        test_btn = NtButton(_TEST_TEXT, accent=theme.HK_RINK_RED,
                            upper=False)
        test_btn.clicked.connect(self._test_shot)
        layout.addWidget(test_btn)

        layout.addLayout(self._build_log(), stretch=1)

        guide_btn = NtButton("Гайд", upper=False,
                             accent=theme.HK_ICE_SOFT, filled=True)
        guide_btn.setMinimumHeight(30)
        layout.addWidget(guide_btn)

        # Backdrop: templates/hockey_frost.* by default, video first —
        # configured here, still inside _build_ui and so still before the
        # window has ever been shown, since a video decodes asynchronously
        # and the earlier it is told to start, the more likely a real frame
        # is already waiting the moment the switch-on animation asks
        # backdrop_ready whether to hold for one.
        self._panel.background_failed.connect(
            lambda msg: self._log.add_log(msg, level="error"))
        self._apply_backdrop(fade=False)

        drag = NtDragHandle(dot_color=theme.HK_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _apply_backdrop(self, fade: bool = True):
        video    = getattr(self.config, "video_background", True)
        backdrop = getattr(self.config, "background", "") or _default_backdrop(video)
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self._log.add_log(f"Фон не загружен: {backdrop}", level="error")


    def _crt_open_ready(self) -> bool:
        """Hold the switch-on until the video backdrop has a frame to show."""
        return self._panel.backdrop_ready

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        self._status_dot = NtStatusDot(accent=theme.HK_ICE)
        self._status_dot.set_stopped()

        title = QLabel("ХОККЕЙ")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.HK_ICE}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.HK_ICE)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.HK_ICE_SOFT)
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

    def _build_log(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)

        head = QHBoxLayout()
        title = QLabel("Hockey Log:")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.HK_TEXT}; background:transparent;")
        head.addWidget(title)
        head.addStretch()
        head.addLayout(build_log_actions(self._log, theme.HK_BORDER))
        block.addLayout(head)

        block.addWidget(self._log, stretch=1)
        return block

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._settings is not None:
            self._settings.keep_inside_host()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    # ── Bot loop ─────────────────────────────────────────────────────────────

    def _toggle_running(self):
        if self._running:
            self._running = False
            if self._scan_timer is not None:
                self._scan_timer.stop()
                self._scan_timer = None
            self._tracker = Tracker()
            self._tracking.clear()
            self._dragging = False
            self._waiting_since = None
            self._start_btn.setText(_START_TEXT)
            self._start_btn.set_active(False)
            self._status_dot.set_stopped()
            self._log.add_log("Бот остановлен", level="plain")
            return

        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        self._running = True
        self._shot_count = 0
        self._next_shot_at = 0.0
        self._waiting_since = None
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._status_dot.set_running()
        self._log.add_log_segments(
            [("Бот запущен — ", theme.TEXT_SECONDARY),
             ("слежение за игроками и первый заброс", theme.HK_ICE)],
            level="plain")

        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(lambda: self._bot_tick(hwnd))
        self._scan_timer.start(_SCAN_MS)

    def _bot_tick(self, hwnd: int):
        if not self._running:
            return
        now = time.monotonic()
        detections = scan(hwnd, FIXED_AREA.region)
        tracked = self._tracker.update(detections, now)
        self._tracking.update_targets(
            [QRect(int(d.x), int(d.y), d.w, d.h) for d in detections])

        if self._dragging or now < self._next_shot_at:
            return
        self._attempt_shot(hwnd, tracked, now)

    def _detect_players(self):
        """The same scan the bot loop runs, done once by hand — % per
        match, and the tracking boxes light up over whatever it found."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        detections = scan(hwnd, FIXED_AREA.region)
        if not detections:
            self._log.add_log("Игроков не найдено", level="plain")
            self._tracking.clear()
            return
        for det in detections:
            self._log.add_log_segments(
                [("Игрок — ", theme.HK_ICE_SOFT),
                 (f"{det.score * 100:.1f}%", theme.HK_ICE),
                 (f"  ({det.template})", theme.TEXT_DIM)],
                level="plain")
        self._tracking.update_targets(
            [QRect(int(d.x), int(d.y), d.w, d.h) for d in detections])

    def _test_shot(self):
        """A fixed, known curve_frac — nothing predicted, nothing scored
        — so what a given strength actually does to the shot can be
        watched and reported back, rather than inferred from whether a
        scored shot happened to clear its threats. Cycles through
        _TEST_FRACS on every press."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        if self._dragging:
            self._log.add_log("Уже тянем — подождите отпускания",
                              level="plain")
            return

        curve_frac = _TEST_FRACS[self._test_index % len(_TEST_FRACS)]
        self._test_index += 1

        p0 = FIXED_AREA.point(SHOOTER_X_FRAC, SHOOTER_Y_FRAC)
        pull = self._pull_point(p0, curve_frac)
        self._log.add_log_segments(
            [("ТЕСТ — ", theme.ACCENT_RED),
             (f"curve_frac {curve_frac:+.2f}", theme.HK_ICE),
             ("  старт ", theme.HK_ICE_SOFT),
             (f"({p0[0]}, {p0[1]})", theme.HK_ICE),
             ("  тянем к ", theme.HK_ICE_SOFT),
             (f"({pull[0]:.0f}, {pull[1]:.0f})", theme.HK_ICE),
             ("  — посмотри, куда полетела шайба", theme.TEXT_SECONDARY)],
            level="plain")
        self._execute_shot(hwnd, p0, pull)

    # ── Shot prediction ──────────────────────────────────────────────────────

    def _attempt_shot(self, hwnd: int, tracked: list, now: float):
        """Sweeps curve_frac (the one real control knob — see the module
        docstring), scores each resulting curve against every tracked
        defender's predicted position at the moment it would cross their
        own depth, and either drags out the safest one or, if nothing is
        safe enough yet, waits for a clearer moment instead of forcing a
        bad shot every cooldown."""
        p0 = FIXED_AREA.point(SHOOTER_X_FRAC, SHOOTER_Y_FRAC)
        goal_y = FIXED_AREA.point(0, GOAL_Y_FRAC)[1]
        goal_left  = FIXED_AREA.point(GOAL_LEFT_FRAC, GOAL_Y_FRAC)[0]
        goal_right = FIXED_AREA.point(GOAL_RIGHT_FRAC, GOAL_Y_FRAC)[0]
        goal_half   = (goal_right - goal_left) / 2
        goal_centre = (goal_left + goal_right) / 2

        # Only defenders actually between the shooter and the goal, in y,
        # can be in the puck's own path.
        threats = [t for t in tracked if goal_y < t.y < p0[1]]

        best = None   # (clearance, tie-break, curve_frac, end, control)
        for ci in range(_CURVE_STEPS):
            curve_frac = -1.0 + 2.0 * ci / max(1, _CURVE_STEPS - 1)
            end_x = goal_centre + curve_frac * goal_half
            end = (end_x, goal_y)
            control = ((p0[0] + end_x) / 2 + curve_frac * _CURVE_MAX_PX,
                      (p0[1] + goal_y) / 2)
            clearance = self._min_clearance(p0, control, end, threats, now)
            # Among equally safe candidates (all clear, or a tie), prefer
            # the straightest shot — no reason to curve when nothing
            # forces it.
            tie = -abs(curve_frac)
            if best is None or (clearance, tie) > (best[0], best[1]):
                best = (clearance, tie, curve_frac, end, control)

        clearance, _tie, curve_frac, end, control = best
        safe = clearance == float("inf") or clearance >= _SAFETY_PX

        if not safe:
            if self._waiting_since is None:
                self._waiting_since = now
            waited = now - self._waiting_since
            if waited < _MAX_WAIT_S:
                self._log_waiting(clearance, threats, now)
                return   # try again next scan tick — cooldown not spent
            # Waited long enough with nothing clearing up — take the
            # least-bad shot rather than stall forever.

        self._waiting_since = None
        pull = self._pull_point(p0, curve_frac)
        self._log_shot_debug(p0, control, end, pull, curve_frac, threats,
                            clearance, now)
        self._execute_shot(hwnd, p0, pull)
        self._next_shot_at = now + _SHOT_COOLDOWN_MS / 1000

    def _log_waiting(self, clearance: float, threats: list, now: float):
        if now - self._last_wait_log < 1.0:
            return   # once a second is plenty — this can retry every scan
        self._last_wait_log = now
        self._log.add_log_segments(
            [("Ждём момента — ", theme.TEXT_SECONDARY),
             (f"лучший запас {clearance:.0f}px", theme.ACCENT_AMBER),
             (f" при {len(threats)} угрозах", theme.TEXT_SECONDARY)],
            level="plain")

    def _pull_point(self, p0: tuple, curve_frac: float) -> tuple:
        """Where the drag itself actually goes for a given curve_frac
        ([-1, 1]) — a constant upward pull (just what performs the drag
        gesture at all) plus a sideways pull that scales with curve_frac
        itself, zero at dead straight, full _PULL_HORIZONTAL_PX at ±1."""
        return (p0[0] + curve_frac * _PULL_HORIZONTAL_PX,
                p0[1] - _PULL_VERTICAL_PX)

    def _min_clearance(self, p0: tuple, control: tuple, end: tuple,
                       threats: list, now: float) -> float:
        """How far the curve's own path passes from the closest predicted
        defender, across every threat between shooter and goal — the
        smallest of those decides whether a candidate counts as safe.
        inf when there is nothing to dodge."""
        if not threats:
            return float("inf")
        y0, y2 = p0[1], end[1]
        span = y2 - y0
        if span == 0:
            return float("inf")
        worst = float("inf")
        for t in threats:
            frac = (t.y - y0) / span
            if not 0.0 <= frac <= 1.0:
                continue
            arrive_at = now + frac * (_PUCK_TRAVEL_MS / 1000)
            predicted_x = self._tracker.predict_x(t, arrive_at)
            puck_x = _bezier_x(p0[0], control[0], end[0], frac)
            worst = min(worst, abs(puck_x - predicted_x))
        return worst

    def _log_shot_debug(self, p0: tuple, control: tuple, end: tuple,
                        pull: tuple, curve_frac: float, threats: list,
                        clearance: float, now: float):
        """Mirrors the reference bot's own debug format (start/control/end
        point) — "конец" is the predicted goal target the candidate was
        scored against, "тянем" is where the drag itself actually goes
        (see _pull_point) — the two are no longer the same point, so both
        are worth having in the log while the drag mechanic is still
        being checked."""
        self._shot_count += 1
        safe = clearance == float("inf") or clearance >= _SAFETY_PX
        clear_txt = "нет угроз" if clearance == float("inf") else f"{clearance:.0f}px"
        colour = theme.ACCENT_GREEN if safe else theme.ACCENT_RED
        self._log.add_log_segments(
            [(f"Удар #{self._shot_count} — ", theme.TEXT_SECONDARY),
             (f"curve_frac {curve_frac:+.2f}", theme.HK_ICE),
             ("  старт ", theme.HK_ICE_SOFT),
             (f"({p0[0]}, {p0[1]})", theme.HK_ICE),
             ("  контроль ", theme.HK_ICE_SOFT),
             (f"({control[0]:.0f}, {control[1]:.0f})", theme.HK_ICE),
             ("  конец ", theme.HK_ICE_SOFT),
             (f"({end[0]:.0f}, {end[1]:.0f})", theme.HK_ICE),
             ("  тянем к ", theme.HK_ICE_SOFT),
             (f"({pull[0]:.0f}, {pull[1]:.0f})", theme.HK_ICE),
             ("  запас ", theme.HK_ICE_SOFT),
             (clear_txt, colour)],
            level="plain")
        if threats:
            self._log.add_log_segments(
                [("Угроз в створе — ", theme.TEXT_SECONDARY),
                 (str(len(threats)), theme.ACCENT_AMBER)],
                level="plain")
        if not safe:
            self._log.add_log_segments(
                [("Безопасной дуги не нашлось — ", theme.ACCENT_RED),
                 ("бьём по наименее опасной", theme.TEXT_SECONDARY)],
                level="plain")

    # ── Shot execution ───────────────────────────────────────────────────────

    def _execute_shot(self, hwnd: int, p0: tuple, pull: tuple):
        """The drag itself — a short, local pull from the shooter's own
        spot to `pull` (see _pull_point), held briefly, then released.
        Not a trace of the puck's predicted flight path — see the module
        docstring for why that was the wrong gesture."""
        points = [
            (round(p0[0] + (pull[0] - p0[0]) * i / _DRAG_STEPS),
             round(p0[1] + (pull[1] - p0[1]) * i / _DRAG_STEPS))
            for i in range(1, _DRAG_STEPS + 1)
        ]
        self._dragging = True
        mouse_down_at(hwnd, p0[0], p0[1])
        self._drag_step(hwnd, points, 0)

    def _drag_step(self, hwnd: int, points: list, i: int):
        # Stopped mid-drag still has to let go of the button wherever it
        # is — abandoning it here would leave the game thinking the mouse
        # is held down forever.
        if i >= len(points) or not self._running:
            x, y = points[min(i, len(points) - 1)] if points else (0, 0)
            mouse_up_at(hwnd, x, y)
            self._dragging = False
            return
        x, y = points[i]
        mouse_move_to(hwnd, x, y)
        if i + 1 >= len(points):
            # Last point reached — a short hold before letting go, not an
            # instant release on the tail of the last move.
            QTimer.singleShot(_HOLD_MS,
                              lambda: self._drag_step(hwnd, points, i + 1))
        else:
            QTimer.singleShot(_DRAG_STEP_MS,
                              lambda: self._drag_step(hwnd, points, i + 1))

    # ── Rectangle calibration ────────────────────────────────────────────────

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
             ("x", theme.HK_ICE_SOFT), (f" {box.left()}", theme.HK_ICE),
             ("  y", theme.HK_ICE_SOFT), (f" {box.top()}", theme.HK_ICE),
             ("  w", theme.HK_ICE_SOFT), (f" {box.width()}", theme.HK_ICE),
             ("  h", theme.HK_ICE_SOFT), (f" {box.height()}", theme.HK_ICE),
             ("  центр (", theme.TEXT_SECONDARY),
             (f"{cx}, {cy}", theme.HK_ICE),
             (")", theme.TEXT_SECONDARY)],
            level="plain")

    # ── Settings ─────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = HockeySettingsPanel(self)
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
        """Nothing of ours should outlive the window — the scan loop,
        tracking boxes and calibration box included."""
        self._running = False
        if self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
        self._tracking.clear()
        self._calib.clear()
        self._panel.stop_background()
