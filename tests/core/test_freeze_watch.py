# tests/core/test_freeze_watch.py
import numpy as np
import pytest

from app.core.freeze_watch import (PIXEL_TOL, SAME_RATIO, SCALE_WIDTH,
                                   downscale, same_ratio)


def _noise(h, w, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w), dtype=np.uint8)


def test_identical_frames_count_as_frozen():
    frame = _noise(180, 320)
    assert same_ratio(frame, frame) == 1.0


def test_capture_noise_still_counts_as_frozen():
    # Захват отдаёт один и тот же экран чуть по-разному: сглаживание, дрожание
    # яркости на единицу-другую. Это не движение на экране.
    before = _noise(180, 320)
    after  = np.clip(before.astype(np.int16) + PIXEL_TOL - 1, 0, 255).astype(np.uint8)
    assert same_ratio(before, after) >= SAME_RATIO


def test_a_living_screen_is_nowhere_near_the_threshold():
    assert same_ratio(_noise(180, 320, seed=1), _noise(180, 320, seed=2)) < SAME_RATIO


def test_a_small_moving_corner_is_enough_to_count_as_alive():
    # Меняется меньше пяти процентов кадра — но игра всё равно живая.
    before = np.zeros((180, 320), dtype=np.uint8)
    after  = before.copy()
    after[:40, :60] = 255
    assert same_ratio(before, after) < SAME_RATIO


def test_resized_window_is_not_a_freeze():
    assert same_ratio(_noise(180, 320), _noise(200, 320)) == 0.0


def test_downscale_greys_and_shrinks_wide_frames():
    small = downscale(np.zeros((1340, 2534, 3), dtype=np.uint8))
    assert small.ndim == 2
    assert small.shape[1] == SCALE_WIDTH
    assert small.shape[0] == pytest.approx(1340 * SCALE_WIDTH / 2534, abs=1)


def test_downscale_leaves_already_small_frames_alone():
    frame = _noise(90, 160)
    assert downscale(frame).shape == frame.shape
