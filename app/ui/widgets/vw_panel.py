# app/ui/widgets/vw_panel.py
"""Vaporwave-aesthetic panel for the Ava Dancers module window."""
import math
from pathlib import Path

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (QPainter, QColor, QPen, QLinearGradient, QBrush,
                           QPainterPath, QPolygonF, QMovie, QPixmap, QImage)
from PySide6.QtCore import (Qt, QRectF, QPointF, QTimer, QUrl, Signal,
                            QVariantAnimation, QEasingCurve)
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from app.ui import theme

VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".wmv")

# Fixed star field: (x%, y_in_sky%, radius_px)
_STARS = [
    (0.08, 0.06, 1), (0.15, 0.18, 1), (0.22, 0.04, 2), (0.30, 0.12, 1),
    (0.38, 0.22, 1), (0.45, 0.03, 1), (0.52, 0.14, 2), (0.60, 0.08, 1),
    (0.68, 0.20, 1), (0.75, 0.05, 1), (0.82, 0.16, 2), (0.90, 0.10, 1),
    (0.12, 0.28, 1), (0.25, 0.32, 1), (0.35, 0.38, 1), (0.50, 0.30, 2),
    (0.65, 0.24, 1), (0.80, 0.35, 1), (0.93, 0.28, 1), (0.04, 0.40, 1),
    (0.58, 0.42, 1), (0.72, 0.08, 1), (0.42, 0.26, 1),
]

# Mountain ridge silhouette as (x%, peak height as % of the sky) pairs
_RIDGE = [
    (0.00, 0.00), (0.06, 0.16), (0.13, 0.05), (0.21, 0.26), (0.28, 0.10),
    (0.36, 0.22), (0.44, 0.34), (0.50, 0.20), (0.57, 0.31), (0.64, 0.14),
    (0.71, 0.28), (0.79, 0.09), (0.86, 0.21), (0.93, 0.07), (1.00, 0.15),
]

_HORIZON     = 0.46   # horizon line, share of panel height
_GRID_N_VERT = 26     # vertical lines across three screen widths
_GRID_N_DEEP = 11     # scrolling depth lines
_SUN_R       = 0.17   # sun radius, share of the smaller panel side
_VEIL_ALPHA  = 95     # darkening over the scene so controls stay readable
_FADE_MS     = 320    # cross-fade when the backdrop is swapped


class VwPanel(QWidget):
    """Vaporwave panel: sunset grid scene, or a user-supplied video/GIF/image."""

    background_failed = Signal(str)   # playback died after it had started

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase  = 0.0
        self._movie  = None    # animated GIF background, if configured
        self._pixmap = None    # static image background, if configured
        self._player = None    # video background, if configured
        self._sink   = None
        self._frame  = None    # latest decoded video frame

        # Where to bias the crop when KeepAspectRatioByExpanding overflows
        # one axis — e.g. a 16:9 clip in a tall, narrow panel overflows in
        # width, and by default that overflow is trimmed evenly off both
        # sides. -1.0 keeps the source's left/top edge in frame, +1.0 keeps
        # its right/bottom edge, 0.0 is the old centred crop. Public: the
        # owning window sets it once, right after construction, to whatever
        # its own backdrop needs — see StatsWindow / Overlay.
        self.focus_x = 0.0
        self.focus_y = 0.0

        self._fade        = 0.0    # opacity of the outgoing backdrop
        self._fade_pixmap = None
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(_FADE_MS)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade_anim.valueChanged.connect(self._on_fade)

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ── Custom background ────────────────────────────────────────────────────

    def set_background(self, path: str, fade: bool = False) -> bool:
        """Use a video, GIF or image from disk instead of the drawn scene.

        Returns False if the file is missing or unreadable — the caller can
        report that; the panel just keeps drawing its own scene. Video decodes
        asynchronously, so a later failure arrives on `background_failed`.
        """
        file = Path(path) if path else None
        if file is not None and not file.is_file():
            return False
        if fade:
            self._start_fade()   # snapshot before the old source is dropped

        self.stop_background()
        if file is None:
            return True

        suffix = file.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            if not self._start_video(file):
                return False
        elif suffix == ".gif":
            movie = QMovie(str(file))
            if not movie.isValid():
                return False
            movie.frameChanged.connect(self.update)
            movie.start()
            self._movie = movie
        else:
            pixmap = QPixmap(str(file))
            if pixmap.isNull():
                return False
            self._pixmap = pixmap

        self._timer.stop()   # nothing procedural left to animate
        self.update()
        return True

    def _start_video(self, file: Path) -> bool:
        """Decode into a sink and paint the frames ourselves — a video widget
        would sit on top of the panel and break the rounded clip."""
        sink   = QVideoSink(self)
        player = QMediaPlayer(self)
        player.setVideoSink(sink)
        player.setLoops(QMediaPlayer.Loops.Infinite)   # silent, no audio output
        sink.videoFrameChanged.connect(self._on_video_frame)
        player.errorOccurred.connect(self._on_video_error)
        player.setSource(QUrl.fromLocalFile(str(file.resolve())))
        player.play()
        self._player = player
        self._sink   = sink
        return True

    @property
    def backdrop_ready(self) -> bool:
        """False only while a configured video has yet to decode a frame.

        Anything else — the drawn sunset scene, a still, a GIF — can be
        painted the instant it is asked for. A video cannot: for the first
        moments after the window opens there is nothing decoded, and the
        panel falls back to the scene. Whoever needs a true picture of this
        window (the switch-on animation does) has to wait for this.
        """
        if self._player is None:
            return True
        return self._frame is not None

    def _on_video_frame(self, frame):
        # A frame already queued in Qt's event loop when stop_background()
        # swapped self._sink for a new source (or None) still arrives here —
        # deleteLater() only schedules the old sink's destruction, it does
        # not cancel signals already in flight. Without this check that
        # stale frame would win the race and flash the old backdrop back up
        # for the one paint before the *next* frame (spamming the video/
        # photo toggle was the reliable way to catch it).
        if self.sender() is not self._sink:
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._frame = QImage(image)   # detach: the frame buffer is reused
        self.update()

    def _on_video_error(self, error, message: str = ""):
        if self.sender() is not self._player:
            return   # a stale error from a player already replaced
        if error == QMediaPlayer.Error.NoError:
            return
        self.stop_background()
        self.background_failed.emit(f"Видео-фон не воспроизводится: {message}")

    def stop_background(self):
        """Release the decoder. Call it when the window closes — a running
        player keeps decoding and pushing frames at a dying widget."""
        if self._player is not None:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
            self._sink   = None
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
        self._frame  = None
        self._pixmap = None
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        self._phase = (self._phase + 0.003) % 1.0
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        body = QPainterPath()
        body.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1),
                            theme.PANEL_RADIUS, theme.PANEL_RADIUS)

        painter.save()
        painter.setClipPath(body)
        self._paint_backdrop(painter, w, h)
        # Cross-fade: the previous backdrop lingers on top, fading out
        if self._fade_pixmap is not None and self._fade > 0:
            painter.setOpacity(self._fade)
            painter.drawPixmap(0, 0, self._fade_pixmap)
            painter.setOpacity(1.0)
        painter.restore()

        # Hairline edge + magenta highlight along the top
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        bc = QColor(theme.VW_BORDER); bc.setAlpha(200)
        painter.setPen(QPen(bc, 1))
        painter.drawPath(body)

        edge = QLinearGradient(0, 0, w, 0)
        m0 = QColor(theme.VW_MAGENTA); m0.setAlpha(0)
        m1 = QColor(theme.VW_MAGENTA); m1.setAlpha(120)
        edge.setColorAt(0.0, m0)
        edge.setColorAt(0.35, m1)
        edge.setColorAt(1.0, m0)
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawLine(theme.PANEL_RADIUS, 1, w - theme.PANEL_RADIUS, 1)

        painter.end()

    def _paint_backdrop(self, painter, w, h):
        if not self._draw_media(painter, w, h):
            self._draw_scene(painter, w, h)

    # ── Cross-fade between backdrops ─────────────────────────────────────────

    def _snapshot_backdrop(self):
        """Freeze what is on screen now so the new backdrop can fade in."""
        if self.width() <= 0 or self.height() <= 0:
            return None
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_backdrop(painter, self.width(), self.height())
        painter.end()
        return pixmap

    def _start_fade(self):
        self._fade_anim.stop()
        self._fade_pixmap = self._snapshot_backdrop()
        if self._fade_pixmap is None:
            return
        self._fade = 1.0
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.start()

    def _on_fade(self, value):
        self._fade = float(value)
        if self._fade <= 0:
            self._fade_pixmap = None
        self.update()

    def _draw_media(self, painter, w, h) -> bool:
        """Cover the panel with the configured media, cropping the overflow.
        False when nothing is configured yet — the scene gets drawn instead."""
        if self._frame is not None:
            source = QPixmap.fromImage(self._frame)
        elif self._movie is not None:
            source = self._movie.currentPixmap()
        elif self._pixmap is not None:
            source = self._pixmap
        else:
            return False
        if source.isNull():
            return False   # first frame not decoded yet — show the scene

        scaled = source.scaled(w, h, Qt.KeepAspectRatioByExpanding,
                               Qt.SmoothTransformation)
        x = self._cropped_offset(w, scaled.width(),  self.focus_x)
        y = self._cropped_offset(h, scaled.height(), self.focus_y)
        painter.drawPixmap(x, y, scaled)
        painter.fillRect(QRectF(0, 0, w, h), QColor(6, 2, 20, _VEIL_ALPHA))
        return True

    @staticmethod
    def _cropped_offset(panel_size: int, scaled_size: int, focus: float) -> int:
        """Where to draw an axis whose scaled media overflows the panel.

        0 at focus=0 (the old centred crop); as focus moves towards ±1 the
        crop slides towards the source's trailing/leading edge instead,
        clamped so it can never slide past the point where a gap would open
        up on one side.
        """
        centred = (panel_size - scaled_size) // 2
        if scaled_size <= panel_size:
            return centred   # nothing overflows on this axis — focus is moot
        slack = (scaled_size - panel_size) // 2
        offset = centred - int(slack * max(-1.0, min(1.0, focus)))
        return max(panel_size - scaled_size, min(0, offset))

    def _draw_scene(self, painter, w, h):
        horizon = h * _HORIZON

        # 1. Sky and floor
        sky = QLinearGradient(0, 0, 0, horizon)
        sky.setColorAt(0.0, QColor("#150a35"))
        sky.setColorAt(1.0, QColor("#3d1063"))
        painter.fillRect(QRectF(0, 0, w, horizon), QBrush(sky))

        floor = QLinearGradient(0, horizon, 0, h)
        floor.setColorAt(0.0, QColor("#1a0740"))
        floor.setColorAt(1.0, QColor(theme.VW_BG))
        painter.fillRect(QRectF(0, horizon, w, h - horizon), QBrush(floor))

        # 2. Stars
        painter.setPen(Qt.NoPen)
        for i, (xr, yr, size) in enumerate(_STARS):
            alpha = int(80 + 90 * (0.5 + 0.5 * math.sin(
                2 * math.pi * (self._phase * 2.5 + i * 0.41)
            )))
            painter.setBrush(QColor(210, 200, 255, alpha))
            painter.drawEllipse(QPointF(xr * w, yr * horizon), size, size)

        self._draw_sun(painter, w, h, horizon)
        self._draw_ridge(painter, w, horizon)
        self._draw_horizon(painter, w, horizon)
        self._draw_grid(painter, w, h, horizon)

        # Veil: the scene is a backdrop, the controls on top must stay legible
        painter.fillRect(QRectF(0, 0, w, h), QColor(6, 2, 20, _VEIL_ALPHA))

    def _draw_sun(self, painter, w, h, horizon):
        radius = min(w, h) * _SUN_R
        centre = QPointF(w / 2, horizon - radius * 0.42)

        # The horizon cuts the sun off — nothing of it spills onto the floor
        painter.save()
        painter.setClipRect(QRectF(0, 0, w, horizon))

        grad = QLinearGradient(0, centre.y() - radius, 0, centre.y() + radius)
        grad.setColorAt(0.0, QColor("#ffd166"))
        grad.setColorAt(0.45, QColor("#ff3d8b"))
        grad.setColorAt(1.0, QColor("#a4198a"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(centre, radius, radius)

        # Cut the classic horizontal slots out of the lower half
        painter.setBrush(QColor("#150a35"))
        for i in range(5):
            band_y = centre.y() + radius * (0.18 + i * 0.17)
            band_h = radius * (0.035 + i * 0.022)
            painter.drawRect(QRectF(centre.x() - radius, band_y,
                                    radius * 2, band_h))
        painter.restore()

    def _draw_ridge(self, painter, w, horizon):
        points = [QPointF(0, horizon)]
        points += [QPointF(xr * w, horizon - hr * horizon * 0.55)
                   for xr, hr in _RIDGE]
        points.append(QPointF(w, horizon))

        grad = QLinearGradient(0, horizon * 0.55, 0, horizon)
        grad.setColorAt(0.0, QColor("#ff4d9d"))
        grad.setColorAt(1.0, QColor("#5a1160"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPolygon(QPolygonF(points))

    def _draw_horizon(self, painter, w, horizon):
        for i in range(6, 0, -1):
            glow = QColor(theme.VW_MAGENTA)
            glow.setAlpha(9 * (7 - i))
            painter.setPen(QPen(glow, i * 2))
            painter.drawLine(QPointF(0, horizon), QPointF(w, horizon))
        painter.setPen(QPen(QColor("#ffd6f2"), 1.6))
        painter.drawLine(QPointF(0, horizon), QPointF(w, horizon))

    def _draw_grid(self, painter, w, h, horizon):
        """Perspective floor: full-width depth lines, verticals running off
        the sides instead of pinching into a triangle."""
        painter.setRenderHint(QPainter.Antialiasing, True)
        depth = h - horizon
        if depth <= 0:
            return
        vanish = QPointF(w / 2, horizon)

        # Verticals: sampled across three screen widths so the visible band
        # stays dense and reaches both edges.
        for i in range(_GRID_N_VERT + 1):
            x_bottom = -w + 3 * w * i / _GRID_N_VERT
            fade = 1.0 - min(1.0, abs(x_bottom - w / 2) / (1.6 * w))
            colour = QColor(theme.VW_MAGENTA)
            colour.setAlpha(int(40 + 90 * fade))
            painter.setPen(QPen(colour, 1))
            painter.drawLine(vanish, QPointF(x_bottom, h))

        # Depth lines: span the full width, spacing compressed towards the
        # horizon, scrolling towards the viewer.
        for j in range(_GRID_N_DEEP + 2):
            z = ((j / _GRID_N_DEEP) + self._phase) % 1.0
            if z < 0.015:
                continue
            y = horizon + depth * (z ** 1.9)
            if y > h:
                continue
            colour = QColor(theme.VW_MAGENTA)
            colour.setAlpha(int(35 + 150 * z))
            painter.setPen(QPen(colour, 1 + z))
            painter.drawLine(QPointF(0, y), QPointF(w, y))
