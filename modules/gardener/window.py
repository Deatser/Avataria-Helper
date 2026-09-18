# modules/gardener/window.py
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QWidget
from datetime import datetime
import time

from PySide6.QtCore import Qt, QRect, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor

import cv2
import numpy as np

from app.core.capture import ScreenCapture, grab_screen_region, grab_window
from app.core.input_sender import click_at
from app.core.template_match import TEMPLATES_DIR, best_match, load_template
from app.ui import theme
from app.ui.area_overlay import AreaOverlay
from app.ui.marker_overlay import Marker, MarkerOverlay
from app.ui.module_window import ModuleWindow
from app.ui.widgets.gd_panel import GdPanel
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.progress_board import ProgressBoard
from modules.gardener.garden_area import Area, FIXED_AREA
from modules.gardener.settings_panel import GardenerSettingsPanel
from modules.gardener.timer_read import read_timer
from modules.gardener.trash import BUTTERFLY, TRASH_KINDS, count_by_kind, scan

_DEFAULT_W = 380
_DEFAULT_H = 800
_MIN_W     = 320
_MIN_H     = 700   # tall enough that the log below still gets its full height

# Three times what it was. Only a floor now, not a fixed size — the log is
# the tallest thing in the window, so it is what should soak up the slack
# when the window is made taller still.
_LOG_H = 200   # a floor; the log takes all the slack when the board is away

_LOG_H_RUN = 110   # ...and what it may be squeezed to while the bars are up

# Breathing room under the bars, added to the window along with them.
_BOARD_GAP = 8

# TEMPORARY — the real-time screen-diff test behind "Определить игрока":
# one frame a second, watching how much the picture changed since the last
# one. A gap any shorter barely lets the character move between frames at
# all. Idle noise from bugs and butterflies read 0.06–0.13%; the two
# stretches watched while the character actually walked read 0.29–0.41% —
# _TRACK_MOVE_PCT sits between the two.
_TRACK_MS       = 1000
_TRACK_MOVE_PCT = 0.20

_ORDINAL_ONES = ["", "первый", "второй", "третий", "четвёртый", "пятый",
                 "шестой", "седьмой", "восьмой", "девятый"]
_ORDINAL_TEENS = ["десятый", "одиннадцатый", "двенадцатый", "тринадцатый",
                  "четырнадцатый", "пятнадцатый", "шестнадцатый",
                  "семнадцатый", "восемнадцатый", "девятнадцатый"]
_ORDINAL_TENS = ["", "", "двадцатый", "тридцатый", "сороковой",
                 "пятидесятый", "шестидесятый", "семидесятый",
                 "восьмидесятый", "девяностый"]
_CARDINAL_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят",
                  "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]


def _ordinal_ru(n: int) -> str:
    """"Первый", "второй", ... — spelled out the way the log reads it,
    since that is how "Ищем такой-то объект" was asked for. Covers what
    one garden could plausibly hold; past that it falls back to a plain
    "N-й" rather than guessing at a compound this code has never been
    told the shape of.
    """
    if 1 <= n <= 9:
        return _ORDINAL_ONES[n]
    if 10 <= n <= 19:
        return _ORDINAL_TEENS[n - 10]
    if 20 <= n <= 99:
        tens, ones = divmod(n, 10)
        return _ORDINAL_TENS[tens] if ones == 0 \
            else f"{_CARDINAL_TENS[tens]} {_ORDINAL_ONES[ones]}"
    return f"{n}-й"


def _brace(row: int, total: int) -> str:
    """The piece of a curly brace that belongs on this row.

    Drawn out of the four bracket pieces rather than one tall glyph: a log
    line is a line, and this is the only way a brace can span several of
    them and still line up in a monospace font.
    """
    if total == 1:
        return "⎨"
    if row == 0:
        return "⎧"
    if row == total - 1:
        return "⎩"
    return "⎨" if row == total // 2 else "⎪"


def _extrapolate(history: list[tuple[float, int, int]]) -> tuple[int, int]:
    """Where a butterfly is heading, judged from its whole recent trail
    at once rather than just its two ends: a least-squares fit through
    every point gives velocity less say to any one noisy reading than a
    plain "first to last" secant would, which matters more the shorter
    and jumpier the trail gets. The prediction itself still only reaches
    one more step past the last point, the same gap as between the last
    two looks, since that is roughly how far off a click actually
    landing is.
    """
    t_last, x_last, y_last = history[-1]
    if len(history) < 2:
        return x_last, y_last
    lead = t_last - history[-2][0]

    n = len(history)
    t_mean = sum(t for t, _, _ in history) / n
    denom = sum((t - t_mean) ** 2 for t, _, _ in history)
    if denom <= 0:
        return x_last, y_last

    x_mean = sum(x for _, x, _ in history) / n
    y_mean = sum(y for _, _, y in history) / n
    vx = sum((t - t_mean) * (x - x_mean) for t, x, _ in history) / denom
    vy = sum((t - t_mean) * (y - y_mean) for t, _, y in history) / denom
    return int(x_last + vx * lead), int(y_last + vy * lead)


def _clamp(value: int, origin: int, span: int) -> int:
    """Keeps an extrapolated point from landing outside the garden — a
    butterfly predicted past the edge is still clicked at the edge."""
    return max(origin, min(origin + span - 1, value))


def _track_box(x0: int, y0: int) -> QRect:
    """The _HUNT_TRACK_BOX square centred on a point — what gets both
    shown on screen and, via _track_region, actually searched."""
    half = _HUNT_TRACK_BOX // 2
    return QRect(x0 - half, y0 - half, _HUNT_TRACK_BOX, _HUNT_TRACK_BOX)


def _track_region(area: Area, x0: int, y0: int) -> dict:
    """The same box _track_box shows, clipped to the garden itself and
    turned into the shape scan() wants — searching only this instead of
    the whole area is what lets the trail be sampled fast enough to mean
    anything by the time a click actually lands."""
    box = _track_box(x0, y0)
    left   = max(area.left, box.left())
    top    = max(area.top, box.top())
    right  = min(area.left + area.width, box.right() + 1)
    bottom = min(area.top + area.height, box.bottom() + 1)
    return {"left": left, "top": top,
           "width": max(1, right - left), "height": max(1, bottom - top)}


_START_TEXT = "▶  Запустить бота по уборке"
_STOP_TEXT  = "■  Выключить бота по уборке"

# A score is never read after a click — the walk itself is the
# confirmation. A look twice a second, for as long as it takes, watching
# for the screen to hold still. A single noisy dip is not enough to end
# the wait, only a real stretch of stillness — a walking character never
# holds still that long on its own, so nothing short of an actual stop or
# an actual fake ever reaches it. _TRACK_MOVE_PCT (defined above) is the
# same line the manual "Определить игрока" check uses to call something
# movement.
_WATCH_MS = 500

# How long the stillness has to hold before it is believed — a real stop,
# once the character has actually set off, always waits this long
# (_STILL_S). A fake that never moved at all is judged much faster during
# the final check specifically (_FINAL_REJECT_S): by then every real pick
# has already had its full, patient look during the main pass, so a
# leftover near miss that never budges is far more likely to just be
# noise, and there is no reason to make the whole check sit through 2.5
# seconds of confirming that hundreds of times over.
_STILL_S        = 2.5
_FINAL_REJECT_S = 0.75

# A drop counts as the object being gone once its score has at least halved
# from what it was when found — used only by the popup closer now that
# litter itself is confirmed by watching the character instead.
_DROP_RATIO = 0.5

# TEMPORARY — the click lands, but nothing is done yet with whether it
# actually caught anything. Full-garden, 26-template searches are too
# slow to run more than a few times a second, so this — the wide, "is
# anything at all here" search — only ever runs while nothing has been
# spotted yet.
_HUNT_MS = 400

# The moment something is spotted, tracking switches to a small box
# around it (_HUNT_TRACK_BOX, shown on screen and searched via
# _track_region) instead of the whole garden — small enough, and cheap
# enough, to check every _HUNT_TRACK_MS rather than every _HUNT_MS, which
# is what actually closes the gap between a sighting and a click landing
# somewhere still true. _HUNT_MIN_HISTORY trail points (the first from
# the wide search, the rest from tracking) is enough to judge a direction
# from before a click goes out, aimed ahead of the trail rather than at
# it. Named apart from the unrelated _TRACK_MS above — that one is the
# manual "Определить игрока" screen-diff check.
_HUNT_TRACK_MS    = 60
_HUNT_TRACK_BOX   = 50
_HUNT_MIN_HISTORY = 2

# How long a click on a butterfly is given before it is judged one way or
# the other — its own clock, separate from litter's _WATCH_MS/_STILL_S,
# since a butterfly that got away is not worth watching for as long as a
# bush might be worth waiting on.
_CATCH_WATCH_MS    = 500
_CATCH_STILL_TICKS = 3

# A click landing near a butterfly is not the same as catching it — so
# rather than declare victory after any one chase, the hunt keeps going
# until the search itself has turned up nothing at all for this long.
# Long enough that one flying out of frame for a few ticks is not
# mistaken for none being left.
_HUNT_GIVE_UP_S = 20.0

# How far below a kind's own bar a match is still worth showing as a near
# miss — used by the final check and the per-kind debug buttons, both of
# which want a wide window to judge a threshold by. Lower than trash.py's
# own NEAR_THRESHOLD on purpose: that one is a floor the fast-repeating
# butterfly hunt also scans at, where a wide window means a slow one:
# these two run once, not several times a second, and can afford it.
_WIDE_NEAR = 0.30

# Locates clean_next_time on screen — see _run_next_shift_search below.
_NEXTSHIFT_TEMPLATE = "gardener_nextshift_placeholder.png"

# Below this the placeholder was not actually found — the "best" match is
# just wherever in the game window happened to look least unlike it, and a
# crop taken there is not the timer at all.
_NEXTSHIFT_THRESHOLD = 0.90

_POPUP_TEMPLATE = "button_close.png"

# Calibrated once by hand (2026-08-03): the close button was found at
# left=1224, top=764, size 112×33 — padded 50 px on every side, so a popup
# that lands a little differently than that one did is still caught.
_POPUP_REGION = {"left": 1174, "top": 714, "width": 212, "height": 133}

# How well the button has to match to be believed at all — comfortably
# below the 99.8% it scored when it was actually found, comfortably above
# noise.
_POPUP_THRESHOLD = 0.85

# How long to give the game to draw the popup gone before checking again.
_POPUP_RECHECK_MS = 300

# "Здесь вся работа завершена" — the game's own sign that the garden has
# nothing left at all. Checked across the whole game window, not just the
# garden rectangle, since there is no telling in advance where the game
# draws it; on its own clock, independent of whatever the run is doing at
# that moment (cleaning, hunting, the final check), since it can turn up
# during any of them.
_DONE_TEMPLATE   = "gardener_done.png"
_DONE_THRESHOLD  = 0.85
_DONE_CHECK_MS   = 1000

# Re-entering the garden once it is done: Places, then Professions, then
# Gardener — the same three taps a person would make — is what makes the
# timer badge redraw, so "Поиск времени"'s own logic has a fresh one to
# read once this chain finishes.
_NAV_STEPS = [
    ("button_places.png",   "Места"),
    ("button_jobs.png",     "Профессии"),
    ("button_gardener.png", "Садовник"),
]
_NAV_THRESHOLD      = 0.85
_NAV_POLL_MS        = 400    # how often a step retries while its button is not yet on screen
_NAV_TIMEOUT_S      = 15.0   # how long a single step waits before giving up
_NAV_AFTER_CLICK_MS = 800    # time given to the game to start reacting to a click

# The Gardener click is the odd one out: it does not just switch a panel,
# it loads the whole location — the badge is not drawn yet at
# _NAV_AFTER_CLICK_MS, which is what read a stale screen at 60% and landed
# the crop somewhere wrong. Given the load its own, longer wait.
_NAV_LOCATION_LOAD_MS = 5000


class GardenerWindow(ModuleWindow):
    """Садовник — the shell only: header, frame and the usual window habits.

    Everything the other module windows do comes from ModuleWindow and the
    overlay: the switch-on and switch-off animations, the wire back to the
    helper, dragging by any empty spot, resizing by the edges, collapsing,
    the star, and the remembered position. What goes inside is still to be
    decided, so the body is deliberately empty rather than filled with
    controls that would have to be thrown away.
    """

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None,
                stats=None):
        super().__init__("Садовник", config, save_fn, parent_overlay)
        self._wm = window_manager
        self._stats = stats
        self._running  = False
        self._settings: GardenerSettingsPanel | None = None
        self._kind_buttons_anim: QPropertyAnimation | None = None
        # The marks the gardener walks: a dot on each piece of litter, in the
        # colour of its kind, over the game window and nowhere else.
        self._markers  = MarkerOverlay(window_manager, reference=self)
        # TEMPORARY — a debug toggle to check the scanned rectangle against
        # the game by eye; take it back out once that is confirmed good.
        self._area_overlay = AreaOverlay(window_manager, reference=self)
        # The box the butterfly hunt is actually searching right now, once
        # it has one — shown so the search itself can be watched, not
        # just its result.
        _hunt_fill = QColor(BUTTERFLY.colour); _hunt_fill.setAlpha(60)
        _hunt_border = QColor(BUTTERFLY.colour); _hunt_border.setAlpha(220)
        self._hunt_box = AreaOverlay(window_manager, reference=self,
                                     fill=_hunt_fill, border=_hunt_border)
        # The garden's own rectangle — calibrated by hand once and fixed;
        # every grab is pointed at this instead of the whole screen.
        self._garden_area: Area = FIXED_AREA
        self._move_timer: QTimer | None = None
        self._done_timer: QTimer | None = None
        self._butterfly_timer: QTimer | None = None
        self._catch_timer: QTimer | None = None
        self._hunting = False   # true across both the search and watch halves
        self._hunt_history: list[tuple[float, int, int]] = []
        self._hunt_last_seen = 0.0   # when the search last found one at all
        # The final pass's own worklist — every near miss left over once
        # the butterflies are done, best score first.
        self._final_queue: list = []
        self._track_timer: QTimer | None = None
        self._track_prev_frame = None
        self._track_moving = False
        # Every position already given a click, real or not. Litter can
        # regrow in the same spot, and without this a spot that keeps
        # refilling would look "fresh" forever. (kind key, x, y).
        self._handled: set[tuple[str, int, int]] = set()
        # Where the last object taken on was — the next pick is whichever
        # is nearest to this, not whatever is furthest left. None only at
        # the very start of a run, before anywhere has been visited yet.
        self._last_pos: tuple[int, int] | None = None
        self._object_number = 0
        self._done: dict[str, int] = {}   # kind key -> how many actually cleared
        self._next_run_at: datetime | None = None
        self._board_room = 0     # extra height lent to the bars, given back later
        self.resize(max(getattr(config, "width",  _DEFAULT_W), _MIN_W),
                    max(getattr(config, "height", _DEFAULT_H), _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = GdPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background:{theme.GD_BORDER};")
        layout.addWidget(separator)
        layout.addSpacing(4)

        self._start_btn = NtButton(_START_TEXT, accent=theme.GD_OLIVE,
                                   upper=False)
        self._start_btn.clicked.connect(self._toggle_cleaning)
        layout.addWidget(self._start_btn)

        settings_btn = NtButton("⚙  Настройки", accent=theme.GD_MOSS,
                                upper=False)
        settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(settings_btn)

        # Off by default, and rolled open or shut from Settings — a
        # troubleshooting tool, not something a normal run needs to see.
        self._kind_buttons_box = QWidget(self._panel)
        self._kind_buttons_box.setLayout(self._build_kind_buttons())
        self._kind_buttons_box.setMaximumHeight(
            self._kind_buttons_box.sizeHint().height()
            if getattr(self.config, "show_kind_buttons", False) else 0)
        layout.addWidget(self._kind_buttons_box)

        self._board = ProgressBoard(self._panel)
        layout.addWidget(self._board)

        layout.addLayout(self._build_log(), stretch=1)

        drag = NtDragHandle(dot_color=theme.GD_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _build_kind_buttons(self) -> QVBoxLayout:
        """One small button per kind, two rows of three — each one a look
        at just that kind: where it is, how well it matched, and the bar
        it is being judged against right now. Hidden behind the Settings
        switch by default."""
        block = QVBoxLayout()
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(theme.SPACING)
        rows = [QHBoxLayout(), QHBoxLayout()]
        for row in rows:
            row.setSpacing(theme.SPACING)
        for index, kind in enumerate(TRASH_KINDS):
            btn = NtButton(kind.singular, accent=kind.colour, upper=False)
            if kind.key == BUTTERFLY.key:
                # Butterflies never hold still for a one-off look — this
                # button toggles the same continuous hunt the run turns to
                # once the rest is cleared, so it can be watched on its own
                # without waiting for a full run first.
                btn.clicked.connect(self._toggle_butterfly_hunt)
            else:
                btn.clicked.connect(
                    lambda _checked=False, k=kind: self._scan_kind(k))
            rows[index // 3].addWidget(btn)
        for row in rows:
            block.addLayout(row)
        return block

    def _set_kind_buttons_visible(self, enabled: bool):
        """Rolled open or shut, not just shown or hidden — a switch flipped
        in Settings should read as something happening, not a jump cut."""
        box = self._kind_buttons_box
        target = box.sizeHint().height() if enabled else 0
        anim = QPropertyAnimation(box, b"maximumHeight", self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.setStartValue(box.maximumHeight())
        anim.setEndValue(target)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._kind_buttons_anim = anim   # keep it alive until it finishes

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        # Offline, not stopped: there is nothing to run here yet, and a red
        # dot on an empty window reads as something having gone wrong.
        self._status_dot = NtStatusDot(accent=theme.GD_OLIVE)
        self._status_dot.set_offline()

        title = QLabel("САДОВНИК")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.GD_OLIVE}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.GD_OLIVE)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.GD_OLIVE_SOFT)
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
        """Log with its clear button on the heading row, as in the overlay."""
        block = QVBoxLayout()
        block.setSpacing(4)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)

        head = QHBoxLayout()
        title = QLabel("Garden Log:")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.GD_TEXT}; background:transparent;")
        head.addWidget(title)
        head.addStretch()
        head.addLayout(build_log_actions(self._log, theme.GD_BORDER,
                                         on_clear=self._clear_log))
        block.addLayout(head)

        block.addWidget(self._log, stretch=1)
        return block

    def _clear_log(self):
        self._log.clear_logs()
        if not self._running:
            self._markers.clear()
            # The bars are the record of a run that is over: they go with it.
            self._put_board_away()

    # ── Cleaning ─────────────────────────────────────────────────────────────

    def _toggle_cleaning(self):
        if self._running:
            self._stop_cleaning("Уборка остановлена")
            return
        if self._cooling_down():
            return
        QTimer.singleShot(0, self._begin_cleaning)

    def _cooling_down(self) -> bool:
        """The garden refills on its own schedule; hammering it does nothing."""
        if self._next_run_at is None or datetime.now() >= self._next_run_at:
            return False
        left = self._next_run_at - datetime.now()
        minutes = int(left.total_seconds() // 60) + 1
        self._log.add_log_segments(
            [("Цикл ещё не доступен — осталось ", theme.TEXT_SECONDARY),
             (f"{minutes} мин", theme.ACCENT_AMBER),
             (f", в {self._next_run_at:%H:%M:%S}", theme.TEXT_SECONDARY)],
            level="plain")
        return True

    def _begin_cleaning(self):
        """One object at a time: click it, watch the character either set
        off toward it or not, and either way move on once that is settled
        — until a look turns up nothing left within reach."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log_start(False)
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        area = self._garden_area

        try:
            found = scan(region=area.region)
        except Exception as exc:
            self._log_start(False)
            self._log.add_log(str(exc), level="error")
            return

        counts = count_by_kind(found)
        self._log_start(True)
        self._log_to_helper("— Запущена уборка в саду")

        self._running = True
        self._handled = set()
        self._last_pos = None
        self._object_number = 0
        self._done = {}
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._status_dot.set_running()

        self._board.build(
            sum(counts.values()),
            [(k.key, f"Найдено {k.plural}", counts.get(k.key, 0), k.colour)
             for k in TRASH_KINDS])
        self._make_room_for_board()

        self._done_timer = QTimer(self)
        self._done_timer.timeout.connect(lambda: self._check_done(hwnd))
        self._done_timer.start(_DONE_CHECK_MS)

        self._handle_next_object(hwnd, area)

    def _check_done(self, hwnd: int):
        """The game's own word that the garden is finished — checked on
        its own clock the whole time the run is going, whatever else it
        happens to be doing at that moment."""
        try:
            gray = cv2.cvtColor(grab_window(hwnd), cv2.COLOR_BGR2GRAY)
        except Exception:
            return   # a dropped frame here is not worth stopping the run over
        template = load_template(_DONE_TEMPLATE)
        if template is None:
            return
        score, _loc = best_match(gray, template)
        if score < _DONE_THRESHOLD:
            return
        self._stop_cleaning("Работа завершена")
        if self._stats is not None:
            self._stats.record_gardener_cleanup()
        self._log_to_helper("— Завершена уборка в саду, определяем время до следующей")
        self._reenter_garden()

    def _reenter_garden(self):
        """Places, then Professions, then Gardener — see _NAV_STEPS. Once
        the chain is through, the same read "Поиск времени" does is run on
        its own, since re-entering is what makes the badge worth reading."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        self._nav_step(hwnd, 0, time.monotonic())

    def _nav_step(self, hwnd: int, step: int, started: float):
        if step >= len(_NAV_STEPS):
            self._run_next_shift_search()
            return

        filename, label = _NAV_STEPS[step]
        template = load_template(filename)
        if template is None:
            self._log.add_log(f"Шаблон «{label}» не найден", level="error")
            return

        clicked = False
        try:
            frame = grab_window(hwnd)
            rect = self._wm.window_rect_screen(hwnd)
        except Exception:
            frame, rect = None, None
        if frame is not None and rect is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            score, (x, y) = best_match(gray, template)
            if score >= _NAV_THRESHOLD:
                th, tw = template.shape[:2]
                ox, oy, _w, _h = rect
                click_at(hwnd, ox + x + tw // 2, oy + y + th // 2)
                clicked = True

        if clicked:
            next_step = step + 1
            delay = (_NAV_LOCATION_LOAD_MS if next_step >= len(_NAV_STEPS)
                     else _NAV_AFTER_CLICK_MS)
            QTimer.singleShot(
                delay,
                lambda: self._nav_step(hwnd, next_step, time.monotonic()))
            return

        if time.monotonic() - started >= _NAV_TIMEOUT_S:
            self._log.add_log(f"Не удалось найти «{label}» — переход прерван",
                              level="error")
            return
        QTimer.singleShot(_NAV_POLL_MS,
                          lambda: self._nav_step(hwnd, step, started))

    def _handle_next_object(self, hwnd: int, area: Area):
        """Look again, and take on whatever is nearest to wherever the last
        object was."""
        if not self._running:
            return      # stopped in between two objects

        try:
            found = scan(region=area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        target = self._pick_target([item for item in found if item.accepted])
        self._log_search_result(target is not None, self._object_number + 1)
        if target is None:
            self._start_butterfly_hunt(hwnd, area)
            return

        self._handled.add((target.kind.key, target.x, target.y))
        self._last_pos = (target.x, target.y)
        self._object_number += 1
        self._show_dots([target])

        self._log.add_log("Идём убирать объект", level="plain")
        click_at(hwnd, target.x, target.y)
        self._start_watching(target, area, hwnd,
                             lambda: self._handle_next_object(hwnd, area))

    def _pick_target(self, accepted: list):
        """Nearest to wherever the last object was, not strictly left to
        right — two objects can sit at very different heights and still be
        close together, and a raster sweep across the whole garden would
        walk straight past the nearer one just to keep the row going. The
        very first pick, with nowhere to measure from yet, starts top-left.

        Butterflies never sit still long enough to be walked to and
        clicked the way a bush is — once none of the rest are left, they
        get their own hunt instead of turning up here.
        """
        fresh = [item for item in accepted
                if item.kind.key != BUTTERFLY.key
                and (item.kind.key, item.x, item.y) not in self._handled]
        if not fresh:
            return None
        if self._last_pos is None:
            return min(fresh, key=lambda item: (item.x, item.y))
        lx, ly = self._last_pos
        return min(fresh, key=lambda item: (item.x - lx) ** 2 + (item.y - ly) ** 2)

    def _credit(self, key: str):
        """One more of that kind gone — as seen, not as clicked."""
        self._done[key] = self._done.get(key, 0) + 1
        self._board.advance(key, self._done[key], sum(self._done.values()))

    def _log_start(self, ok: bool):
        self._log.add_log_segments(
            [("Начинаем уборку — ", theme.TEXT_SECONDARY),
             ("успешно" if ok else "не успешно",
              theme.ACCENT_GREEN if ok else theme.ACCENT_RED)],
            level="plain")

    def _log_to_helper(self, message: str):
        """Only the events worth knowing about without the module even
        being open — start and finish, not every object along the way —
        go to the helper's own log, [Садовник] marking whose line it is."""
        if not self.parent_overlay:
            return
        self.parent_overlay.add_log_segments(
            [("[Садовник] ", theme.GD_OLIVE),
             (message, theme.TEXT_SECONDARY)],
            level="plain")

    def _log_search_result(self, found: bool, number: int):
        self._log.add_log_segments(
            [(f"Ищем {_ordinal_ru(number)} объект — ", theme.TEXT_SECONDARY),
             ("Объект найден" if found else "Объект не найден",
              theme.ACCENT_GREEN if found else theme.ACCENT_RED)],
            level="plain")

    def _start_watching(self, target, area: Area, hwnd: int, on_done,
                        reject_s: float = _STILL_S):
        """A baseline frame, right as the click lands — nothing is compared
        to it yet, since it is the only frame there is so far. `on_done` is
        called once this target is resolved, whichever way — the main loop
        moves on to its next nearest pick; the final check moves on to the
        next one in its own, separately ordered queue. `reject_s` is how
        long standing still with no movement at all has to hold before
        it's called a fake — the final check uses a much shorter one of
        its own; everything else keeps the same wait either way.
        """
        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._watch_target(
                target, area, hwnd, frame, None, False, on_done, reject_s))
        self._move_timer.start(_WATCH_MS)

    def _watch_target(self, target, area: Area, hwnd: int, prev_frame,
                      still_since: float | None, did_move: bool, on_done,
                      reject_s: float):
        """One look at a time, for as long as it takes, until the screen
        has held still long enough to believe it: _STILL_S once it has
        actually moved, `reject_s` if it never did at all.

        Whether it ever moved before the stillness began decides what
        that stillness means: litter that was never real never sends the
        character anywhere, so holding still throughout is a misdetection;
        litter that did gets walked to and then stood over, so holding
        still only after moving is the character having arrived. Any
        movement resets the clock — a single quiet reading is never
        enough on its own, in either direction.
        """
        if not self._running:
            return      # stopped mid-watch

        self._check_popup(hwnd)

        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        changed = 100.0
        if prev_frame.shape == frame.shape:
            changed = float(np.abs(
                frame.astype(np.int16) - prev_frame.astype(np.int16)).mean()) \
                / 255 * 100

        now = time.monotonic()
        if changed >= _TRACK_MOVE_PCT:
            did_move = True
            still_since = None
        else:
            if still_since is None:
                still_since = now
            threshold = _STILL_S if did_move else reject_s
            if now - still_since >= threshold:
                if did_move:
                    self._finish_target(target, on_done)
                else:
                    self._reject_target(target, on_done)
                return

        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._watch_target(
                target, area, hwnd, frame, still_since, did_move, on_done,
                reject_s))
        self._move_timer.start(_WATCH_MS)

    def _finish_target(self, target, on_done):
        self._log.add_log_segments(
            [("Игрок подошёл к объекту №", theme.TEXT_SECONDARY),
             (str(self._object_number), theme.GD_OLIVE),
             (" — убираем...", theme.TEXT_SECONDARY)],
            level="plain")
        self._log.add_log("Объект убран", level="plain")
        self._credit(target.kind.key)
        QTimer.singleShot(0, on_done)

    def _reject_target(self, target, on_done):
        """The click landed, but the character never set off toward it —
        read as a misdetection rather than real litter, so it is dropped
        for good rather than tried again for the same nothing."""
        self._log.add_log_segments(
            [("Игрок стоит — ", theme.TEXT_SECONDARY),
             ("объект фейк", theme.ACCENT_RED)],
            level="plain")
        self._write_off(target, on_done)

    def _write_off(self, target, on_done):
        self._board.dec_total(target.kind.key)
        QTimer.singleShot(0, on_done)

    def _toggle_butterfly_hunt(self):
        """The "Бабочка" button: the same hunt the run turns to once
        everything walkable is cleared, started or stopped by hand so it
        can be watched without waiting on a full run first."""
        if self._hunting:
            self._stop_butterfly_hunt()
            return
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        self._start_butterfly_hunt(hwnd, self._garden_area)

    def _start_butterfly_hunt(self, hwnd: int, area: Area):
        """TEMPORARY — the click lands, but nothing is done yet with
        whether it actually caught anything.

        Once nothing walkable is left, the run does not stop — it turns to
        the one kind that was never going to be walked to in the first
        place.
        """
        self._log.add_log("Начинаем ловить бабочек", level="plain")
        self._hunting = True
        self._resume_search(hwnd, area)

    def _stop_butterfly_hunt(self):
        self._hunting = False
        if self._butterfly_timer is not None:
            self._butterfly_timer.stop()
            self._butterfly_timer = None
        if self._catch_timer is not None:
            self._catch_timer.stop()
            self._catch_timer = None
        self._hunt_history.clear()
        self._markers.clear()
        self._hunt_box.clear()

    def _resume_search(self, hwnd: int, area: Area):
        """Only while nothing is already being clicked and watched —
        search and watch never run at once, so a found butterfly is never
        clicked twice over. The trail from whatever was being chased
        before does not belong to whatever turns up next, and the
        "nothing found in a while" clock starts fresh from here too — a
        chase just happened, so a butterfly being around is not in doubt
        yet.
        """
        self._hunt_history.clear()
        self._hunt_last_seen = time.monotonic()
        self._hunt_box.clear()
        self._butterfly_timer = QTimer(self)
        self._butterfly_timer.timeout.connect(lambda: self._hunt_tick(hwnd, area))
        self._butterfly_timer.start(_HUNT_MS)

    def _hunt_tick(self, hwnd: int, area: Area):
        """The whole garden, looking for anything at all — slow going
        with 26 templates over that much area, so this only ever runs
        while nothing has been spotted yet. The moment something is,
        _hunt_track_tick takes over: a small box around it, checked much
        faster, since a fast, narrow look is what actually keeps a click
        from landing behind where it already flew off to. A tick that
        finds nothing at all is not itself a reason to stop — only
        _HUNT_GIVE_UP_S seconds straight of that is.
        """
        try:
            found = scan(region=area.region, kinds=[BUTTERFLY])
        except Exception:
            return   # try again on the next tick
        tracked = [item for item in found if item.accepted]
        if not tracked:
            self._markers.clear()
            self._hunt_history.clear()
            if time.monotonic() - self._hunt_last_seen >= _HUNT_GIVE_UP_S:
                self._finish_hunt(hwnd, area)
            return

        self._hunt_last_seen = time.monotonic()
        target = min(tracked, key=lambda item: item.x)   # leftmost of them
        self._hunt_history = [(time.monotonic(), target.x, target.y)]
        self._markers.show_markers(
            [Marker(target.x, target.y, BUTTERFLY.colour, True)])
        self._hunt_box.show_area(_track_box(target.x, target.y))

        self._butterfly_timer.stop()
        self._butterfly_timer = QTimer(self)
        self._butterfly_timer.setSingleShot(True)
        self._butterfly_timer.timeout.connect(
            lambda: self._hunt_track_tick(hwnd, area, target.x, target.y))
        self._butterfly_timer.start(_HUNT_TRACK_MS)

    def _hunt_track_tick(self, hwnd: int, area: Area, last_x: int, last_y: int):
        """A small box around wherever it was a moment ago, checked every
        _HUNT_TRACK_MS — the trail this builds is both fresher and far
        more frequent than the wide search could manage, which is what
        makes the extrapolation from it actually land. Nothing left in
        the box at all means it flew out of it entirely; worth a fresh,
        wide look rather than guessing from a trail that is no longer
        true.
        """
        region = _track_region(area, last_x, last_y)
        try:
            found = scan(region=region, kinds=[BUTTERFLY])
        except Exception:
            self._resume_search(hwnd, area)
            return
        tracked = [item for item in found if item.accepted]
        if not tracked:
            self._resume_search(hwnd, area)
            return

        self._hunt_last_seen = time.monotonic()
        target = min(tracked, key=lambda item:
                    (item.x - last_x) ** 2 + (item.y - last_y) ** 2)
        self._hunt_history.append((time.monotonic(), target.x, target.y))
        self._hunt_box.show_area(_track_box(target.x, target.y))

        if len(self._hunt_history) < _HUNT_MIN_HISTORY:
            self._markers.show_markers(
                [Marker(target.x, target.y, BUTTERFLY.colour, True)])
            self._butterfly_timer = QTimer(self)
            self._butterfly_timer.setSingleShot(True)
            self._butterfly_timer.timeout.connect(
                lambda: self._hunt_track_tick(hwnd, area, target.x, target.y))
            self._butterfly_timer.start(_HUNT_TRACK_MS)
            return   # still building a trail — nothing to aim ahead of yet

        click_x, click_y = _extrapolate(self._hunt_history)
        click_x = _clamp(click_x, area.left, area.width)
        click_y = _clamp(click_y, area.top, area.height)
        self._markers.show_markers(
            [Marker(click_x, click_y, BUTTERFLY.colour, True)])
        self._hunt_box.clear()

        self._butterfly_timer.stop()
        self._butterfly_timer = None
        click_at(hwnd, click_x, click_y)
        self._log.add_log("Кликнули по бабочке", level="plain")
        self._start_catch_watch(hwnd, area)

    def _finish_hunt(self, hwnd: int, area: Area):
        if self._butterfly_timer is not None:
            self._butterfly_timer.stop()
            self._butterfly_timer = None
        self._markers.clear()
        self._hunt_box.clear()
        self._log.add_log("Поймали всех бабочек", level="plain")
        self._log.add_log("Начинаем последнюю проверку", level="plain")
        self._start_final_check(hwnd, area)

    def _start_final_check(self, hwnd: int, area: Area):
        """One more pass, over everything the first pass would have had to
        skip: litter that spawned partly hidden behind something else
        never clears its own kind's bar, no matter how real it is. Best
        score first, on the theory that the closest near misses are the
        ones most likely to actually be litter rather than noise.
        """
        try:
            found = scan(region=area.region, near=_WIDE_NEAR)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return
        self._final_queue = sorted(
            (item for item in found
             if item.kind.key != BUTTERFLY.key
             and (item.kind.key, item.x, item.y) not in self._handled),
            key=lambda item: -item.score)
        self._show_final_markers()
        self._handle_final_next(hwnd, area)

    def _handle_final_next(self, hwnd: int, area: Area):
        if not self._running:
            return
        if not self._final_queue:
            self._markers.clear()
            return   # TEMPORARY — what happens once this empties is next
        target = self._final_queue.pop(0)
        self._handled.add((target.kind.key, target.x, target.y))
        self._object_number += 1
        self._show_final_markers(current=target)

        self._log.add_log_segments(
            [("Проверяем невошедшую координату ", theme.TEXT_SECONDARY),
             (f"({target.x}, {target.y})", theme.GD_OLIVE_SOFT),
             (" — ", theme.TEXT_SECONDARY),
             (f"{target.score * 100:.0f}%", theme.GD_OLIVE_SOFT),
             (" (", theme.TEXT_SECONDARY),
             (target.kind.singular, target.kind.colour),
             (")", theme.TEXT_SECONDARY)],
            level="plain")
        click_at(hwnd, target.x, target.y)
        self._start_watching(target, area, hwnd,
                             lambda: self._handle_final_next(hwnd, area),
                             reject_s=_FINAL_REJECT_S)

    def _show_final_markers(self, current=None):
        """Every near miss still waiting its turn, red and hollow like the
        per-kind buttons already draw them — plus the one actually being
        tried right now, picked out in white so it reads apart from the
        rest of the pack at a glance."""
        markers = [Marker(item.x, item.y, theme.ACCENT_RED, False)
                  for item in self._final_queue]
        if current is not None:
            markers.append(Marker(current.x, current.y, theme.ACCENT_WHITE, True))
        self._markers.show_markers(markers)

    def _start_catch_watch(self, hwnd: int, area: Area):
        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._resume_search(hwnd, area)
            return
        self._catch_timer = QTimer(self)
        self._catch_timer.setSingleShot(True)
        self._catch_timer.timeout.connect(
            lambda: self._watch_catch(hwnd, area, frame, 0, False))
        self._catch_timer.start(_CATCH_WATCH_MS)

    def _watch_catch(self, hwnd: int, area: Area, prev_frame,
                     still_streak: int, did_move: bool):
        """The same wait a click on litter gets, just on its own, shorter
        clock: up to _CATCH_STILL_TICKS quiet looks in a row before
        anything is concluded, so one noisy tick cannot pass for the
        character having already set off and come back. Nothing is
        credited either way yet — only whether the click seems to have
        sent the character somewhere at all — and the search resumes once
        it is, whichever way it went.
        """
        if not self._hunting:
            return      # hunting was stopped mid-watch

        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._resume_search(hwnd, area)
            return

        changed = 100.0
        if prev_frame.shape == frame.shape:
            changed = float(np.abs(
                frame.astype(np.int16) - prev_frame.astype(np.int16)).mean()) \
                / 255 * 100

        if changed >= _TRACK_MOVE_PCT:
            did_move = True
            still_streak = 0
        else:
            still_streak += 1

        if still_streak >= _CATCH_STILL_TICKS:
            if not did_move:
                self._log.add_log_segments(
                    [("Игрок стоит — ", theme.TEXT_SECONDARY),
                     ("не попали по бабочке", theme.ACCENT_RED)],
                    level="plain")
            self._resume_search(hwnd, area)
            return

        self._catch_timer = QTimer(self)
        self._catch_timer.setSingleShot(True)
        self._catch_timer.timeout.connect(
            lambda: self._watch_catch(
                hwnd, area, frame, still_streak, did_move))
        self._catch_timer.start(_CATCH_WATCH_MS)

    def _check_popup(self, hwnd: int):
        """A quiet look for an unrelated popup sitting over the garden.

        Nothing goes to the log for this unless a click of ours actually
        appears to have closed something — a look that finds nothing, or a
        click that does not seem to have worked, is not worth a line: the
        run already has plenty to say about the litter itself.
        """
        template = load_template(_POPUP_TEMPLATE)
        if template is None:
            return
        try:
            gray = cv2.cvtColor(grab_screen_region(_POPUP_REGION),
                                cv2.COLOR_BGR2GRAY)
        except Exception:
            return

        score, (x, y) = best_match(gray, template)
        if score < _POPUP_THRESHOLD:
            return

        h, w = template.shape[:2]
        cx = _POPUP_REGION["left"] + x + w // 2
        cy = _POPUP_REGION["top"] + y + h // 2
        click_at(hwnd, cx, cy)
        QTimer.singleShot(_POPUP_RECHECK_MS,
                          lambda: self._confirm_popup_closed(score))

    def _confirm_popup_closed(self, before_score: float):
        if not self._running:
            return      # stopped in the moment between the click and this
        template = load_template(_POPUP_TEMPLATE)
        if template is None:
            return
        try:
            gray = cv2.cvtColor(grab_screen_region(_POPUP_REGION),
                                cv2.COLOR_BGR2GRAY)
        except Exception:
            return

        score, _loc = best_match(gray, template)
        if score <= before_score * _DROP_RATIO:
            self._log.add_log("Закрыли лишнее всплывающее окно",
                              level="plain")

    def _show_dots(self, found: list):
        """A dot on every piece of litter, in its own kind's colour.

        Brown on the dry bushes, blue on the blue ones — the same colours the
        counts are written in, so the log and the garden read together.
        """
        self._markers.show_markers(
            [Marker(item.x, item.y, item.kind.colour, True)
             for item in found if item.accepted])

    def _stop_cleaning(self, message: str | None):
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        if self._done_timer is not None:
            self._done_timer.stop()
            self._done_timer = None
        if self._butterfly_timer is not None:
            self._butterfly_timer.stop()
            self._butterfly_timer = None
        if self._catch_timer is not None:
            self._catch_timer.stop()
            self._catch_timer = None
        self._hunting = False
        self._running = False
        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_offline()
        self._markers.clear()      # the mark has been walked, or given up on
        self._hunt_box.clear()
        if message:
            self._log.add_log(message, level="plain")

    # ── Room for the bars ────────────────────────────────────────────────────

    def _make_room_for_board(self):
        """Lend the board the height it needs by making the window taller.

        The window has a minimum of its own, and an explicit minimum beats the
        one the layout works out — so nothing grows on its own here. Left
        alone, the layout has less room than its contents need and hands out
        overlapping rows: the log climbs over the bottom bars.
        """
        wanted = self._board.wanted_height()
        wanted += _BOARD_GAP if wanted else 0
        delta = wanted - self._board_room
        if not delta:
            return
        self._board_room = wanted

        # While the bars are up the log gives up its floor: on a screen too
        # short for both, the log is the one that can afford to be smaller.
        self._log.setMinimumHeight(_LOG_H_RUN if wanted else _LOG_H)

        # Высота окна — живая, а _MIN_H и delta посчитаны по содержимому,
        # то есть в расчётных пикселях: на ужатой игре окно обязано вырасти
        # во столько же раз меньше, иначе полоски бота выталкивают его за
        # край игры. Предел — тоже по игре, а не по монитору (game_fit.py).
        scale  = self.ui_scale
        room   = self.fit_room_height()
        height = max(int(round(_MIN_H * scale)),
                     self.height() + int(round(delta * scale)))
        self.resize(self.width(), min(height, room) if room else height)

    def _put_board_away(self):
        """Take the bars down and give the borrowed height back."""
        self._board.clear()
        self._make_room_for_board()

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = GardenerSettingsPanel(self.config, self.save_fn,
                                                   self)
            self._settings.kind_buttons_toggled.connect(
                self._set_kind_buttons_visible)
        self._settings.toggle()

    # ── Trash detection ──────────────────────────────────────────────────────

    def _scan_trash(self):
        """Temporary readout: what is on screen and where, right now.

        Deferred a turn so the "scanning" line is on screen before the
        matching starts — two full-screen passes take a moment.
        """
        # Deferred a turn so the button repaints as pressed before the
        # matching starts — ten templates over the whole screen take a moment.
        QTimer.singleShot(0, self._run_scan)

    def _run_scan(self):
        try:
            found = scan(region=self._garden_area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return

        self._log_counts(count_by_kind(found))
        self._show_dots(found)      # the same dots a run would walk

    def _run_next_shift_search(self):
        """Where gardener_nextshift_placeholder.png is found on the whole
        game screen — read into clean_next_time once it scores well
        enough, run automatically once _reenter_garden finishes."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        template = load_template(_NEXTSHIFT_TEMPLATE)
        if template is None:
            self._log.add_log("Шаблон не найден", level="error")
            return
        try:
            frame = grab_window(hwnd)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return
        rect = self._wm.window_rect_screen(hwnd)
        if rect is None:
            self._log.add_log("Не удалось определить положение окна игры",
                              level="error")
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score, (x, y) = best_match(gray, template)
        ox, oy, _w, _h = rect
        th, tw = template.shape[:2]
        abs_x, abs_y = ox + x, oy + y

        self._log.add_log_segments(
            [("Поиск времени — ", theme.TEXT_SECONDARY),
             (f"({abs_x}, {abs_y})", theme.GD_OLIVE_SOFT),
             (" — ", theme.TEXT_SECONDARY),
             (f"{score * 100:.1f}%",
              theme.GD_OLIVE_SOFT if score >= _NEXTSHIFT_THRESHOLD
              else theme.ACCENT_RED)],
            level="plain")
        if score < _NEXTSHIFT_THRESHOLD:
            # Too weak a match to trust the crop under it — reading a
            # timer from the wrong spot on screen is worse than not
            # reading one at all, so nothing below is saved or counted.
            return

        crop = frame[y:y + th, x:x + tw]
        cv2.imwrite(str(TEMPLATES_DIR / "gardener_nextshift.png"), crop)

        timer_text = read_timer(crop)
        self._log.add_log_segments(
            [("Распознанное время — ", theme.TEXT_SECONDARY),
             (timer_text if timer_text else "не распознано",
              theme.GD_OLIVE_SOFT if timer_text else theme.ACCENT_RED)],
            level="plain")
        if timer_text and self._stats is not None:
            # Stamps clean_was_time too — whoever displays the countdown
            # (the stats window) works out how much is actually left from
            # wall-clock time elapsed since then, so nothing here needs to
            # keep ticking it down by hand.
            self._stats.set_gardener_next_time(timer_text)

    def _toggle_area_overlay(self):
        """TEMPORARY — a filled rectangle over exactly what gets scanned,
        plus a live look at how much that rectangle changes frame to frame:
        bugs and butterflies keep it moving a little on their own, so the
        point is to watch what a real, much bigger jump looks like when the
        character walks through it.
        """
        if self._area_overlay.isVisible():
            self._stop_tracking()
            return
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return
        area = self._garden_area
        self._area_overlay.show_area(
            QRect(area.left, area.top, area.width, area.height))
        self._start_tracking(hwnd, area)

    def _start_tracking(self, hwnd, area):
        self._log.add_log(
            "Начинаем отслеживать весь экран в режиме реального времени",
            level="plain")
        self._track_moving = False
        try:
            self._track_prev_frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return
        self._track_timer = QTimer(self)
        self._track_timer.timeout.connect(lambda: self._track_tick(hwnd, area))
        self._track_timer.start(_TRACK_MS)

    def _track_tick(self, hwnd, area):
        """Only the edges are worth a line — the moment movement starts and
        the moment it stops — not every second in between it stays true.

        Read straight from the game window itself, not the screen — so
        alt-tabbing away and covering it with something else does not
        start reporting changes in whatever came to the front instead.
        """
        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return

        prev = self._track_prev_frame
        self._track_prev_frame = frame
        if prev is None or prev.shape != frame.shape:
            return

        changed = float(np.abs(frame.astype(np.int16) - prev.astype(np.int16)).mean())
        percent = changed / 255 * 100
        moving = percent >= _TRACK_MOVE_PCT
        if moving == self._track_moving:
            return
        self._track_moving = moving
        self._log.add_log(
            "Игрок начал движение" if moving else "Игрок прекратил движение",
            level="plain")

    def _stop_tracking(self):
        if self._track_timer is not None:
            self._track_timer.stop()
            self._track_timer = None
        self._track_prev_frame = None
        self._track_moving = False
        self._area_overlay.clear()

    def _scan_kind(self, kind):
        """One kind, on its own — for reading its threshold against what
        is actually on screen right now, not the whole garden at once."""
        QTimer.singleShot(0, lambda: self._run_kind_scan(kind))

    def _run_kind_scan(self, kind):
        try:
            found = scan(region=self._garden_area.region, near=_WIDE_NEAR)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return

        items = [item for item in found if item.kind.key == kind.key]
        self._log_kind_scan(kind, items)
        self._markers.show_markers(
            [Marker(item.x, item.y,
                    kind.colour if item.accepted else theme.ACCENT_RED,
                    item.accepted)
             for item in items])

    def _log_kind_scan(self, kind, items: list):
        """The bar first, so a coordinate's percentage means something
        without scrolling back up to look it up. Below that bar is red
        whatever the kind — the colour says pass or fail, not which kind
        this is; that is already said by the button that was pressed.
        """
        self._log.add_log_segments(
            [("Найдено ", theme.TEXT_SECONDARY),
             (kind.plural, kind.colour),
             (" — ", theme.TEXT_SECONDARY),
             (str(len(items)), kind.colour if items else theme.TEXT_DIM),
             (", порог ", theme.TEXT_SECONDARY),
             (f"{kind.threshold * 100:.0f}%", theme.GD_OLIVE_SOFT)],
            level="plain")
        for item in items:
            colour = kind.colour if item.accepted else theme.ACCENT_RED
            self._log.add_log_segments(
                [("   ", theme.TEXT_DIM),
                 (f"({item.x}, {item.y})", colour),
                 (" — ", theme.TEXT_DIM),
                 (f"{item.score * 100:.1f}%", colour)],
                level="plain")
        self._log.blank_line()

    def _log_counts(self, counts: dict):
        """The summary block: the whole haul, then a braced line per kind.

        Shared by the one-off scan and the start of a cleaning run — they
        report the same thing and should not drift apart.
        """
        total = sum(counts.values())
        self._log.add_log_segments(
            [("Найдено мусора — ", theme.GD_TEXT),
             (str(total), theme.GD_OLIVE if total else theme.TEXT_DIM)],
            level="plain")
        for row, kind in enumerate(TRASH_KINDS):
            number = counts.get(kind.key, 0)
            self._log.add_log_segments(
                [(f"   {_brace(row, len(TRASH_KINDS))} ", theme.TEXT_DIM),
                 (f"Найдено {kind.plural} — ", theme.TEXT_SECONDARY),
                 (str(number), kind.colour if number else theme.TEXT_DIM)],
                level="plain")
        self._log.blank_line()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._settings is not None:
            self._settings.keep_inside_host()

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
        if self.parent_overlay:
            self.parent_overlay.add_log_segments([
                (f"Окно {self.module_name} ", theme.TEXT_SECONDARY),
                (verb, colour),
                (tail, theme.TEXT_SECONDARY),
            ])

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    def _teardown(self):
        """Nothing of ours should outlive the window — the dots included."""
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        if self._done_timer is not None:
            self._done_timer.stop()
            self._done_timer = None
        if self._butterfly_timer is not None:
            self._butterfly_timer.stop()
            self._butterfly_timer = None
        if self._catch_timer is not None:
            self._catch_timer.stop()
            self._catch_timer = None
        if self._track_timer is not None:
            self._track_timer.stop()
            self._track_timer = None
        self._markers.clear()
        self._area_overlay.clear()
        self._hunt_box.clear()
