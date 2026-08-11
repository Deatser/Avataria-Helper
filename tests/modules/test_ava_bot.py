from pathlib import Path

import cv2
import numpy as np
import pytest

from modules.ava_dancers.bot import (split_tiles, classify_hsv, classify_tile,
                                     EMPTY, NICE, BONUS, BAD, DISLIKE, BOMB)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def load_hsv(name: str) -> np.ndarray:
    """Load a dump written by the window's "Тест детекции" button.

    imwrite/imread do not convert anything, so the file round-trips the raw HSV
    array byte for byte — channel 0 is hue, not blue.
    """
    img = cv2.imread(str(FIXTURES / f"hsv_{name}.png"), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture hsv_{name}.png"
    return img


# ── split_tiles ─────────────────────────────────────────────────────────────
# TILE_REGION is a 30px-tall strip now, not a 150px-tall square — split_tiles
# only ever crops width, so a strip in, four narrower strips out.

def test_split_tiles_returns_four():
    img = np.zeros((30, 700, 3), dtype=np.uint8)
    assert len(split_tiles(img)) == 4


def test_split_tiles_keeps_strip_height():
    img = np.zeros((30, 700, 3), dtype=np.uint8)
    for tile in split_tiles(img):
        h, w = tile.shape[:2]
        assert h == 30
        assert w == 150


# ── classify: real captures ─────────────────────────────────────────────────
# These fixtures predate the 150x30 strip (captured full-tile, 150x150) and
# classify_hsv no longer crops internally — production now only ever hands it
# a strip, cropped at capture time in bot.py. Passed whole here, they still
# exercise the hue-band logic (does red mean BAD, does cyan mean NICE...) at
# a healthy margin over the new thresholds, but they do NOT verify that the
# strip's Y-position actually intersects a real glyph in the live game — only
# a fresh debug dump (the "Сохранить последние кадры" button) can confirm that.

@pytest.mark.parametrize("name,expected", [
    ("empty",   EMPTY),
    ("nice",    NICE),
    ("bonus",   BONUS),
    ("bad",     BAD),
    ("dislike", DISLIKE),
    ("bomb",    BOMB),
])
def test_classify_real_tile(name, expected):
    assert classify_hsv(load_hsv(name)).kind == expected


@pytest.mark.parametrize("name,pressable", [
    ("empty",   False),
    ("nice",    True),
    ("bonus",   True),
    ("bad",     False),
    ("dislike", False),
    ("bomb",    False),   # a bomb is let through, not hit
])
def test_pressable_matches_kind(name, pressable):
    assert classify_hsv(load_hsv(name)).pressable is pressable


def test_magenta_wins_over_cyan_for_bomb():
    """The bomb's ring is the same cyan as a plain arrow — if a tile carries
    both a strong cyan ring and a magenta fuse, it must read as BOMB, not
    NICE. Guards the check order in classify_hsv, not just the fixture."""
    hsv = np.zeros((30, 150, 3), dtype=np.uint8)
    hsv[:20, :100]  = (90, 255, 255)    # cyan ring — would be NICE alone
    hsv[:15, 100:130] = (140, 255, 255)  # magenta fuse curl
    assert classify_hsv(hsv).kind == BOMB


def test_only_bad_carries_red():
    """The red halo is what tells bad apart from dislike — nothing else has it."""
    red = {n: classify_hsv(load_hsv(n)).red
           for n in ("empty", "nice", "bonus", "bad", "dislike")}
    assert red["bad"] > 0.10
    assert max(v for k, v in red.items() if k != "bad") < 0.01


def test_idle_panel_is_not_mistaken_for_lit():
    """An idle tile is dim teal, not black; it must still read as empty."""
    assert classify_hsv(load_hsv("empty")).lit < 0.01


def test_nice_and_dislike_are_equally_bright():
    """Guards the old bug: brightness alone cannot separate these two."""
    nice    = classify_hsv(load_hsv("nice"))
    dislike = classify_hsv(load_hsv("dislike"))
    assert abs(nice.lit - dislike.lit) < 0.05      # same amount of neon
    assert nice.cyan > 0.5 and dislike.cyan < 0.05  # different saturation


# ── classify: synthetic edge cases ──────────────────────────────────────────

def test_classify_black_tile_is_empty():
    tile = np.zeros((150, 150, 3), dtype=np.uint8)
    assert classify_tile(tile).kind == EMPTY


def test_classify_does_not_skip_any_rows():
    """No more internal crop — bot.py now supplies exactly the strip to read,
    so a glyph anywhere in the given array, including row 0, must count."""
    hsv = np.zeros((30, 150, 3), dtype=np.uint8)
    hsv[:5, :] = (90, 255, 255)   # saturated cyan, only in the very first rows
    assert classify_hsv(hsv).kind == NICE


def test_classify_empty_array_is_empty():
    assert classify_tile(np.zeros((0, 0, 3), dtype=np.uint8)).kind == EMPTY


def test_classify_white_tile_is_dislike():
    """Bright but colourless is the dislike signature — never press it."""
    hsv = np.zeros((150, 150, 3), dtype=np.uint8)
    hsv[:, :] = (0, 0, 255)   # V max, S zero
    reading = classify_hsv(hsv)
    assert reading.kind == DISLIKE
    assert reading.pressable is False


# ── the bomb is found with room to spare ────────────────────────────────────

def test_a_bomb_is_recognised_well_under_the_threshold():
    """A missed bomb is a press on a tile that should have been let through,
    so the fuse is looked for with a far lower bar than any other hue. The
    reference bomb has to clear it by a wide margin."""
    from modules.ava_dancers.tiles import _BOMB_MAGENTA_SHARE

    reading = classify_hsv(load_hsv("bomb"))
    assert reading.kind == BOMB
    assert reading.magenta > _BOMB_MAGENTA_SHARE * 3


def test_no_arrow_carries_any_magenta():
    """The gap the low threshold lives in: measured over a recorded round,
    real arrows show no magenta at all."""
    for name in ("nice", "bonus", "dislike", "bad"):
        assert classify_hsv(load_hsv(name)).magenta == 0.0
