# modules/hockey/settings_panel.py
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_switch import NtSwitch
from app.ui.widgets.segmented_control import SegmentedControl

_W = 360
_H = 700

# How readily a pixel counts as helmet red. Detection stands or falls on
# this one call, and which end of the range a given rink needs cannot be
# known without looking at it, so it is a control rather than a constant —
# the three presets are the whole useful range, and a finer knob would only
# be a slower way to reach the same three answers.
_SENSITIVITY = ["Строго", "Средне", "Мягко"]
_SENSITIVITY_VALUES = [(160, 110), (120, 80), (80, 55)]   # (sat_min, val_min)
_DEFAULT_SENSITIVITY = 1

# How hard a red blob has to look like one of templates/hockey_player*.png
# before it counts as a defender. Those are three specific avatars and every
# level brings different ones, so this filters red scenery rather than
# insisting on a known face — hence the low steps and the off switch.
_PHOTO = ["Выкл", "Мягко", "Средне", "Строго"]
_PHOTO_VALUES = [0.0, 0.30, 0.45, 0.60]
_DEFAULT_PHOTO = 1

_MASK_TEXT  = "Маска разметки катка"
_CLEAR_TEXT = "Сбросить все ряды"
_LOG_TEXT   = "Писать логи"

# Where the prediction boxes are drawn for. "Сейчас" is a checking mode: the
# box has to ride exactly on its defender, so leading or lagging shows up at
# once, which it never does at a second and a half.
_HORIZON = ["Сейчас (проверка)", "Через 1.5 с (бой)"]


class HockeySettingsPanel(SheetPanel):
    """What detection is allowed to count as a helmet, and a way out of a
    bad row calibration."""

    rows_cleared = Signal()
    horizon_changed = Signal(bool)   # True → drawing for now, not for the shot

    def __init__(self, config, save_fn, host):
        self.config  = config
        self.save_fn = save_fn
        super().__init__(host, "НАСТРОЙКИ", (_W, _H),
                         accent=theme.HK_ICE, close_accent=theme.HK_ICE)

    def _build_body(self, layout: QVBoxLayout):
        """Everything is still built and still wired; all of it but the log
        switch is hidden.

        The rink is calibrated and the bot plays a level without a mistake,
        so the knobs that got it there are now only in the way — but they
        are the knobs that would get it there again on a rink that does not
        behave, and deleting them would mean rebuilding them. Hidden, not
        removed: the settings they hold still come from the config file and
        still take effect, and one line here brings any of them back.
        """
        layout.addWidget(self._hide(self.caption("Детекция")))

        self._sensitivity = SegmentedControl(
            _SENSITIVITY, accent=theme.HK_ICE_SOFT,
            active=self._current_sensitivity())
        self._sensitivity.changed.connect(self._on_sensitivity)
        layout.addWidget(self._hide(self._sensitivity))
        layout.addWidget(self._hide(self.hint(
            "Насколько охотно пиксель считается красным шлемом. Если "
            "«Тест детекции» никого не находит — смягчите; если находит "
            "лишнее на льду — ужесточите.")))

        self._photo = SegmentedControl(
            _PHOTO, accent=theme.HK_ICE_SOFT, active=self._current_photo())
        self._photo.changed.connect(self._on_photo)
        layout.addWidget(self._hide(self._photo))
        layout.addWidget(self._hide(self.hint(
            "Сверка найденного пятна с фото вратарей — отсекает красное, "
            "что вратарём не является. Аватарки у всех разные, поэтому "
            "строгий порог может отбросить настоящего игрока: смотрите "
            "проценты в логе «Теста детекции» и на снимке.")))

        self._mask_switch = NtSwitch(_MASK_TEXT, accent=theme.HK_ICE)
        self._mask_switch.set_checked_silently(self.static_mask)
        self._mask_switch.toggled.connect(self._on_static_mask)
        layout.addWidget(self._hide(self._mask_switch))
        layout.addWidget(self._hide(self.hint(
            "Запоминает пиксели, красные во всех кадрах, и вычитает их. "
            "Нужно, только если вратарь пропадает, проезжая по красной "
            "линии катка — тогда его пятно слипается с ней. Требует пары "
            "секунд разогрева перед первой детекцией.")))

        layout.addWidget(self._hide(self.caption("Предсказание")))

        self._horizon = SegmentedControl(
            _HORIZON, accent=theme.HK_ICE_SOFT,
            active=0 if self.predict_now else 1)
        self._horizon.changed.connect(self._on_horizon)
        layout.addWidget(self._hide(self._horizon))
        layout.addWidget(self._hide(self.hint(
            "«Сейчас» — малиновая рамка рисуется там, где вратарь прямо "
            "сейчас: она обязана ехать ровно по нему, и отставание или "
            "опережение видно мгновенно. Точность в логе при этом "
            "по-прежнему меряется на 1.5 с. Действует сразу, бросок в этом "
            "режиме запрещён.")))

        layout.addWidget(self._hide(self.caption("Ряды")))

        clear_btn = NtButton(_CLEAR_TEXT, accent=theme.ACCENT_AMBER,
                             upper=False)
        clear_btn.clicked.connect(self._on_clear_rows)
        layout.addWidget(self._hide(clear_btn))
        layout.addWidget(self._hide(self.hint(
            "Стирает все размеченные ряды вместе с их бортами. Обычно "
            "проще перезаписать нужный ряд поверх: выберите его номер, "
            "цель «Ряд» и отметьте заново.")))

        # The one control left standing.
        layout.addWidget(self.caption("Лог"))
        self._log_switch = NtSwitch(_LOG_TEXT, accent=theme.HK_ICE)
        self._log_switch.set_checked_silently(self.verbose_log)
        self._log_switch.toggled.connect(self._on_verbose_log)
        layout.addWidget(self._log_switch)
        layout.addWidget(self.hint(
            "Подробности слежения: перепись состава, точность предсказания "
            "по каждому ряду, разбор отказа от броска. Выключите, и в логе "
            "останутся только сами броски и ошибки."))

    @staticmethod
    def _hide(widget):
        """Built and wired, out of sight. Hidden widgets take no space in a
        layout, so this leaves the panel with exactly what is on it."""
        widget.hide()
        return widget

    # ── Detection ────────────────────────────────────────────────────────

    def _current_sensitivity(self) -> int:
        """Whichever preset the stored thresholds sit closest to — the file
        is hand-editable, so it can hold something between presets."""
        stored = int(getattr(self.config, "red_sat_min",
                             _SENSITIVITY_VALUES[_DEFAULT_SENSITIVITY][0]))
        distances = [abs(sat - stored) for sat, _ in _SENSITIVITY_VALUES]
        return distances.index(min(distances))

    def _on_sensitivity(self, index: int):
        sat, val = _SENSITIVITY_VALUES[index]
        self.config.red_sat_min = sat
        self.config.red_val_min = val
        self.save_fn()

    def _current_photo(self) -> int:
        stored = float(getattr(self.config, "player_match_min",
                               _PHOTO_VALUES[_DEFAULT_PHOTO]))
        distances = [abs(value - stored) for value in _PHOTO_VALUES]
        return distances.index(min(distances))

    def _on_photo(self, index: int):
        self.config.player_match_min = _PHOTO_VALUES[index]
        self.save_fn()

    @property
    def predict_now(self) -> bool:
        return bool(getattr(self.config, "predict_now", False))

    def _on_horizon(self, index: int):
        self.config.predict_now = (index == 0)
        self.save_fn()
        self.horizon_changed.emit(self.config.predict_now)

    @property
    def static_mask(self) -> bool:
        return bool(getattr(self.config, "static_mask", False))

    def _on_static_mask(self, enabled: bool):
        self.config.static_mask = enabled
        self.save_fn()

    # ── The log ──────────────────────────────────────────────────────────

    @property
    def verbose_log(self) -> bool:
        return bool(getattr(self.config, "verbose_log", True))

    def _on_verbose_log(self, enabled: bool):
        self.config.verbose_log = enabled
        self.save_fn()

    # ── Rows ─────────────────────────────────────────────────────────────

    def _on_clear_rows(self):
        self.config.lanes = []
        self.save_fn()
        self.rows_cleared.emit()
