from __future__ import annotations
import time
import threading
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.capture import ScreenCapture
from app.core.input_sender import press_key

# Region containing all 4 dance tiles
TILE_REGION: dict = {"left": 930, "top": 1050, "width": 700, "height": 150}

# Detection parameters
_TILE_CROP    = 150   # crop each tile to this square
_SKIP_TOP     = 75    # ignore top N pixels (UI chrome)
_GRAY_THRESH  = 40    # grayscale threshold for "white"

# Tile index → keyboard key
KEY_MAP: dict[int, str] = {0: "a", 1: "s", 2: "w", 3: "d"}


# ── Pure image processing functions (no Qt, no win32) ──────────────────────

def split_tiles(img: np.ndarray) -> list[np.ndarray]:
    """Split a 4-tile row image into 4 equal cropped squares."""
    h, w = img.shape[:2]
    tw = w // 4
    tiles = []
    for i in range(4):
        col = img[:, i * tw:(i + 1) * tw]
        ch, cw = col.shape[:2]
        cs = min(_TILE_CROP, ch, cw)
        cx, cy = cw // 2, ch // 2
        tile = col[cy - cs // 2:cy + cs // 2, cx - cs // 2:cx + cs // 2]
        tiles.append(tile)
    return tiles


def detect_tile(tile: np.ndarray, min_active: int) -> tuple[int, bool]:
    """Return (white_pixel_count, is_active). Ignores top _SKIP_TOP rows."""
    gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    gray = gray[_SKIP_TOP:, :]
    _, thresh = cv2.threshold(gray, _GRAY_THRESH, 255, cv2.THRESH_BINARY)
    white = int(np.sum(thresh == 255))
    if white < min_active:
        return white, False
    if is_fake_tile(thresh):
        return white, False
    return white, True


def is_fake_tile(thresh: np.ndarray) -> bool:
    """Return True if white mass is offset bottom-right (false positive indicator)."""
    ys, xs = np.where(thresh == 255)
    if len(xs) == 0:
        return False
    h, w = thresh.shape
    off_x = float(np.mean(xs)) - w / 2
    off_y = float(np.mean(ys)) - h / 2
    return off_x > 15 and off_y > 8


# ── QThread bot ─────────────────────────────────────────────────────────────

class AvaBot(QThread):
    tile_detected = Signal(int, str)   # tile_id, key
    key_pressed   = Signal(str)        # key name
    stats_updated = Signal(dict)       # {tile_id: white_pixel_count}
    error         = Signal(str)

    def __init__(self, game_hwnd: int, min_active: int = 3500):
        super().__init__()
        self._hwnd       = game_hwnd
        self._min_active = min_active
        self._stop_event = threading.Event()
        self._prev_tiles: set[int]        = set()
        self._last_time:  dict[int, float] = {}
        self._cooldown   = 0.15

    def configure(self, min_active: int):
        self._min_active = min_active

    def stop_bot(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        self._prev_tiles.clear()
        capture = ScreenCapture.get()

        while not self._stop_event.is_set():
            try:
                img    = capture.grab(TILE_REGION)
                tiles  = split_tiles(img)
                stats  = {}
                active = []

                for i, tile in enumerate(tiles):
                    white, is_active = detect_tile(tile, self._min_active)
                    stats[i] = white
                    if is_active:
                        active.append(i)

                for tile_id in set(active) - self._prev_tiles:
                    self._try_press(tile_id)

                self._prev_tiles = set(active)

            except Exception as exc:
                self.error.emit(str(exc))

            time.sleep(0.02)

    def _try_press(self, tile_id: int):
        now  = time.time()
        last = self._last_time.get(tile_id, 0.0)
        if now - last < self._cooldown:
            return
        key = KEY_MAP.get(tile_id)
        if not key:
            return
        self.tile_detected.emit(tile_id, key)
        if press_key(self._hwnd, key):
            self.key_pressed.emit(key)
            self._last_time[tile_id] = now
