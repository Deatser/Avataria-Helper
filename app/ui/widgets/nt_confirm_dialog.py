# app/ui/widgets/nt_confirm_dialog.py
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from app.ui import theme
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_checkbox import NtCheckBox


class NtConfirmDialog(QDialog):
    """Modal confirmation in the panel style, with an optional 'don't ask' box."""

    def __init__(self, title: str, message: str, confirm_text: str,
                 cancel_text: str = "Отмена", remember_text: str = None,
                 accent: str = None, parent=None):
        super().__init__(parent)
        self._accent = accent or theme.ACCENT_RED

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(420)

        self._panel = NtPanel(self, traces=False)
        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING + 4, theme.PADDING + 2,
                                  theme.PADDING + 4, theme.PADDING + 2)
        layout.setSpacing(theme.SPACING)

        head = QLabel(title.upper())
        head.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        head.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        layout.addWidget(head)

        body = QLabel(message)
        body.setWordWrap(True)
        body.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        body.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        layout.addWidget(body)
        layout.addSpacing(2)

        self._remember = None
        if remember_text:
            self._remember = NtCheckBox(remember_text)
            layout.addWidget(self._remember)
            layout.addSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)
        cancel = NtButton(cancel_text, upper=False, accent=theme.BORDER_BRIGHT)
        confirm = NtButton(confirm_text, upper=False,
                           accent=self._accent, outline=True, filled=True)
        for btn in (cancel, confirm):
            btn.setMinimumHeight(32)
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(confirm)
        layout.addLayout(row)

        self.setFixedHeight(layout.sizeHint().height())
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._center_on_parent()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def remembered(self) -> bool:
        """True when the user ticked the 'don't ask again' box."""
        return bool(self._remember and self._remember.isChecked())

    # ── Internals ─────────────────────────────────────────────────────────────

    def _center_on_parent(self):
        parent = self.parentWidget()
        if parent is None:
            return
        geo = parent.frameGeometry()
        self.move(geo.center().x() - self.width() // 2,
                  geo.center().y() - self.height() // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._panel.setGeometry(0, 0, self.width(), self.height())
