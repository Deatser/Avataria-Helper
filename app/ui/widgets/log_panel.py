# app/ui/widgets/log_panel.py
import random
from datetime import datetime
from html import escape
from PySide6.QtWidgets import QTextEdit
from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor
from app.ui import theme

CLAUDE_ORANGE = "#E8712A"
_SCRAMBLE = "!@#$%^&*<>?/|[]{}~+=_-"


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

    # ── Normal log ───────────────────────────────────────────────────────────

    def add_log(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        color = self._level_color(level)
        html = (
            f'<span style="color:{theme.TEXT_DIM}">[{ts}]</span> '
            f'<span style="color:{color}">{escape(message)}</span>'
        )
        self.append(html)
        self._scroll_end()

    # ── Scramble animation ───────────────────────────────────────────────────

    def animate_log(self, segments: list, include_ts: bool = True,
                    delay_ms: int = 0, on_done=None):
        """Reveal text left-to-right with a scramble effect.

        segments — list of (text, color) pairs that form one log line.
        """
        if delay_ms > 0:
            QTimer.singleShot(delay_ms,
                              lambda: self.animate_log(segments, include_ts, 0, on_done))
            return

        ts = datetime.now().strftime("%H:%M:%S") if include_ts else None
        full_len = sum(len(t) for t, _ in segments)
        total_frames = max(18, full_len)

        self.append("")
        block_num = self.document().blockCount() - 1
        self._write_block(block_num, self._build_frame(segments, 0), ts)

        def tick(f):
            revealed = int((f / total_frames) * full_len)
            self._write_block(block_num, self._build_frame(segments, revealed), ts)
            if f < total_frames:
                QTimer.singleShot(38, lambda: tick(f + 1))
            elif on_done:
                on_done()

        QTimer.singleShot(38, lambda: tick(1))

    # ── Internals ────────────────────────────────────────────────────────────

    def _build_frame(self, segments: list, revealed: int) -> list:
        """Return [(text, color)] for current animation frame."""
        result, char_idx = [], 0
        for text, target_color in segments:
            buf, cur_color = "", None
            for ch in text:
                if ch == " ":
                    c, out = target_color, " "
                elif char_idx < revealed:
                    c, out = target_color, ch
                else:
                    c, out = theme.TEXT_DIM, random.choice(_SCRAMBLE)
                char_idx += 1
                if c == cur_color:
                    buf += out
                else:
                    if buf:
                        result.append((buf, cur_color))
                    buf, cur_color = out, c
            if buf:
                result.append((buf, cur_color))
        return result

    def _write_block(self, block_num: int, char_segs: list, ts: str = None):
        block = self.document().findBlockByNumber(block_num)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                            QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()

        fmt = QTextCharFormat()
        if ts:
            fmt.setForeground(QColor(theme.TEXT_DIM))
            cursor.setCharFormat(fmt)
            cursor.insertText(f"[{ts}] ")

        for text, color in char_segs:
            fmt.setForeground(QColor(color))
            cursor.setCharFormat(fmt)
            cursor.insertText(text)

        self._scroll_end()

    def _level_color(self, level: str) -> str:
        return {
            "error":   theme.ACCENT_RED,
            "success": theme.ACCENT_GREEN,
            "orange":  CLAUDE_ORANGE,
        }.get(level, theme.TEXT_SECONDARY)

    def _scroll_end(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_logs(self):
        self.clear()
