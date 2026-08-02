# app/ui/stats_window.py
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QGridLayout
from PySide6.QtCore import Qt, QTimer, Signal

from app.core.stats import shown
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.stat_tile import StatTile

_MIN_W = 380
_MIN_H = 360   # header + player rows + one game's tiles + hint + handle


class StatsWindow(ModuleWindow):
    """Everything the helper has counted, in one place.

    A module window in every mechanical sense — dragged, resized and
    remembered the same way — but it owns no bot and no screen watching, so
    it is not in the module registry and the overlay opens it directly.
    """

    closed = Signal()

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, stats, window_manager, overlay=None):
        # Not passed as parent_overlay: the base class would report this as a
        # module closing, and it is not one. The overlay listens to `closed`
        # instead, and `overlay` here is only somewhere to write a log line.
        super().__init__("Статистика", config, save_fn, parent_overlay=None)
        self._wm      = window_manager
        self._stats   = stats
        self._overlay = overlay
        self.resize(max(getattr(config, "width",  _MIN_W), _MIN_W),
                    max(getattr(config, "height", _MIN_H), _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)
        self.refresh()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = NtPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())
        layout.addWidget(self._hairline())
        layout.addSpacing(2)

        layout.addWidget(self._section_label("ИГРОК", theme.ACCENT))
        self._id_value   = self._add_row(layout, "ID")
        self._name_value = self._add_row(layout, "Имя")
        self._date_value = self._add_row(layout, "Регистрация")
        layout.addSpacing(6)

        layout.addWidget(self._section_label("AVA DANCERS", theme.VW_MAGENTA))
        layout.addLayout(self._build_tiles())
        layout.addStretch()

        drag = NtDragHandle()
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("СТАТИСТИКА")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.ACCENT_SOFT}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.ACCENT)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.favorite else "☆",
                                 accent=theme.ACCENT_AMBER)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)

        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._fav_btn)
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        return header

    def _build_tiles(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(theme.SPACING)
        self._games_tile  = StatTile("игр сыграно", accent=theme.ACCENT)
        self._gold_tile   = StatTile("золота",  accent=theme.ACCENT_AMBER)
        self._silver_tile = StatTile("серебра", accent=theme.ACCENT_STEEL)
        for column, tile in enumerate((self._games_tile, self._gold_tile,
                                       self._silver_tile)):
            grid.addWidget(tile, 0, column)
        return grid

    # ── Small parts ──────────────────────────────────────────────────────────

    def _hairline(self) -> QLabel:
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.BORDER_DIM};")
        return line

    def _section_label(self, text: str, accent: str) -> QLabel:
        label = QLabel(f"◈  {text}")
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        label.setStyleSheet(f"color:{accent}; background:transparent;")
        return label

    def _add_row(self, layout: QVBoxLayout, caption: str) -> QLabel:
        """Caption on the left, value on the right; returns the value label."""
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)
        name = QLabel(caption)
        name.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        name.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        value = QLabel("—")
        value.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(name)
        row.addStretch()
        row.addWidget(value)
        layout.addLayout(row)
        return value

    # ── Favorite ─────────────────────────────────────────────────────────────
    # Same star as the module windows carry, and the same meaning: the
    # overlay reopens it on the next launch. See Overlay.restore_favourites.

    @property
    def favorite(self) -> bool:
        return bool(getattr(self.config, "favorite", False))

    def _toggle_favorite(self):
        self.config.favorite = not self.favorite
        self.save_fn()
        self._fav_btn.setText("★" if self.favorite else "☆")

        if self.favorite:
            verb, tail, colour = ("добавлено", " в автозагрузку при старте",
                                  theme.ACCENT_GREEN)
        else:
            verb, tail, colour = ("удалено", " из автозагрузки при старте",
                                  theme.ACCENT_AMBER)
        if self._overlay:
            self._overlay.add_log_segments([
                ("Окно Статистика ", theme.TEXT_SECONDARY),
                (verb, colour),
                (tail, theme.TEXT_SECONDARY),
            ])

    # ── Data ─────────────────────────────────────────────────────────────────

    def reload(self):
        """Re-read stats.json, then redraw — the file can be edited by hand."""
        self._stats.reload()
        self.refresh()

    def refresh(self):
        player = self._stats.data.player
        ava    = self._stats.data.ava_dancers

        self._set_value(self._id_value,
                        shown(player.player_id, "player_id"))
        self._set_value(self._name_value,
                        shown(player.player_name, "player_name"))
        self._set_value(self._date_value,
                        shown(player.registration_date, "registration_date"))

        self._games_tile.set_value(str(ava.games_played))
        self._gold_tile.set_value(str(ava.gold_won))
        self._silver_tile.set_value(str(ava.silver_won))

    def _set_value(self, label: QLabel, text: str):
        """Unfilled values are marked, not hidden — that is the point of them."""
        empty  = text.startswith("%") and text.endswith("%")
        colour = theme.ACCENT_AMBER if empty else theme.TEXT_PRIMARY
        label.setText(text)
        label.setStyleSheet(f"color:{colour}; background:transparent;")

    # ── Window plumbing ──────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # Re-read on every show: the pin button took the refresh button's
        # place, and stats.json can be edited by hand between openings.
        self.reload()
        QTimer.singleShot(30, self.repaint)

    def _teardown(self):
        self.closed.emit()
