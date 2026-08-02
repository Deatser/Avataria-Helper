# modules/gardener/window.py
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                               QLabel)
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor

from app.ui import theme
from app.ui.marker_overlay import Marker, MarkerOverlay
from app.ui.module_window import ModuleWindow
from app.ui.widgets.gd_panel import GdPanel
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.progress_board import ProgressBoard
from modules.gardener.butterfly import ButterflyTracker, colour_for
from modules.gardener.cleaning import (COOLDOWN_MINUTES, CleaningRun, Job,
                                       format_duration)
from modules.gardener.settings_panel import GardenerSettingsPanel
from modules.gardener.trash import (TRASH_KINDS, count_by_kind, count_kind,
                                    scan)

# TEMPORARY, alongside the per-find log lines: how many near misses are worth
# listing per kind before the log turns into noise.
_NEAR_SHOWN = 6

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

# Five kinds means five rows of these; at the usual button height they would
# cost the window another 90 px.
_GROUP_BTN_H = 24

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
        self._markers  = MarkerOverlay(window_manager, reference=self)
        # Butterflies get a layer of their own: they are tracked all the
        # time, and highlighting some other kind must not wipe them off.
        self._flutter  = MarkerOverlay(window_manager, reference=self)
        self._flutter_shown = True
        self._last_found: list = []
        self._highlighted: tuple | None = None
        self._tracker: ButterflyTracker | None = None
        self._run: CleaningRun | None = None
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

        layout.addLayout(self._build_highlight_grid())

        self._board = ProgressBoard(self._panel)
        layout.addWidget(self._board)

        layout.addLayout(self._build_log(), stretch=1)

        drag = NtDragHandle(dot_color=theme.GD_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

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

    def _build_highlight_grid(self) -> QVBoxLayout:
        """Four buttons, one per group, laid out as a 2x2 so they fit.

        In a column they would cost the window another 140 px of height, and
        the pairing reads better this way anyway: a row per kind, passed on
        the left and failed on the right.
        """
        block = QVBoxLayout()
        block.setSpacing(4)

        caption = QLabel("Подсветить найденное:")
        caption.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        caption.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        block.addWidget(caption)

        grid = QGridLayout()
        grid.setSpacing(theme.SPACING // 2)
        self._highlight_buttons = {}
        for row, kind in enumerate(TRASH_KINDS):
            for column, ok in enumerate((True, False)):
                mark = "✓" if ok else "✗"
                what = "правильный" if ok else "неправильный"
                button = NtButton(f"{mark}  тип {row + 1}",
                                  accent=self._group_colour(row, ok),
                                  upper=False)
                button.setFixedHeight(_GROUP_BTN_H)   # five rows of these fit
                button.setToolTip(f"Подсветить {what} мусор типа {row + 1} "
                                  f"— {kind.plural}")
                button.clicked.connect(
                    lambda _=False, k=row, o=ok: self._toggle_highlight(k, o))
                self._highlight_buttons[(row, ok)] = button
                grid.addWidget(button, row, column)
        block.addLayout(grid)
        return block

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
        """The dots belong to the lines that named them, so they go together."""
        self._log.clear_logs()
        self._markers.clear()
        if not self._running:
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
        """Find what is there, show the bars, then work through it."""
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        try:
            found = scan()
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return

        self._last_found = found
        counts = count_by_kind(found)
        self._log_counts(counts)
        self._log.add_log("Начинаем уборку", level="plain")

        jobs = [Job(item.kind.key, item.x, item.y)
                for item in found if item.accepted]
        self._board.build(
            len(jobs),
            [(k.key, f"Найдено {k.plural}", counts.get(k.key, 0), k.colour)
             for k in TRASH_KINDS])
        self._make_room_for_board()

        self._running = True
        self._start_btn.setText(_STOP_TEXT)
        self._start_btn.set_active(True)
        self._status_dot.set_running()
        self._start_tracking()

        self._run = CleaningRun(hwnd, jobs, self._recount, self)
        self._run.cleaned.connect(self._board.advance)
        self._run.finished.connect(self._on_cleaned)
        self._run.start()

    def _recount(self, key: str) -> int:
        """How many of that kind are still on screen — the run's own evidence."""
        kind = next(k for k in TRASH_KINDS if k.key == key)
        return count_kind(kind)

    def _on_cleaned(self, seconds: float):
        # The bars are deliberately left where the counting put them: if
        # something could not be cleared, the board should say so rather than
        # round itself up to a tidy 100%.
        left = self._run.total - self._run.done if self._run else 0
        self._next_run_at = datetime.now() + timedelta(minutes=COOLDOWN_MINUTES)
        self._log.add_log_segments(
            [("Уборка завершена за ", theme.TEXT_SECONDARY),
             (format_duration(seconds), theme.GD_OLIVE),
             (", новый цикл будет доступен через ", theme.TEXT_SECONDARY),
             (f"{COOLDOWN_MINUTES} мин", theme.GD_OLIVE_SOFT),
             (f" в {self._next_run_at:%H:%M:%S}", theme.TEXT_SECONDARY)],
            level="plain")
        if left:
            self._log.add_log_segments(
                [("Не удалось убрать — ", theme.TEXT_SECONDARY),
                 (str(left), theme.ACCENT_AMBER)],
                level="plain")
        self._log.blank_line()
        self._stop_cleaning(None)

    def _stop_cleaning(self, message: str | None):
        if self._run is not None:
            self._run.stop()
            self._run = None
        self._running = False
        self._start_btn.setText(_START_TEXT)
        self._start_btn.set_active(False)
        self._status_dot.set_offline()
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
            found = scan()
        except Exception as exc:
            self._log.add_log(str(exc), level="error")
            return

        self._log_counts(count_by_kind(found))

        # No dots of its own: a colour per find was too many colours to read.
        # The four buttons above put them up a group at a time instead.
        self._last_found = found
        self._highlighted = None
        self._markers.clear()
        self._refresh_highlight_buttons()
        # From here until the window closes, butterflies are followed rather
        # than found: one that flies behind something and comes back out is
        # picked up again by itself.
        self._start_tracking()

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

    # ── Highlighting ─────────────────────────────────────────────────────────

    def _group_colour(self, kind_index: int, ok: bool) -> str:
        """The kind's own colour, dimmed for the finds that missed the bar.

        Only one group is ever highlighted, so the colour is free to say
        which kind it is rather than which half of it; passed and failed are
        told apart by shade here and by a filled or hollow dot on screen.
        """
        colour = QColor(TRASH_KINDS[kind_index].colour)
        return colour.name() if ok else colour.darker(175).name()

    def _toggle_highlight(self, kind_index: int, ok: bool):
        """Show one group's dots, or take them down if they are already up.

        The butterfly tracking is never stopped here: it runs from the scan
        until the window closes, on its own layer, so pressing any of these
        does not interrupt it.
        """
        if self._highlighted == (kind_index, ok):
            self._highlighted = None
            self._markers.clear()
            self._refresh_highlight_buttons()
            return

        kind = TRASH_KINDS[kind_index]

        # The butterflies are already being followed; their button only says
        # whether their dots are on screen, and repeats where they are now.
        if kind.key == "butterfly" and ok:
            self._toggle_flutter(kind_index)
            return

        colour = self._group_colour(kind_index, ok)
        items  = self._group_finds(kind, ok)
        self._highlighted = (kind_index, ok)
        self._markers.show_markers(
            [Marker(item.x, item.y, colour, ok) for item in items])
        self._refresh_highlight_buttons()

        what = "правильного" if ok else "неправильного"
        self._log.add_log_segments(
            [(f"{kind.singular}: {what} мусора — ", theme.TEXT_SECONDARY),
             (str(len(items)), colour)],
            level="plain")
        # The coordinates again, so the list can be read without scrolling
        # back to whatever the scan printed.
        for number, item in enumerate(items, 1):
            self._log.add_log_segments(
                [(f"   {'№' + str(number) if ok else '≈'} ", theme.TEXT_DIM),
                 (f"({item.x}, {item.y})", colour),
                 (f" — {100 * item.score:.1f}%", theme.TEXT_SECONDARY)],
                level="plain")
        self._log.blank_line()

    def _group_finds(self, kind, ok: bool) -> list:
        """Exactly what the log printed for this group, in the same order.

        The rejects are cut to the same few the log lists: below the bar
        there can be dozens of weak matches, and putting a dot on every one
        of them buries the screen in colour instead of pointing at anything.
        """
        items = [f for f in self._last_found
                 if f.kind is kind and f.accepted == ok]
        return items if ok else items[:_NEAR_SHOWN]

    # ── Butterflies ──────────────────────────────────────────────────────────

    def _start_tracking(self):
        """Follow the butterflies until the window goes away. Idempotent."""
        if self._tracker is not None:
            return
        kind = self._butterfly_kind()
        if kind is None:
            return
        self._tracker = ButterflyTracker(kind, self)
        self._tracker.updated.connect(self._on_tracked)
        if not self._tracker.start():
            self._log.add_log(f"Не найден шаблон: {kind.filenames[0]}",
                              level="error")
            self._tracker = None
            return
        self._flutter_shown = True
        self._mark_flutter_button()
        self._log.add_log("Слежу за бабочками — точки идут за ними",
                          level="plain")

    def _butterfly_kind(self):
        return next((k for k in TRASH_KINDS if k.key == "butterfly"), None)

    def _mark_flutter_button(self):
        """Its button is lit while the live dots are up.

        Without this the dots are already on after a scan while the button
        looks untouched, so the first press reads as "show them" and in fact
        turns them off.
        """
        index = self._butterfly_index()
        button = self._highlight_buttons.get((index, True)) if index is not None             else None
        if button is not None:
            button.set_active(self._flutter_shown)

    def _butterfly_index(self):
        return next((i for i, k in enumerate(TRASH_KINDS)
                     if k.key == "butterfly"), None)

    def _toggle_flutter(self, kind_index: int):
        """Show or hide the live dots, and say where the butterflies are."""
        self._flutter_shown = not self._flutter_shown
        self._mark_flutter_button()
        if not self._flutter_shown:
            self._flutter.clear()
            self._log.add_log("Точки бабочек скрыты — слежение продолжается",
                              level="plain")
            return
        seen = self._tracker._tracks.positions() if self._tracker else []
        self._log.add_log_segments(
            [("Бабочка: сейчас видно — ", theme.TEXT_SECONDARY),
             (str(len(seen)), self._group_colour(kind_index, True))],
            level="plain")
        for number, x, y, score in seen:
            self._log.add_log_segments(
                [(f"   №{number} ", theme.TEXT_DIM),
                 (f"({x}, {y})", colour_for(number)),
                 (f" — {100 * score:.1f}%", theme.TEXT_SECONDARY)],
                level="plain")
        self._log.blank_line()

    def _stop_tracking(self):
        if self._tracker is not None:
            self._tracker.stop()
            self._tracker = None

    def _on_tracked(self, seen: list):
        """Dots only — one per butterfly, in that butterfly's own colour.

        The running readout of numbers and percentages that used to sit above
        the bars is gone: it was there to judge the threshold by, and the dots
        say the same thing without a wall of figures.
        """
        if not self._flutter_shown:
            return
        self._flutter.show_markers(
            [Marker(x, y, colour_for(number), True)
             for number, x, y, _score in seen])

    def _refresh_highlight_buttons(self):
        butterfly = (self._butterfly_index(), True)
        for group, button in self._highlight_buttons.items():
            if group == butterfly:
                continue          # that one follows the live dots instead
            button.set_active(group == self._highlighted)

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
        if self._run is not None:
            self._run.stop()
            self._run = None
        self._stop_tracking()
        self._markers.clear()
        self._flutter.clear()
