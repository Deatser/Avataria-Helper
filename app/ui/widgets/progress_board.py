# app/ui/widgets/progress_board.py
"""The bars for one cleaning run: the whole job, then one per kind.

Kept out of the log on purpose, the same way Ava Dancers keeps its four
arrows out of its own: a log is a record of things that happened, and these
are one thing that is happening, rewritten continuously.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.ui import theme
from app.ui.widgets.neon_bar import NeonBar

TOTAL_KEY = "__total__"

_HEADING_H = 17
_SPACING   = 2


class ProgressBoard(QWidget):
    """A bar for the run as a whole, and a bar for every kind in it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars: dict[str, NeonBar] = {}

        # Fixed, not stretchy: the bars have one right height, and the log
        # below them takes whatever is left. Left to negotiate, a short window
        # squeezes the rows into each other until they overlap.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(_SPACING)

        self._heading = QLabel("УБОРКА")
        self._heading.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        self._heading.setStyleSheet(
            f"color:{theme.GD_OLIVE}; background:transparent;")
        self._heading.setFixedHeight(_HEADING_H)
        self._layout.addWidget(self._heading)
        self.setFixedHeight(0)
        self.hide()

    # ── Public API ───────────────────────────────────────────────────────────

    def build(self, total: int, rows: list[tuple[str, str, int, str]]):
        """rows — (key, caption, total, colour), in the order they are shown.

        Kinds that are not in the garden get no bar: a row that says 0/0 is a
        line to read past, and the point of the board is the ones moving.
        """
        self._clear()
        self._add(TOTAL_KEY, "Найдено мусора всего", total, theme.GD_OLIVE)
        for key, caption, count, colour in rows:
            if count:
                self._add(key, caption, count, colour)
        self.setFixedHeight(self.wanted_height())
        self.show()

    def wanted_height(self) -> int:
        """What the bars need — the window grows by this while a run is on."""
        if not self._bars:
            return 0
        return _HEADING_H + len(self._bars) * (NeonBar.ROW_H + _SPACING)

    def advance(self, key: str, done: int, total_done: int):
        """One more piece of that kind is gone, and one more overall."""
        if key in self._bars:
            self._bars[key].set_done(done)
        if TOTAL_KEY in self._bars:
            self._bars[TOTAL_KEY].set_done(total_done)

    def dec_total(self, key: str, amount: int = 1):
        """A pick that turned out not to be real litter after all — taken
        back out of both its own bar and the overall one, the same way a
        cleared one is credited to both."""
        if key in self._bars:
            bar = self._bars[key]
            bar.set_total(bar.total - amount)
        if TOTAL_KEY in self._bars:
            bar = self._bars[TOTAL_KEY]
            bar.set_total(bar.total - amount)

    def bar(self, key: str) -> NeonBar | None:
        return self._bars.get(key)

    def clear(self):
        self._clear()
        self.setFixedHeight(0)
        self.hide()

    # ── Internals ────────────────────────────────────────────────────────────

    def _add(self, key: str, caption: str, total: int, colour: str):
        bar = NeonBar(caption, total, colour, self)
        self._bars[key] = bar
        self._layout.addWidget(bar)

    def _clear(self):
        for bar in self._bars.values():
            bar.setParent(None)
            bar.deleteLater()
        self._bars = {}
