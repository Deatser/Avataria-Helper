# tests/core/test_template_match.py
import numpy as np
import pytest

from app.core.template_match import best_match, load_template


def _noise(h, w, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w), dtype=np.uint8)


def test_finds_a_patch_cut_from_the_scene_itself():
    scene = _noise(200, 300)
    template = scene[40:90, 120:200].copy()
    score, (x, y) = best_match(scene, template)
    assert score == pytest.approx(1.0, abs=1e-3)
    assert (x, y) == (120, 40)


def test_unrelated_noise_scores_far_below_threshold():
    score, _ = best_match(_noise(200, 300, seed=1), _noise(50, 80, seed=2))
    assert score < 0.5


def test_template_larger_than_scene_scores_zero_instead_of_raising():
    # cv2.matchTemplate raises on this; the button must report "not found"
    # rather than blow up when the game is windowed smaller than the capture.
    score, loc = best_match(_noise(50, 50), _noise(100, 100))
    assert score == 0.0
    assert loc == (0, 0)


def test_empty_inputs_score_zero():
    assert best_match(np.zeros((0, 0), np.uint8), _noise(10, 10))[0] == 0.0
    assert best_match(_noise(10, 10), np.zeros((0, 0), np.uint8))[0] == 0.0


def test_load_template_returns_none_for_missing_file():
    assert load_template("definitely_not_a_real_template.png") is None
