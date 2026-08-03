# modules/janitor/window.py
"""Уборщик — the cleaning loop itself, mirrored down from Садовник's own
window.py: click the nearest/best-scoring trash, watch the character
confirm it, credit the bar, repeat; once the confirmed pile is gone, check
whether the park says it is done, sweep whatever fell short of its own
threshold, and — once it really is done — walk back in through the menus
to read the next shift's countdown off the badge.

No butterflies here, nothing moves, so there is no hunt — just the click,
watch and final-check machinery, and the bookkeeping around it.
"""
from __future__ import annotations

import math
import time

import cv2

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

from app.core.capture import ScreenCapture, grab_window
from app.core.input_sender import click_at
from app.core.template_match import TEMPLATES_DIR, best_match, load_template
from app.ui import theme
from app.ui.marker_overlay import Marker, MarkerOverlay
from app.ui.module_window import ModuleWindow
from app.ui.widgets.jn_panel import JnPanel
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.progress_board import ProgressBoard
from modules.janitor.park_area import FIXED_AREA
from modules.janitor.settings_panel import JanitorSettingsPanel
from modules.janitor.timer_read import read_timer
from modules.janitor.trash import TRASH_KINDS, count_by_kind, scan, score_at

_DEFAULT_W = 380
_DEFAULT_H = 800
_MIN_W     = 320
_MIN_H     = 620

_LOG_H     = 200   # a floor; the log gives up height to the board while a run is on
_LOG_H_RUN = 110
_BOARD_GAP = 8

_START_TEXT = "▶  Запустить бота по уборке"
_STOP_TEXT  = "■  Выключить бота по уборке"

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
    """"Первый", "второй", ... — see Садовник's own copy of this for why."""
    if 1 <= n <= 9:
        return _ORDINAL_ONES[n]
    if 10 <= n <= 19:
        return _ORDINAL_TEENS[n - 10]
    if 20 <= n <= 99:
        tens, ones = divmod(n, 10)
        return _ORDINAL_TENS[tens] if ones == 0 \
            else f"{_CARDINAL_TENS[tens]} {_ORDINAL_ONES[ones]}"
    return f"{n}-й"


# How far below a kind's own bar a match is still worth showing as a near
# miss — used by both the per-kind debug buttons and the final sweep below.
_NEAR_WIDE = 0.65

# How long the helper's own windows stay hidden before a capture-sensitive
# grab actually reads the game — see _hide_own_windows. Long enough for
# Chrome to notice the cover is gone and paint a real frame again; short
# enough that hide-and-show barely reads as a flicker. Only used for the
# one-off grabs (a kind-scan button, the post-run timer read) — the run's
# own continuous watching does not hide anything, the same as Садовник.
_HIDE_MS = 150

# TRIED AND DROPPED (2026-08-03): confirming a click the way Садовник does
# — diffing the whole watched rectangle frame to frame and waiting for it
# to hold still — needs real movement to stand out from background noise.
# In the park it does not: measured live, idle alone (fountain animation,
# sparkle effects, other avatars) read 0.22-1.09%, and a real short walk
# read only 0.36-0.63% — the two overlap, so no threshold anywhere in that
# range can tell "still walking" from "arrived and idling" apart, and the
# still-check kept resetting off ordinary noise instead of ever settling.
# The park is a much bigger rectangle than the garden and the character is
# a small share of it either way, which is what drowns a short walk's own
# signal in the first place.
#
# Confirming the *item* is gone instead — see _poll_item below — asks the
# question directly rather than inferring it from something noisier.
_ITEM_POLL_MS   = 1000
# Its match score has to drop to this share of what it scored when found
# before it counts as actually picked up — some dip from the character
# standing over it is normal and should not by itself read as gone.
_ITEM_GONE_RATIO = 0.6
# How long to poll one target before giving up on it ever disappearing —
# generous, since a walk across the whole park can genuinely take a while.
_ITEM_TIMEOUT_S       = 12.0
_FINAL_ITEM_TIMEOUT_S = 5.0   # the final sweep's own, shorter patience

# "Здесь вся работа завершена" — reused from Садовник's own template: the
# banner is the game's own generic "nothing left here" sign, not specific
# to which location drew it, so the same picture is what to look for here
# too. Checked across the whole game window, once a second, the whole time
# a run is going — whatever else it happens to be doing at that moment.
_DONE_TEMPLATE  = "gardener_done.png"
_DONE_THRESHOLD = 0.85
_DONE_CHECK_MS  = 1000

_POPUP_TEMPLATE  = "button_close.png"
_POPUP_REGION    = {"left": 1174, "top": 714, "width": 212, "height": 133}
_POPUP_THRESHOLD = 0.85
_POPUP_RECHECK_MS = 300
_DROP_RATIO = 0.5

# Re-entering the park once it is done: Places, then Professions, then
# Уборщик — the same three taps a person would make — is what makes the
# timer badge redraw, so the read that follows has something fresh to see.
_NAV_STEPS = [
    ("button_places.png", "Места"),
    ("button_jobs.png",   "Профессии"),
    ("janitor.png",       "Уборщик"),
]
_NAV_THRESHOLD      = 0.85
_NAV_POLL_MS        = 400
_NAV_TIMEOUT_S      = 15.0
_NAV_AFTER_CLICK_MS = 800
# The Уборщик click is the odd one out: it does not just switch a panel,
# it loads the whole location — the badge is not drawn yet a moment later.
_NAV_LOCATION_LOAD_MS = 5000

# Locates the timer badge on screen — reused from Садовник's own
# placeholder: it is the same badge graphic wherever it is shown.
_NEXTSHIFT_TEMPLATE  = "gardener_nextshift_placeholder.png"
_NEXTSHIFT_THRESHOLD = 0.90


class JanitorWindow(ModuleWindow):
    """Уборщик — header, frame, the usual module-window habits, and the
    cleaning loop itself."""

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None,
                stats=None):
        super().__init__("Уборщик", config, save_fn, parent_overlay)
        self._wm = window_manager
        self._stats = stats
        self._running = False
        self._settings: JanitorSettingsPanel | None = None
        self._kind_buttons_anim: QPropertyAnimation | None = None
        self._markers = MarkerOverlay(window_manager, reference=self)
        self._park_area = FIXED_AREA
        self._hidden_for_scan: list = []   # see _hide_own_windows

        self._move_timer: QTimer | None = None
        self._done_timer: QTimer | None = None
        self._watch_started = 0.0   # see _start_watching / _poll_item
        # Every position already given a click, real or not — trash does
        # not regrow mid-run, but a near miss found again in the final
        # sweep would otherwise be tried twice. (kind key, x, y).
        self._handled: set[tuple[str, int, int]] = set()
        self._last_pos: tuple[int, int] | None = None
        self._object_number = 0
        self._done: dict[str, int] = {}   # kind key -> how many actually cleared
        self._final_queue: list = []
        self._board_room = 0

        self.resize(max(getattr(config, "width",  _DEFAULT_W), _MIN_W),
                    max(getattr(config, "height", _DEFAULT_H), _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = JnPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background:{theme.JN_BORDER};")
        layout.addWidget(separator)
        layout.addSpacing(4)

        self._start_btn = NtButton(_START_TEXT, accent=theme.JN_AMBER,
                                   upper=False)
        self._start_btn.clicked.connect(self._toggle_running)
        layout.addWidget(self._start_btn)

        settings_btn = NtButton("⚙  Настройки", accent=theme.JN_WOOD,
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

        drag = NtDragHandle(dot_color=theme.JN_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        self._status_dot = NtStatusDot(accent=theme.JN_AMBER)
        self._status_dot.set_offline()

        title = QLabel("УБОРЩИК")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.JN_AMBER}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.JN_AMBER)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.JN_AMBER_SOFT)
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

        head = QHBoxLayout()
        title = QLabel("Janitor Log:")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.JN_TEXT}; background:transparent;")
        clear_btn = NtButton("⌫", accent=theme.JN_BORDER)
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
            self._put_board_away()

    def _build_kind_buttons(self) -> QHBoxLayout:
        """One button per kind — where it is, how well it matched, and the
        bar it is being judged against right now. Hidden behind the
        Settings switch by default."""
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)
        for kind in TRASH_KINDS:
            btn = NtButton(kind.singular, accent=kind.colour, upper=False)
            btn.clicked.connect(
                lambda _checked=False, k=kind: self._scan_kind(k))
            row.addWidget(btn)
        return row

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

    # ── Hiding ourselves for a capture-sensitive grab ───────────────────────

    def _hide_own_windows(self) -> list:
        """Every window the helper owns that is actually up right now —
        this one, Садовник, Статистика, the launcher bar, our own markers
        — hidden so a capture right after does not read one of them
        instead of the park. See _scan_kind's own note on why grab_window
        alone is not enough. Returns what it hid, to hand back to
        _restore_hidden_windows once the grab is done.
        """
        own = self.parent_overlay.own_windows() if self.parent_overlay else [self]
        candidates = [*own, self._markers]
        hidden = [w for w in candidates if w.isVisible()]
        for widget in hidden:
            widget.hide()
        return hidden

    def _restore_hidden_windows(self, hidden: list):
        for widget in hidden:
            widget.show()

    # ── Per-kind debug scan ──────────────────────────────────────────────────

    def _scan_kind(self, kind):
        """One kind, on its own — for reading its threshold against what
        is actually on screen right now, not the whole park at once."""
        self._hidden_for_scan = self._hide_own_windows()
        QTimer.singleShot(_HIDE_MS, lambda: self._run_kind_scan(kind))

    def _run_kind_scan(self, kind):
        try:
            hwnd = self._wm.get_game_hwnd()
            if not hwnd:
                self._log.add_log("Игровое окно не найдено", level="error")
                return
            try:
                found = scan(region=self._park_area.region, near=_NEAR_WIDE,
                            hwnd=hwnd)
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
        finally:
            self._restore_hidden_windows(self._hidden_for_scan)
            self._hidden_for_scan = []

    def _log_kind_scan(self, kind, items: list):
        self._log.add_log_segments(
            [("Найдено ", theme.TEXT_SECONDARY),
             (kind.plural, kind.colour),
             (" — ", theme.TEXT_SECONDARY),
             (str(len(items)), kind.colour if items else theme.TEXT_DIM),
             (", порог ", theme.TEXT_SECONDARY),
             (f"{kind.threshold * 100:.0f}%", theme.JN_AMBER_SOFT)],
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

    # ── Cleaning ─────────────────────────────────────────────────────────────

    def _toggle_running(self):
        if self._running:
            self._stop_cleaning("Бот остановлен")
            return
        self._begin_cleaning()

    def _begin_cleaning(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log_start(False)
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        try:
            found = scan(region=self._park_area.region, hwnd=hwnd)
        except Exception as exc:
            self._log_start(False)
            self._log.add_log(str(exc), level="error")
            return

        counts = count_by_kind(found)
        self._log_start(True)
        self._log_to_helper("Начинаем работу уборщика")

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

        self._handle_next_object(hwnd)

    def _log_start(self, ok: bool):
        self._log.add_log_segments(
            [("Начинаем уборку — ", theme.TEXT_SECONDARY),
             ("успешно" if ok else "не успешно",
              theme.ACCENT_GREEN if ok else theme.ACCENT_RED)],
            level="plain")

    def _log_to_helper(self, message: str):
        """Only start and finish go to the helper's own log — [Уборщик]
        marking whose line it is, the same convention Садовник uses."""
        if not self.parent_overlay:
            return
        self.parent_overlay.add_log_segments(
            [("[Уборщик] ", theme.JN_AMBER),
             (message, theme.TEXT_SECONDARY)],
            level="plain")

    def _handle_next_object(self, hwnd: int):
        if not self._running:
            return

        try:
            found = scan(region=self._park_area.region, hwnd=hwnd)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        target = self._pick_target([item for item in found if item.accepted])
        self._log_search_result(target is not None, self._object_number + 1)
        if target is None:
            self._finish_main_sweep(hwnd)
            return

        self._handled.add((target.kind.key, target.x, target.y))
        self._last_pos = (target.x, target.y)
        self._object_number += 1
        self._show_dots([target])

        self._log.add_log("Идём убирать объект", level="plain")
        click_at(hwnd, target.x, target.y)
        self._start_watching(target, hwnd,
                             lambda: self._handle_next_object(hwnd))

    def _pick_target(self, accepted: list):
        """Nearest to the last pick, blended with how well it matched —
        half and half, so the walk both keeps a straight-ish line *and*
        clears the strongest matches first rather than dragging itself to
        whichever near-100% piece happens to be furthest away. The very
        first pick, with nowhere to measure a distance from yet, just
        takes the best match on screen.
        """
        fresh = [item for item in accepted
                if (item.kind.key, item.x, item.y) not in self._handled]
        if not fresh:
            return None
        if self._last_pos is None:
            return max(fresh, key=lambda item: item.score)

        lx, ly = self._last_pos
        max_dist = math.hypot(self._park_area.width, self._park_area.height)

        def cost(item):
            dist = math.hypot(item.x - lx, item.y - ly) / max_dist
            return 0.5 * dist + 0.5 * (1.0 - item.score)

        return min(fresh, key=cost)

    def _credit(self, key: str):
        self._done[key] = self._done.get(key, 0) + 1
        self._board.advance(key, self._done[key], sum(self._done.values()))

    def _log_search_result(self, found: bool, number: int):
        self._log.add_log_segments(
            [(f"Ищем {_ordinal_ru(number)} объект — ", theme.TEXT_SECONDARY),
             ("Объект найден" if found else "Объект не найден",
              theme.ACCENT_GREEN if found else theme.ACCENT_RED)],
            level="plain")

    def _show_dots(self, found: list):
        self._markers.show_markers(
            [Marker(item.x, item.y, item.kind.colour, True)
             for item in found if item.accepted])

    # ── Watching a click land ────────────────────────────────────────────────
    # Confirmed by polling the clicked item's own match score, not by
    # inferring it from character movement — see the note above
    # _ITEM_POLL_MS for why the movement-diff approach was dropped.

    def _start_watching(self, target, hwnd: int, on_done,
                        timeout_s: float = _ITEM_TIMEOUT_S):
        self._watch_started = time.monotonic()
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._poll_item(target, hwnd, on_done, timeout_s))
        self._move_timer.start(_ITEM_POLL_MS)

    def _poll_item(self, target, hwnd: int, on_done, timeout_s: float):
        if not self._running:
            return

        self._check_popup(hwnd)

        # Re-sent every tick, not just once at the start: the same score
        # holding dead level, tick after tick, with not even the usual
        # jitter a real still-there object reads with, is what a click
        # that never actually landed looks like — clicking the same spot
        # again costs nothing if the first one did land, and is the only
        # way to recover if it did not.
        click_at(hwnd, target.x, target.y)

        try:
            frame = grab_window(hwnd, self._park_area.region)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            self._stop_cleaning(None)
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        origin = (self._park_area.left, self._park_area.top)
        score = score_at(target.kind, gray, target.x, target.y, origin)
        gone = score < target.score * _ITEM_GONE_RATIO

        self._log.add_log_segments(
            [("   объект на месте: ", theme.TEXT_DIM),
             (f"{score * 100:.0f}%", theme.ACCENT_GREEN if gone else theme.TEXT_DIM),
             (f" (было {target.score * 100:.0f}%)", theme.TEXT_DIM)],
            level="plain")

        if gone:
            self._finish_target(target, on_done)
            return

        if time.monotonic() - self._watch_started >= timeout_s:
            self._reject_target(target, on_done)
            return

        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(
            lambda: self._poll_item(target, hwnd, on_done, timeout_s))
        self._move_timer.start(_ITEM_POLL_MS)

    def _finish_target(self, target, on_done):
        self._log.add_log_segments(
            [("Игрок подошёл к объекту №", theme.TEXT_SECONDARY),
             (str(self._object_number), theme.JN_AMBER),
             (" — убираем...", theme.TEXT_SECONDARY)],
            level="plain")
        self._log.add_log("Объект убран", level="plain")
        try:
            self._credit(target.kind.key)
        except Exception as exc:
            # Never let a bookkeeping slip silently end the run — Qt swallows
            # an exception raised here to stderr, which nobody watching the
            # in-app log would ever see, and the walk would just stop dead
            # with no explanation.
            self._log.add_log(f"Ошибка при зачёте объекта: {exc}", level="error")
        QTimer.singleShot(0, on_done)

    def _reject_target(self, target, on_done):
        self._log.add_log_segments(
            [("Игрок стоит — ", theme.TEXT_SECONDARY),
             ("объект фейк", theme.ACCENT_RED)],
            level="plain")
        self._write_off(target, on_done)

    def _write_off(self, target, on_done):
        try:
            self._board.dec_total(target.kind.key)
        except Exception as exc:
            self._log.add_log(f"Ошибка при списании объекта: {exc}", level="error")
        QTimer.singleShot(0, on_done)

    def _check_popup(self, hwnd: int):
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
            return
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

    # ── Finishing the confirmed pile, then the leftovers ────────────────────

    def _finish_main_sweep(self, hwnd: int):
        self._markers.clear()
        self._log.add_log("Убрали основной мусор, проверяем закончили ли работу",
                          level="plain")
        if self._check_done(hwnd):
            return
        self._log.add_log("Заканчиваем обход спрятанного мусора", level="plain")
        self._start_final_check(hwnd)

    def _start_final_check(self, hwnd: int):
        """Every near miss the main sweep had to skip — best score first,
        on the theory that the closest near misses are the ones most
        likely to actually be trash rather than noise."""
        if not self._running:
            return
        try:
            found = scan(region=self._park_area.region, near=_NEAR_WIDE,
                        hwnd=hwnd)
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return
        self._final_queue = sorted(
            (item for item in found
             if (item.kind.key, item.x, item.y) not in self._handled),
            key=lambda item: -item.score)
        self._show_final_markers()
        self._handle_final_next(hwnd)

    def _handle_final_next(self, hwnd: int):
        if not self._running:
            return
        if not self._final_queue:
            # Nothing left below the bar either — the background check
            # (_check_done, once a second) is what actually ends the run;
            # this just keeps looking in the meantime rather than giving up.
            QTimer.singleShot(_DONE_CHECK_MS,
                              lambda: self._start_final_check(hwnd))
            return

        target = self._final_queue.pop(0)
        self._handled.add((target.kind.key, target.x, target.y))
        self._object_number += 1
        self._show_final_markers(current=target)

        self._log.add_log_segments(
            [("Проверяем невошедшую координату ", theme.TEXT_SECONDARY),
             (f"({target.x}, {target.y})", theme.JN_AMBER_SOFT),
             (" — ", theme.TEXT_SECONDARY),
             (f"{target.score * 100:.0f}%", theme.JN_AMBER_SOFT),
             (" (", theme.TEXT_SECONDARY),
             (target.kind.singular, target.kind.colour),
             (")", theme.TEXT_SECONDARY)],
            level="plain")
        click_at(hwnd, target.x, target.y)
        self._start_watching(target, hwnd,
                             lambda: self._handle_final_next(hwnd),
                             timeout_s=_FINAL_ITEM_TIMEOUT_S)

    def _show_final_markers(self, current=None):
        markers = [Marker(item.x, item.y, theme.ACCENT_RED, False)
                  for item in self._final_queue]
        if current is not None:
            markers.append(Marker(current.x, current.y, theme.ACCENT_WHITE, True))
        self._markers.show_markers(markers)

    def _check_done(self, hwnd: int) -> bool:
        """The game's own word that the park is finished — checked on its
        own clock the whole time a run is going, whatever else it happens
        to be doing at that moment, plus explicitly right after the main
        sweep empties out. True means the run has already been finished
        and torn down by this call.
        """
        if not self._running:
            return False
        try:
            gray = cv2.cvtColor(grab_window(hwnd), cv2.COLOR_BGR2GRAY)
        except Exception:
            return False   # a dropped frame here is not worth stopping the run over
        template = load_template(_DONE_TEMPLATE)
        if template is None:
            return False
        score, _loc = best_match(gray, template)
        if score < _DONE_THRESHOLD:
            return False
        self._finish_run(hwnd)
        return True

    def _finish_run(self, hwnd: int):
        self._stop_cleaning("Работа завершена")
        if self._stats is not None:
            self._stats.record_janitor_cleanup()
        self._log_to_helper(
            "Завершена уборка в парке, определяем время до следующей")
        self._reenter_park()

    def _stop_cleaning(self, message: str | None):
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        if self._done_timer is not None:
            self._done_timer.stop()
            self._done_timer = None
        self._running = False
        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_offline()
        self._markers.clear()
        if message:
            self._log.add_log(message, level="plain")

    # ── Room for the bars ────────────────────────────────────────────────────

    def _make_room_for_board(self):
        wanted = self._board.wanted_height()
        wanted += _BOARD_GAP if wanted else 0
        delta = wanted - self._board_room
        if not delta:
            return
        self._board_room = wanted

        self._log.setMinimumHeight(_LOG_H_RUN if wanted else _LOG_H)

        room = self.screen().availableGeometry().height() if self.screen() else 0
        height = max(_MIN_H, self.height() + delta)
        self.resize(self.width(), min(height, room) if room else height)

    def _put_board_away(self):
        self._board.clear()
        self._make_room_for_board()

    # ── Re-entering the park and reading the next shift ─────────────────────

    def _reenter_park(self):
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
                delay, lambda: self._nav_step(hwnd, next_step, time.monotonic()))
            return

        if time.monotonic() - started >= _NAV_TIMEOUT_S:
            self._log.add_log(f"Не удалось найти «{label}» — переход прерван",
                              level="error")
            return
        QTimer.singleShot(_NAV_POLL_MS,
                          lambda: self._nav_step(hwnd, step, started))

    def _run_next_shift_search(self):
        """Where the timer badge is found on the whole game screen — read
        into clean_next_time once it scores well enough. Hidden the same
        way a kind-scan is: a one-off grab, worth the brief flicker to
        make sure nothing of ours is what gets read instead of the badge.
        """
        hidden = self._hide_own_windows()
        QTimer.singleShot(_HIDE_MS, lambda: self._do_next_shift_search(hidden))

    def _do_next_shift_search(self, hidden: list):
        try:
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
                 (f"({abs_x}, {abs_y})", theme.JN_AMBER_SOFT),
                 (" — ", theme.TEXT_SECONDARY),
                 (f"{score * 100:.1f}%",
                  theme.JN_AMBER_SOFT if score >= _NEXTSHIFT_THRESHOLD
                  else theme.ACCENT_RED)],
                level="plain")
            if score < _NEXTSHIFT_THRESHOLD:
                return

            crop = frame[y:y + th, x:x + tw]
            cv2.imwrite(str(TEMPLATES_DIR / "janitor_nextshift.png"), crop)

            timer_text = read_timer(crop)
            self._log.add_log_segments(
                [("Распознанное время — ", theme.TEXT_SECONDARY),
                 (timer_text if timer_text else "не распознано",
                  theme.JN_AMBER_SOFT if timer_text else theme.ACCENT_RED)],
                level="plain")
            if timer_text and self._stats is not None:
                self._stats.set_janitor_next_time(timer_text)
        finally:
            self._restore_hidden_windows(hidden)

    # ── Settings ─────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = JanitorSettingsPanel(self.config, self.save_fn,
                                                  self)
            self._settings.kind_buttons_toggled.connect(
                self._set_kind_buttons_visible)
        self._settings.toggle()

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
        """Nothing of ours should outlive the window — the trash markers
        included."""
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        if self._done_timer is not None:
            self._done_timer.stop()
            self._done_timer = None
        self._markers.clear()
