# modules/gardener/window.py
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from datetime import datetime

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
from modules.gardener.trash import (TRASH_KINDS, count_by_kind, scan,
                                    score_at)

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


_START_TEXT = "▶  Запустить бота по уборке"
_STOP_TEXT  = "■  Выключить бота по уборке"

# How often the one object being worked is checked, once it has been clicked.
_POLL_MS = 2000

# A click on litter that was never really there does not send the
# character walking — so before settling in to watch its score, a click
# gets this many seconds, one look a second, to show the screen moving at
# all. Nothing seen moving in that stretch means the object was misread,
# not missed; _TRACK_MOVE_PCT (defined above) is the same line the manual
# "Определить игрока" check uses to call something movement.
_MOVE_CHECK_TICKS = 5
_MOVE_CHECK_MS    = 1000

# Looks spent on one object with the score never once reaching the drop
# below before it is set aside. Not "the reading never changes" — a real
# screen is never that quiet, and a character walking near an object it has
# not yet reached wobbles the score up and down without it ever answering
# the one question that matters: is it actually on its way to being gone.
_STUCK_LOOKS = 10

# A drop counts as the object being gone once its score has at least halved
# from what it was when found — noise alone does not do that.
_DROP_RATIO = 0.5

# Some kinds barely look different clean vs dirty, so _DROP_RATIO's halving
# never fires for them and every one of them rides out the full
# _STUCK_LOOKS before being deferred for nothing. Calibrated from a real
# run (2026-08-03) where one settled at 87.0% → 82.8%, a ~5% drop, and held
# there — a smaller drop counts too, once it has stopped moving rather
# than just been read once: the last _SETTLE_LOOKS readings within
# _SETTLE_SPREAD of each other, at or below _SETTLE_RATIO of the opening
# score.
_SETTLE_RATIO  = 0.96
_SETTLE_LOOKS  = 3
_SETTLE_SPREAD = 0.02

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
        self._poll_timer: QTimer | None = None
        self._move_timer: QTimer | None = None
        self._track_timer: QTimer | None = None
        self._track_prev_frame = None
        self._track_moving = False
        # Objects the main circle never got a result on: set aside rather
        # than given up on, and tried once more in a second circle once
        # nothing fresh is left. (kind key, x, y).
        self._deferred: list[tuple[str, int, int]] = []
        # Every position the main circle has already taken a first try at,
        # cleared or not. Litter can regrow in the same spot before the
        # circle is done, and without this a spot that keeps refilling
        # would look "fresh" forever — the circle would never run out of
        # things to do there and never reach the ones it hasn't touched
        # at all yet. (kind key, x, y).
        self._handled: set[tuple[str, int, int]] = set()
        self._round = 1
        self._object_number = 0        # running count, kept across both circles
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
        """Debug pass: deal with objects one at a time, leftmost first.

        Each one is clicked exactly once and watched until it is gone or it
        stalls out — either way, the next one is then looked for the same
        way, until a look turns up nothing left to try in this circle. What
        stalled is not given up on outright: once the whole garden has had
        this first pass, a second circle goes back over exactly those and
        gives each one more try before the run is done.
        """
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
        self._log_counts(counts)
        self._log_start(True)

        self._running = True
        self._deferred = []
        self._handled = set()
        self._round = 1
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
        """Look again, and deal with whatever this circle leaves to try."""
        if not self._running:
            return      # stopped in between two objects

        try:
            found = scan(region=area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        target = self._pick_target([item for item in found if item.accepted])

        if target is None and self._round == 1 and self._deferred:
            self._log_round_done()
            self._round = 2
            QTimer.singleShot(0, lambda: self._handle_next_object(hwnd, area))
            return

        self._log_search_result(target is not None)
        if target is None:
            self._stop_cleaning(None)
            return

        self._handled.add((target.kind.key, target.x, target.y))
        self._object_number += 1
        self._log_target(target, self._object_number)
        self._show_dots([target])

        self._log.add_log("Идём убирать объект", level="plain")
        click_at(hwnd, target.x, target.y)
        self._verify_movement(target, area, hwnd, 0, None)

    def _pick_target(self, accepted: list):
        """The main circle takes whatever hasn't been tried yet, leftmost
        first — a position already given a try does not count again even
        if the same spot keeps refilling, or the circle would never run
        out of things to do there. The second circle takes only what
        stalled in the main one — each such object gets exactly the one
        extra try, so it comes off the list the moment it is picked rather
        than when it succeeds or fails again.
        """
        if self._round == 1:
            fresh = [item for item in accepted
                    if (item.kind.key, item.x, item.y) not in self._handled]
            return min(fresh, key=lambda item: (item.x, item.y)) if fresh \
                else None

        left = [item for item in accepted if self._is_deferred(item)]
        if not left:
            return None
        target = min(left, key=lambda item: (item.x, item.y))
        self._deferred.remove((target.kind.key, target.x, target.y))
        return target

    def _is_deferred(self, item) -> bool:
        return (item.kind.key, item.x, item.y) in self._deferred

    def _credit(self, key: str):
        """One more of that kind gone — as seen, not as clicked."""
        self._done[key] = self._done.get(key, 0) + 1
        self._board.advance(key, self._done[key], sum(self._done.values()))

    def _log_round_done(self):
        self._log.add_log_segments(
            [("Основной круг завершён — ", theme.TEXT_SECONDARY),
             ("начинаем второй круг с неуспешными объектами",
              theme.ACCENT_AMBER)],
            level="plain")

    def _log_start(self, ok: bool):
        self._log.add_log_segments(
            [("Начинаем уборку — ", theme.TEXT_SECONDARY),
             ("успешно" if ok else "не успешно",
              theme.ACCENT_GREEN if ok else theme.ACCENT_RED)],
            level="plain")

    def _log_search_result(self, found: bool):
        self._log.add_log_segments(
            [("Ищем первый объект — ", theme.TEXT_SECONDARY),
             ("Объект найден" if found else "Объект не найден",
              theme.ACCENT_GREEN if found else theme.ACCENT_RED)],
            level="plain")

    def _log_target(self, target, number: int):
        self._log.add_log_segments(
            [("Выбираем объект №", theme.TEXT_SECONDARY),
             (str(number), theme.GD_OLIVE),
             (" — ", theme.TEXT_SECONDARY),
             (target.kind.singular, target.kind.colour),
             (", координаты — ", theme.TEXT_SECONDARY),
             (f"({target.x}, {target.y})", theme.GD_OLIVE_SOFT),
             (", топ ", theme.TEXT_SECONDARY),
             (f"{target.score * 100:.1f}%", theme.GD_OLIVE_SOFT)],
            level="plain")

    def _verify_movement(self, target, area: Area, hwnd: int,
                         attempts: int, prev_frame):
        """A click on litter that was never really there does not send the
        character walking anywhere — the screen keeps doing whatever it was
        already doing (bugs, butterflies) and nothing more. So before
        settling in to watch the object's own score, this watches the whole
        picture for a second at a time, the same way the manual "Определить
        игрока" check does, and only moves on to that once something has
        actually moved.
        """
        if not self._running:
            return      # stopped mid-check

        try:
            frame = grab_window(hwnd, area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        if prev_frame is not None and prev_frame.shape == frame.shape:
            changed = float(np.abs(
                frame.astype(np.int16) - prev_frame.astype(np.int16)).mean())
            if changed / 255 * 100 >= _TRACK_MOVE_PCT:
                self._poll_target(target, area, hwnd, 0)
                return

        attempts += 1
        if attempts >= _MOVE_CHECK_TICKS:
            self._reject_target(target, area, hwnd)
            return

        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._verify_movement(target, area, hwnd, attempts, frame))
        self._move_timer.start(_MOVE_CHECK_MS)

    def _reject_target(self, target, area: Area, hwnd: int):
        """The click landed, but nothing about the screen ever changed more
        than bugs and butterflies already do on their own — read as a
        misdetection rather than real litter, so it is dropped for good
        rather than deferred to a second circle that would only click it
        again for the same nothing.
        """
        self._log.add_log_segments(
            [("Игрок не стал двигаться — ", theme.TEXT_SECONDARY),
             ("определили неверно", theme.ACCENT_RED)],
            level="plain")
        self._board.dec_total(target.kind.key)
        QTimer.singleShot(0, lambda: self._handle_next_object(hwnd, area))

    def _poll_target(self, target, area: Area, hwnd: int, attempts: int,
                     recent: list[float] | None = None):
        """Every couple of seconds, look at just this one spot.

        A sharp drop from what it scored when found is the object going —
        the game does not remove it instantly, so this is checked on a
        stretched-out clock instead of the tight one a real run would use.
        `attempts` counts looks, not "the same reading again" — a real
        screen wobbles too much on its own for two consecutive numbers to
        ever be trusted to mean nothing happened. `recent` is the last few
        readings, kept to tell a real settle apart from that same wobble
        when the drop itself is too small for _DROP_RATIO to ever fire.
        """
        if not self._running:
            return      # stopped mid-wait

        self._check_popup(hwnd)

        try:
            gray = cv2.cvtColor(ScreenCapture.get().grab(area.region),
                                cv2.COLOR_BGR2GRAY)
            score = score_at(target.kind, gray, target.x, target.y,
                             (area.left, area.top))
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        self._log.add_log_segments(
            [("Состояние — ", theme.TEXT_SECONDARY),
             (f"{score * 100:.1f}%", theme.GD_OLIVE_SOFT)],
            level="plain")

        recent = (recent or [])[-(_SETTLE_LOOKS - 1):] + [score]

        if score <= target.score * _DROP_RATIO or (
                len(recent) == _SETTLE_LOOKS
                and score <= target.score * _SETTLE_RATIO
                and max(recent) - min(recent) <= _SETTLE_SPREAD):
            self._log.add_log_segments(
                [("Удалили объект", theme.ACCENT_GREEN)], level="plain")
            self._credit(target.kind.key)
            QTimer.singleShot(0, lambda: self._handle_next_object(hwnd, area))
            return

        attempts += 1
        if attempts >= _STUCK_LOOKS:
            head = ("Откладываем на второй круг — " if self._round == 1
                    else "Не поддалось и во втором круге — ")
            self._log.add_log_segments(
                [(head, theme.TEXT_SECONDARY),
                 (f"процент не упал за {_STUCK_LOOKS} проверок",
                  theme.ACCENT_AMBER)],
                level="plain")
            if self._round == 1:
                self._deferred.append((target.kind.key, target.x, target.y))
            QTimer.singleShot(0, lambda: self._handle_next_object(hwnd, area))
            return

        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(
            lambda: self._poll_target(target, area, hwnd, attempts, recent))
        self._poll_timer.start(_POLL_MS)

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
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
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
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        if self._track_timer is not None:
            self._track_timer.stop()
            self._track_timer = None
        self._markers.clear()
        self._area_overlay.clear()
