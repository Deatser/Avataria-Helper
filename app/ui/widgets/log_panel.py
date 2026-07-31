# app/ui/widgets/log_panel.py
from datetime import datetime
from html import escape
from PySide6.QtWidgets import QTextEdit
from app.ui import theme


class LogPanel(QTextEdit):
    """Scrolling log area in Nothing style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {theme.BG_BASE};
                color: {theme.TEXT_SECONDARY};
                border: 1px solid {theme.BORDER};
                padding: 6px;
                selection-background-color: {theme.TEXT_DIM};
            }}
            QScrollBar:vertical {{
                background: {theme.BG_BASE};
                width: 4px;
                border: none;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {theme.BORDER};
                min-height: 16px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

    def add_log(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        if level == "error":
            msg_color = theme.ACCENT_RED
        elif level == "success":
            msg_color = theme.ACCENT_GREEN
        else:
            msg_color = theme.TEXT_SECONDARY
        html = (
            f'<span style="color:{theme.TEXT_DIM}">[{ts}]</span> '
            f'<span style="color:{msg_color}">{escape(message)}</span>'
        )
        self.append(html)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_logs(self):
        self.clear()
