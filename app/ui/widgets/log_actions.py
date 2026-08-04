# app/ui/widgets/log_actions.py
"""The copy + clear button pair every module's own log header carries.

Used to be a lone clear button, sized 22x20 — its own odd shape, matching
nothing else in the window. Both buttons here are the same rounded square
the window's own close/collapse/star buttons already use (24x24), copy
sitting to clear's own left.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QHBoxLayout

from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton

_BTN_SIZE = 24

_COPY_GLYPH  = "⧉"
_CLEAR_GLYPH = "×"   # same glyph the window's own close button uses


def build_log_actions(log: LogPanel, accent: str, on_clear=None) -> QHBoxLayout:
    """Copy (left) then clear (right). `on_clear`, if given, replaces the
    plain log.clear_logs() default — a couple of modules clear extra state
    alongside the log text itself and still need to do that."""
    row = QHBoxLayout()
    row.setSpacing(6)

    copy_btn = NtButton(_COPY_GLYPH, accent=accent)
    copy_btn.setFixedSize(_BTN_SIZE, _BTN_SIZE)
    copy_btn.setToolTip("Скопировать логи")
    copy_btn.clicked.connect(lambda: _copy_logs(log))
    row.addWidget(copy_btn)

    clear_btn = NtButton(_CLEAR_GLYPH, accent=accent)
    clear_btn.setFixedSize(_BTN_SIZE, _BTN_SIZE)
    clear_btn.setToolTip("Очистить логи")
    clear_btn.clicked.connect(on_clear or log.clear_logs)
    row.addWidget(clear_btn)

    return row


def _copy_logs(log: LogPanel):
    text = log.toPlainText()
    clipboard = QApplication.clipboard()
    if not text:
        log.add_log("Логи пусты — копировать нечего", level="plain")
        return
    clipboard.setText(text)
    log.add_log("Логи скопированы в буфер обмена", level="plain")
