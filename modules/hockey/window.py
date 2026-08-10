# modules/hockey/window.py
"""Хоккей — the module window: the calibration and detection tools the bot
is being built on top of.

What this window does *not* do yet is shoot on its own. The game allows one
attempt per level, so a shot fired on an unverified model does not cost a
miss, it costs the level — which makes "не уверен — не стреляй" a hard rule
rather than a preference, and makes the order of work run detection → motion
model → trajectory → planner, each step verifiable by eye before the next
leans on it. See docs/superpowers/specs/2026-08-08-hockey-bot-design.md.

So the start button runs the detector and nothing else: it tracks defenders
live and draws boxes on them, which is both useful on its own and exactly
the observation phase the motion model will need.

Calibration writes straight into config.json. One control says *what* the
drawn box means, another says *which row* — and that row number is also the
row the overlay shows, since picking a row, looking at it and correcting it
are the same job.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QRect, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

import cv2

from app.core.capture import grab_window
from app.core.input_sender import mouse_down_at, mouse_move_to, mouse_up_at
from app.core.template_match import best_match, load_template
from app.ui import theme
from app.ui.calibration_overlay import CalibrationOverlay
from app.ui.module_window import ModuleWindow
from app.ui.tracking_overlay import TrackingOverlay
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.segmented_control import SegmentedControl
from app.ui.widgets.vw_panel import VIDEO_SUFFIXES, VwPanel
from app.ui.zones_overlay import ZonesOverlay
from modules.hockey import debug_frame, trajectory
from modules.hockey.detect import NO_LANE, HelmetDetector
from modules.hockey.level_strip import LevelStrip
from modules.hockey.motion import _MIN_BOUNCES as _MIN_BOUNCES_SHOWN
from modules.hockey.motion import MotionModel
from modules.hockey.planner import _MIN_CLEARANCE_PX as _MIN_CLEARANCE_SHOWN
from modules.hockey.planner import _MIN_WINDOW_S as _MIN_WINDOW_SHOWN
from modules.hockey.planner import best_effort as best_effort_shot
from modules.hockey.planner import best_effort_each
from modules.hockey.planner import plan as plan_shot
from modules.hockey.planner import why_not as why_not_shot
from modules.hockey.rink_area import from_config
from modules.hockey.settings_panel import HockeySettingsPanel

_BACKDROP_STEM  = "snowboard_sinthwawe"
_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_TEMPLATES      = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

_DEFAULT_W = 420
_DEFAULT_H = 620
_MIN_W     = 340
_MIN_H     = 520

_LOG_H = 150

_START_TEXT = "▶  Запустить слежение за вратарями"
_STOP_TEXT  = "■  Остановить слежение"

_CALIB_START_TEXT = "📐  Отметить область"
_CALIB_STOP_TEXT  = "📐  Записать область"

_FIELD_TEXT = "🥅  Показать поле"
_ROW_TEXT   = "👁  Показать ряд"
_TEST_TEXT   = "🔬  Тест детекции"

# The progress strip above the rink: nine cells, each of which fills with a
# tick or a cross once its level has been played. Nine is exactly enough to
# tell ten levels apart — none filled is level 1, all nine filled is level
# 10. Marked one at a time, the button naming whichever comes next.
_LEVEL_CELLS = 9
_LEVEL_TOTAL = 10
_LEVEL_ICONS = {"nice": "hockey_nice.png", "bad": "hockey_bad.png"}
_LEVEL_MATCH_MIN = 0.55     # a cell scoring under this is still empty
_LEVEL_EVERY_S = 1.0        # how often the counter re-reads the strip

# Readings in a row that must agree before the level is taken as changed. A
# level lasts a minute; a cell failing to match lasts a frame.
_LEVEL_CONFIRM = 3

# Shown until the strip has been marked and read. The convention for a value
# that has no reading yet, the same one stats.py uses.
_LEVEL_UNKNOWN = "Уровень %уровень%/10"

# The box a level cell starts at before it has ever been marked.
_LEVEL_BOX = 40

# Slack around each marked cell when it is read. The icons are 43x43 and
# 41x39 while the cells were marked 40x40, and a template larger than the
# patch it is searched in scores zero — so every cell read as empty and the
# counter sat on level 1 for ever (2026-08-10). Padding costs nothing: the
# cells are far enough apart that a few pixels cannot reach the next one.
_LEVEL_PAD = 8

_LEVEL_CHECK_TEXT = "🏅  Проверить уровни"
_REPORT_TEXT = "📊  Отчёт по модели"
# One button per aim rather than one that cycles: a calibration shot costs
# a level, so which one is about to be fired should never be a matter of
# remembering how many times the button has been pressed.
_SHOT_BUTTONS = [("🎯  Центр", 0.0),
                 ("🎯  Лево", -0.5),
                 ("🎯  Право", 0.5)]
_AIM_NAMES = {0.0: "центр ворот", -0.5: "левая штанга", 0.5: "правая штанга"}
# The same three, short enough to put all of them on one line.
_AIM_SHORT = {-0.5: "лево", 0.0: "центр", 0.5: "право"}


def _aim_name(aim: float) -> str:
    """What to call a pull. The three measured ones have names; the ones in
    between have only their number, and the number is what matters — it is
    the thing that was chosen and the thing to check a miss against."""
    named = _AIM_NAMES.get(round(aim, 3))
    if named:
        return named
    side = "влево" if aim < 0 else "вправо"
    return f"натяжение {abs(aim):.2f} {side}"

_FIRE_TEXT  = "💥  Сделать бросок"
# Same preconditions, thresholds ignored. Its own button rather than a
# fallback inside the first one: "no clean window" is the whole safety story
# here, and something that quietly fired anyway would undo it.
_FORCE_TEXT = "⚠  Сделать принудительный бросок"

# How long the pull itself takes, and therefore the soonest a release can be
# planned for. Aiming and timing are separate here because the game lets the
# button be held indefinitely: the pull is made first and simply waited on,
# so the release is a single mouse-up at an exact moment rather than the
# tail of a gesture that has to be started early and hoped over.
_PULL_LEAD_S = 0.35

# The last stretch before release is counted down in small steps against the
# real clock. Qt's timers resolve to about 15ms on Windows, which is 3px of
# a defender's travel — cheap to remove, and this is the one moment in the
# whole run where being late cannot be taken back.
_RELEASE_FINE_MS   = 3
_RELEASE_COARSE_S  = 0.05

# What the pull itself takes from the moment a plan exists — _DRAG_STEPS at
# _DRAG_STEP_MS plus the press, rounded up. A plan whose moment is nearer
# than this cannot be acted on at all.
_GESTURE_S = 0.20

# Slack added when a plan has to be drawn up again because the first one
# aimed at a moment that had already passed, and how many attempts are worth
# making before calling it off.
_PLAN_MARGIN_S = 0.10
_PLAN_TRIES    = 3

# And the soonest a shot may be planned for. Well past what the gesture
# needs: a third of a second leaves nothing between the plan and the release
# for anything to go slightly slower than it did while the plan was drawn
# up, and waiting costs nothing here — the game does not hurry a shot, and
# the next window along is as good as this one.
_MIN_DELAY_S = 1.00

# How long a level may be spent calibrating before what has been measured
# so far has to do. The level ends whether or not a shot is taken, so a row
# still arguing with itself has cost the attempt as surely as a miss.
_FORCE_AFTER_S = 60.0

# How long planning is expected to take, before it has been timed. Only the
# opening guess: the real cost of the last attempt is what the next one
# budgets with.
_PLAN_COST_S = 0.45

# How long a shot takes to reach the goal. Everything the motion model is
# judged on is judged over exactly this stretch, because it is the only
# stretch that matters: a prediction is right if the defender really is
# where it was said to be at the moment the puck gets there.
#
# 1.5s is the user's own rough timing (2026-08-04) and stands in until the
# measured trajectory table replaces it — that is step 4 of the plan, and it
# will give a per-row arrival time rather than one number for the whole
# flight.
_PUCK_TRAVEL_S = 1.5

# Where the model says every defender will be when a shot would arrive —
# the only thing drawn over the game while a watch runs. Magenta on
# purpose: none of the five row colours is anywhere near it, and the ice
# blue it used to be was indistinguishable from row 1's own strip.
_PREDICT_FILL   = QColor(255, 0, 208, 40)
_PREDICT_BORDER = QColor(255, 0, 208, 230)

# And a defender who stands still, drawn black instead. Not a prediction at
# all — he is measured, and he will be in that spot whenever the puck gets
# there — so it would be misleading to paint him the colour that means "this
# is where the model thinks somebody will have got to".
_STAND_FILL   = QColor(0, 0, 0, 225)
_STAND_BORDER = QColor(0, 0, 0, 255)

# The stretch of ice a crowded row is being timed in — see
# MotionModel.choose_zones. Orange because nothing else on the rink is, and
# because it is a working aid rather than part of the plan.
_ZONE_COLOUR = QColor(255, 150, 30)

# What the box drawn by "Отметить область" describes. "Борт" is singular on
# purpose: each row's defender turns round in its own place (2026-08-09), so
# boards belong to the row picked below, not to the rink.
# "Вратарь" is the defender's whole outline: detection finds a helmet, but
# a shot is blocked by the body under it, and the two are different
# rectangles (2026-08-09).
(_TARGET_RINK, _TARGET_GOAL, _TARGET_PUCK,
 _TARGET_ROW, _TARGET_WALLS, _TARGET_BODY) = range(6)
_TARGETS = ["Каток", "Ворота", "Шайба", "Ряд", "Борт", "Вратарь"]

# Four rows for most of the game, five on the last level (2026-08-09).
_MAX_ROWS = 5

# Overlay colours, so a glance says which row is being shown.
_ROW_COLOURS = ["#67d3f5", "#4bdb96", "#f5b544", "#c9a6ff", "#ff8ca6"]

# Where the calibration box first appears — centred on the game window.
_CALIB_DEFAULT_W = 300
_CALIB_DEFAULT_H = 200

# Detection cadence. The old template-matching detector could not come close
# to keeping up with its own 70ms timer (six full-rink matchTemplate passes,
# ~250ms of work), which is half of why tracking never settled; one HSV pass
# over the rink costs a few milliseconds, so the limit is now how smoothly
# the boxes should move rather than how fast the scan can go.
_SCAN_MS = 30

# Consecutive failed captures before tracking gives up and says so. A single
# failure is routine (grab_window's own GDI calls fail transiently under
# load), but swallowing them forever would leave the boxes frozen on stale
# positions with nothing in the log to say why.
_MAX_SCAN_FAILURES = 20

# How long "Тест детекции" records for. Long enough that a patrol crosses a
# stander and a helmet passes behind another — the moments detection is
# meant to survive and a single snapshot cannot show. Five seconds also
# covers most of a patrol's own period, so a row is judged over a whole
# sweep rather than over whichever part of one the button caught.
_BURST_S = 5.0

# And how long a watch counts heads before it tries to calibrate anything.
# Long enough to tell a stander from a patrol — that takes _MIN_STILL_FRAMES
# of watching, which at the ~13 frames a second this rink actually manages
# is about three seconds.
_CENSUS_S = 5.0

# The flight is watched as fast as the machine manages rather than on a
# fixed beat. At 30ms the loop was measured taking 61ms a frame — the timer
# was never the limit, the capture and the encode were — so a whole flight
# came to 22 frames and every crossing time was interpolated across a 60ms
# gap (2026-08-10). Asking for 10ms simply stops the timer adding to it.
_FLIGHT_SCAN_MS = 10

# How often a watch reports its own accuracy. Watching a ghost and waiting
# for the real box to walk into it cannot answer "and was that exactly 1.5
# seconds?" by eye (2026-08-09) — but the model already measures precisely
# that, so the number goes in front of you while the watch runs instead of
# sitting behind a button.
_STATUS_EVERY_S = 5.0

# ── The shot gesture ─────────────────────────────────────────────────────
# Still only driven by hand, through "Тестовый бросок". It is the part of
# the first version that live testing actually confirmed — pull left curves
# left, pull right curves right (2026-08-04) — and it is how the measured
# trajectory table will get filled, so it stays until it moves to shot.py
# with the rest of the shooting side.
#
# The aim scale is the player's own, not an invented one: 0 is the middle
# of the goal and ±0.5 are its two edges, which is where a hand player aims
# (2026-08-09: "по опыту игры без бота я всегда наводился именно в края").
# So ±0.5 has to be the 110px pull that was measured to land at the posts,
# which puts ±1 at 220 — a shot that goes wide and is never planned. Aim is
# clamped to _MAX_AIM everywhere for that reason.
_PULL_VERTICAL_PX   = 90    # constant upward pull — "cocks" the shot
_PULL_HORIZONTAL_PX = 220   # sideways pull at aim = ±1
_MAX_AIM      = 0.5         # the posts; past this the shot misses the goal
_DRAG_STEPS   = 5
_DRAG_STEP_MS = 30
_HOLD_MS      = 90


def _row_y(entry) -> int:
    """A row entry's own y, however mangled the file it came from — sorting
    must never be the thing that throws an exception."""
    try:
        return int(entry["y"])
    except (KeyError, TypeError, ValueError):
        return 0


def _default_backdrop(video: bool = True) -> str:
    """First existing templates/hockey_frost.* file — same rule Ava Dancers'
    and Snowboard's own backdrop pickers use."""
    moving = VIDEO_SUFFIXES + (".gif",)
    order  = moving + _STILL_SUFFIXES if video else _STILL_SUFFIXES + moving
    for suffix in order:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


def _goalies(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "вратарь"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "вратаря"
    return "вратарей"


def _census_words(still: int, moving: int) -> list:
    """"Найдено 2 вратаря: один стоит, другой едет" and its neighbours."""
    total = still + moving
    if not total:
        return [("пусто", theme.TEXT_DIM)]
    head = [(f"найден{'' if total == 1 else 'о'} {total} {_goalies(total)}",
             theme.HK_ICE)]
    if total == 1:
        return head + [(", стоит" if still else ", едет",
                        theme.TEXT_SECONDARY)]
    if not moving:
        tail = "оба стоят" if total == 2 else f"все {total} стоят"
    elif not still:
        tail = "оба едут" if total == 2 else f"все {total} едут"
    elif total == 2:
        tail = "один стоит, другой едет"
    else:
        tail = (f"{still} {'стоит' if still == 1 else 'стоят'}, "
                f"{moving} {'едет' if moving == 1 else 'едут'}")
    return head + [(f": {tail}", theme.TEXT_SECONDARY)]


@dataclass
class _Burst:
    """One run of "Тест детекции": where the frames go, what is judging
    them, and how many defenders each row has given up so far."""
    directory: Path
    detector: HelmetDetector
    geom: object
    until: float
    frames: int = 0
    seen: dict = field(default_factory=dict)
    timer: QTimer | None = None


class HockeyWindow(ModuleWindow):
    """Header, frame and the usual module-window habits, plus the rink's own
    calibration and detection tools."""

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Хоккей", config, save_fn, parent_overlay)
        self._wm = window_manager
        self._running = False
        self._settings: HockeySettingsPanel | None = None
        self._calib   = CalibrationOverlay(window_manager, reference=self)
        # Only one live layer over the game: where each defender is
        # *predicted* to be when a shot would arrive. Boxes on their current
        # positions were drawn too and have been dropped — they linger
        # wherever they were last put while the defenders move on, which
        # reads as the detector having lost them (2026-08-09). How well
        # detection is doing is answered by "Тест детекции" and its picture;
        # how well the model is doing, by the accuracy line in the log.
        self._predicted = TrackingOverlay(window_manager, reference=self,
                                          fill=_PREDICT_FILL,
                                          border=_PREDICT_BORDER)
        self._standing = TrackingOverlay(window_manager, reference=self,
                                         fill=_STAND_FILL,
                                         border=_STAND_BORDER)
        self._zones   = ZonesOverlay(window_manager, reference=self)

        self._detector: HelmetDetector | None = None
        self._motion: MotionModel | None = None
        self._status_at = 0.0
        self._announced_ready = False
        self._scan_timer: QTimer | None = None
        self._burst: _Burst | None = None
        self._counted_at: float | None = None
        self._counted = False
        self._level_slot = 0
        self._level_shown: int | None = None
        self._level_seen: int | None = None
        self._level_held = 0
        self._zone_boxes: dict[int, QRect] = {}
        self._plan_cost = _PLAN_COST_S
        self._settled_rows: set[int] = set()
        self._fired_this_level = False
        self._forced = False
        self._forcing = False
        self._started_at: float | None = None
        self._scan_failures = 0
        self._field_shown = False
        self._row_shown   = False
        self._dragging    = False
        # A measured flight — non-None only between the mouse coming up and
        # the watching window running out.
        self._flight: trajectory.PuckFlight | None = None
        self._flight_timer: QTimer | None = None
        self._flight_started = 0.0

        w = getattr(config, "width",  _DEFAULT_W)
        h = getattr(config, "height", _DEFAULT_H)
        self.resize(max(w, _MIN_W), max(h, _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI construction ──────────────────────────────────────────────────

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

        # From here to the shot buttons: built and wired exactly as before,
        # then hidden. The rink is calibrated and the bot plays a level
        # clean, so the tools that got it there are now only in the way —
        # but they are the tools that would do it again on a rink that
        # misbehaves, and deleting them would mean writing them twice.
        # Hidden widgets take no space in a layout, and one call brings any
        # of them back.
        layout.addSpacing(4)
        layout.addWidget(self._shelve(self._section_label("ЧТО ОТМЕЧАЕМ")))

        self._target = SegmentedControl(_TARGETS, accent=theme.HK_ICE_SOFT)
        layout.addWidget(self._shelve(self._target))

        # One control, two jobs: which row "Ряд" and "Борт" write to, and
        # which row the overlay draws. Picking a row, looking at it and
        # correcting it are the same piece of work, and two separate row
        # pickers only invited them to disagree.
        layout.addWidget(self._shelve(self._section_label("КАКОЙ РЯД")))
        self._row_slot = SegmentedControl(
            [str(i + 1) for i in range(_MAX_ROWS)], accent=theme.HK_ICE_SOFT)
        self._row_slot.changed.connect(self._on_row_changed)
        layout.addWidget(self._shelve(self._row_slot))

        self._calib_btn = NtButton(_CALIB_START_TEXT,
                                   accent=theme.HK_ICE_SOFT, upper=False)
        self._calib_btn.clicked.connect(self._toggle_calib)
        layout.addWidget(self._shelve(self._calib_btn))

        layout.addSpacing(4)
        layout.addWidget(self._section_label("ПРОВЕРКА"))

        self._field_btn = NtButton(_FIELD_TEXT, accent=theme.HK_ICE_SOFT,
                                   upper=False)
        self._field_btn.clicked.connect(self._toggle_field)
        self._row_btn = NtButton(_ROW_TEXT, accent=theme.HK_ICE_SOFT,
                                 upper=False)
        self._row_btn.clicked.connect(self._toggle_row_view)
        layout.addLayout(self._button_row(self._shelve(self._field_btn),
                                          self._shelve(self._row_btn)))

        detect_btn = NtButton(_TEST_TEXT, accent=theme.HK_ICE_SOFT,
                              upper=False)
        detect_btn.clicked.connect(self._test_detect)
        report_btn = NtButton(_REPORT_TEXT, accent=theme.HK_ICE_SOFT,
                              upper=False)
        report_btn.clicked.connect(self._model_report)
        layout.addLayout(self._button_row(self._shelve(detect_btn),
                                          self._shelve(report_btn)))

        layout.addWidget(self._shelve(self._section_label("БРОСОК С ЗАМЕРОМ")))
        shots = QHBoxLayout()
        shots.setSpacing(4)
        for label, aim in _SHOT_BUTTONS:
            button = NtButton(label, accent=theme.HK_RINK_RED, upper=False)
            button.clicked.connect(
                lambda _checked=False, a=aim: self._measured_shot(a))
            shots.addWidget(self._shelve(button))
        layout.addLayout(shots)

        self._fire_btn = NtButton(_FIRE_TEXT, accent=theme.ACCENT_RED,
                                  upper=False)
        self._fire_btn.clicked.connect(self._fire)
        layout.addWidget(self._fire_btn)

        # Hidden: "Сделать бросок" no longer refuses, so there is nothing
        # left for a second button to override. The code stays — see _force.
        self._force_btn = NtButton(_FORCE_TEXT, accent=theme.ACCENT_AMBER,
                                   upper=False)
        self._force_btn.clicked.connect(self._force)
        layout.addWidget(self._shelve(self._force_btn))

        # Marked once and hidden, like the rest of the calibration tools.
        self._level_btn = NtButton(self._level_btn_text(),
                                   accent=theme.HK_ICE_SOFT, upper=False)
        self._level_btn.clicked.connect(self._toggle_level_calib)
        layout.addWidget(self._shelve(self._level_btn))

        check_btn = NtButton(_LEVEL_CHECK_TEXT, accent=theme.HK_ICE_SOFT,
                             upper=False)
        check_btn.clicked.connect(self._check_levels)
        layout.addWidget(self._shelve(check_btn))

        self._levels = LevelStrip()
        layout.addWidget(self._levels)

        # The strip is watched whether or not a watch is running: a level
        # ends when the puck lands, not when tracking is switched off, and
        # the cell that fills is the only thing on screen that says so.
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._refresh_level)
        self._level_timer.start(int(_LEVEL_EVERY_S * 1000))

        layout.addLayout(self._build_log(), stretch=1)

        guide_btn = NtButton("Гайд", upper=False,
                             accent=theme.HK_ICE_SOFT, filled=True)
        guide_btn.setMinimumHeight(30)
        layout.addWidget(guide_btn)

        # Backdrop: templates/hockey_frost.* by default, video first — set up
        # here, still before the window has ever been shown, since a video
        # decodes asynchronously and the earlier it starts the more likely a
        # real frame is waiting when the switch-on animation asks for one.
        self._panel.background_failed.connect(
            lambda msg: self._log.add_log(msg, level="error"))
        self._apply_backdrop(fade=False)

        drag = NtDragHandle(dot_color=theme.HK_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    @staticmethod
    def _shelve(widget):
        """Built and wired, out of sight — see the comment in _build_body."""
        widget.hide()
        return widget

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        return label

    def _button_row(self, left: NtButton, right: NtButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(left)
        row.addWidget(right)
        return row

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

    # ── Shared plumbing ──────────────────────────────────────────────────

    def _geometry(self):
        return from_config(self.config)

    def _grab(self, geom):
        """One rink-sized frame, or None with the reason already logged."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return None
        try:
            return grab_window(hwnd, geom.region)
        except Exception as exc:
            self._log.add_log(f"Не удалось снять каток: {exc}", level="error")
            return None

    def _report_problems(self, geom) -> bool:
        """Log whatever is wrong with the calibration. True when it is
        usable — a caller that needs rows checks that itself."""
        for problem in geom.problems():
            self._log.add_log_segments(
                [("Калибровка — ", theme.ACCENT_AMBER),
                 (problem, theme.TEXT_SECONDARY)], level="plain")
        return geom.width > 0 and geom.height > 0

    # ── Live tracking ────────────────────────────────────────────────────

    def _toggle_running(self):
        if self._running:
            self._stop_running()
            self._log.add_log("Слежение остановлено", level="plain")
            return

        geom = self._geometry()
        if not self._report_problems(geom):
            return
        if not geom.lanes:
            self._log.add_log(
                "Нечего отслеживать — сначала отметьте ряды: цель «Ряд», "
                "номер ряда, «Отметить область»", level="error")
            return
        if self._grab(geom) is None:
            return

        # The model always measures itself over a real shot's flight, even
        # while the boxes are being drawn for *now*: at a zero horizon the
        # accuracy check compares a prediction against the instant it was
        # made for and is true by construction. Keeping the two apart means
        # the checking mode shows an honest error figure as well as a box
        # that should ride its defender.
        self._detector = HelmetDetector(geom, self.config)
        self._motion = MotionModel(geom, _PUCK_TRAVEL_S, orange=self._orange())
        self._scan_failures = 0
        self._status_at = 0.0
        self._announced_ready = False
        # Measured from the first frame that actually arrives, not from the
        # button press: the window has to find the game and grab it first.
        self._counted_at = None
        self._counted = False
        self._fired_this_level = False
        self._forced = False
        self._forcing = False
        # From the first frame that arrives, not from the button press —
        # the same reason as _counted_at.
        self._started_at = None
        self._running = True
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._status_dot.set_running()
        self._settled_rows = set()
        self._announce_level()
        self._chatter(
            [("Слежение запущено — ", theme.TEXT_SECONDARY),
             (f"{len(geom.lanes)} ряд(ов), такт {_SCAN_MS} мс", theme.HK_ICE),
             (f".  Сначала {_CENSUS_S:.0f} с считаю состав — сколько вратарей "
              f"в каждом ряду и кто из них едет.", theme.TEXT_SECONDARY)])
        if self._orange():
            self._chatter(
                [("Режим полосы у борта — ", theme.ACCENT_AMBER),
                 ("каждому ряду достаётся полоса у борта, и скорость каждого "
                  "вратаря берётся из времени между его разворотами в ней. "
                  "Слежение по трекам в предсказании не участвует; проверка "
                  "на 1.5 с прежняя.", theme.TEXT_SECONDARY)])
        if self._checking():
            self._chatter(
                [("Режим проверки — ", theme.ACCENT_AMBER),
                 ("малиновая рамка рисуется там, где вратарь ", theme.TEXT_SECONDARY),
                 ("прямо сейчас", theme.HK_ICE),
                 (".  Она обязана ехать ровно по нему: отстаёт или "
                  "обгоняет — видно сразу. Точность в логе при этом "
                  "по-прежнему меряется на 1.5 с. Бросок запрещён.",
                  theme.TEXT_SECONDARY)])
        else:
            self._chatter(
                [("Малиновые рамки — ", theme.TEXT_SECONDARY),
                 (f"где модель ждёт вратаря целиком через "
                  f"{_PUCK_TRAVEL_S:.1f} с", theme.HK_ICE),
                 (f".  Каждые {_STATUS_EVERY_S:.0f} с в лог будет падать, на "
                  f"сколько пикселей предсказание промахнулось — на глаз это "
                  f"не проверить, а модель меряет ровно это.",
                  theme.TEXT_SECONDARY)])

        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(lambda: self._scan_tick(geom))
        self._scan_timer.start(_SCAN_MS)

    def _stop_running(self):
        self._running = False
        if self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
        self._detector = None
        # The model itself is kept: "Отчёт по модели" is most wanted right
        # after a watch has been stopped, not only during one.
        self._predicted.clear()
        self._standing.clear()
        self._zones.clear()
        self._zone_boxes.clear()
        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_stopped()

    def _scan_tick(self, geom):
        if not self._running or self._detector is None:
            return
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._stop_running()
            self._log.add_log("Окно игры пропало — слежение остановлено",
                              level="error")
            return
        try:
            frame = grab_window(hwnd, geom.region)
        except Exception as exc:
            self._scan_failures += 1
            if self._scan_failures >= _MAX_SCAN_FAILURES:
                self._stop_running()
                self._log.add_log(f"Каток не снимается: {exc}", level="error")
            return   # a transient capture failure costs one tick, not the run
        self._scan_failures = 0
        now = time.monotonic()
        found = self._detector.scan(frame, self._motion.expectations(now))
        self._motion.feed(found, now)
        moving, fixed = self._predicted_boxes(geom, now)
        if self._hidden():
            # Still computed, just not painted: the boxes are a way of
            # showing the plan, not part of making it.
            self._predicted.clear()
            self._standing.clear()
            self._zones.clear()
        else:
            self._predicted.update_targets(moving)
            self._standing.update_targets(fixed)

        if self._counted:
            for index, zone in self._motion.choose_zones().items():
                self._announce_zone(geom, index, zone)


        if not self._counted:
            # Head count first. Nothing is calibrated and nothing is
            # reported until the watch knows what it is looking for, so that
            # "row 3 has two, one of them moving" is a fact it can hold on
            # to rather than something it rediscovers every five seconds.
            if self._counted_at is None:
                self._counted_at = now + _CENSUS_S
            if now >= self._counted_at:
                self._counted = True
                if self._verbose():
                    self._log_census()
            return

        if self._started_at is None:
            self._started_at = now
        if (not self._forced and not self._motion.ready()
                and now - self._started_at >= _FORCE_AFTER_S):
            self._settle_for_now()

        if now - self._status_at >= _STATUS_EVERY_S:
            self._status_at = now
            if self._verbose():
                self._log_watch_status()

    def _announce_zone(self, geom, index: int, zone: tuple):
        """Say which stretch of ice a crowded row is being timed in, and
        draw it, so the choice can be argued with rather than guessed at."""
        low, high, toward_right = zone
        lane = {l.index: l for l in geom.lanes}.get(index)
        if lane is None:
            return
        top, bottom = geom.lane_rows(lane)
        self._zone_boxes[index] = QRect(int(low), top + geom.top,
                                        int(high - low), bottom - top)
        if not self._hidden():
            self._zones.show_zones([(box, _ZONE_COLOUR)
                                    for box in self._zone_boxes.values()])
        self._chatter(
            [(f"Ряд {index + 1} — ", theme.HK_ICE_SOFT),
             ("не даётся обычным слежением. ", theme.ACCENT_AMBER),
             (f"Считаю развороты у {'правого' if toward_right else 'левого'}"
              f" борта в полосе {low:.0f}…{high:.0f}: промежуток между двумя "
              f"из них и есть период, кто бы ни мешал смотреть.",
              theme.TEXT_SECONDARY)])

    def _announce_level(self):
        """The level, once, at the top of its own run. In light mode this is
        the heading everything below belongs to."""
        if not self._light():
            return
        self._rule()
        level = self._levels.level
        self._log.add_log_segments(
            [(f"Уровень {level}/{_LEVEL_TOTAL}" if level else "Новый уровень",
              theme.HK_ICE)], level="plain")
        self._rule()

    def _announce_settled(self):
        """One line per row as it comes good, in the order they manage it.

        The number a row is at while it is still working is only interesting
        to somebody debugging the model; that it is *done* is what a run is
        waiting for.
        """
        if not self._light() or self._motion is None:
            return
        for report in self._motion.reports():
            if report.empty or report.index in self._settled_rows:
                continue
            if not report.ready:
                continue
            self._settled_rows.add(report.index)
            self._log.add_log_segments(
                [(f"Ряд {report.index + 1} ", theme.HK_ICE_SOFT),
                 ("откалиброван", theme.ACCENT_GREEN)], level="plain")

    def _log_census(self):
        """Who is in each row, before anything is calibrated.

        The same reading the detection test gives, taken over five seconds
        so that standing and moving can be told apart at all, and said out
        loud once — it is the thing a run is about to spend its time
        proving, and it belongs in front of you before that starts.
        """
        for index, still, moving in self._motion.census():
            self._log.add_log_segments(
                [(f"Ряд {index + 1} — ", theme.HK_ICE_SOFT)]
                + _census_words(still, moving), level="plain")
        self._rule()
        if self._light():
            self._log.add_log_segments(
                [("Идёт калибровка рядов", theme.TEXT_SECONDARY)],
                level="plain")
            return
        self._log.add_log_segments(
            [("Состав посчитан — ", theme.ACCENT_GREEN),
             ("начинаю калибровку скорости. Стоящие обведены чёрным и "
              "больше не пересчитываются; для каждого едущего ищу его "
              "период, пока ошибка предсказания не станет зелёной. "
              "Как ряд позеленел — он таким и остаётся, скорость взята.",
              theme.TEXT_SECONDARY)], level="plain")

    def _log_watch_status(self):
        """How far each row's prediction has been missing by, in pixels,
        measured over exactly the flight a shot takes.

        This is the answer to "did the red box land on the blue one, and was
        it really 1.5 seconds later" — a question the eye cannot settle and
        the model settles by construction: every prediction is filed for the
        moment it comes true and compared against what happened then.

        It stops once every row has latched. The line exists to show what is
        still being worked out; repeating a row of ticks every five seconds
        says nothing that the last one did not.
        """
        if self._announced_ready:
            return
        self._announce_settled()
        reports = [r for r in self._motion.reports() if not r.empty]
        if not reports:
            return

        segments = [("Точность — ", theme.TEXT_SECONDARY)]
        for i, report in enumerate(reports):
            if i:
                segments.append(("  ·  ", theme.TEXT_DIM))
            segments.append((f"ряд {report.index + 1} ", theme.HK_ICE_SOFT))
            if report.ready and report.error is not None:
                # Latched. Nothing measured afterwards can take it back, so
                # nothing measured afterwards is worth printing either.
                segments.append((f"{report.error:.1f} px ✓",
                                 theme.ACCENT_GREEN))
            elif report.ready:
                segments.append(("готов ✓", theme.ACCENT_GREEN))
            elif not report.settled:
                # Nobody has been classified yet, so "стоит" would be a
                # claim rather than a reading.
                segments.append(("присматриваюсь", theme.TEXT_DIM))
            elif self._orange():
                # Only the zone is answering, so only the zone is worth
                # reporting: what it has timed, and whether it holds.
                if not report.has_mover and report.ready:
                    segments.append(("стоит", theme.TEXT_SECONDARY))
                elif not report.zone_patrols:
                    segments.append((f"жду разворотов у борта "
                                     f"({report.bounces} из "
                                     f"{_MIN_BOUNCES_SHOWN})", theme.TEXT_DIM))
                else:
                    kinds = " · ".join(
                        f"{period:.2f} с / {speed:.0f} px/с"
                        for period, speed in report.zone_patrols)
                    if report.error is None:
                        segments.append((kinds, theme.TEXT_DIM))
                    else:
                        segments.append((f"{report.error:.1f} px",
                                         theme.ACCENT_AMBER))
                        segments.append((f"  ({kinds})", theme.TEXT_DIM))
            elif report.tracked != report.occupants and report.bounces:
                # Being timed by its turns at the board instead. The period
                # alone says nothing about whether the prediction lands, so
                # the error goes first once there is one — that is the
                # question, and the period is the working.
                if report.bounce_period and report.error is not None:
                    segments.append((f"{report.error:.1f} px",
                                     theme.ACCENT_AMBER))
                    segments.append(
                        (f" по отскокам, период {report.bounce_period:.2f} с",
                         theme.TEXT_DIM))
                elif report.bounce_period:
                    segments.append(
                        (f"период {report.bounce_period:.2f} с — жду проверок",
                         theme.TEXT_DIM))
                else:
                    segments.append((f"жду разворотов у борта "
                                     f"({report.bounces} из 3)",
                                     theme.TEXT_DIM))
            elif report.tracked != report.occupants:
                segments.append((f"вижу {report.occupants}, просчитываю "
                                 f"{report.tracked} — не стреляю",
                                 theme.ACCENT_RED))
                # What it is looking at, since the number alone cannot tell
                # a defender the tracker keeps dropping from something else
                # being read as a helmet.
                if report.loose:
                    count, left, right = report.loose
                    segments.append((f" ({count} отметок "
                                     f"{left:.0f}…{right:.0f})",
                                     theme.TEXT_DIM))
            elif not report.has_mover:
                segments.append(("стоит", theme.TEXT_SECONDARY))
            elif not report.fitted:
                # Waiting longer will not help: the patrol does not repeat,
                # so there is nothing to fit and no prediction to check.
                segments.append((f"патруль не читается "
                                 f"({max(0.0, report.confidence) * 100:.0f}%)",
                                 theme.ACCENT_RED))
            elif report.error is None:
                segments.append(("присматриваюсь", theme.TEXT_DIM))
            else:
                segments.append((f"{report.error:.1f} px", theme.ACCENT_AMBER))
        self._chatter(segments)

        if self._motion.ready() and not self._announced_ready:
            self._announced_ready = True
            self._rule()
            if self._light():
                self._log.add_log_segments(
                    [("Все ряды откалиброваны", theme.ACCENT_GREEN)],
                    level="plain")
            else:
                self._log.add_log_segments(
                    [("Все ряды откалиброваны — ", theme.ACCENT_GREEN),
                     ("скорость каждого вратаря найдена и проверена, "
                      "броски разрешены. Дальше эти скорости уже не "
                      "пересчитываются: они свойство уровня, а не съёмки.",
                      theme.TEXT_SECONDARY)], level="plain")
            if self._auto_shot():
                self._take_the_shot()
            elif self._light():
                self._log_plan()

    def _settle_for_now(self):
        """Stop waiting for the rows that will not settle, and take what
        they have.

        A level ends whether or not a shot is taken, so a row still arguing
        with itself after a minute has cost the attempt exactly as surely as
        a miss would. The bar comes down — an unconfident period is used, a
        zone that timed one of two patrols is used for the one, a track with
        neither is carried on its last speed — and the row says so.
        """
        self._forced = True
        rows = self._motion.settle_for_now()
        if not rows:
            return
        names = ", ".join(str(index + 1) for index in rows)
        self._log.add_log_segments(
            [(f"Минута вышла — ", theme.ACCENT_AMBER),
             (f"ряды {names} так и не сошлись. Беру по ним то, что уже "
              f"измерено: скорость приблизительная, но лучше неё ничего не "
              f"будет, а уровень кончится в любом случае.",
              theme.TEXT_SECONDARY)], level="plain")

    def _take_the_shot(self):
        """Fire without waiting to be asked.

        Exactly the shot the button would take — same plan, same thresholds,
        same refusals. Once per level: the game allows one attempt, and the
        watch is torn down and rebuilt when the next one starts, which is
        what arms this again.
        """
        if self._fired_this_level:
            return
        self._fired_this_level = True
        self._log.add_log_segments(
            [("Автоудар — ", theme.ACCENT_AMBER),
             ("бью в ближайшее окно; чистого нет — бью в самое просторное",
              theme.TEXT_SECONDARY)], level="plain")
        self._forcing = True
        try:
            self._fire()
        finally:
            self._forcing = False

    def _checking(self) -> bool:
        """Read every time it is needed rather than latched at start, so
        the switch takes effect the moment it is flipped — a setting that
        needs a restart to show anything is a setting that looks broken."""
        return bool(getattr(self.config, "predict_now", False))

    def _fine_aim(self) -> bool:
        """Whether pulls between the measured three are on the table. Read
        live, like the other switches."""
        return bool(getattr(self.config, "fine_aim", False))

    def _verbose(self) -> bool:
        """Whether the watch narrates itself. Read live, for the same reason
        as _checking. Shots and errors are never suppressed: those are
        events, not commentary."""
        return bool(getattr(self.config, "verbose_log", True))

    def _light(self) -> bool:
        """Whether the narration is cut back to what is happening — the
        level, who is in each row, each row as it is calibrated, the shot."""
        return self._verbose() and bool(getattr(self.config, "light_log",
                                                False))

    def _hidden(self) -> bool:
        return bool(getattr(self.config, "hide_overlays", False))

    def _auto_shot(self) -> bool:
        """Whether the bot presses its own button once the model is ready."""
        return bool(getattr(self.config, "auto_shot", False))

    def _orange(self) -> bool:
        """Whether every row is measured by its board zone alone. Read at
        the start of a watch rather than live: switching method halfway
        through would leave half the rows judged one way and half the
        other."""
        return bool(getattr(self.config, "orange_mode", False))

    def _chatter(self, segments):
        """A line of running commentary: how far along a row is, why an aim
        was refused, what the model is chewing on. Dropped in light mode,
        where the point is to see what happened rather than what is being
        thought about."""
        if self._verbose() and not self._light():
            self._log.add_log_segments(segments, level="plain")

    def _rule(self):
        """A divider, in light mode only — it is there to separate the few
        lines that survive, and among many it would be noise."""
        if self._light():
            self._log.add_log_segments([("─" * 38, theme.TEXT_DIM)],
                                       level="plain")

    def _predicted_boxes(self, geom, now: float
                         ) -> tuple[list[QRect], list[QRect]]:
        """Where every defender will be when a shot fired now would reach
        it — the standers included, since a shot has to clear them too.
        Drawn as the whole defender rather than the helmet, because that is
        the shape a puck has to miss.

        Two lists, because the two are not the same claim: a patrol's box is
        a prediction and might be wrong, a stander's box is where he has
        been all along.

        In checking mode the same boxes are drawn for *now* instead, which
        is the only way to see the model leading or lagging its defender.
        """
        ahead = now + (0.0 if self._checking() else self._motion.horizon)
        moving, fixed = [], []
        for lane in geom.lanes:
            split = self._motion.split_positions(lane.index, ahead)
            if split is None:
                continue   # no honest answer for this row — draw nothing
            standing, patrolling = split
            for x in patrolling:
                moving.append(QRect(*geom.body_box(lane, x, lane.y)))
            for x in standing:
                fixed.append(QRect(*geom.body_box(lane, x, lane.y)))
        return moving, fixed

    # ── Model report ─────────────────────────────────────────────────────

    def _model_report(self):
        """What the motion model has worked out per row, and whether it may
        be trusted. The readiness verdict is the gate on shooting at all:
        the game gives one attempt per level, so a model that has not proved
        itself against reality is worse than not shooting."""
        if self._motion is None:
            self._log.add_log(
                "Модель пуста — запустите слежение и дайте ей посмотреть "
                "секунд 20", level="plain")
            return

        lanes = {lane.index: lane for lane in self._geometry().lanes}
        reports = self._motion.reports()
        for report in reports:
            self._log_model_row(report, lanes.get(report.index))

        self._log_plan()

        not_ready = [r.index + 1 for r in reports if not r.ready]
        if not not_ready:
            self._log.add_log_segments(
                [("Модель готова — ", theme.ACCENT_GREEN),
                 (f"все {len(reports)} ряд(ов) предсказываются на "
                  f"{self._motion.horizon:.1f} с вперёд", theme.TEXT_SECONDARY)],
                level="plain")
        else:
            names = ", ".join(str(n) for n in not_ready)
            self._log.add_log_segments(
                [("Модель не готова — ", theme.ACCENT_AMBER),
                 (f"ряды {names}", theme.ACCENT_RED),
                 (".  Дайте посмотреть дольше; если не сходится — проверьте "
                  "детекцию, ряд может теряться.", theme.TEXT_SECONDARY)],
                level="plain")

    def _log_plan(self):
        """What the bot would shoot right now — printed, not fired.

        Nothing pulls the trigger yet on purpose: the game gives one attempt
        per level, so the plan has to be watched being right for a while
        before it is allowed to be acted on.
        """
        geom = self._geometry()
        measured = trajectory.load()
        aims = sorted({round(track.aim, 2) for track in measured})
        timings = {aim: trajectory.averaged(geom, measured, aim)
                   for aim in aims}
        timings = {aim: rows for aim, rows in timings.items() if rows}
        for aim, row in trajectory.mirror_fill(geom, timings):
            self._chatter(
                [(f"Ряд {row + 1} на прицеле {aim:+.2f} ", theme.TEXT_DIM),
                 ("взят зеркально с противоположного", theme.ACCENT_AMBER),
                 (" — его так и не удалось промерить напрямую",
                  theme.TEXT_SECONDARY)])
        if not timings:
            self._log.add_log_segments(
                [("Плана нет — ", theme.ACCENT_AMBER),
                 ("ни один прицел не промерен, нажмите «Центр», «Лево», "
                  "«Право»", theme.TEXT_SECONDARY)], level="plain")
            return

        if self._fine_aim():
            timings = trajectory.spread_aims(timings)
        found = plan_shot(geom, self._motion, timings, time.monotonic())
        if found is None:
            # "No window" on its own is unactionable. The nearest line there
            # is, and what it would clip, says whether this is a rink full
            # of defenders or an outline marked a few pixels too wide.
            nearest = best_effort_shot(geom, self._motion, timings,
                                       time.monotonic())
            if nearest is None:
                self._log.add_log_segments(
                    [("Чистого окна нет — ", theme.ACCENT_RED),
                     ("ни один прицел не удалось просчитать целиком",
                      theme.TEXT_SECONDARY)], level="plain")
            else:
                short = max(0.0, -nearest.clearance)
                self._log.add_log_segments(
                    [("Чистого окна нет — ", theme.ACCENT_AMBER),
                     ("ближайший вариант ", theme.TEXT_SECONDARY),
                     (_aim_name(nearest.aim), theme.HK_ICE),
                     ((f", не хватает {short:.0f} px" if short > 0
                       else ", но окно слишком узкое"),
                      theme.ACCENT_RED)], level="plain")
            if self._verbose() and not self._light():
                self._log_why_not(geom, timings)
            return

        self._log.add_log_segments(
            [("Удар был бы через ", theme.TEXT_SECONDARY),
             (f"{found.release_at - time.monotonic():.2f} с", theme.HK_ICE),
             (", окно ", theme.TEXT_SECONDARY),
             (f"{found.window * 1000:.0f} мс", theme.HK_ICE),
             (", в ", theme.TEXT_SECONDARY),
             (_aim_name(found.aim), theme.HK_ICE),
             ("   запас ", theme.HK_ICE_SOFT),
             (f"{found.clearance:.0f} px", theme.HK_ICE)], level="plain")

    def _log_why_not(self, geom, timings: dict):
        """Name the row and the shortfall for every aim.

        A refusal that says only "no window" cannot be argued with, and
        therefore cannot be acted on — a defender genuinely blocking every
        line looks exactly like an outline marked wider than the defender
        really is.
        """
        for verdict in why_not_shot(geom, self._motion, timings,
                                    time.monotonic(), lead=_PULL_LEAD_S):
            name = _aim_name(verdict.aim)
            if verdict.best_clearance is None:
                self._log.add_log_segments(
                    [(f"  {name} — ", theme.HK_ICE_SOFT),
                     (f"ряд {(verdict.tightest_row or 0) + 1} нечем проверить",
                      theme.ACCENT_AMBER)], level="plain")
                continue
            row = (verdict.tightest_row or 0) + 1
            if verdict.best_clearance < _MIN_CLEARANCE_SHOWN:
                short = _MIN_CLEARANCE_SHOWN - verdict.best_clearance
                self._log.add_log_segments(
                    [(f"  {name} — ", theme.HK_ICE_SOFT),
                     (f"теснее всего на ряду {row}", theme.HK_ICE),
                     (f", задевает вратаря на {short:.0f} px",
                      theme.ACCENT_RED)], level="plain")
            else:
                # Room enough, but never for long enough to aim at.
                self._log.add_log_segments(
                    [(f"  {name} — ", theme.HK_ICE_SOFT),
                     (f"запас есть ({verdict.best_clearance:.0f} px)",
                      theme.ACCENT_GREEN),
                     (f", но окно всего {verdict.best_window * 1000:.0f} мс "
                      f"— нужно {_MIN_WINDOW_SHOWN * 1000:.0f}",
                      theme.ACCENT_RED)], level="plain")
        self._log.add_log_segments(
            [("    Задевает — ", theme.TEXT_SECONDARY),
             ("габарит вратаря, возможно, отмечен шире туловища: запас "
              "теперь ровно тот, что в разметке «Вратарь», без надбавок",
              theme.ACCENT_AMBER),
             (".  Узкое окно — вратарь проходит мимо слишком быстро, тут "
              "остаётся только ждать другого расклада.",
              theme.TEXT_SECONDARY)], level="plain")

    def _log_model_row(self, report, lane=None):
        colour = _ROW_COLOURS[report.index % len(_ROW_COLOURS)]
        verdict, verdict_colour = (("готов", theme.ACCENT_GREEN) if report.ready
                                   else ("не готов", theme.ACCENT_RED))
        head = [(f"Ряд {report.index + 1} — ", colour)]

        if report.empty:
            # One line and no accuracy figures: "ошибка — по 0 проверкам,
            # виден в 0% кадров, готов" reads like a row that was checked
            # and passed, when it is a row nobody was ever seen in.
            self._log.add_log_segments(
                head + [("никого не видно — считаю его свободным",
                         theme.TEXT_DIM)], level="plain")
            return

        head += [(f"вратарей {report.occupants}", theme.HK_ICE)]
        if report.standing:
            spots = ", ".join(f"{x:.0f}" for x in report.standing)
            head += [(f"  стоят на {spots}", theme.TEXT_SECONDARY)]
        if not report.has_mover:
            head += [("  все стоят", theme.TEXT_SECONDARY)]
        elif report.period:
            head += [(f"  период {report.period:.2f} с", theme.HK_ICE),
                     (f" (совпадение {report.confidence * 100:.0f}%)",
                      theme.TEXT_SECONDARY)]
        else:
            head += [("  период не найден", theme.ACCENT_AMBER)]

        if report.min_x is not None:
            head += [("  размах ", theme.TEXT_SECONDARY),
                     (f"{report.min_x:.0f}…{report.max_x:.0f}", theme.HK_ICE)]
        if report.speed:
            head += [(f"  скорость {report.speed:.0f} px/с",
                      theme.TEXT_SECONDARY)]
        self._log.add_log_segments(head, level="plain")

        if not report.has_mover:
            accuracy = [("    предсказывать нечего — никто не движется",
                         theme.TEXT_SECONDARY)]
        else:
            error = ("%.1f px" % report.error
                     if report.error is not None else "—")
            worst = ("%.1f px" % report.worst
                     if report.worst is not None else "—")
            accuracy = [
                ("    ошибка предсказания ", theme.TEXT_SECONDARY),
                (error, verdict_colour),
                (f"  (худшая {worst})", theme.TEXT_SECONDARY),
                (f"  по {report.checks} проверкам", theme.TEXT_SECONDARY),
                # What a plan has to leave clear on this row because of it.
                (f"  →  запас {report.slack:.0f} px", theme.HK_ICE_SOFT)]
            if (report.live_error is not None and report.error is not None
                    and report.live_error > report.error + 1.0):
                accuracy.append(
                    (f"  —  лучшее при этой посадке, сейчас "
                     f"{report.live_error:.1f} px", theme.TEXT_DIM))
        self._log.add_log_segments(
            accuracy + [("   виден в ", theme.TEXT_SECONDARY),
                        (f"{report.seen_share * 100:.0f}% кадров",
                         theme.HK_ICE_SOFT),
                        ("   ", theme.TEXT_DIM), (verdict, verdict_colour)],
            level="plain")

        # A strip drawn round the whole body sits tens of pixels below the
        # helmets it is meant to hold, which shows up as a row that keeps
        # losing its defender rather than as anything obviously wrong.
        if lane is not None and report.mean_y is not None:
            drift = report.mean_y - lane.y
            if abs(drift) > 15:
                self._log.add_log_segments(
                    [("    полоса смещена — ", theme.ACCENT_AMBER),
                     (f"шлемы в среднем на y {report.mean_y:.0f}",
                      theme.HK_ICE),
                     (f", а ряд отмечен на y {lane.y} ({drift:+.0f} px). "
                      f"Отметьте ряд по шлему, а не по всему вратарю.",
                      theme.TEXT_SECONDARY)], level="plain")

    # ── One-shot detection test ──────────────────────────────────────────

    def _test_detect(self):
        """Three seconds of scans, every frame saved into a folder of its
        own, with a box round each defender found.

        A single frame answers "did it find them"; a run of frames answers
        the question that actually comes up, which is whether it finds the
        same defender *every* time. A row seen in two frames out of three
        looks perfect in any one snapshot and starves the tracker.

        The boxes go onto the frame that was judged rather than over the
        game, so the picture and the judgement are the same instant by
        construction — a box drawn live sits where it was put while the
        defender moves on, which reads as a detector that has lost somebody.
        """
        if self._burst is not None:
            return                      # already running, let it finish
        geom = self._geometry()
        if self._grab(geom) is None:
            return

        try:
            directory = debug_frame.burst_dir()
        except Exception as exc:
            self._log.add_log(f"Папка для снимков не создалась: {exc}",
                              level="error")
            return

        self._burst = _Burst(directory=directory,
                             detector=HelmetDetector(geom, self.config),
                             geom=geom,
                             until=time.monotonic() + _BURST_S)
        self._log.add_log_segments(
            [("Тест детекции — ", theme.HK_ICE_SOFT),
             (f"снимаю {_BURST_S:.0f} с в ", theme.TEXT_SECONDARY),
             (str(directory), theme.HK_ICE)], level="plain")

        timer = QTimer(self)
        timer.timeout.connect(self._burst_tick)
        timer.start(_SCAN_MS)
        self._burst.timer = timer
        self._burst_tick()

    def _burst_tick(self):
        burst = self._burst
        if burst is None:
            return
        frame = self._grab(burst.geom)
        if frame is None:
            self._finish_burst()
            return

        first = not burst.frames        # the first frame reads like before
        if burst.geom.lanes:
            found, _candidates = burst.detector.scan_debug(frame)
            hits = [hit for row in found.values() for hit in row]
            for index, row in found.items():
                burst.seen[index] = burst.seen.get(index, 0) + len(row)
            if first:
                for index in sorted(found):
                    self._log_row_result(index, found[index])
        else:
            hits = burst.detector.scan_all(frame)
            burst.seen[NO_LANE] = burst.seen.get(NO_LANE, 0) + len(hits)
            if first:
                self._log.add_log_segments(
                    [("Рядов нет — ", theme.ACCENT_AMBER),
                     (f"по всему катку найдено шлемов: {len(hits)}",
                      theme.HK_ICE)], level="plain")
        try:
            debug_frame.save_numbered(frame, burst.geom, hits,
                                      burst.directory, burst.frames)
        except Exception as exc:
            self._log.add_log(f"Снимок не сохранился: {exc}", level="error")
            self._finish_burst()
            return

        burst.frames += 1
        if time.monotonic() >= burst.until:
            self._finish_burst()

    def _finish_burst(self):
        """The tally is the answer: how often each row gave up a defender
        over the run, which is the number a single snapshot cannot show."""
        burst, self._burst = self._burst, None
        if burst is None:
            return
        if burst.timer is not None:
            burst.timer.stop()

        segments = [("Снято ", theme.TEXT_SECONDARY),
                    (f"{burst.frames} кадров", theme.HK_ICE),
                    (" → ", theme.TEXT_SECONDARY),
                    (str(burst.directory), theme.HK_ICE_SOFT)]
        self._log.add_log_segments(segments, level="plain")
        if not burst.frames or not burst.geom.lanes:
            return

        tally = [("Вратарей на кадр — ", theme.TEXT_SECONDARY)]
        for position, index in enumerate(sorted(burst.seen)):
            if position:
                tally.append(("  ·  ", theme.TEXT_DIM))
            per_frame = burst.seen[index] / burst.frames
            # Amber is the number worth catching: a row found in most frames
            # but not all is a row that looks perfect in any one snapshot
            # and starves the tracker.
            colour = (theme.TEXT_DIM if per_frame < 0.05
                      else theme.ACCENT_GREEN if per_frame >= 0.95
                      else theme.ACCENT_AMBER)
            tally.append((f"ряд {index + 1} {per_frame:.2f}", colour))
        self._log.add_log_segments(tally, level="plain")

    def _log_row_result(self, index: int, hits: list):
        if not hits:
            self._log.add_log_segments(
                [(f"Ряд {index + 1} — ", theme.HK_ICE_SOFT),
                 ("пусто", theme.TEXT_DIM)], level="plain")
            return
        for hit in hits:
            self._log.add_log_segments(
                [(f"Ряд {index + 1} — ", theme.HK_ICE_SOFT),
                 (f"x {hit.x}", theme.HK_ICE),
                 (f"  y {hit.y}", theme.TEXT_SECONDARY),
                 (f"  площадь {hit.area}", theme.TEXT_SECONDARY),
                 ("  фото ", theme.TEXT_SECONDARY),
                 (f"{hit.match * 100:.0f}%", theme.ACCENT_GREEN)],
                level="plain")

    # ── Rows ─────────────────────────────────────────────────────────────

    def _sort_rows(self):
        """Rows stay in top-to-bottom order in the file, so slot N and
        "ряд N" are the same row everywhere — the geometry sorts them
        anyway, and a config in a different order than the overlay labels
        would be its own small trap."""
        self.config.lanes.sort(key=_row_y)

    def _write_row(self, slot: int, y: int, height: int) -> int:
        """Put a hand-marked row into `slot`, keeping whatever boards that
        slot already had, and hand back the number it ends up with once rows
        are back in top-to-bottom order. A slot past the end appends."""
        entry = {"y": int(y), "height": int(height),
                 "wall_left": 0, "wall_right": 0}
        lanes = self.config.lanes
        if slot < len(lanes):
            existing = lanes[slot]
            if isinstance(existing, dict):
                entry["wall_left"]  = int(existing.get("wall_left")  or 0)
                entry["wall_right"] = int(existing.get("wall_right") or 0)
            lanes[slot] = entry
        else:
            lanes.append(entry)
        self._sort_rows()
        return lanes.index(entry) + 1

    def _write_row_walls(self, slot: int, left: int, right: int) -> bool:
        """This row's own turning points. False when the slot is still
        empty — boards belong to a row, so there is nothing to attach them
        to yet."""
        lanes = self.config.lanes
        if not 0 <= slot < len(lanes) or not isinstance(lanes[slot], dict):
            return False
        lanes[slot]["wall_left"]  = int(left)
        lanes[slot]["wall_right"] = int(right)
        return True

    def _write_row_body(self, slot: int, box: QRect) -> int | None:
        """This row's whole-defender outline, stored relative to the row's
        own line so it can be hung off a helmet wherever one is found.
        Returns that offset, or None when the slot is still empty."""
        lanes = self.config.lanes
        if not 0 <= slot < len(lanes) or not isinstance(lanes[slot], dict):
            return None
        offset = box.top() - _row_y(lanes[slot])
        lanes[slot]["body_w"]  = int(box.width())
        lanes[slot]["body_h"]  = int(box.height())
        lanes[slot]["body_dy"] = int(offset)
        return int(offset)

    def _on_row_changed(self, _index: int):
        if self._row_shown:
            self._log_row()
        self._refresh_zones()

    def _log_row(self):
        """The selected row's own numbers. Strips overlapping is expected
        and no longer worth a warning — the defenders overlap too, and the
        detector settles which row a helmet belongs to rather than letting
        two rows claim it."""
        index = self._row_slot.active()
        lanes = self._geometry().lanes
        if index >= len(lanes):
            self._log.add_log_segments(
                [(f"Ряда {index + 1} нет — ", theme.ACCENT_AMBER),
                 (f"размечено рядов: {len(lanes)}", theme.TEXT_SECONDARY)],
                level="plain")
            return
        lane = lanes[index]
        self._log.add_log_segments(
            [(f"Ряд {index + 1} — ", _ROW_COLOURS[index % len(_ROW_COLOURS)]),
             (f"y {lane.y}", theme.HK_ICE),
             (f"  высота {lane.height}", theme.TEXT_SECONDARY),
             (f"  полоса {lane.top}…{lane.bottom}", theme.TEXT_SECONDARY),
             ("  борта ", theme.HK_ICE_SOFT),
             (f"{lane.wall_left}…{lane.wall_right}", theme.HK_ICE),
             (f"  (ширина {lane.span})", theme.TEXT_SECONDARY)],
            level="plain")
        geom = self._geometry()
        if lane.has_body:
            body = (f"{lane.body_w}×{lane.body_h}, "
                    f"от шлема {lane.body_dy:+d} px")
            colour = theme.HK_ICE
        else:
            body = (f"не отмечен, считаю {2 * geom.goalie_half_w} px в ширину")
            colour = theme.ACCENT_AMBER
        left, right = geom.centre_bounds(lane)
        self._log.add_log_segments(
            [("    габарит вратаря ", theme.HK_ICE_SOFT), (body, colour),
             ("   центр ездит ", theme.TEXT_SECONDARY),
             (f"{left:.0f}…{right:.0f}", theme.HK_ICE)], level="plain")

    # ── Geometry overlays ────────────────────────────────────────────────

    def _toggle_field(self):
        self._field_shown = not self._field_shown
        self._field_btn.set_active(self._field_shown)
        if self._field_shown:
            self._log_field_legend()
        self._refresh_zones()

    def _toggle_row_view(self):
        self._row_shown = not self._row_shown
        self._row_btn.set_active(self._row_shown)
        if self._row_shown:
            self._log_row()
        self._refresh_zones()

    def _refresh_zones(self):
        """One overlay holds both layers, so either can be switched without
        wiping the other."""
        geom = self._geometry()
        zones: list[tuple[QRect, QColor]] = []
        if self._field_shown:
            zones += self._field_zones(geom)
        if self._row_shown:
            zones += self._row_zones(geom)
        if zones:
            self._zones.show_zones(zones)
        else:
            self._zones.clear()

    def _field_zones(self, geom) -> list[tuple[QRect, QColor]]:
        """The rink itself and the two things a shot is aimed between. No
        boards here: they belong to a row, not to the rink."""
        return [
            (QRect(geom.left, geom.top, geom.width, geom.height),
             QColor(theme.HK_ICE)),
            (QRect(geom.goal_left, geom.goal_y - 3,
                   max(1, geom.goal_right - geom.goal_left), 6),
             QColor(theme.HK_RINK_RED)),
            (QRect(geom.shooter_x - 8, geom.shooter_y - 8, 16, 16),
             QColor(theme.ACCENT_GREEN)),
        ]

    def _row_zones(self, geom) -> list[tuple[QRect, QColor]]:
        """The selected row's strip plus its own two turning points, in that
        row's colour. One row at a time on purpose: strips are taller than
        the gaps between rows, so all of them at once is a stack of
        overlapping rectangles nobody can read."""
        index = self._row_slot.active()
        if index >= len(geom.lanes):
            return []
        lane = geom.lanes[index]
        box = geom.lane_region(lane)
        if box["height"] <= 0:
            return []
        colour = QColor(_ROW_COLOURS[index % len(_ROW_COLOURS)])
        zones = [(QRect(box["left"], box["top"],
                        box["width"], box["height"]), colour)]
        for wall_x in (lane.wall_left, lane.wall_right):
            zones.append((QRect(wall_x - 2, box["top"], 4, box["height"]),
                          colour))
        # The defender's own outline, drawn against each board so its width
        # can be compared with the patrol it has to fit inside — the boards
        # are marked at its edges, so these two should just touch.
        for centre in geom.centre_bounds(lane):
            zones.append((QRect(*geom.body_box(lane, centre, lane.y)),
                          QColor(theme.ACCENT_GREEN)))
        return zones

    def _log_field_legend(self):
        geom = self._geometry()
        self._log.add_log_segments(
            [("Поле — ", theme.TEXT_SECONDARY),
             ("каток", theme.HK_ICE), ("  ·  ", theme.TEXT_DIM),
             ("ворота", theme.HK_RINK_RED), ("  ·  ", theme.TEXT_DIM),
             ("шайба", theme.ACCENT_GREEN),
             ("   борта — у каждого ряда свои, см. «Показать ряд»",
              theme.TEXT_SECONDARY)], level="plain")
        self._log.add_log_segments(
            [("  каток ", theme.HK_ICE_SOFT),
             (f"{geom.width}×{geom.height} @ ({geom.left}, {geom.top})",
              theme.HK_ICE),
             ("   ворота ", theme.HK_ICE_SOFT),
             (f"y {geom.goal_y}, {geom.goal_left}…{geom.goal_right}",
              theme.HK_ICE),
             ("   шайба ", theme.HK_ICE_SOFT),
             (f"({geom.shooter_x}, {geom.shooter_y})", theme.HK_ICE)],
            level="plain")

    # ── Rectangle calibration ────────────────────────────────────────────

    def _toggle_calib(self):
        """First press: a box appears over the game — drag its middle to
        move it, an edge or corner to resize. Second press: whatever the
        controls above are set to gets those bounds, written to config.json
        straight away."""
        if self._calib.isVisible():
            self._apply_calibration(self._calib.bounds())
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
        self._log.add_log_segments(
            [("Отмечаем — ", theme.TEXT_SECONDARY),
             (self._target_name(), theme.HK_ICE),
             (".  Тяните за середину, чтобы подвинуть, за край — чтобы "
              "изменить размер, потом нажмите ещё раз.",
              theme.TEXT_SECONDARY)], level="plain")

    # ── The level counter ────────────────────────────────────────────────

    def _level_btn_text(self) -> str:
        return f"🏅  Отметить ячейку {self._level_slot + 1}/{_LEVEL_CELLS}"

    def _toggle_level_calib(self):
        """Mark one cell of the progress strip, then move on to the next.

        Nine presses, nine cells, left to right. Same box as every other
        calibration — drag the middle to move it, an edge to resize — and
        the bounds land in config.json the moment the second press comes.
        """
        if self._calib.isVisible():
            box = self._calib.bounds()
            self._calib.clear()
            self._write_level_cell(self._level_slot, box)
            self._level_slot = (self._level_slot + 1) % _LEVEL_CELLS
            self._level_btn.setText(self._level_btn_text())
            self._level_btn.set_active(False)
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

        ox, oy, w, _h = rect
        stored = self._level_cell(self._level_slot)
        if stored is not None:
            box = QRect(stored["left"], stored["top"],
                        stored["width"], stored["height"])
        else:
            # A small box near the top of the game window, which is where
            # the strip lives — closer to the answer than the middle.
            box = QRect(ox + w // 2 - _LEVEL_BOX // 2, oy + 40,
                        _LEVEL_BOX, _LEVEL_BOX)
        self._calib.show_at(box)
        self._level_btn.setText(_CALIB_STOP_TEXT)
        self._level_btn.set_active(True)
        self._log.add_log_segments(
            [("Отмечаем — ", theme.TEXT_SECONDARY),
             (f"ячейка уровня {self._level_slot + 1}", theme.HK_ICE),
             (".  Тяните за середину, чтобы подвинуть, за край — чтобы "
              "изменить размер, потом нажмите ещё раз.",
              theme.TEXT_SECONDARY)], level="plain")

    def _check_levels(self):
        """Read the strip once and say, level by level, what it holds.

        The counter shows a single number and cannot say why it is that
        number. This says it out loud for all ten, with the match scores
        beside each, so a cell that reads wrong can be told from a cell that
        reads right.
        """
        cells = [self._level_cell(i) for i in range(_LEVEL_CELLS)]
        if not any(cells):
            self._log.add_log("Полоса уровней не размечена", level="error")
            return
        detail = self._cell_detail(cells)
        if detail is None:
            self._log.add_log("Полоса уровней не снимается — игра на месте?",
                              level="error")
            return

        states = [state for state, _scores in detail]
        level = next((i + 1 for i, state in enumerate(states)
                      if state is None), _LEVEL_TOTAL)
        for number in range(1, _LEVEL_TOTAL + 1):
            if number == level:
                word, colour = "Текущий", theme.ACCENT_AMBER
            elif number > _LEVEL_CELLS:
                word, colour = "—", theme.TEXT_DIM
            elif states[number - 1] == "nice":
                word, colour = "Пройден", theme.ACCENT_GREEN
            elif states[number - 1] == "bad":
                word, colour = "Не пройден", theme.ACCENT_RED
            else:
                word, colour = "ещё не сыгран", theme.TEXT_DIM
            line = [(f"Уровень {number} — ", theme.HK_ICE_SOFT),
                    (word, colour)]
            if number <= _LEVEL_CELLS:
                scores = detail[number - 1][1]
                line.append((f"   ✓ {scores.get('nice', 0) * 100:.0f}%"
                             f"   ✗ {scores.get('bad', 0) * 100:.0f}%"
                             f"   (порог {_LEVEL_MATCH_MIN * 100:.0f}%)",
                             theme.TEXT_DIM))
            self._log.add_log_segments(line, level="plain")

    def _cell_detail(self, cells: list) -> list | None:
        """(state, scores) per cell — what _read_cells decides, plus the
        numbers it decided on."""
        marked = [cell for cell in cells if cell]
        hwnd = self._wm.get_game_hwnd()
        if not hwnd or not marked:
            return None
        left = min(cell["left"] for cell in marked) - _LEVEL_PAD
        top = min(cell["top"] for cell in marked) - _LEVEL_PAD
        right = max(cell["left"] + cell["width"] for cell in marked) + _LEVEL_PAD
        bottom = max(cell["top"] + cell["height"]
                     for cell in marked) + _LEVEL_PAD
        try:
            strip = grab_window(hwnd, {"left": left, "top": top,
                                       "width": right - left,
                                       "height": bottom - top})
        except Exception:
            return None
        if strip is None or strip.size == 0:
            return None

        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        detail = []
        for cell in cells:
            if cell is None:
                detail.append((None, {}))
                continue
            patch = self._crop(gray, cell, left, top)
            scores = self._cell_scores(patch)
            best = max(scores, key=scores.get, default=None)
            state = (best if best is not None
                     and scores[best] >= _LEVEL_MATCH_MIN else None)
            detail.append((state, scores))
        return detail

    def _level_cell(self, slot: int) -> dict | None:
        cells = getattr(self.config, "level_cells", None) or []
        if slot < len(cells) and cells[slot]:
            return cells[slot]
        return None

    def _write_level_cell(self, slot: int, box: QRect):
        cells = list(getattr(self.config, "level_cells", None) or [])
        while len(cells) < _LEVEL_CELLS:
            cells.append(None)
        cells[slot] = {"left": box.left(), "top": box.top(),
                       "width": box.width(), "height": box.height()}
        self.config.level_cells = cells
        self.save_fn()
        marked = sum(1 for cell in cells if cell)
        self._log.add_log_segments(
            [(f"Ячейка {slot + 1} — ", theme.ACCENT_GREEN),
             (f"{box.width()}×{box.height()} @ ({box.left()}, {box.top()})",
              theme.HK_ICE),
             (f"   отмечено {marked} из {_LEVEL_CELLS}",
              theme.TEXT_SECONDARY)], level="plain")

    def _refresh_level(self):
        """Read the strip and put the level, and how it has gone, on screen.

        One grab of the whole strip rather than nine of its cells: a window
        capture costs tens of milliseconds and the cells sit side by side.

        The level is the *first cell that is still empty*, not the count of
        the filled ones. Those agree while the strip fills left to right,
        and only the first empty cell is still true if it ever does not.
        """
        cells = [self._level_cell(i) for i in range(_LEVEL_CELLS)]
        if not any(cells):
            self._levels.set_reading([], None)
            return
        states = self._read_cells(cells)
        if states is None:
            return          # the game is gone or the grab failed; keep the
                            # last reading rather than blank it

        level = next((i + 1 for i, state in enumerate(states)
                      if state is None), _LEVEL_TOTAL)
        self._levels.set_reading(states, level)
        if not self._level_holds(level):
            return

        if level != self._level_shown:
            if self._level_shown is not None and level > self._level_shown:
                done = states[level - 2] if level >= 2 else None
                self._log.add_log_segments(
                    [(f"Уровень {level - 1} ", theme.HK_ICE_SOFT),
                     ("пройден" if done == "nice" else "провален",
                      theme.ACCENT_GREEN if done == "nice"
                      else theme.ACCENT_RED),
                     (f" — идёт {level}-й из {_LEVEL_TOTAL}",
                      theme.TEXT_SECONDARY)], level="plain")
                self._new_level()
            self._level_shown = level

    def _level_holds(self, level: int) -> bool:
        """Whether a reading has been the same long enough to act on.

        One cell failing to match for a single frame drops the level by one;
        it reads as a step *back*, so nothing is announced, and the next
        good frame reads as a step forward and restarts the whole watch on
        the level already being played (2026-08-11, 00:04:35). A level lasts
        a minute or more and a misread lasts a frame, so agreeing with
        itself three times running tells them apart with room to spare.
        """
        if level != self._level_seen:
            self._level_seen = level
            self._level_held = 1
            return False
        self._level_held += 1
        return self._level_held >= _LEVEL_CONFIRM

    def _new_level(self):
        """Stop and start the watch, exactly as a hand would.

        Speeds are drawn afresh for every level — a row that patrolled
        slowly last time can be the quickest thing on the ice this time — so
        nothing measured before is worth keeping: not the periods, not the
        head count, not where anybody stood. Rather than reset the pieces
        one by one and hope none was missed, the whole watch goes down and
        comes back up, which is what was being done by hand between levels
        anyway.
        """
        if not self._running:
            return
        self._stop_running()
        self._motion = None
        self._toggle_running()

    def _read_cells(self, cells: list) -> list | None:
        """"nice", "bad" or None for each marked cell; None for the lot when
        the strip could not be read at all."""
        marked = [cell for cell in cells if cell]
        hwnd = self._wm.get_game_hwnd()
        if not hwnd or not marked:
            return None

        left = min(cell["left"] for cell in marked) - _LEVEL_PAD
        top = min(cell["top"] for cell in marked) - _LEVEL_PAD
        right = max(cell["left"] + cell["width"] for cell in marked) + _LEVEL_PAD
        bottom = max(cell["top"] + cell["height"]
                     for cell in marked) + _LEVEL_PAD
        try:
            strip = grab_window(hwnd, {"left": left, "top": top,
                                       "width": right - left,
                                       "height": bottom - top})
        except Exception:
            return None     # a transient capture failure costs one reading
        if strip is None or strip.size == 0:
            return None

        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        return [None if cell is None else
                self._cell_state(self._crop(gray, cell, left, top))
                for cell in cells]

    @staticmethod
    def _crop(gray, cell: dict, left: int, top: int):
        """One cell out of the strip, with slack round it — see _LEVEL_PAD."""
        y = cell["top"] - top - _LEVEL_PAD
        x = cell["left"] - left - _LEVEL_PAD
        return gray[max(0, y):y + cell["height"] + 2 * _LEVEL_PAD,
                    max(0, x):x + cell["width"] + 2 * _LEVEL_PAD]

    @staticmethod
    def _cell_scores(patch) -> dict:
        """How much this cell looks like each icon, 0..1.

        The icon is shrunk to fit when the marked cell is smaller than it —
        a template bigger than what it is searched in scores zero, which is
        not "no match" but "could not look".
        """
        scores = {}
        for state, name in _LEVEL_ICONS.items():
            icon = load_template(name)
            if icon is None or patch.size == 0:
                scores[state] = 0.0
                continue
            ph, pw = patch.shape[:2]
            ih, iw = icon.shape[:2]
            if ih > ph or iw > pw:
                factor = min(ph / ih, pw / iw)
                icon = cv2.resize(icon, (max(4, int(iw * factor)),
                                         max(4, int(ih * factor))),
                                  interpolation=cv2.INTER_AREA)
            scores[state], _corner = best_match(patch, icon)
        return scores

    @classmethod
    def _cell_state(cls, patch) -> str | None:
        """Whether the level behind this cell was won, lost, or has not been
        played yet — "nice", "bad" or None."""
        scores = cls._cell_scores(patch)
        best = max(scores, key=scores.get, default=None)
        if best is None or scores[best] < _LEVEL_MATCH_MIN:
            return None
        return best

    def _target_name(self) -> str:
        target = self._target.active()
        name   = _TARGETS[target]
        if target in (_TARGET_ROW, _TARGET_WALLS):
            return f"{name} {self._row_slot.active() + 1}"
        return name

    def _apply_calibration(self, box: QRect):
        target = self._target.active()
        cfg = self.config

        if target == _TARGET_RINK:
            cfg.rink_left, cfg.rink_top = box.left(), box.top()
            cfg.rink_width, cfg.rink_height = box.width(), box.height()
            detail = (f"{box.width()}×{box.height()} @ "
                      f"({box.left()}, {box.top()})")
        elif target == _TARGET_GOAL:
            # The bottom edge is the line the puck has to cross, since the
            # goal sits at the top of the rink and the shot travels upward.
            cfg.goal_left, cfg.goal_right = box.left(), box.right()
            cfg.goal_y = box.bottom()
            detail = f"y {box.bottom()}, {box.left()}…{box.right()}"
        elif target == _TARGET_PUCK:
            centre = box.center()
            cfg.shooter_x, cfg.shooter_y = centre.x(), centre.y()
            detail = f"({centre.x()}, {centre.y()})"
        elif target == _TARGET_ROW:
            centre_y = box.center().y()
            slot = self._row_slot.active()
            number = self._write_row(slot, centre_y, box.height())
            detail = (f"слот {slot + 1} → ряд {number}:  y {centre_y}, "
                      f"высота {box.height()}, всего рядов {len(cfg.lanes)}")
        elif target == _TARGET_WALLS:
            slot = self._row_slot.active()
            if self._write_row_walls(slot, box.left(), box.right()):
                detail = (f"ряд {slot + 1}:  {box.left()}…{box.right()}  "
                          f"(ширина {box.right() - box.left()})")
            else:
                detail = (f"ряда {slot + 1} ещё нет — сначала отметьте сам "
                          f"ряд, выбрав цель «Ряд»")
        elif target == _TARGET_BODY:
            slot = self._row_slot.active()
            written = self._write_row_body(slot, box)
            if written is None:
                detail = (f"ряда {slot + 1} ещё нет — сначала отметьте сам "
                          f"ряд, выбрав цель «Ряд»")
            else:
                detail = (f"ряд {slot + 1}:  {box.width()}×{box.height()}, "
                          f"от шлема {written:+d} px вниз")
        else:
            return

        self.save_fn()
        self._log.add_log_segments(
            [("Записано — ", theme.TEXT_SECONDARY),
             (self._target_name(), theme.ACCENT_GREEN),
             ("  ", theme.TEXT_DIM), (detail, theme.HK_ICE)], level="plain")
        self._refresh_zones()

    # ── Test shot ────────────────────────────────────────────────────────

    def _measured_shot(self, aim: float):
        """A shot with a known aim, followed all the way to the goal.

        The point is not where it lands but *when* it reaches each row: the
        top row and the bottom row are crossed a long way apart in time, and
        each has to be dodged at its own moment. One number for the whole
        flight cannot answer that, so the flight is measured.
        """
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        if self._dragging or self._flight_timer is not None:
            self._log.add_log("Бросок ещё в полёте — подождите",
                              level="plain")
            return
        if self._running:
            self._log.add_log(
                "Сначала остановите слежение — оно занимает захват экрана",
                level="plain")
            return

        geom = self._geometry()
        start = geom.shooter
        pull = (start[0] + aim * _PULL_HORIZONTAL_PX,
                start[1] - _PULL_VERTICAL_PX)
        self._log.add_log_segments(
            [("Бросок — ", theme.ACCENT_RED),
             (_AIM_NAMES.get(aim, "между"), theme.HK_ICE),
             (f"  (прицел {aim:+.2f})", theme.TEXT_SECONDARY),
             ("  тянем к ", theme.HK_ICE_SOFT),
             (f"({pull[0]:.0f}, {pull[1]:.0f})", theme.HK_ICE)],
            level="plain")
        self._execute_shot(hwnd, start, pull,
                           on_release=lambda: self._watch_flight(hwnd, geom, aim))

    # ── The real shot ────────────────────────────────────────────────────

    def _plan_in_time(self, geom, timings, planner):
        """A plan for a moment that has not happened yet.

        Drawing one up takes a few hundred milliseconds, and the pull a
        couple of hundred more. A plan made for `now + lead` can therefore be
        for a moment already past by the time anything can act on it — the
        release then fires at once and lands half a second late, which at
        patrol speed is a hundred pixels of defender and a certain miss
        (2026-08-10, "удар будет через -0.31 с").

        So: plan for no sooner than _MIN_DELAY_S away, budgeting for what
        the last plan actually cost to draw up, and if the moment chosen has
        come too close anyway, do it again from further out. Two or three
        passes settle it; if none do, that is a refusal, not a reason to
        fire in a hurry. Waiting is free — the next window is as good.

        Hands back (plan, in_time). `(None, True)` is an honest "no window";
        `(None, False)` is "there may be one, but not one I can still hit".
        """
        # An automatic shot has a level running out on it, so it settles for
        # a moment it can merely still act on rather than a comfortable one.
        soonest = _GESTURE_S if self._forcing else _MIN_DELAY_S
        lead = self._plan_cost + _MIN_DELAY_S + _PLAN_MARGIN_S
        for _ in range(_PLAN_TRIES):
            started = time.monotonic()
            found = planner(geom, self._motion, timings, started, lead=lead)
            self._plan_cost = time.monotonic() - started
            if found is None:
                return None, True
            if found.release_at - time.monotonic() >= soonest:
                return found, True
            lead = self._plan_cost + _MIN_DELAY_S + _PLAN_MARGIN_S
        self._log.add_log_segments(
            [("Не успеваю — ", theme.ACCENT_RED),
             (f"расчёт идёт дольше, чем остаётся до выбранного момента "
              f"(упреждение дошло до {lead:.2f} с). Стрелять с опозданием "
              f"хуже, чем не стрелять.", theme.TEXT_SECONDARY)],
            level="plain")
        return None, False

    def _least_bad(self, geom, timings):
        """The line that misses by least, when none of them misses by none.

        The refusal used to be the whole safety story here and forcing a
        shot had its own button. It does not survive contact with the game:
        a level ends whether or not a shot is taken, so declining costs the
        attempt just the same, and eight pixels of overlap against a body
        outline drawn by hand is not a miss anybody can be sure of. So the
        shot goes out, and what it is short by goes in the log — all three
        lines, so the choice can be checked rather than trusted.
        """
        per_aim = {}

        def pick(geometry, motion, tables, now, lead):
            nonlocal per_aim
            per_aim = best_effort_each(geometry, motion, tables, now,
                                       lead=lead)
            return max(per_aim.values(), key=lambda plan: plan.clearance,
                       default=None)

        found, in_time = self._plan_in_time(geom, timings, pick)
        if found is None:
            if in_time:
                self._log.add_log_segments(
                    [("Не из чего выбирать — ", theme.ACCENT_RED),
                     ("ни один прицел не удалось просчитать целиком:",
                      theme.TEXT_SECONDARY)], level="plain")
                self._log_why_not(geom, timings)
            return None

        # The three measured lines always, and the interpolated ones only
        # when one of them is what got picked — twenty-one entries on a log
        # line is not something anybody reads.
        shown = sorted(set(_AIM_SHORT) & set(per_aim) | {found.aim},
                       reverse=True)
        shortfalls = []
        for aim in shown:
            short = max(0.0, -per_aim[aim].clearance)
            shortfalls.append(f"{_AIM_SHORT.get(round(aim, 3), f'{aim:+.2f}')}"
                              f" {short:.0f} px")
        self._chatter(
            [("Чистого окна нет — ", theme.ACCENT_AMBER),
             (f"не хватает пикселей ({' · '.join(shortfalls)}), выбираю ",
              theme.TEXT_SECONDARY),
             (_aim_name(found.aim), theme.HK_ICE)])
        return found

    def _fire(self):
        """Work out when every row can be cleared, say so once, and shoot."""
        setup = self._shot_setup()
        if setup is None:
            return
        hwnd, geom, timings = setup

        found, in_time = self._plan_in_time(geom, timings, plan_shot)
        if found is None and in_time:
            found = self._least_bad(geom, timings)
        if found is None:
            return              # already said why

        delay = found.release_at - time.monotonic()
        if self._light():
            self._log.add_log_segments(
                [("Делаем удар через ", theme.ACCENT_GREEN),
                 (f"{delay:.2f} с", theme.HK_ICE),
                 (" — ", theme.TEXT_SECONDARY),
                 (_aim_name(found.aim), theme.HK_ICE)], level="plain")
        else:
            self._log.add_log_segments(
                [("Удар будет через ", theme.ACCENT_GREEN),
                 (f"{delay:.2f} с", theme.HK_ICE),
                 ("  —  ", theme.TEXT_DIM),
                 (_aim_name(found.aim), theme.HK_ICE),
                 ("   запас ", theme.HK_ICE_SOFT),
                 (f"{found.clearance:.0f} px", theme.HK_ICE),
                 ("   окно ", theme.HK_ICE_SOFT),
                 (f"{found.window * 1000:.0f} мс", theme.HK_ICE)],
                level="plain")
        self._pull_and_hold(hwnd, geom, found)

    def _force(self):
        """Shoot the least-bad line there is, thresholds ignored.

        Never reached from _fire, and never as a fallback from it: the
        refusal is the safety story, and something that quietly fired anyway
        would undo it. This is for someone who has read the shortfall,
        judged it to be inside their own calibration error, and chosen to
        spend the attempt finding out.
        """
        setup = self._shot_setup()
        if setup is None:
            return
        hwnd, geom, timings = setup

        found, in_time = self._plan_in_time(geom, timings, best_effort_shot)
        if found is None:
            if not in_time:
                return          # already said why
            self._log.add_log_segments(
                [("Не из чего выбирать — ", theme.ACCENT_RED),
                 ("ни один прицел не удалось просчитать целиком:",
                  theme.TEXT_SECONDARY)], level="plain")
            self._log_why_not(geom, timings)
            return

        delay = found.release_at - time.monotonic()
        short = _MIN_CLEARANCE_SHOWN - found.clearance
        self._log.add_log_segments(
            [("Принудительный удар через ", theme.ACCENT_AMBER),
             (f"{delay:.2f} с", theme.HK_ICE),
             ("  —  ", theme.TEXT_DIM),
             (_aim_name(found.aim),
              theme.HK_ICE),
             ("   лучший запас ", theme.HK_ICE_SOFT),
             (f"{found.clearance:.0f} px", theme.HK_ICE),
             ((f", задел бы вратаря на {short:.0f} px" if short > 0
               else ", проходит чисто"),
              theme.ACCENT_RED if short > 0 else theme.ACCENT_GREEN)],
            level="plain")
        self._pull_and_hold(hwnd, geom, found)

    def _shot_setup(self):
        """(hwnd, geometry, measured timings) once everything a shot needs
        is in place — or None, with the reason already in the log.

        Shared by both shot buttons on purpose: the forced one skips the
        thresholds, not the preconditions. Firing without a converged model
        or without a measured trajectory is not a bolder shot, it is a
        random one.
        """
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        if self._dragging or self._flight_timer is not None:
            self._log.add_log("Бросок уже в работе — подождите", level="plain")
            return
        if self._motion is None or not self._running:
            self._log.add_log(
                "Сначала запустите слежение и дайте модели сойтись",
                level="error")
            return
        if self._checking():
            # Not a safety belt: in checking mode the boxes on screen are
            # for now, so nothing on screen has anything to do with the shot
            # being planned, and firing would be flying blind on purpose.
            self._log.add_log_segments(
                [("Не бросаю — ", theme.ACCENT_RED),
                 ("включён режим проверки «Сейчас». Переключите "
                  "предсказание на «Через 1.5 с» в настройках.",
                  theme.TEXT_SECONDARY)], level="plain")
            return
        if not self._motion.ready():
            not_ready = [r.index + 1 for r in self._motion.reports()
                         if not r.ready]
            self._log.add_log_segments(
                [("Не бросаю — ", theme.ACCENT_RED),
                 (f"модель ещё не сошлась на рядах "
                  f"{', '.join(map(str, not_ready))}. Дайте посмотреть "
                  f"дольше.", theme.TEXT_SECONDARY)], level="plain")
            return

        geom = self._geometry()
        measured = trajectory.load()
        timings = {aim: trajectory.averaged(geom, measured, aim)
                   for aim in sorted({round(t.aim, 2) for t in measured})}
        timings = {aim: rows for aim, rows in timings.items() if rows}
        for aim, row in trajectory.mirror_fill(geom, timings):
            self._chatter(
                [(f"Ряд {row + 1} на прицеле {aim:+.2f} ", theme.TEXT_DIM),
                 ("взят зеркально с противоположного", theme.ACCENT_AMBER),
                 (" — его так и не удалось промерить напрямую",
                  theme.TEXT_SECONDARY)])
        if not timings:
            self._log.add_log_segments(
                [("Не бросаю — ", theme.ACCENT_RED),
                 ("ни один прицел не промерен: нажмите «Центр», «Лево», "
                  "«Право»", theme.TEXT_SECONDARY)], level="plain")
            return
        if self._fine_aim():
            measured_count = len(timings)
            timings = trajectory.spread_aims(timings)
            self._chatter(
                [("Промежуточные натяжения включены — ", theme.ACCENT_AMBER),
                 (f"рассматриваю {len(timings)} прицелов вместо "
                  f"{measured_count}", theme.HK_ICE)])
        return hwnd, geom, timings

    def _pull_and_hold(self, hwnd: int, geom, found):
        """Pull to the chosen aim and keep holding. The shot is not this
        gesture — it is the mouse-up at the planned moment."""
        start = geom.shooter
        pull = (start[0] + found.aim * _PULL_HORIZONTAL_PX,
                start[1] - _PULL_VERTICAL_PX)
        points = [
            (round(start[0] + (pull[0] - start[0]) * i / _DRAG_STEPS),
             round(start[1] + (pull[1] - start[1]) * i / _DRAG_STEPS))
            for i in range(1, _DRAG_STEPS + 1)
        ]
        self._dragging = True
        mouse_down_at(hwnd, start[0], start[1])
        self._pull_step(hwnd, points, 0, found)

    def _pull_step(self, hwnd: int, points: list, i: int, found):
        if not self._running and i < len(points):
            # Stopped mid-pull: let go where we are rather than leave the
            # game thinking the button is still down.
            self._release(hwnd, points[max(0, i - 1)], found, aborted=True)
            return
        if i >= len(points):
            self._await_release(hwnd, points[-1], found)
            return
        mouse_move_to(hwnd, *points[i])
        QTimer.singleShot(_DRAG_STEP_MS,
                          lambda: self._pull_step(hwnd, points, i + 1, found))

    def _await_release(self, hwnd: int, point: tuple, found):
        """Count down to the planned instant against the real clock."""
        remaining = found.release_at - time.monotonic()
        if remaining <= 0:
            self._release(hwnd, point, found)
            return
        delay = (_RELEASE_FINE_MS if remaining <= _RELEASE_COARSE_S
                 else int((remaining - _RELEASE_COARSE_S) * 1000))
        QTimer.singleShot(max(1, delay),
                          lambda: self._await_release(hwnd, point, found))

    def _release(self, hwnd: int, point: tuple, found, aborted: bool = False):
        mouse_up_at(hwnd, *point)
        self._dragging = False
        if aborted:
            self._log.add_log("Бросок прерван — слежение остановлено",
                              level="plain")
            return
        late = (time.monotonic() - found.release_at) * 1000
        self._chatter(
            [("Бросок сделан", theme.ACCENT_GREEN),
             (f"  (отпустил с задержкой {late:+.0f} мс)", theme.TEXT_DIM)])

    # ── Measuring the flight ─────────────────────────────────────────────

    def _watch_flight(self, hwnd: int, geom, aim: float):
        """Collect the whole flight first and decide afterwards. Picking the
        puck out frame by frame put the marks nowhere near it (2026-08-09):
        the shooter's own avatar animates the instant the button comes up,
        right where the puck starts, and a wrong first choice defends itself
        for the rest of the flight."""
        self._flight = trajectory.PuckFlight(geom, aim, geom.shooter)
        self._flight_started = time.monotonic()
        self._flight_timer = QTimer(self)
        self._flight_timer.timeout.connect(
            lambda: self._flight_tick(hwnd, geom, aim))
        self._flight_timer.start(_FLIGHT_SCAN_MS)

    def _flight_tick(self, hwnd: int, geom, aim: float):
        elapsed = time.monotonic() - self._flight_started
        try:
            frame = grab_window(hwnd, geom.region)
        except Exception:
            return   # one lost frame out of fifty changes nothing
        if not self._flight.feed(frame, elapsed):
            self._finish_flight(geom, aim)

    def _finish_flight(self, geom, aim: float):
        if self._flight_timer is not None:
            self._flight_timer.stop()
            self._flight_timer = None
        flight, self._flight = self._flight, None
        track = flight.resolve()

        # Whatever happens, leave a picture of the path and say what the
        # frames themselves held: a track that wandered off is obvious in one
        # glance and invisible in a list of timings, and the two reasons a
        # track can break are indistinguishable without these numbers.
        frames, seen, highest, step = flight.summary()
        self._log.add_log_segments(
            [("Кадров ", theme.TEXT_SECONDARY), (str(frames), theme.HK_ICE),
             (", с шайбой ", theme.TEXT_SECONDARY), (str(seen), theme.HK_ICE),
             (", по ", theme.TEXT_SECONDARY),
             (f"{step:.0f} мс на кадр", theme.HK_ICE),
             (", выше всего её видели на y ", theme.TEXT_SECONDARY),
             ("—" if highest is None else f"{highest:.0f}", theme.HK_ICE),
             (f"  (ворота y {geom.goal_y})", theme.TEXT_DIM)], level="plain")

        # Drawn on the frame where the track ended, not on the opening one:
        # when a track breaks, what matters is what was on screen at the
        # place it broke.
        last_moment = track.samples[-1].t if track.samples else 0.0
        canvas = flight.frame_at(last_moment)
        if canvas is not None:
            try:
                path = debug_frame.save_flight(canvas, geom, track, aim,
                                               flight.candidates())
                self._log.add_log_segments(
                    [("Путь шайбы — ", theme.TEXT_SECONDARY),
                     (str(path), theme.HK_ICE)], level="plain")
            except Exception as exc:
                self._log.add_log(f"Снимок пути не сохранился: {exc}",
                                  level="error")

        if len(track.samples) < 4:
            self._log.add_log_segments(
                [("Шайбу не удалось проследить — ", theme.ACCENT_RED),
                 ("связной траектории не нашлось. Замер не записан.",
                  theme.TEXT_SECONDARY)], level="plain")
            return
        if not trajectory.arrived(geom, track):
            # A chain that died halfway is not a shorter flight, it is a
            # lost one; storing it next to good measurements is how a table
            # fills with numbers that disagree for no reason.
            blame = ("цепочка не пошла дальше, хотя шайбу видели выше"
                     if highest is not None and highest < track.samples[-1].y - 20
                     else "выше её просто не видно")
            self._log.add_log_segments(
                [("Шайбу потеряли на полпути — ", theme.ACCENT_RED),
                 (f"трек оборвался на y {track.samples[-1].y:.0f}; {blame}. "
                  f"Замер не записан.", theme.TEXT_SECONDARY)], level="plain")
            return

        found = trajectory.crossings(geom, track)
        measured = {crossing.row for crossing in found}
        for crossing in found:
            self._log.add_log_segments(
                [(f"Ряд {crossing.row + 1} — ",
                  _ROW_COLOURS[crossing.row % len(_ROW_COLOURS)]),
                 (f"шайба входит через {crossing.t_enter:.2f} с",
                  theme.HK_ICE),
                 (f" (x {crossing.x_enter:.0f})", theme.TEXT_SECONDARY),
                 (f", выходит через {crossing.t_exit:.2f} с", theme.HK_ICE),
                 (f" (x {crossing.x_exit:.0f})", theme.TEXT_SECONDARY)],
                level="plain")
        # A row with no timing is not a row that was quietly fine — it is a
        # row this shot says nothing about, and the planner will have to
        # treat it that way.
        for lane in geom.lanes:
            if lane.index not in measured:
                self._log.add_log_segments(
                    [(f"Ряд {lane.index + 1} — ",
                      _ROW_COLOURS[lane.index % len(_ROW_COLOURS)]),
                     ("замерить не удалось: шайба не прошла его насквозь",
                      theme.ACCENT_AMBER)], level="plain")

        self._snap_crossings(flight, geom, track, found, aim)
        stored = trajectory.append(track)
        self._log.add_log_segments(
            [("Полёт измерен — ", theme.ACCENT_GREEN),
             (f"{track.travel_s:.2f} с до ворот, точек {len(track.samples)}",
              theme.HK_ICE),
             (f".  Замеров в таблице: {stored}", theme.TEXT_SECONDARY)],
            level="plain")
        self._log_averaged(geom, aim)

    def _log_averaged(self, geom, aim: float):
        """What every flight at this aim agrees on so far.

        A single crossing time is only ever as sharp as the capture rate,
        because it is interpolated across one frame's gap. Repeats are not
        limited by it, so this is the number the planner will actually use —
        and the spread next to it says whether the row agrees with itself.
        """
        timings = trajectory.averaged(geom, trajectory.load(), aim)
        if not timings or timings[0].shots < 2:
            return
        self._log.add_log_segments(
            [("Среднее по ", theme.TEXT_SECONDARY),
             (f"{timings[0].shots} броскам", theme.HK_ICE),
             (f" на прицеле {aim:+.2f}", theme.TEXT_SECONDARY)],
            level="plain")
        for timing in timings:
            colour = (theme.ACCENT_GREEN if timing.spread <= 0.10
                      else theme.ACCENT_AMBER)
            self._log.add_log_segments(
                [(f"  ряд {timing.row + 1} — ",
                  _ROW_COLOURS[timing.row % len(_ROW_COLOURS)]),
                 (f"вход {timing.t_enter:.2f} с", theme.HK_ICE),
                 (f", выход {timing.t_exit:.2f} с", theme.TEXT_SECONDARY),
                 (f", x {timing.x_enter:.0f}", theme.TEXT_SECONDARY),
                 ("   расхождение ", theme.TEXT_SECONDARY),
                 (f"{timing.spread * 1000:.0f} мс", colour)], level="plain")

    def _snap_crossings(self, flight, geom, track, found: list, aim: float):
        """A picture per row at the moment the puck reached it, cut from the
        kept frames *after* the path is known. Taken live they photographed
        whatever the tracker believed at the time, which is worthless
        exactly when it is wrong."""
        lanes = {lane.index: lane for lane in geom.lanes}
        for crossing in found:
            lane = lanes.get(crossing.row)
            # Entry, not the middle: it means the same thing for every row —
            # the puck exactly on the band's lower edge — so the pictures are
            # comparable with each other and with the line drawn on them.
            moment = crossing.t_enter
            frame = flight.frame_at(moment)
            puck = trajectory.puck_at(track, moment)
            if lane is None or frame is None or puck is None:
                continue
            try:
                debug_frame.save_crossing(frame, geom, lane, puck, moment, aim)
            except Exception as exc:
                self._log.add_log(f"Снимок ряда не сохранился: {exc}",
                                  level="error")

    def _execute_shot(self, hwnd: int, start: tuple, pull: tuple,
                      on_release=None):
        """A short, local pull from the puck to `pull`, held briefly, then
        released. Not a trace of the puck's flight: dragging the cursor the
        whole way to the goal made the game's own drag-tracking never
        resolve at all (live, 2026-08-04) — no shot fired, the aim line just
        followed the cursor until the bot was stopped."""
        points = [
            (round(start[0] + (pull[0] - start[0]) * i / _DRAG_STEPS),
             round(start[1] + (pull[1] - start[1]) * i / _DRAG_STEPS))
            for i in range(1, _DRAG_STEPS + 1)
        ]
        self._dragging = True
        mouse_down_at(hwnd, start[0], start[1])
        self._drag_step(hwnd, points, 0, on_release)

    def _drag_step(self, hwnd: int, points: list, i: int, on_release=None):
        # Whatever happens, the button has to be let go of somewhere —
        # abandoning a drag here would leave the game thinking the mouse is
        # held down forever.
        if i >= len(points):
            x, y = points[-1] if points else (0, 0)
            mouse_up_at(hwnd, x, y)
            self._dragging = False
            if on_release is not None:
                on_release()   # the flight starts here, not at the press
            return
        x, y = points[i]
        mouse_move_to(hwnd, x, y)
        delay = _HOLD_MS if i + 1 >= len(points) else _DRAG_STEP_MS
        QTimer.singleShot(delay,
                          lambda: self._drag_step(hwnd, points, i + 1,
                                                  on_release))

    # ── Settings ─────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = HockeySettingsPanel(self.config, self.save_fn,
                                                 self)
            self._settings.rows_cleared.connect(self._on_rows_cleared)
            self._settings.horizon_changed.connect(self._on_horizon_changed)
        self._settings.toggle()

    def _on_horizon_changed(self, checking: bool):
        """Say it out loud. A switch whose only effect is somewhere else on
        screen is one you cannot tell you have pressed."""
        if checking:
            self._log.add_log_segments(
                [("Предсказание — ", theme.TEXT_SECONDARY),
                 ("рисую где вратарь сейчас", theme.ACCENT_AMBER),
                 (".  Рамка должна ехать ровно по нему. Бросок запрещён.",
                  theme.TEXT_SECONDARY)], level="plain")
        else:
            self._log.add_log_segments(
                [("Предсказание — ", theme.TEXT_SECONDARY),
                 (f"рисую где вратарь будет через {_PUCK_TRAVEL_S:.1f} с",
                  theme.ACCENT_GREEN)], level="plain")

    def _on_rows_cleared(self):
        if self._running:
            self._stop_running()
        self._refresh_zones()
        self._log.add_log_segments(
            [("Ряды сброшены — ", theme.ACCENT_AMBER),
             ("отметьте их заново: цель «Ряд», номер ряда, «Отметить область»",
              theme.TEXT_SECONDARY)], level="plain")

    # ── Favorite ─────────────────────────────────────────────────────────

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

    # ── Close ────────────────────────────────────────────────────────────

    def _teardown(self):
        """Nothing of ours should outlive the window — the scan loop and
        every overlay included."""
        self._running = False
        if self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer = None
        if self._flight_timer is not None:
            self._flight_timer.stop()
            self._flight_timer = None
        self._finish_burst()
        self._flight = None
        self._detector = None
        self._motion = None
        self._predicted.clear()
        self._standing.clear()
        self._zones.clear()
        self._calib.clear()
        self._panel.stop_background()
