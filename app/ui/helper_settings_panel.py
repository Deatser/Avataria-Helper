# app/ui/helper_settings_panel.py
"""The main helper window's own settings sheet — anchored to Overlay the
same way every module's own "Настройки" sheet anchors to its own window,
except these toggles are not any single window's own: the connector wires
and the close-confirmation dialog both live on OverlayConfig already, and
log animation is one flag on the log_panel module that every window's
LogPanel reads.
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets import log_panel
from app.ui.widgets.neon_section import NeonSection
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_switch import NtSwitch

_W = 360
_H = 435


class HelperSettingsPanel(SheetPanel):
    def __init__(self, overlay):
        self._overlay = overlay
        super().__init__(overlay, "НАСТРОЙКИ", (_W, _H), accent=theme.ACCENT)

    # ── Body ─────────────────────────────────────────────────────────────────

    def _build_body(self, layout: QVBoxLayout):
        cfg = self._overlay.config.data.overlay
        section = NeonSection("Хелпер", theme.ACCENT)
        body = section.body()

        links_switch = NtSwitch("Линии между окнами", accent=theme.ACCENT)
        links_switch.set_checked_silently(getattr(cfg, "show_links", True))
        links_switch.toggled.connect(self._toggle_links)
        body.addWidget(links_switch)

        confirm_switch = NtSwitch("Подтверждать закрытие помощника",
                                  accent=theme.ACCENT)
        confirm_switch.set_checked_silently(
            not getattr(cfg, "skip_close_confirm", False))
        confirm_switch.toggled.connect(self._toggle_confirm)
        body.addWidget(confirm_switch)

        anim_switch = NtSwitch("Анимация логов", accent=theme.ACCENT)
        anim_switch.set_checked_silently(getattr(cfg, "log_animation", True))
        anim_switch.toggled.connect(self._toggle_log_animation)
        body.addWidget(anim_switch)

        layout.addWidget(section)

        marking = NeonSection("Разметка", theme.ACCENT)
        mark_body = marking.body()
        self._zone_btn = NtButton(self._overlay.zone_button_text(),
                                  accent=theme.ACCENT, upper=False)
        self._zone_btn.clicked.connect(self._mark_zone)
        mark_body.addWidget(self._zone_btn)
        mark_body.addWidget(self.hint(
            "Две области окна ежедневной награды: по чему его узнать и куда "
            "нажать. Первое нажатие показывает рамку над игрой, второе "
            "записывает, где она встала; потом то же для второй. Координаты "
            "уходят в лог помощника — панель для этого можно закрыть."))
        layout.addWidget(marking)

        size = NeonSection("Размер игры", theme.ACCENT)
        size_body = size.body()
        self._reference_btn = NtButton("Запомнить как эталонный",
                                       accent=theme.ACCENT, upper=False)
        self._reference_btn.clicked.connect(
            self._overlay.record_game_reference)
        size_body.addWidget(self._reference_btn)
        size_body.addWidget(self.hint(
            "Всё, что помощник знает про координаты игры, снято при одном её "
            "размере — развёрнутом на весь экран. Дальше он сам пересчитывает "
            "под любой другой: окна ужимаются в тех же долях, детекты и клики "
            "уезжают вместе с картинкой. Эталон записывается сам при первом "
            "запуске с развёрнутой игрой; нажимать сюда стоит, только если "
            "шаблоны пересняты заново или сменился монитор."))
        layout.addWidget(size)

    def _mark_zone(self):
        """Кнопка меняет подпись под текущий шаг, иначе по ней не понять,
        показываешь ты рамку или уже записываешь."""
        self._overlay.mark_zone()
        self._zone_btn.setText(self._overlay.zone_button_text())

    # ── Connector wires ──────────────────────────────────────────────────────

    def _toggle_links(self, enabled: bool):
        self._overlay.config.data.overlay.show_links = enabled
        self._overlay.config.save()
        self._overlay._links.set_enabled(enabled)
        self._overlay.add_log_segments(
            [("Линии между окнами — ", theme.TEXT_SECONDARY),
             ("включены" if enabled else "выключены",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")   # a setting's new value, not an action's outcome

    # ── Log animation ────────────────────────────────────────────────────────

    def _toggle_log_animation(self, enabled: bool):
        self._overlay.config.data.overlay.log_animation = enabled
        self._overlay.config.save()
        log_panel.set_animation_enabled(enabled)
        self._overlay.add_log_segments(
            [("Анимация логов — ", theme.TEXT_SECONDARY),
             ("включена" if enabled else "выключена",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")   # a setting's new value, not an action's outcome

    # ── Close confirmation ───────────────────────────────────────────────────

    def _toggle_confirm(self, enabled: bool):
        self._overlay.config.data.overlay.skip_close_confirm = not enabled
        self._overlay.config.save()
        self._overlay.add_log_segments(
            [("Подтверждение закрытия — ", theme.TEXT_SECONDARY),
             ("включено" if enabled else "выключено",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")   # a setting's new value, not an action's outcome
