# app/ui/widgets/log_panel.py
import random
from datetime import datetime
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import (QTextCursor, QTextCharFormat, QTextBlockFormat,
                           QColor)
from app.ui import theme

CLAUDE_ORANGE = "#E8712A"
_SCRAMBLE = "!@#$%^&*<>?/|[]{}~+=_-"

# Clearing the log, split-flap style: the wave takes _FLIP_SPAN characters to
# pass over a letter — long enough to read as flipping rather than blinking —
# and each line starts _LINE_LAG frames after the one above it, so the board
# empties from the top down instead of all at once.
_FLIP_MS   = 16
_FLIP_SPAN = 4
_LINE_LAG  = 2


class LogPanel(QTextEdit):
    """Scrolling log area — every message animates with scramble effect."""

    # Emitted for any clicked `<a href="...">` other than a `copy:` one —
    # the caller decides what the href means (see promo_window.py).
    anchor_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wiping = False   # a second press should not race the first
        self.setReadOnly(True)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        self.setStyleSheet(f"""
            QTextEdit {{
                background-color: rgba(5, 4, 10, 160);
                color: {theme.TEXT_SECONDARY};
                border: 1px solid {theme.BORDER_DIM};
                border-radius: {theme.RADIUS}px;
                padding: 10px 16px;
                selection-background-color: {theme.TEXT_DIM};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                border: none;
                margin: 6px 2px 6px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {theme.BORDER_BRIGHT};
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        # Translucent dark fill: panel traces stay visible, but dimmed
        self.viewport().setAutoFillBackground(True)
        self.setMouseTracking(True)   # for the hand cursor over copy: anchors

    # ── Public API ────────────────────────────────────────────────────────────

    def add_log(self, message: str, level: str = "info"):
        """Append a log line with scramble animation and status suffix."""
        self.add_log_segments([(message, theme.TEXT_SECONDARY)], level)

    def add_html(self, html: str):
        """One line of already-formatted HTML — no scramble, no timestamp.
        For content built with its own styling, like the promo-code
        listing's `copy:` anchors (see mousePressEvent)."""
        self.append(html)
        self._scroll_end()

    # ── copy: anchors ─────────────────────────────────────────────────────────
    # Any inserted HTML can carry <a href="copy:...">, and clicking it copies
    # whatever follows "copy:" to the clipboard — a generic hook, not tied to
    # promo codes specifically.

    def mousePressEvent(self, event):
        href = self.anchorAt(event.pos())
        if href.startswith("copy:"):
            QApplication.clipboard().setText(href[len("copy:"):])
            return
        if href:
            self.anchor_clicked.emit(href)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        href = self.anchorAt(event.pos())
        self.viewport().setCursor(
            Qt.PointingHandCursor if href else Qt.IBeamCursor)
        super().mouseMoveEvent(event)

    def add_log_segments(self, segments: list, level: str = "info"):
        """Same, but the message is built from pre-coloured (text, colour) parts.

        level="plain" prints the line as-is — for readings and events that are
        not the outcome of an action and need no status word.
        """
        segs = list(segments)
        if level != "plain":
            status = (("Ошибка", theme.ACCENT_RED) if level == "error"
                      else ("Успешно", theme.ACCENT_GREEN))
            segs += [(" — ", theme.TEXT_SECONDARY), status]
        # Fast scramble for regular logs (interval 22ms, min 10 frames)
        self.animate_log(segs, include_ts=True, interval_ms=22, min_frames=10)

    def blank_line(self):
        """Spacer so groups of related lines don't run together."""
        self.append("")
        self._scroll_end()

    # ── Scramble animation ────────────────────────────────────────────────────

    def animate_log(self, segments: list, include_ts: bool = True,
                    delay_ms: int = 0, on_done=None,
                    interval_ms: int = 38, min_frames: int = 18):
        """Reveal text left-to-right with scramble effect.

        segments — list of (text, color) pairs forming one log line.
        interval_ms / min_frames control animation speed.
        """
        if delay_ms > 0:
            QTimer.singleShot(
                delay_ms,
                lambda: self.animate_log(segments, include_ts, 0, on_done,
                                         interval_ms, min_frames),
            )
            return

        ts = datetime.now().strftime("%H:%M:%S") if include_ts else None
        full_len    = sum(len(t) for t, _ in segments)
        total_frames = max(min_frames, full_len)

        self.append("")
        block_num = self.document().blockCount() - 1
        self._set_line_height(block_num)
        self._write_block(block_num, self._build_frame(segments, 0), ts)

        def tick(f):
            revealed = int((f / total_frames) * full_len)
            self._write_block(block_num, self._build_frame(segments, revealed), ts)
            if f < total_frames:
                QTimer.singleShot(interval_ms, lambda: tick(f + 1))
            elif on_done:
                on_done()

        QTimer.singleShot(interval_ms, lambda: tick(1))

    # ── Internals ─────────────────────────────────────────────────────────────

    def _build_frame(self, segments: list, revealed: int) -> list:
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

    def _set_line_height(self, block_num: int):
        """Airy leading — log lines should not sit on top of each other."""
        block = self.document().findBlockByNumber(block_num)
        if not block.isValid():
            return
        fmt = QTextBlockFormat()
        fmt.setLineHeight(145, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        QTextCursor(block).setBlockFormat(fmt)

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
            fmt.setForeground(QColor(theme.LOG_TS_COLOR))
            cursor.setCharFormat(fmt)
            cursor.insertText(f"[{ts}] ")

        for text, color in char_segs:
            fmt.setForeground(QColor(color))
            cursor.setCharFormat(fmt)
            cursor.insertText(text)

        self._scroll_end()

    def _scroll_end(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Clearing ──────────────────────────────────────────────────────────────
    # A departure board emptying out: every character flips through a few
    # glyphs and then is gone, the wave running along each line and the lines
    # starting one after another from the top.

    def clear_logs(self):
        if self._wiping or self.document().isEmpty():
            self.clear()
            return
        lines = [self._block_segments(i)
                 for i in range(self.document().blockCount())]
        if not any(segs for segs in lines):
            self.clear()
            return

        self._wiping = True
        longest = max((sum(len(t) for t, _ in segs) for segs in lines),
                      default=0)
        frames = int(longest + _FLIP_SPAN + len(lines) * _LINE_LAG) + 1
        self._wipe_frame(lines, 0, frames)

    def _wipe_frame(self, lines: list, frame: int, frames: int):
        if frame > frames:
            self.clear()
            self._wiping = False
            return

        for index, segments in enumerate(lines):
            gone = frame - index * _LINE_LAG          # this line's own clock
            self._write_block(index, self._flip_frame(segments, gone))

        QTimer.singleShot(_FLIP_MS,
                          lambda: self._wipe_frame(lines, frame + 1, frames))

    def _flip_frame(self, segments: list, gone: float) -> list:
        """One line mid-flip: settled, flipping and blank, in that order."""
        result, index = [], 0
        for text, colour in segments:
            buf, current = "", None
            for ch in text:
                lead = gone - index      # how far the wave has passed this one
                if lead <= 0:
                    out, c = ch, colour              # not reached yet
                elif lead < _FLIP_SPAN and ch != " ":
                    out, c = random.choice(_SCRAMBLE), theme.TEXT_DIM
                else:
                    out, c = " ", colour             # flipped away
                index += 1
                if c == current:
                    buf += out
                else:
                    if buf:
                        result.append((buf, current))
                    buf, current = out, c
            if buf:
                result.append((buf, current))
        return result

    def _block_segments(self, block_num: int) -> list:
        """A block's text back out as (text, colour) runs, colours intact."""
        block = self.document().findBlockByNumber(block_num)
        if not block.isValid():
            return []
        text = block.text()
        runs = [(text[r.start:r.start + r.length],
                 r.format.foreground().color().name())
                for r in block.textFormats() if r.length > 0]
        return runs or ([(text, theme.TEXT_SECONDARY)] if text else [])
