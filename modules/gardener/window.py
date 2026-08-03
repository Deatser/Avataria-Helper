# modules/gardener/window.py
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from datetime import datetime
import time

from PySide6.QtCore import Qt, QRect, QTimer

import cv2
import numpy as np

from app.core.capture import ScreenCapture, grab_window
from app.core.input_sender import click_at
from app.core.template_match import best_match, load_template
from app.ui import theme
from app.ui.area_overlay import AreaOverlay
from app.ui.marker_overlay import Marker, MarkerOverlay
from app.ui.module_window import ModuleWindow
from app.ui.widgets.gd_panel import GdPanel
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.progress_board import ProgressBoard
from modules.gardener.garden_area import Area, FIXED_AREA
from modules.gardener.settings_panel import GardenerSettingsPanel
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
    """Where a butterfly is heading, judged from as much of its recent
    trail as there has been time to collect. Velocity comes from the
    oldest and newest points in the trail — the longer that span, the
    less a single noisy reading can throw it off — but the prediction
    itself only reaches one more step past the last point, the same gap
    as between the last two looks, since that is roughly how far off a
    click actually landing is.
    """
    t_last, x_last, y_last = history[-1]
    t_first, x_first, y_first = history[0]
    span = t_last - t_first
    if span <= 0:
        return x_last, y_last
    lead = t_last - history[-2][0]
    vx = (x_last - x_first) / span
    vy = (y_last - y_first) / span
    return int(x_last + vx * lead), int(y_last + vy * lead)


def _clamp(value: int, origin: int, span: int) -> int:
    """Keeps an extrapolated point from landing outside the garden — a
    butterfly predicted past the edge is still clicked at the edge."""
    return max(origin, min(origin + span - 1, value))


_START_TEXT = "▶  Запустить бота по уборке"
_STOP_TEXT  = "■  Выключить бота по уборке"

# A score is never read after a click — the walk itself is the
# confirmation. One look a second, for as long as it takes, watching for
# the screen to hold still for _STILL_TICKS seconds running. A single
# noisy dip is not enough to end the wait, only a real streak of them —
# a walking character never produces that many quiet readings in a row,
# so nothing short of an actual stop or an actual fake ever reaches it.
# _TRACK_MOVE_PCT (defined above) is the same line the manual "Определить
# игрока" check uses to call something movement.
_WATCH_MS    = 1000
_STILL_TICKS = 5

# A drop counts as the object being gone once its score has at least halved
# from what it was when found — used only by the popup closer now that
# litter itself is confirmed by watching the character instead.
_DROP_RATIO = 0.5

# TEMPORARY — the click lands, but nothing is done yet with whether it
# actually caught anything. Full-garden, 26-template searches many times
# a second were too slow to keep up with themselves, so this is a
# compromise: faster than once a second, not so fast it lags again.
_HUNT_MS = 400

# A butterfly is never where it was first seen by the time a click could
# reach it. Rather than one fresh close-up look after the fact, every
# search tick that finds one is kept as a trail; once there is enough of
# a trail to judge a direction from (_HUNT_MIN_HISTORY points, capped at
# _HUNT_HISTORY so an old trail never outweighs a fresh one), the click
# goes to where that trail says it is heading, not where it already was.
_HUNT_HISTORY     = 4
_HUNT_MIN_HISTORY = 3

# How long a click on a butterfly is given before it is judged one way or
# the other — its own clock, separate from litter's _WATCH_MS/_STILL_TICKS,
# since a butterfly that got away is not worth watching for as long as a
# bush might be worth waiting on.
_CATCH_WATCH_MS    = 500
_CATCH_STILL_TICKS = 3

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

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Садовник", config, save_fn, parent_overlay)
        self._wm = window_manager
        self._running  = False
        self._settings: GardenerSettingsPanel | None = None
        # The marks the gardener walks: a dot on each piece of litter, in the
        # colour of its kind, over the game window and nowhere else.
        self._markers  = MarkerOverlay(window_manager, reference=self)
        # TEMPORARY — a debug toggle to check the scanned rectangle against
        # the game by eye; take it back out once that is confirmed good.
        self._area_overlay = AreaOverlay(window_manager, reference=self)
        # The garden's own rectangle — calibrated by hand once and fixed;
        # every grab is pointed at this instead of the whole screen.
        self._garden_area: Area = FIXED_AREA
        self._move_timer: QTimer | None = None
        self._butterfly_timer: QTimer | None = None
        self._catch_timer: QTimer | None = None
        self._hunting = False   # true across both the search and watch halves
        self._hunt_history: list[tuple[float, int, int]] = []
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

        scan_btn = NtButton("◎  Определить мусор", accent=theme.GD_OLIVE_SOFT,
                            upper=False)
        scan_btn.clicked.connect(self._scan_trash)
        layout.addWidget(scan_btn)

        player_btn = NtButton("▭  Определить игрока", accent=theme.ACCENT_AMBER,
                              upper=False)
        player_btn.clicked.connect(self._toggle_area_overlay)
        layout.addWidget(player_btn)

        layout.addLayout(self._build_kind_buttons())

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
        it is being judged against right now."""
        block = QVBoxLayout()
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

        head = QHBoxLayout()
        title = QLabel("Garden Log:")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.GD_TEXT}; background:transparent;")
        clear_btn = NtButton("⌫", accent=theme.GD_BORDER)
        clear_btn.setFixedSize(22, 20)
        clear_btn.setToolTip("Очистить логи")
        clear_btn.clicked.connect(self._clear_log)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(clear_btn)
        block.addLayout(head)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)
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

        self._handle_next_object(hwnd, area)

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
        self._start_watching(target, area, hwnd)

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

    def _log_search_result(self, found: bool, number: int):
        self._log.add_log_segments(
            [(f"Ищем {_ordinal_ru(number)} объект — ", theme.TEXT_SECONDARY),
             ("Объект найден" if found else "Объект не найден",
              theme.ACCENT_GREEN if found else theme.ACCENT_RED)],
            level="plain")

    def _start_watching(self, target, area: Area, hwnd: int):
        """A baseline frame, right as the click lands — nothing is compared
        to it yet, since it is the only frame there is so far."""
        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._watch_target(target, area, hwnd, frame, 0, False))
        self._move_timer.start(_WATCH_MS)

    def _watch_target(self, target, area: Area, hwnd: int, prev_frame,
                      still_streak: int, did_move: bool):
        """One look a second, for as long as it takes, until the screen has
        held still for _STILL_TICKS seconds running.

        Whether it ever moved before that streak began decides what the
        stillness means: litter that was never real never sends the
        character anywhere, so a streak with no movement in it at all is a
        misdetection; litter that did gets walked to and then stood over,
        so a streak that follows real movement is the character having
        arrived. Any movement during the streak resets it — a single
        quiet reading is never enough on its own, in either direction.
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

        if changed >= _TRACK_MOVE_PCT:
            did_move = True
            still_streak = 0
        else:
            still_streak += 1

        if still_streak >= _STILL_TICKS:
            if did_move:
                self._finish_target(target, area, hwnd)
            else:
                self._reject_target(target, area, hwnd)
            return

        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._watch_target(
                target, area, hwnd, frame, still_streak, did_move))
        self._move_timer.start(_WATCH_MS)

    def _finish_target(self, target, area: Area, hwnd: int):
        self._log.add_log_segments(
            [("Игрок подошёл к объекту №", theme.TEXT_SECONDARY),
             (str(self._object_number), theme.GD_OLIVE),
             (" — убираем...", theme.TEXT_SECONDARY)],
            level="plain")
        self._log.add_log("Объект убран", level="plain")
        self._credit(target.kind.key)
        QTimer.singleShot(0, lambda: self._handle_next_object(hwnd, area))

    def _reject_target(self, target, area: Area, hwnd: int):
        """The click landed, but the character never set off toward it —
        read as a misdetection rather than real litter, so it is dropped
        for good rather than tried again for the same nothing."""
        self._log.add_log_segments(
            [("Игрок стоит — ", theme.TEXT_SECONDARY),
             ("объект фейк", theme.ACCENT_RED)],
            level="plain")
        self._write_off(target, hwnd, area)

    def _write_off(self, target, hwnd: int, area: Area):
        self._board.dec_total(target.kind.key)
        QTimer.singleShot(0, lambda: self._handle_next_object(hwnd, area))

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

    def _resume_search(self, hwnd: int, area: Area):
        """Only while nothing is already being clicked and watched —
        search and watch never run at once, so a found butterfly is never
        clicked twice over. The trail from whatever was being chased
        before does not belong to whatever turns up next."""
        self._hunt_history.clear()
        self._butterfly_timer = QTimer(self)
        self._butterfly_timer.timeout.connect(lambda: self._hunt_tick(hwnd, area))
        self._butterfly_timer.start(_HUNT_MS)

    def _hunt_tick(self, hwnd: int, area: Area):
        """Every tick a butterfly is seen adds to its trail rather than
        acting right away — only once that trail is long enough to judge
        a direction from does a click actually go out, aimed ahead of the
        trail rather than at it.
        """
        try:
            found = scan(region=area.region, kinds=[BUTTERFLY])
        except Exception:
            return   # try again on the next tick
        tracked = [item for item in found if item.accepted]
        if not tracked:
            self._markers.clear()
            self._hunt_history.clear()
            return

        target = min(tracked, key=lambda item: item.x)   # leftmost of them
        self._hunt_history.append((time.monotonic(), target.x, target.y))
        del self._hunt_history[:-_HUNT_HISTORY]

        if len(self._hunt_history) < _HUNT_MIN_HISTORY:
            self._markers.show_markers(
                [Marker(target.x, target.y, BUTTERFLY.colour, True)])
            return   # still building a trail — nothing to aim ahead of yet

        click_x, click_y = _extrapolate(self._hunt_history)
        click_x = _clamp(click_x, area.left, area.width)
        click_y = _clamp(click_y, area.top, area.height)
        self._markers.show_markers(
            [Marker(click_x, click_y, BUTTERFLY.colour, True)])

        self._butterfly_timer.stop()
        self._butterfly_timer = None
        click_at(hwnd, click_x, click_y)
        self._log.add_log("Кликнули по бабочке", level="plain")
        self._start_catch_watch(hwnd, area)

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
            gray = cv2.cvtColor(ScreenCapture.get().grab(_POPUP_REGION),
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
            gray = cv2.cvtColor(ScreenCapture.get().grab(_POPUP_REGION),
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

        room = self.screen().availableGeometry().height() if self.screen() else 0
        height = max(_MIN_H, self.height() + delta)
        self.resize(self.width(), min(height, room) if room else height)

    def _put_board_away(self):
        """Take the bars down and give the borrowed height back."""
        self._board.clear()
        self._make_room_for_board()

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = GardenerSettingsPanel(self.config, self.save_fn,
                                                   self)
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
            found = scan(region=self._garden_area.region)
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
