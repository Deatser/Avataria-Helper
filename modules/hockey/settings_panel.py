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
_LOG_TEXT     = "Писать логи"
_LIGHT_TEXT   = "Лёгкие логи"
_OVERLAY_TEXT = "Скрыть рамки поверх игры"
_FINE_TEXT    = "Промежуточные натяжения (тест)"
_ORANGE_TEXT  = "Только по полосе у борта"
_AUTO_TEXT    = "Бить самому, как только готов"

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

        layout.addWidget(self.caption("Замер вратарей"))
        self._orange_switch = NtSwitch(_ORANGE_TEXT, accent=theme.HK_ICE)
        self._orange_switch.set_checked_silently(self.orange_mode)
        self._orange_switch.toggled.connect(self._on_orange_mode)
        layout.addWidget(self._orange_switch)
        layout.addWidget(self.hint(
            "Каждому ряду даётся полоса у борта, и скорость каждого вратаря "
            "берётся из времени между его разворотами в ней: два размаха "
            "ряда, делённые на период. Ни слежения, ни автокорреляции — "
            "один и тот же способ для всех рядов, сколько бы вратарей там "
            "ни стояло и ни ездило. Проверка честности та же: предсказание "
            "на 1.5 с сверяется с тем, что вышло."))

        layout.addWidget(self.caption("Бросок"))
        self._auto_switch = NtSwitch(_AUTO_TEXT, accent=theme.HK_ICE)
        self._auto_switch.set_checked_silently(self.auto_shot)
        self._auto_switch.toggled.connect(self._on_auto_shot)
        layout.addWidget(self._auto_switch)
        layout.addWidget(self.hint(
            "Как только все ряды откалиброваны — удар в ближайшее окно, без "
            "нажатия кнопки. План тот же, что построила бы кнопка: отказы и "
            "пороги не меняются, меняется только кто нажимает. Один удар на "
            "уровень; следующий — после смены уровня."))

        self._fine_switch = NtSwitch(_FINE_TEXT, accent=theme.HK_ICE)
        self._fine_switch.set_checked_silently(self.fine_aim)
        self._fine_switch.toggled.connect(self._on_fine_aim)
        layout.addWidget(self._fine_switch)
        layout.addWidget(self.hint(
            "Замерены три натяжения: ноль и обе штанги. С этим переключателем "
            "рассматриваются и промежуточные — 0.15, 0.35 и так далее, — "
            "форма дуги берётся измеренная, а её размах считается растущим "
            "пропорционально натяжению. Вариантов становится в семь раз "
            "больше, и окно находится чаще. Это единственное место, где "
            "число не измерено, а выведено: выключите, если броски начнут "
            "мазать."))

        layout.addWidget(self.caption("Экран"))
        self._overlay_switch = NtSwitch(_OVERLAY_TEXT, accent=theme.HK_ICE)
        self._overlay_switch.set_checked_silently(self.hide_overlays)
        self._overlay_switch.toggled.connect(self._on_hide_overlays)
        layout.addWidget(self._overlay_switch)
        layout.addWidget(self.hint(
            "Малиновые рамки предсказания, чёрные стоящих и оранжевые полосы "
            "замера. Бот пользуется ими по-прежнему — они просто перестают "
            "рисоваться поверх игры."))

        layout.addWidget(self.caption("Лог"))
        self._log_switch = NtSwitch(_LOG_TEXT, accent=theme.HK_ICE)
        self._log_switch.set_checked_silently(self.verbose_log)
        self._log_switch.toggled.connect(self._on_verbose_log)
        layout.addWidget(self._log_switch)
        layout.addWidget(self.hint(
            "Подробности слежения: перепись состава, точность предсказания "
            "по каждому ряду, разбор отказа от броска. Выключите, и в логе "
            "останутся только сами броски и ошибки."))

        self._light_switch = NtSwitch(_LIGHT_TEXT, accent=theme.HK_ICE)
        self._light_switch.set_checked_silently(self.light_log)
        self._light_switch.toggled.connect(self._on_light_log)
        self._light_switch.setEnabled(self.verbose_log)
        layout.addWidget(self._light_switch)
        layout.addWidget(self.hint(
            "Только то, что происходит: номер уровня, кто в каком ряду, "
            "по строке на каждый откалиброванный ряд и сам бросок. Всё "
            "остальное продолжает считаться, просто не пишется. Работает "
            "только пока логи вообще включены."))

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

    # ── Measuring the defenders ──────────────────────────────────────────

    @property
    def orange_mode(self) -> bool:
        return bool(getattr(self.config, "orange_mode", False))

    def _on_orange_mode(self, enabled: bool):
        self.config.orange_mode = enabled
        self.save_fn()

    # ── The shot ─────────────────────────────────────────────────────────

    @property
    def auto_shot(self) -> bool:
        return bool(getattr(self.config, "auto_shot", False))

    def _on_auto_shot(self, enabled: bool):
        self.config.auto_shot = enabled
        self.save_fn()

    @property
    def fine_aim(self) -> bool:
        return bool(getattr(self.config, "fine_aim", False))

    def _on_fine_aim(self, enabled: bool):
        self.config.fine_aim = enabled
        self.save_fn()

    # ── The log ──────────────────────────────────────────────────────────

    @property
    def verbose_log(self) -> bool:
        return bool(getattr(self.config, "verbose_log", True))

    def _on_verbose_log(self, enabled: bool):
        self.config.verbose_log = enabled
        self.save_fn()
        # There is nothing to make light of when nothing is being written.
        self._light_switch.setEnabled(enabled)

    @property
    def light_log(self) -> bool:
        return bool(getattr(self.config, "light_log", False))

    def _on_light_log(self, enabled: bool):
        self.config.light_log = enabled
        self.save_fn()

    # ── The screen ───────────────────────────────────────────────────────

    @property
    def hide_overlays(self) -> bool:
        return bool(getattr(self.config, "hide_overlays", False))

    def _on_hide_overlays(self, enabled: bool):
        self.config.hide_overlays = enabled
        self.save_fn()

    # ── Rows ─────────────────────────────────────────────────────────────

    def _on_clear_rows(self):
        self.config.lanes = []
        self.save_fn()
        self.rows_cleared.emit()
