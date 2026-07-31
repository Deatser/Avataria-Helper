import numpy as np
import pytest
from modules.ava_dancers.bot import split_tiles, detect_tile, is_fake_tile


def test_split_tiles_returns_four():
    img = np.zeros((150, 700, 3), dtype=np.uint8)
    tiles = split_tiles(img)
    assert len(tiles) == 4


def test_split_tiles_each_is_square():
    img = np.zeros((150, 700, 3), dtype=np.uint8)
    tiles = split_tiles(img)
    for tile in tiles:
        h, w = tile.shape[:2]
        assert h == w


def test_detect_empty_tile():
    # Pure black → 0 white pixels → not active
    tile = np.zeros((150, 150, 3), dtype=np.uint8)
    white, active = detect_tile(tile, min_active=3500)
    assert white == 0
    assert active is False


def test_detect_bright_tile_active():
    # Pure white → many white pixels → active
    tile = np.ones((150, 150, 3), dtype=np.uint8) * 255
    white, active = detect_tile(tile, min_active=3500)
    assert white > 3500
    assert active is True


def test_detect_tile_skips_top_75px():
    # White only in top 75px → should be ignored → not active
    tile = np.zeros((150, 150, 3), dtype=np.uint8)
    tile[:75, :] = 255
    white, active = detect_tile(tile, min_active=3500)
    assert active is False


def test_is_fake_centered_white_not_fake():
    thresh = np.zeros((75, 150), dtype=np.uint8)
    thresh[30:45, 60:90] = 255  # center
    assert is_fake_tile(thresh) is False


def test_is_fake_bottom_right_corner_is_fake():
    thresh = np.zeros((75, 150), dtype=np.uint8)
    thresh[65:75, 130:150] = 255  # bottom-right → offset_x > 15, offset_y > 8
    assert is_fake_tile(thresh) is True


def test_is_fake_empty_thresh_not_fake():
    thresh = np.zeros((75, 150), dtype=np.uint8)
    assert is_fake_tile(thresh) is False
