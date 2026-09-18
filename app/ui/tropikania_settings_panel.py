# app/ui/tropikania_settings_panel.py
"""Settings sheet for the Tropikania helper window — same shape as
HelperSettingsPanel, scoped to Tropikania's own config (tropikania_config.json)."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets.neon_section import NeonSection
from app.ui.widgets.nt_switch import NtSwitch

_W = 360
_H = 255


class TropikaniaSettingsPanel(SheetPanel):
    def __init__(self, overlay):
        self._overlay = overlay
        super().__init__(overlay, "НАСТРОЙКИ", (_W, _H), accent=theme.ACCENT_GREEN)

    def _build_body(self, layout: QVBoxLayout):
        cfg = self._overlay.config.data.overlay
        section = NeonSection("Tropikania Helper", theme.ACCENT_GREEN)
        body = section.body()

        links_switch = NtSwitch("Линии между окнами", accent=theme.ACCENT_GREEN)
        links_switch.set_checked_silently(getattr(cfg, "show_links", True))
        links_switch.toggled.connect(self._toggle_links)
        body.addWidget(links_switch)

        confirm_switch = NtSwitch("Подтверждать закрытие",
                                  accent=theme.ACCENT_GREEN)
        confirm_switch.set_checked_silently(
            not getattr(cfg, "skip_close_confirm", False))
        confirm_switch.toggled.connect(self._toggle_confirm)
        body.addWidget(confirm_switch)

        layout.addWidget(section)

    def _toggle_links(self, enabled: bool):
        self._overlay.config.data.overlay.show_links = enabled
        self._overlay.config.save()
        self._overlay._links.set_enabled(enabled)
        self._overlay.add_log_segments(
            [("Линии между окнами — ", theme.TEXT_SECONDARY),
             ("включены" if enabled else "выключены",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")

    def _toggle_confirm(self, enabled: bool):
        self._overlay.config.data.overlay.skip_close_confirm = not enabled
        self._overlay.config.save()
        self._overlay.add_log_segments(
            [("Подтверждение закрытия — ", theme.TEXT_SECONDARY),
             ("включено" if enabled else "выключено",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")
