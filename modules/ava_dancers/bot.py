from __future__ import annotations
import time
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
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

# Value bands. An idle tile is not black — it is a dim teal panel that tops out
# around V=98, so anything below _IDLE_V is background, not a glyph.
_IDLE_V = 110   # above this a pixel is part of the drawing, not the panel
_LIT_V  = 150   # the neon glyph itself; every icon peaks well past 200

# Saturation floors
_NEON_S = 180   # saturated enough that the hue names a real colour
_RED_S  = 90    # red neon fades into a dim halo — that halo still counts

# Hue bands, OpenCV scale 0..179 (measured from tests/fixtures/hsv_*.png)
_RED_LOW,   _RED_HIGH   = 14, 160   # bad tile: red neon, wraps around 0
_GREEN_LOW, _GREEN_HIGH = 35, 78    # bonus tile: green star
_CYAN_LOW,  _CYAN_HIGH  = 79, 125   # good tile: cyan arrow

# Tile kinds
EMPTY, NICE, BONUS, BAD, DISLIKE = "empty", "nice", "bonus", "bad", "dislike"
PRESSABLE = (NICE, BONUS)

# Tile index → keyboard key
KEY_MAP: dict[int, str] = {0: "a", 1: "s", 2: "w", 3: "d"}

# ── Pacing ───────────────────────────────────────────────────────────────
# Detection is single-frame, edge-triggered: a press fires the instant a tile
# reads as pressable, with no confirmation delay. That's deliberate — any
# scheme that waits for a second frame before acting adds a full poll
# interval of latency to every press, not just the rare bad ones, and at high
# game speed that latency is what breaks combo. Robustness against a single
# smeared frame (previous glyph fading out, next fading in on the same tile)
# is handled inside classify_hsv() instead, at zero extra latency — see
# _RED_TAINT_DIV.
_POLL_SLEEP = 0.01    # main loop tick
_COOLDOWN   = 0.09    # per-tile debounce against flicker across the threshold
_RED_TAINT_DIV = 3    # a third of the BAD threshold already counts as tainted

# ── Post-press settle ────────────────────────────────────────────────────
# Hitting a tile triggers its own flash/pop animation — a burst that floods
# most of the tile in a colour that can itself pass as a fresh pressable
# reading (a bonus star's pop flashes cyan) or as BAD (its red afterglow
# saturates as strongly as a real red glyph, so hue alone can't tell them
# apart — confirmed from tests/fixtures/debug captures). Left unhandled, the
# flash re-triggers a second press on the same tile, burning the cooldown
# window a real next note would need. Since we know exactly when we caused
# it, we settle that lane after our own press instead of trusting colour:
# ignore it until it's visibly back near idle, capped so a stuck reading
# can't blind the lane forever.
_SETTLE_MAX      = 0.6    # hard cap — longer than any observed pop+decay
_SETTLE_CLEAR_LIT = 0.02  # lit share this low means the flash has cleared

# ── Debug recording ─────────────────────────────────────────────────────
# At very high game speed a tile can cross from "just arriving" to "must be
# hit" in well under half a second, and no one has actually seen what the
# capture looks like during that window — every threshold so far was tuned
# from a single settled frame per kind, grabbed by hand at low speed. Rather
# than guess a fourth set of constants, the bot keeps a rolling few seconds
# of every tile it ever captured (full height, not the _SKIP_TOP crop used
# for classification, since the approach may only be visible up there) and
# can dump it to disk on demand — real frames from the actual failure, not
# another blind tuning pass.
_DEBUG_SECONDS = 2.5
_DEBUG_FRAMES  = int(_DEBUG_SECONDS / _POLL_SLEEP)
_DEBUG_MIN_V   = _IDLE_V   # below this a pixel is background noise, skip saving it


@dataclass
class Thresholds:
    """Cut-offs for the four measured shares, 0..1."""
    lit_share: float = 0.04   # lit pixels (of the tile) before a tile is "on"
    red_share: float = 0.03   # red pixels (of the tile) that make a tile bad
    hue_share: float = 0.15   # share *of the lit pixels* one hue must own


@dataclass
class TileReading:
    kind:  str
    lit:   float   # share of the tile taken up by neon
    red:   float   # share of the tile that is red, halo included
    green: float   # share of the *lit* pixels that are green
    cyan:  float   # share of the *lit* pixels that are cyan

    @property
    def pressable(self) -> bool:
        return self.kind in PRESSABLE

    @property
    def colour(self) -> float:
        """How strongly the tile owns a pressable hue — for the UI readout."""
        return max(self.green, self.cyan)


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


def classify_tile(tile: np.ndarray, th: Thresholds = None) -> TileReading:
    """Identify what is on a BGR tile, ignoring the top rows."""
    if tile.size == 0:
        return TileReading(EMPTY, 0.0, 0.0, 0.0, 0.0)
    return classify_hsv(cv2.cvtColor(tile, cv2.COLOR_BGR2HSV), th)


def classify_hsv(hsv: np.ndarray, th: Thresholds = None) -> TileReading:
    """Identify what is on a tile from an HSV image, ignoring the top rows.

    Counting bright pixels cannot do this — the four active kinds all light up
    to roughly the same extent (~25% of the tile), and the idle panel is itself
    bright enough to beat a naive floor. What separates them is *which* hue owns
    the neon:

      empty    nothing lit at all
      nice     lit pixels are saturated cyan          (S≈240, H≈90)
      bonus    lit pixels are green                   (H≈65)
      bad      a wide red halo around the glyph       (H≈175 or H≈5, S≈120)
      dislike  lit, but no hue owns it — washed out   (S≈70, no red halo)

    Hue is only meaningful on saturated pixels, so the green/cyan tests run on
    _NEON_S and up. Red gets a lower floor: red neon bleeds a dim halo that is
    most of its red signal, and nothing else on a tile is red at all.
    """
    th  = th or Thresholds()
    hsv = hsv[_SKIP_TOP:, :]
    if hsv.size == 0:
        return TileReading(EMPTY, 0.0, 0.0, 0.0, 0.0)

    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    total = hue.size

    lit   = val >= _LIT_V
    neon  = lit & (sat >= _NEON_S)
    red   = (val >= _IDLE_V) & (sat >= _RED_S) & \
            ((hue <= _RED_LOW) | (hue >= _RED_HIGH))
    green = neon & (hue >= _GREEN_LOW) & (hue <= _GREEN_HIGH)
    cyan  = neon & (hue >= _CYAN_LOW)  & (hue <= _CYAN_HIGH)

    n_lit       = int(lit.sum())
    lit_share   = n_lit / total
    red_share   = float(red.sum()) / total
    # Green and cyan are shares of the lit area, not of the tile, so a small
    # arrow and a big star are judged the same way.
    green_share = float(green.sum()) / n_lit if n_lit else 0.0
    cyan_share  = float(cyan.sum())  / n_lit if n_lit else 0.0

    if lit_share < th.lit_share:
        kind = EMPTY
    elif red_share >= th.red_share:
        kind = BAD
    elif red_share >= th.red_share / _RED_TAINT_DIV:
        # Too little red to call BAD outright, but real cyan/green tiles carry
        # ~0% red (see tests/fixtures/hsv_*.png) — this share only shows up
        # while one glyph is fading out and the next fades in on top of it in
        # the same tile. Treat it as unresolved rather than risk pressing a
        # tile that is a red one mid-reveal.
        kind = DISLIKE
    elif green_share >= th.hue_share and green_share >= cyan_share:
        kind = BONUS
    elif cyan_share >= th.hue_share:
        kind = NICE
    else:
        # Lit, not red, and no hue owns it. Falling through to DISLIKE rather
        # than to a press is the safe way round: a missed bonus costs nothing,
        # a pressed dislike costs points.
        kind = DISLIKE
    return TileReading(kind, lit_share, red_share, green_share, cyan_share)


# ── QThread bot ─────────────────────────────────────────────────────────────

class AvaBot(QThread):
    tile_detected = Signal(int, str)   # tile_id, key — a tile worth pressing
    tile_seen     = Signal(int, str)   # tile_id, kind — any tile that lit up
    key_pressed   = Signal(str)        # key name
    error         = Signal(str)

    def __init__(self, game_hwnd: int, thresholds: Thresholds = None):
        super().__init__()
        self._hwnd       = game_hwnd
        self._th         = thresholds or Thresholds()
        self._stop_event = threading.Event()
        self._prev_kinds: list[str]        = [EMPTY] * 4
        self._last_time:  dict[int, float] = {}
        self._settle_until: dict[int, float] = {}   # tile_id -> ignore-until timestamp
        self._debug: deque = deque(maxlen=_DEBUG_FRAMES)
        self._debug_lock = threading.Lock()

    def configure(self, thresholds: Thresholds):
        self._th = thresholds

    def stop_bot(self):
        self._stop_event.set()

    def run(self):
        try:
            self._loop()
        finally:
            # This thread dies on every stop — hand its GDI context back
            ScreenCapture.release()

    def _loop(self):
        self._stop_event.clear()
        self._prev_kinds   = [EMPTY] * 4
        self._settle_until = {}
        with self._debug_lock:
            self._debug.clear()
        capture = ScreenCapture.get()

        while not self._stop_event.is_set():
            try:
                img      = capture.grab(TILE_REGION)
                tiles    = split_tiles(img)
                readings = [classify_tile(t, self._th) for t in tiles]
                now      = time.time()

                with self._debug_lock:
                    self._debug.append((now, [t.copy() for t in tiles], readings))

                for i, reading in enumerate(readings):
                    settle_end = self._settle_until.get(i, 0.0)
                    if now < settle_end:
                        if reading.lit <= _SETTLE_CLEAR_LIT:
                            # Back to idle well before the cap — reopen now
                            # instead of waiting out the rest of the budget.
                            self._settle_until[i] = 0.0
                        else:
                            # Still our own hit's flash/afterglow — a real bad
                            # tile this soon after we just cleared the lane is
                            # implausible, and trusting colour here is exactly
                            # what re-triggers a press on our own pop effect.
                            continue

                    kind = reading.kind
                    if kind == self._prev_kinds[i]:
                        continue
                    self._prev_kinds[i] = kind
                    if kind == EMPTY:
                        continue
                    # Report every tile that lights up, pressable or not: the
                    # window colours its arrow by kind.
                    self.tile_seen.emit(i, kind)
                    if kind in PRESSABLE:
                        self._try_press(i)

            except Exception as exc:
                self.error.emit(str(exc))

            time.sleep(_POLL_SLEEP)

    def dump_debug_frames(self, root: Path) -> Path | None:
        """Write the last ~2.5s of raw tile captures to disk, natural colour.

        Called from the GUI thread on demand (e.g. right after a bad miss),
        while the bot thread keeps appending to the same deque — the lock
        just protects against tearing the snapshot, not against staleness.
        Only tiles brighter than idle get a file, so a run with three quiet
        lanes doesn't dump hundreds of blank squares.
        """
        with self._debug_lock:
            snapshot = list(self._debug)
        if not snapshot:
            return None

        out_dir = root / f"debug_{time.strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        t0   = snapshot[0][0]
        rows = ["frame,t_ms,tile,kind,lit,red,green,cyan"]

        for idx, (t, tiles, readings) in enumerate(snapshot):
            t_ms = int((t - t0) * 1000)
            for tile_id, (tile, r) in enumerate(zip(tiles, readings)):
                rows.append(f"{idx},{t_ms},{tile_id},{r.kind},"
                            f"{r.lit:.3f},{r.red:.3f},{r.green:.3f},{r.cyan:.3f}")
                # tile.max() looks at the *whole* crop, not just the region
                # classify_tile() analyses after _SKIP_TOP — an approaching
                # glyph that only shows up in the skipped rows still triggers
                # this, which is the point.
                if int(tile.max()) > _DEBUG_MIN_V:
                    name = f"f{idx:04d}_t{t_ms:05d}_tile{tile_id}_{r.kind}.png"
                    cv2.imwrite(str(out_dir / name), tile)

        (out_dir / "log.csv").write_text("\n".join(rows), encoding="utf-8")
        return out_dir

    def _try_press(self, tile_id: int):
        now  = time.time()
        last = self._last_time.get(tile_id, 0.0)
        if now - last < _COOLDOWN:
            return
        key = KEY_MAP.get(tile_id)
        if not key:
            return
        self.tile_detected.emit(tile_id, key)
        if press_key(self._hwnd, key):
            self.key_pressed.emit(key)
            self._last_time[tile_id]    = now
            self._settle_until[tile_id] = now + _SETTLE_MAX
