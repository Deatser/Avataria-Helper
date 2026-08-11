# app/ui/energy_window.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
                               QVBoxLayout, QWidget)

from app.core import energy_bar
from app.core.energy_farm import EnergyFarmFlow
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.en_panel import EnPanel
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_slider import NtSlider
from app.ui.widgets.nt_status_dot import NtStatusDot

# Taken from the size the window was actually left at (config.json,
# 2026-08-12): the bar row, the payment pair and the two readout lines all
# fit at exactly this width, and anything narrower starts clipping them.
_DEFAULT_W = 763
_DEFAULT_H = 452
_MIN_W     = _DEFAULT_W
_MIN_H     = _DEFAULT_H

# The same tile the launcher's board uses (see app/ui/overlay.py) — one
# button size across the helper, whatever window it is standing in.
_TILE_W = 210
_TILE_H = 58

_LOG_H = 140   # a floor; the log takes the slack when the window grows

# The payment pair — smaller than the tile, so they read as a setting for the
# run rather than a second thing to press, but lettered at the same size as
# the readout below them.
_PAY_BTN_W = 126
_PAY_BTN_H = 40

_START_TEXT = "В кафе"
_STOP_TEXT  = "Остановить"

# How far the slider itself reaches. It is the coarse way to pick — dragging
# moves in _STEP-sized jumps and a dot marks every _TICKS-th of the way —
# while the field next to it takes any number at all, past this maximum
# included and off the step too; the handle then just parks at the right end.
_MAX_ENERGY = 1000
_STEP       = 15
_TICKS      = 11   # 0, 100, 200 … 1000


class EnergyWindow(ModuleWindow):
    """Энергия — buying energy at the cafe.

    A module window in every mechanical sense (dragged, resized and
    remembered the same way) but not in the module registry: it owns no bot
    and watches no screen yet. The overlay opens it directly, the way it
    already does with Статистика and Промокоды.
    """

    closed = Signal()

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, stats=None,
                 overlay=None):
        # Not parent_overlay: the base class would report this as a module
        # closing, and it is not one — the overlay listens to `closed`.
        super().__init__("Энергия", config, save_fn, parent_overlay=None)
        self._wm      = window_manager
        self._stats   = stats
        self._overlay = overlay
        self._flow: EnergyFarmFlow | None = None
        chosen = getattr(config, "pay_with", energy_bar.GOLD)
        self._currency = chosen if chosen in (energy_bar.SILVER,
                                              energy_bar.GOLD) else energy_bar.GOLD

        w = getattr(config, "width",  _DEFAULT_W)
        h = getattr(config, "height", _DEFAULT_H)
        self.resize(max(w, _MIN_W), max(h, _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = EnPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.EN_BORDER_DIM};")
        layout.addWidget(sep)
        layout.addSpacing(10)

        layout.addWidget(self._caption("ЗАКУП В КАФЕ"))
        layout.addSpacing(2)
        layout.addLayout(self._build_bar_row())
        layout.addSpacing(6)
        layout.addLayout(self._build_payment_row())
        layout.addSpacing(6)
        layout.addWidget(self._build_readout())

        layout.addSpacing(10)
        layout.addLayout(self._build_log(), stretch=1)

        drag = NtDragHandle(dot_color=theme.EN_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        self._status_dot = NtStatusDot()
        self._status_dot.set_offline()   # nothing runs here yet

        title = QLabel("ЭНЕРГИЯ")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.EN_YELLOW}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._fav_btn = NtButton("★" if self.favorite else "☆",
                                 accent=theme.ACCENT_AMBER)
        self._fav_btn.setFixedSize(26, 26)
        self._fav_btn.setToolTip("Закрепить — открывать вместе с помощником")
        self._fav_btn.clicked.connect(self._toggle_favorite)

        self._collapse_btn = NtButton("▲", accent=theme.EN_YELLOW)
        self._collapse_btn.setFixedSize(26, 26)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)

        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._fav_btn)
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        return header

    # ── Favorite ─────────────────────────────────────────────────────────────
    # The same star the module windows and Статистика carry, and the same
    # meaning: the overlay reopens it on the next launch — see
    # Overlay.restore_favorite_windows.

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
                ("Окно Энергия ", theme.TEXT_SECONDARY),
                (verb, colour),
                (tail, theme.TEXT_SECONDARY),
            ])

    def _build_bar_row(self) -> QHBoxLayout:
        """The button, the slider it is aimed at, and the field to type in."""
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)

        self._bar_btn = NtButton(_START_TEXT, accent=theme.EN_YELLOW)
        self._bar_btn.setFixedSize(_TILE_W, _TILE_H)
        self._bar_btn.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        self._bar_btn.clicked.connect(self._toggle_run)
        row.addWidget(self._bar_btn)

        self._amount = max(0, int(getattr(self.config, "bar_amount", 0)))

        self._slider = NtSlider(0, _MAX_ENERGY, step=_STEP, ticks=_TICKS,
                                accent=theme.EN_YELLOW)
        self._slider.set_value(min(self._amount, _MAX_ENERGY))
        self._slider.valueChanged.connect(self._on_slider)
        row.addWidget(self._slider, 1)

        self._field = QLineEdit(str(self._amount))
        self._field.setValidator(QIntValidator(0, 10 ** 6, self))
        self._field.setAlignment(Qt.AlignCenter)
        self._field.setFixedSize(84, _TILE_H)
        self._field.setStyleSheet(f"""
            QLineEdit {{
                background: {theme.EN_ELEVATED};
                color: {theme.EN_TEXT};
                border: 1px solid {theme.EN_BORDER};
                border-radius: {theme.RADIUS}px;
            }}
            QLineEdit:focus {{ border: 1px solid {theme.EN_YELLOW}; }}
        """)
        self._field.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        # Typed live, so the handle follows the digits as they are entered;
        # anything past the maximum simply parks it at the right end.
        self._field.textEdited.connect(self._on_typed)
        self._field.editingFinished.connect(self._settle_field)
        row.addWidget(self._field)

        return row

    def _build_payment_row(self) -> QHBoxLayout:
        """Serebro or zoloto — one of the two, always exactly one.

        Two small buttons rather than a switch: they are the same control in
        two states, and the lit one is ringed in olive the way an active
        module tile is ringed in its own colour.
        """
        row = QHBoxLayout()
        row.setSpacing(6)

        label = QLabel("Оплата:")
        label.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        row.addWidget(label)
        row.addSpacing(4)

        self._pay_btns: dict[str, NtButton] = {}
        for currency, face in ((energy_bar.SILVER, "Серебро"),
                               (energy_bar.GOLD,   "Золото")):
            btn = NtButton(face, accent=theme.GD_OLIVE, upper=False)
            btn.setFixedSize(_PAY_BTN_W, _PAY_BTN_H)
            btn.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
            btn.clicked.connect(lambda _, c=currency: self._choose_currency(c))
            self._pay_btns[currency] = btn
            row.addWidget(btn)

        row.addStretch()
        self._paint_payment()
        return row

    def _build_readout(self) -> QWidget:
        """What is chosen and what it will cost — the two lines to glance at."""
        box = QWidget()
        # A floor, not a fixed height: the cost line wraps when the purchase
        # list gets long (many drinks, six-figure prices), and the plaque
        # grows with it instead of clipping.
        box.setMinimumHeight(96)
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        box.setStyleSheet(f"""
            QWidget {{
                background: {theme.EN_SURFACE};
                border: 1px solid {theme.EN_BORDER_DIM};
                border-radius: {theme.RADIUS}px;
            }}
        """)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(16, 10, 16, 10)
        inner.setSpacing(6)

        self._readout = QLabel()
        self._readout.setFont(theme.get_display_font(theme.FONT_SIZE_L))
        self._readout.setStyleSheet(
            f"color:{theme.EN_YELLOW}; background:transparent; border:0;")

        self._cost_label = QLabel()
        self._cost_label.setWordWrap(True)
        self._cost_label.setTextFormat(Qt.RichText)   # per-drink colouring
        self._cost_label.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        self._cost_label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent; border:0;")

        inner.addLayout(self._readout_line("Выбрано для фарма:",
                                           self._readout))
        inner.addLayout(self._readout_line("Будет потрачено:",
                                           self._cost_label))
        self._refresh_readout()
        return box

    @staticmethod
    def _readout_line(caption: str, value: QLabel) -> QHBoxLayout:
        label = QLabel(caption)
        label.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent; border:0;")
        # The caption keeps its own width and the value takes the rest,
        # right-aligned — which is also what gives a wrapping value a width
        # to wrap against.
        # Both centred on the same line, not the caption pinned to the top:
        # the two labels are set at different sizes, so anything but a shared
        # vertical centre leaves the caption riding above its own value.
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        line.addWidget(label, 0)
        line.addWidget(value, 1)
        return line

    def _build_log(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)

        head = QHBoxLayout()
        head.addWidget(self._caption("Энергия:"))
        head.addStretch()
        head.addLayout(build_log_actions(self._log, theme.EN_BORDER))
        block.addLayout(head)

        block.addWidget(self._log, stretch=1)
        return block

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        return label

    # ── Amount ───────────────────────────────────────────────────────────────

    def amount(self) -> int:
        """How much energy the cafe run is set to buy."""
        return self._amount

    def set_amount(self, value: int):
        """The chosen amount is its own number, not the handle's position.

        The slider only covers 0.._MAX_ENERGY, and it is a coarse way to
        pick — typing is the exact one. So a hand-typed 824572 is kept as
        it was typed and the handle simply sits at the far right; 16 is kept
        as 16 even though the drag itself moves in whole units.
        """
        self._amount = max(0, int(value))
        blocked = self._slider.blockSignals(True)
        self._slider.set_value(min(self._amount, self._slider.maximum()))
        self._slider.blockSignals(blocked)
        self._store()

    def _on_slider(self, value: int):
        self._amount = value
        if self._field.text() != str(value):
            self._field.setText(str(value))
        self._store()

    def _on_typed(self, text: str):
        if not text.strip():
            return          # mid-edit: an empty field is not a zero yet
        self.set_amount(int(text))

    def _settle_field(self):
        """Once editing ends, show the number that was actually kept."""
        self._field.setText(str(self._amount))

    def _store(self):
        self.config.bar_amount = self._amount
        self._refresh_readout()
        self._save_later.start()   # not once per pixel of the drag

    def _refresh_readout(self):
        self._readout.setText(f"{self._amount} энергии")
        self._cost_label.setText(
            energy_bar.plan(self._amount, self._currency)
            .describe_html(theme.TEXT_SECONDARY))

    # ── The bar run ──────────────────────────────────────────────────────────

    def _toggle_run(self):
        if self._flow is not None:
            self._stop_run("Закуп остановлен")
            return

        hwnd = self._wm.get_game_hwnd() if self._wm else None
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        plan = energy_bar.plan(self._amount, self._currency)
        if not plan.rounds:
            self._log.add_log("Сначала выберите количество энергии",
                              level="error")
            return

        flow = EnergyFarmFlow(hwnd, plan)
        flow.opening_menu.connect(self._on_opening_menu)
        flow.going_to_cafe.connect(self._on_going_to_cafe)
        flow.finished_run.connect(self._on_run_finished)
        flow.error.connect(self._on_run_error)
        flow.finished.connect(self._on_thread_done)
        self._flow = flow
        self._status_dot.set_running()
        self._bar_btn.setText(_STOP_TEXT)
        self._bar_btn.set_active(True)
        flow.start()

    def _on_opening_menu(self):
        self._log.add_log_segments([("Открываем меню", theme.EN_YELLOW)],
                                   level="plain")

    def _on_going_to_cafe(self):
        self._log.add_log_segments(
            [("Мы не в кафе, идём в кафе", theme.TEXT_SECONDARY)],
            level="plain")

    def _on_run_finished(self, plan):
        self._log.add_log_segments(
            plan.segments(theme.TEXT_SECONDARY, theme.EN_YELLOW),
            level="plain")
        self._record(plan)

    def _record(self, plan):
        """One finished run into the books — all-time and today's file at
        once, the same way every other module records (StatsManager)."""
        if self._stats is None:
            return
        gold   = plan.cost if plan.currency == energy_bar.GOLD else 0
        silver = plan.cost if plan.currency == energy_bar.SILVER else 0
        self._stats.record_energy_purchase(
            energy = plan.energy,
            gold   = gold,
            silver = silver,
            pie        = plan.count_of(energy_bar.PIE),
            cheesecake = plan.count_of(energy_bar.CHEESECAKE),
            brownie    = plan.count_of(energy_bar.BROWNIE),
        )

    def _on_run_error(self, message: str):
        self._log.add_log(message, level="error")

    def _on_thread_done(self):
        """The thread ended — however it ended, the button goes back."""
        self._flow = None
        self._status_dot.set_offline()
        self._bar_btn.setText(_START_TEXT)
        self._bar_btn.set_active(False)

    def _stop_run(self, message: str | None = None):
        flow, self._flow = self._flow, None
        if flow is None:
            return
        flow.stop_flow()
        flow.wait()
        if message:
            self._log.add_log(message, level="plain")

    # ── Payment ──────────────────────────────────────────────────────────────

    def currency(self) -> str:
        """Which of the two the run should pay with — silver or gold."""
        return self._currency

    def _choose_currency(self, currency: str):
        if currency == self._currency:
            return          # one of the two is always lit; nothing to turn off
        self._currency = currency
        self.config.pay_with = currency
        self._paint_payment()
        self._refresh_readout()
        self._save_later.start()

    def _paint_payment(self):
        for name, btn in self._pay_btns.items():
            btn.set_active(name == self._currency)

    # ── Window plumbing ──────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    def closeEvent(self, event):
        if self.crt_close_started():
            event.ignore()
            return
        self._stop_run()   # never leave the run clicking at a closed window
        self.closed.emit()
        event.accept()
