# tests/core/test_template_match.py
import numpy as np
import pytest

from app.core.template_match import (
    FINISH_GOLD, FINISH_SILVER, LEAVE_TEMPLATES, MATCH_THRESHOLD, Match,
    best_match, load_template, search_screen,
)


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
    assert score < MATCH_THRESHOLD


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


def test_load_template_reads_the_leave_templates_as_grayscale():
    for _label, filename in LEAVE_TEMPLATES.values():
        img = load_template(filename)
        assert img is not None, filename
        assert img.ndim == 2, filename
        assert img.size > 0, filename


def test_leave_templates_are_keyed_by_the_config_values():
    assert set(LEAVE_TEMPLATES) == {FINISH_GOLD, FINISH_SILVER}


def test_threshold_demands_the_fully_drawn_reward_line():
    # Gold scored 78.7% live with its checkmark still missing — the round was
    # not over. Anything at or below that must not count as "may leave".
    assert MATCH_THRESHOLD > 0.787


def test_match_percent_is_score_scaled_to_100():
    assert Match(FINISH_GOLD, "Золото", 0.7123, 0, 0, 10, 10).percent \
        == pytest.approx(71.23)


def test_match_found_reflects_the_threshold():
    assert Match(FINISH_GOLD, "Золото", MATCH_THRESHOLD, 0, 0, 10, 10).found is True
    assert Match(FINISH_GOLD, "Золото", MATCH_THRESHOLD - 0.01, 0, 0, 10, 10).found is False


def test_search_screen_scores_both_templates_against_a_given_image():
    # A scene the templates cannot appear in: both must still be reported,
    # so the readout always shows two numbers rather than going silent.
    matches = search_screen(screen=_noise(1440, 2560, seed=3))
    assert [m.key for m in matches] == [FINISH_GOLD, FINISH_SILVER]
    assert all(not m.found for m in matches)
