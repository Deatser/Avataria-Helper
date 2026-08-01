from pathlib import Path

import cv2
import numpy as np
import pytest

from modules.ava_dancers.bot import (split_tiles, classify_hsv, classify_tile,
                                     EMPTY, NICE, BONUS, BAD, DISLIKE)

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

def test_split_tiles_returns_four():
    img = np.zeros((150, 700, 3), dtype=np.uint8)
    assert len(split_tiles(img)) == 4


def test_split_tiles_each_is_square():
    img = np.zeros((150, 700, 3), dtype=np.uint8)
    for tile in split_tiles(img):
        h, w = tile.shape[:2]
        assert h == w


# ── classify: real captures ─────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("empty",   EMPTY),
    ("nice",    NICE),
    ("bonus",   BONUS),
    ("bad",     BAD),
    ("dislike", DISLIKE),
])
def test_classify_real_tile(name, expected):
    assert classify_hsv(load_hsv(name)).kind == expected


@pytest.mark.parametrize("name,pressable", [
    ("empty",   False),
    ("nice",    True),
    ("bonus",   True),
    ("bad",     False),
    ("dislike", False),
])
def test_pressable_matches_kind(name, pressable):
    assert classify_hsv(load_hsv(name)).pressable is pressable


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


def test_classify_skips_top_75px():
    """Glyph confined to the top rows is UI chrome and must be ignored."""
    hsv = np.zeros((150, 150, 3), dtype=np.uint8)
    hsv[:75, :] = (90, 255, 255)   # saturated cyan, but above the cut line
    assert classify_hsv(hsv).kind == EMPTY


def test_classify_empty_array_is_empty():
    assert classify_tile(np.zeros((0, 0, 3), dtype=np.uint8)).kind == EMPTY


def test_classify_white_tile_is_dislike():
    """Bright but colourless is the dislike signature — never press it."""
    hsv = np.zeros((150, 150, 3), dtype=np.uint8)
    hsv[:, :] = (0, 0, 255)   # V max, S zero
    reading = classify_hsv(hsv)
    assert reading.kind == DISLIKE
    assert reading.pressable is False
