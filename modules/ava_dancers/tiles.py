# modules/ava_dancers/tiles.py
"""What a tile looks like: the HSV bands, the six kinds, and the one
decision tree that names one from the other.

Split out of bot.py so the lane tracker reaches its verdicts through the
exact same code the old strip detector does — one set of thresholds, one
decision tree, no second copy to drift out of step. Nothing here knows
about capture, Qt or where on screen anything is.
"""
from __future__ import annotations
from dataclasses import dataclass
import cv2
import numpy as np

# Per-tile crop width inside its own column — the margin split_tiles keeps
# away from the lane's edges.
_STRIP_W = 150

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
# A bomb is not worth pressing — it is a tile to let through, same as a
# dislike. It used to be in here, from back when the detector could barely
# tell one apart and pressing it was the lesser risk.
PRESSABLE = (NICE, BONUS)

# ── Early-tip false positives ───────────────────────────────────────────
# Every arrow glyph points its own direction, and the strip sits at the very
# top of the tile. The up and down lanes' arrows both carry vertical light-ray
# decoration reaching toward the strip, so their tip/rays alone used to cross
# in early, before the glyph had really arrived — this used to get a
# stricter, lane-specific lit_share floor for just those two lanes. Removed
# 2026-08-05 on request: all four lanes now read against the exact same
# thresholds, no per-lane special case. If the up/down lanes start firing
# early again, _EARLY_TIP_LIT_SHARE below is the floor that used to guard
# them — reintroduce the per-lane branch in _thresholds_for if so.
_EARLY_TIP_LIT_SHARE = 0.025   # unused unless _thresholds_for is reverted
# How much of a note's neon has to be the bomb's magenta fuse before it is
# called a bomb. Deliberately far below the hue_share every other kind is
# judged on, because the cost is lopsided now that a bomb is never pressed:
# calling an arrow a bomb loses one note, while missing a bomb presses a tile
# that should have been let through, and the game charges for that.
#
# There is room for it. Measured over 2288 frames of real arrows in a
# recorded round, the magenta share was 0.0000 — not small, zero — while the
# reference bomb (tests/fixtures/hsv_bomb.png) reads 0.189 over its whole
# glyph. Anything in this gap works; this sits about five times under the
# bomb and infinitely over the arrows.
_BOMB_MAGENTA_SHARE  = 0.04

_MINORITY_LIT_SHARE  = 0.025   # lit_share floor for BOMB, any lane — unrelated
                                # to the above, still active (see classify_hsv)

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

    kind = decide_kind(lit_share, red_share, green_share, cyan_share,
                       magenta_share, th)
    return TileReading(kind, lit_share, red_share, green_share, cyan_share, magenta_share)


def decide_kind(lit_share: float, red_share: float, green_share: float,
                cyan_share: float, magenta_share: float,
                th: Thresholds = None) -> str:
    """Name a tile from its five measured shares.

    Split out of classify_hsv so the lane tracker can reach the same verdict
    from the same numbers measured over a whole note (see tracker.py's
    classify_blob) instead of over a 30px slice of one — one decision tree,
    not two that drift apart.
    """
    th = th or Thresholds()
    if lit_share < th.lit_share:
        return EMPTY
    if red_share >= th.red_share:
        return BAD
    if magenta_share >= _BOMB_MAGENTA_SHARE:
        # The fuse curls up off the top of the ring, so it — like the star's
        # point — can cross a top-anchored strip before the bomb has really
        # arrived. See _MINORITY_LIT_SHARE.
        return BOMB if lit_share >= _MINORITY_LIT_SHARE else DISLIKE
    if green_share >= th.hue_share:
        # Plain threshold check, same shape as NICE below — no extra bar, and
        # no longer required to also outnumber cyan_share (that was the one
        # remaining asymmetry: cyan never had to beat green to win, so green
        # shouldn't have had to beat cyan either). The star's point does
        # reach the strip before the rest of it, same as bomb's fuse, but
        # gating on that traded an early press for occasionally dropping the
        # press entirely (logged green, never actually pressed). Between
        # "sometimes a touch early" and "sometimes never fires", never-fires
        # is worse — bonus gets the exact same treatment as a plain arrow.
        return BONUS
    if cyan_share >= th.hue_share:
        return NICE
    # Lit, not red, and no hue owns it. Falling through to DISLIKE rather
    # than to a press is the safe way round: a missed bonus costs nothing,
    # a pressed dislike costs points.
    return DISLIKE


