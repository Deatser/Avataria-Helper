from __future__ import annotations
import time
import threading
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.capture import ScreenCapture
from app.core.input_sender import press_key

# A thin strip near the top of the tile — wide capture heights and
# repositioning experiments (bottom, both at once) were all tried chasing the
# post-speed-wave miss and reverted; none of them changed that failure, so the
# extra height was pure risk with no proven benefit. Back to the size that was
# in place the last time this was reported as working reasonably well.
_STRIP_H = 30
_STRIP_Y_OFFSET = 20
TILE_REGION: dict = {"left": 930, "top": 1050 + _STRIP_Y_OFFSET,
                     "width": 700, "height": _STRIP_H}

# Detection parameters
_STRIP_W = 150   # per-tile width — same margin inside the 175px column as before

# Value bands. An idle tile is not black — it is a dim teal panel that tops out
# around V=98, so anything below _IDLE_V is background, not a glyph.
_IDLE_V = 110   # above this a pixel is part of the drawing, not the panel
_LIT_V  = 150   # the neon glyph itself; every icon peaks well past 200

# Saturation floors
_NEON_S    = 180   # saturated enough that the hue names a real colour.
                     #
                     # Tried lowering this: a debug dump caught a real arrow
                     # going completely unseen (0% cyan) during a brief flash
                     # that washes the tile toward white, hue staying at
                     # H≈87 (dead centre of cyan) while S collapsed to ~80.
                     # But real DISLIKE tiles turned out to sit in the exact
                     # same H≈87-88, S≈79-84 neighbourhood (measured from
                     # tests/fixtures/hsv_dislike.png) — indistinguishable by
                     # hue or saturation alone. Lowering the floor enough to
                     # recover the missed arrow also turns every real dislike
                     # into a false NICE, which is worse (a pressed dislike
                     # costs points; a missed press costs nothing). Left at
                     # 180 on purpose — see the "flash" note in classify_hsv.
_RED_S     = 90     # red neon fades into a dim halo — that halo still counts
_MAGENTA_S = 40     # bomb's fuse, measured from a flat reference icon (not an
                     # in-game capture like the others) — every other kind's
                     # in-game bloom came out *more* saturated than its flat
                     # icon, so this floor is deliberately generous until a
                     # real capture confirms it. See tests/fixtures/hsv_bomb.png.

# Hue bands, OpenCV scale 0..179 (measured from tests/fixtures/hsv_*.png)
_RED_LOW,    _RED_HIGH    = 14, 160    # bad tile: red neon, wraps around 0
_GREEN_LOW,  _GREEN_HIGH  = 35, 78     # bonus tile: green star
_CYAN_LOW,   _CYAN_HIGH   = 79, 125    # good tile: cyan arrow
_MAGENTA_LOW, _MAGENTA_HIGH = 126, 159 # bomb tile: magenta fuse curl (its ring
                                        # is cyan like the good arrow — see BOMB
                                        # below, magenta is what tells them apart)

# Tile kinds
EMPTY, NICE, BONUS, BAD, DISLIKE, BOMB = \
    "empty", "nice", "bonus", "bad", "dislike", "bomb"
PRESSABLE = (NICE, BONUS, BOMB)

# Tile index → keyboard key
KEY_MAP: dict[int, str] = {0: "a", 1: "s", 2: "w", 3: "d"}

# ── Early-tip false positives ───────────────────────────────────────────
# Every arrow glyph points its own direction, and the strip sits at the very
# top of the tile. The up and down lanes' arrows both carry vertical light-ray
# decoration reaching toward the strip, so their tip/rays alone can cross in
# early, before the glyph has really arrived — left/right don't have this
# (confirmed: up always mistriggers, down sometimes does, left/right don't).
# The bomb has the same problem on every lane: its fuse curls up off the top
# of the ring, a *minority* of the icon's own lit pixels, so it needs a
# higher lit_share bar than the two clean lanes — only the base threshold
# changes; hue_share/red_share are unaffected. (Bonus had the same guard
# for its star's point, but it traded an early press for sometimes dropping
# the press entirely, which is worse — see classify_hsv.)
_UP_TILE         = 2       # tile index for the "w" / ↑ lane
_DOWN_TILE       = 1       # tile index for the "s" / ↓ lane
_EARLY_TIP_TILES = (_UP_TILE, _DOWN_TILE)
_EARLY_TIP_LIT_SHARE = 0.025   # lit_share floor for the up/down lanes
_MINORITY_LIT_SHARE  = 0.025   # lit_share floor for BOMB, any lane

# ── Pacing ───────────────────────────────────────────────────────────────
# A tile no longer sits still and glows for its whole lifetime — at high game
# speed it's essentially in motion, sweeping through the capture strip once
# and gone. That's why the old cooldown/settle machinery (guarding a *static*
# glyph's own flash echo against re-triggering) is gone: a single edge-trigger
# (kind changed from the previous poll) is enough on its own — a real second
# crossing needs the lane to go dark first. It does NOT need a literal EMPTY
# reading first, though — two things re-arm a lane's next press:
#
#   1. _REARM_LIT — an absolute floor. A debug dump at high speed showed the
#      tail of one note's fade-out overlapping the head of the next note's
#      fade-in, so the lane passed through a dim, colourless DISLIKE instead
#      of a clean EMPTY. Anything this dim counts as dark enough.
#   2. _REARM_DIP_RATIO — a *relative* drop from the lane's own recent peak.
#      Two notes back to back don't always dip anywhere near empty between
#      them — the gap can sit well above the absolute floor and still be a
#      real gap. A confirmed miss: the first of two fast notes pressed fine,
#      the second never did, because the dip between them never reached
#      _REARM_LIT, so the whole two-note run read as one continuous lit
#      stretch. Falling to half the peak seen since the lane last armed is a
#      clear "it's letting go of this one" signal even while still well lit.
_POLL_SLEEP = 0.01
_SPEEDUP_POLL_SLEEP = 0.0005   # temporary boost for the ~15s after a known
                                # speed-wave timer mark (see AvaBot.boost_poll_rate)
_REARM_LIT       = 0.03   # lit_share this low re-arms a lane, however classify_hsv named it
_REARM_DIP_RATIO = 0.5    # or: dropped to this fraction of its own recent peak

# Tried a blind repress-on-timeout for the no-visible-gap case (re-press any
# lane that stays pressable longer than one note plausibly does). Made things
# worse, not better: a wrong/extra press apparently costs combo the same way
# a pressed DISLIKE does, unlike a missed press, which costs nothing. So a
# timeout short enough to catch a genuine second note was also short enough
# to routinely re-fire on a single, ordinary note that simply holds its
# reading a while — trading a rare silent miss for a frequent active one.
# Reverted. The safe default stands: never press without real evidence in
# the pixels that something changed.

# Also tried holding the key down longer instead of pressing again, on the
# theory the game might check "is this key down" against each note's window.
# No better in practice — reverted. If anything, holding the key down through
# a second note's arrival can cost it the fresh keydown *edge* a game usually
# needs to register a hit at all, which would make a longer hold actively
# worse for exactly the back-to-back case it was meant to help.

# ── Debug recording ─────────────────────────────────────────────────────
# The bot keeps a rolling few seconds of every strip it captures and can dump
# it to disk on demand — real frames from the actual failure, not a blind
# tuning pass. A fixed frame count, not _DEBUG_SECONDS / _POLL_SLEEP: the
# real loop rate is set by how long capture+classify actually takes, not by
# the nominal sleep, and during a speed-wave boost _POLL_SLEEP drops low
# enough that dividing by it would ask for a many-hundred-MB buffer.
_DEBUG_FRAMES  = 400
_DEBUG_MIN_V   = _IDLE_V   # below this a pixel is background noise, skip saving it


@dataclass
class Thresholds:
    """Cut-offs for the three measured shares, 0..1.

    Recalibrated for the 150x30 strip (see tests/fixtures/hsv_*.png sliced to
    their own top 30 rows): a strip only ever sees a cross-section of a glyph,
    not the whole thing, so the lit/red shares that used to hold across a full
    150x150 tile land far lower here — a clean "nice" arrow crossing the strip
    measured ~2% lit, not ~17%. hue_share is a share *of lit pixels*, so it
    isn't diluted by the strip being smaller and keeps its old value.
    """
    lit_share: float = 0.008   # lit pixels (of the strip) before it's "on"
    red_share: float = 0.03    # red pixels (of the strip) that make it bad
    hue_share: float = 0.15    # share *of the lit pixels* one hue must own


@dataclass
class TileReading:
    kind:    str
    lit:     float   # share of the strip taken up by neon
    red:     float   # share of the strip that is red, halo included
    green:   float   # share of the *lit* pixels that are green
    cyan:    float   # share of the *lit* pixels that are cyan
    magenta: float = 0.0   # share of the *lit* pixels that are magenta

    @property
    def pressable(self) -> bool:
        return self.kind in PRESSABLE

    @property
    def colour(self) -> float:
        """How strongly the tile owns a pressable hue — for the UI readout."""
        return max(self.green, self.cyan, self.magenta)


# ── Pure image processing functions (no Qt, no win32) ──────────────────────

def split_tiles(img: np.ndarray) -> list[np.ndarray]:
    """Split a 4-tile strip image into 4 equal width-cropped strips."""
    h, w = img.shape[:2]
    tw = w // 4
    tiles = []
    for i in range(4):
        col = img[:, i * tw:(i + 1) * tw]
        cw = col.shape[1]
        cs = min(_STRIP_W, cw)
        cx = cw // 2
        tile = col[:, cx - cs // 2:cx + cs // 2]
        tiles.append(tile)
    return tiles


def classify_tile(tile: np.ndarray, th: Thresholds = None) -> TileReading:
    """Identify what is on a BGR strip."""
    if tile.size == 0:
        return TileReading(EMPTY, 0.0, 0.0, 0.0, 0.0)
    return classify_hsv(cv2.cvtColor(tile, cv2.COLOR_BGR2HSV), th)


def classify_hsv(hsv: np.ndarray, th: Thresholds = None) -> TileReading:
    """Identify what is on a tile from an HSV strip.

    Counting bright pixels cannot do this on its own — several kinds light up
    to a similar extent, and the idle panel is itself bright enough to beat a
    naive floor. What separates them is *which* hue owns the neon:

      empty    nothing lit at all
      nice     lit pixels are saturated cyan          (S≈240, H≈90)
      bonus    lit pixels are green                   (H≈65)
      bomb     a cyan ring plus a magenta fuse curl    (ring H≈90, fuse H≈140)
      bad      a wide red halo around the glyph       (H≈175 or H≈5, S≈120)
      dislike  lit, but no hue owns it — washed out   (S≈70, no red halo)

    Bomb's ring is the same cyan as a plain arrow, so magenta has to be
    checked *before* cyan — otherwise the ring alone would win the arrow's
    own hue_share and read as NICE, and the magenta fuse (a minority of the
    icon's own pixels) would never get a look in.

    Hue is only meaningful on saturated pixels, so the green/cyan/magenta
    tests run on _NEON_S and up. Red gets a lower floor: red neon bleeds a
    dim halo that is most of its red signal, and nothing else on a tile is
    red at all.
    """
    th = th or Thresholds()
    if hsv.size == 0:
        return TileReading(EMPTY, 0.0, 0.0, 0.0, 0.0)

    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)
    total = hue.size

    lit     = val >= _LIT_V
    neon    = lit & (sat >= _NEON_S)
    red     = (val >= _IDLE_V) & (sat >= _RED_S) & \
              ((hue <= _RED_LOW) | (hue >= _RED_HIGH))
    green   = neon & (hue >= _GREEN_LOW)   & (hue <= _GREEN_HIGH)
    cyan    = neon & (hue >= _CYAN_LOW)    & (hue <= _CYAN_HIGH)
    # Its own floor, not _NEON_S — see _MAGENTA_S.
    magenta = lit & (sat >= _MAGENTA_S) & (hue >= _MAGENTA_LOW) & (hue <= _MAGENTA_HIGH)

    n_lit       = int(lit.sum())
    lit_share   = n_lit / total
    red_share   = float(red.sum()) / total
    # Green/cyan/magenta are shares of the lit area, not of the strip, so a
    # small arrow and a big star are judged the same way.
    green_share   = float(green.sum())   / n_lit if n_lit else 0.0
    cyan_share    = float(cyan.sum())    / n_lit if n_lit else 0.0
    magenta_share = float(magenta.sum()) / n_lit if n_lit else 0.0

    if lit_share < th.lit_share:
        kind = EMPTY
    elif red_share >= th.red_share:
        kind = BAD
    elif magenta_share >= th.hue_share:
        # The fuse curls up off the top of the ring, so it — like the star's
        # point — can cross a top-anchored strip before the bomb has really
        # arrived. See _MINORITY_LIT_SHARE.
        kind = BOMB if lit_share >= _MINORITY_LIT_SHARE else DISLIKE
    elif green_share >= th.hue_share:
        # Plain threshold check, same shape as NICE below — no extra bar, and
        # no longer required to also outnumber cyan_share (that was the one
        # remaining asymmetry: cyan never had to beat green to win, so green
        # shouldn't have had to beat cyan either). The star's point does
        # reach the strip before the rest of it, same as bomb's fuse, but
        # gating on that traded an early press for occasionally dropping the
        # press entirely (logged green, never actually pressed). Between
        # "sometimes a touch early" and "sometimes never fires", never-fires
        # is worse — bonus gets the exact same treatment as a plain arrow.
        kind = BONUS
    elif cyan_share >= th.hue_share:
        kind = NICE
    else:
        # Lit, not red, and no hue owns it. Falling through to DISLIKE rather
        # than to a press is the safe way round: a missed bonus costs nothing,
        # a pressed dislike costs points.
        kind = DISLIKE
    return TileReading(kind, lit_share, red_share, green_share, cyan_share, magenta_share)


# ── QThread bot ─────────────────────────────────────────────────────────────

class AvaBot(QThread):
    # Every tile that changed this poll, as [(tile_id, kind), ...] — a single
    # signal per poll so simultaneous tiles (two arrows at once, an arrow and
    # a bad tile at once) land as one event instead of several.
    tiles_seen  = Signal(list)
    key_pressed = Signal(str)
    error       = Signal(str)

    def __init__(self, game_hwnd: int, thresholds: Thresholds = None):
        super().__init__()
        self._hwnd       = game_hwnd
        self._th         = thresholds or Thresholds()
        self._stop_event = threading.Event()
        self._prev_kinds: list[str]   = [EMPTY] * 4
        self._peak_lit:   list[float] = [0.0] * 4    # highest lit seen since armed
        self._armed:      list[bool]  = [True] * 4   # may the next pressable reading press?
        self._debug: deque = deque(maxlen=_DEBUG_FRAMES)
        self._debug_lock = threading.Lock()
        self._poll_sleep = _POLL_SLEEP

    def configure(self, thresholds: Thresholds):
        self._th = thresholds

    def boost_poll_rate(self):
        """Temporarily poll much faster — used for a window right after a
        known speed-wave timer mark. See window.py's _on_speedup_detected."""
        self._poll_sleep = _SPEEDUP_POLL_SLEEP

    def reset_poll_rate(self):
        self._poll_sleep = _POLL_SLEEP

    def _thresholds_for(self, tile_id: int) -> Thresholds:
        """The up/down lanes need a stricter lit_share than left/right — their
        arrows' vertical rays reach the strip before the glyph has really
        arrived. See _EARLY_TIP_LIT_SHARE."""
        if tile_id in _EARLY_TIP_TILES and self._th.lit_share < _EARLY_TIP_LIT_SHARE:
            return replace(self._th, lit_share=_EARLY_TIP_LIT_SHARE)
        return self._th

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
        self._prev_kinds = [EMPTY] * 4
        self._peak_lit   = [0.0] * 4
        self._armed      = [True] * 4
        self._poll_sleep = _POLL_SLEEP
        with self._debug_lock:
            self._debug.clear()
        capture = ScreenCapture.get()

        while not self._stop_event.is_set():
            try:
                img      = capture.grab(TILE_REGION)
                tiles    = split_tiles(img)
                readings = [classify_tile(t, self._thresholds_for(i))
                            for i, t in enumerate(tiles)]

                with self._debug_lock:
                    self._debug.append((time.time(), [t.copy() for t in tiles], readings))

                events: list[tuple[int, str]] = []
                to_press: list[int] = []
                for i, reading in enumerate(readings):
                    kind = reading.kind
                    lit  = reading.lit

                    # Track every poll, not just on a kind change, and re-arm
                    # the lane as soon as it's clearly letting go of whatever
                    # it was showing — by an absolute floor (_REARM_LIT) or by
                    # a sharp drop from its own recent peak (_REARM_DIP_RATIO).
                    # The second one matters even when the lane never gets
                    # near empty: two notes back to back can dip only partway
                    # between them, and a fixed floor alone reads that whole
                    # stretch as one continuous note, eating the second press.
                    if lit > self._peak_lit[i]:
                        self._peak_lit[i] = lit
                    if not self._armed[i] and (
                        lit <= _REARM_LIT or lit <= self._peak_lit[i] * _REARM_DIP_RATIO
                    ):
                        self._armed[i]    = True
                        self._peak_lit[i] = lit   # next note tracks its own peak fresh

                    if kind == self._prev_kinds[i]:
                        continue
                    self._prev_kinds[i] = kind
                    if kind == EMPTY:
                        continue
                    events.append((i, kind))
                    if kind in PRESSABLE and self._armed[i]:
                        to_press.append(i)
                        self._armed[i]    = False
                        self._peak_lit[i] = lit

                if events:
                    # Fire every key this poll before reporting: as close to
                    # a single simultaneous chord as PostMessage allows,
                    # rather than one tile's press delaying the next tile's.
                    for i in to_press:
                        self._press(i)
                    self.tiles_seen.emit(events)

            except Exception as exc:
                self.error.emit(str(exc))

            time.sleep(self._poll_sleep)

    def dump_debug_frames(self, root: Path) -> Path | None:
        """Write the last _DEBUG_FRAMES polls of raw tile captures to disk,
        natural colour — a few seconds, though exactly how many depends on
        the real loop rate, not a fixed duration.

        Called from the GUI thread on demand (e.g. right after a bad miss),
        while the bot thread keeps appending to the same deque — the lock
        just protects against tearing the snapshot, not against staleness.
        Only tiles brighter than idle get a file, so a run with three quiet
        lanes doesn't dump hundreds of blank strips.
        """
        with self._debug_lock:
            snapshot = list(self._debug)
        if not snapshot:
            return None

        out_dir = root / f"debug_{time.strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        t0   = snapshot[0][0]
        rows = ["frame,t_ms,tile,kind,lit,red,green,cyan,magenta"]

        for idx, (t, tiles, readings) in enumerate(snapshot):
            t_ms = int((t - t0) * 1000)
            for tile_id, (tile, r) in enumerate(zip(tiles, readings)):
                rows.append(f"{idx},{t_ms},{tile_id},{r.kind},"
                            f"{r.lit:.3f},{r.red:.3f},{r.green:.3f},{r.cyan:.3f},{r.magenta:.3f}")
                if int(tile.max()) > _DEBUG_MIN_V:
                    name = f"f{idx:04d}_t{t_ms:05d}_tile{tile_id}_{r.kind}.png"
                    cv2.imwrite(str(out_dir / name), tile)

        (out_dir / "log.csv").write_text("\n".join(rows), encoding="utf-8")
        return out_dir

    def _press(self, tile_id: int):
        key = KEY_MAP.get(tile_id)
        if not key:
            return
        if press_key(self._hwnd, key):
            self.key_pressed.emit(key)
