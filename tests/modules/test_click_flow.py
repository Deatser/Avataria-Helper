# tests/modules/test_click_flow.py
import cv2
import numpy as np

from app.core.template_match import load_template
from modules.ava_dancers import click_flow
from modules.ava_dancers.click_flow import (BUTTON_THRESHOLD, CONFIRM_DROP,
                                            confirmed_gone)
from modules.ava_dancers.entry_flow import (ENTRY_STEPS, EXIT_BUTTON,
                                            EXIT_PRESENT, EntryFlow)
from modules.ava_dancers.exit_flow import OK_STEP, RESTART_STEPS, ExitFlow


# ── Collapse rule ───────────────────────────────────────────────────────────

def test_a_button_still_matching_its_peak_is_not_gone():
    assert confirmed_gone(0.99, 0.99) is False


def test_a_small_dip_is_not_a_collapse():
    assert confirmed_gone(0.99, 0.99 - CONFIRM_DROP / 2) is False


def test_a_drop_is_measured_against_the_peak_not_the_threshold():
    # The measured Игры transition: still a high score, still above the click
    # bar, but plainly off its own best reading — the screen moved.
    assert confirmed_gone(0.997, 0.938) is True
    assert 0.938 > BUTTON_THRESHOLD


def test_the_click_bar_leaves_room_for_a_drop_to_be_visible():
    # If the drop were larger than the threshold, a button could vanish
    # completely (score 0) without ever counting as gone.
    assert CONFIRM_DROP < BUTTON_THRESHOLD


def test_the_in_game_check_is_looser_than_the_click_bar():
    assert EXIT_PRESENT < BUTTON_THRESHOLD


# ── Step lists ──────────────────────────────────────────────────────────────

def test_nothing_after_ok_when_restart_is_off():
    """OK is not in the step list at all any more — it is clicked at its
    known spot once the ИГРА ОКОНЧЕНА banner says the screen is up, because
    matching leave_ok.png only worked some of the time and a round that
    missed it never started the next one."""
    assert ExitFlow(0, restart=False).steps() == []


def test_restart_runs_repeat_and_start_after_ok():
    assert ExitFlow(0, restart=True).steps() == RESTART_STEPS


def test_the_entry_chain_ends_on_the_button_that_starts_the_round():
    assert EntryFlow(0).steps() == ENTRY_STEPS
    assert ENTRY_STEPS[-1][1] == "leave_start.png"


def test_every_template_the_flows_reference_is_on_disk():
    steps = [OK_STEP, EXIT_BUTTON] + RESTART_STEPS + ENTRY_STEPS
    for label, filename in steps:
        img = load_template(filename)
        assert img is not None, f"{label}: {filename} не найден"
        assert img.size > 0, filename


# ── The step loop, driven by a scripted screen ──────────────────────────────

class _ScriptedCapture:
    """Serves a list of prepared BGR frames, repeating the last one.

    Stands in for grab_window(hwnd, region) — same scripted-frames idea, just
    a plain callable instead of an object with its own .grab().
    """

    def __init__(self, frames):
        self.frames = frames
        self.taken  = 0

    def __call__(self, _hwnd, _region):
        frame = self.frames[min(self.taken, len(self.frames) - 1)]
        self.taken += 1
        return frame


_BUTTON_X, _BUTTON_Y = 200, 300   # where the button is pasted, screen coords


def _scene_with(template_gray, size=(700, 1200)):
    """A dark screen with the button pasted in at a known spot, in BGR."""
    scene = np.zeros(size, np.uint8)
    h, w = template_gray.shape[:2]
    scene[_BUTTON_Y:_BUTTON_Y + h, _BUTTON_X:_BUTTON_X + w] = template_gray
    return cv2.cvtColor(scene, cv2.COLOR_GRAY2BGR)


def _empty_scene(size=(700, 1200)):
    return cv2.cvtColor(np.zeros(size, np.uint8), cv2.COLOR_GRAY2BGR)


def _scene_with_both(first_gray, second_gray, size=(700, 1200)):
    """Both buttons on screen at once — a permanent nav button plus the panel
    it just opened."""
    scene = np.zeros(size, np.uint8)
    h, w = first_gray.shape[:2]
    scene[_BUTTON_Y:_BUTTON_Y + h, _BUTTON_X:_BUTTON_X + w] = first_gray
    h2, w2 = second_gray.shape[:2]
    scene[20:20 + h2, 20:20 + w2] = second_gray
    return cv2.cvtColor(scene, cv2.COLOR_GRAY2BGR)


def _run_one_step(monkeypatch, frames, reclick=None, timeout=None,
                  following=None):
    template = load_template(OK_STEP[1])
    clicks   = []

    monkeypatch.setattr(click_flow, "grab_window", _ScriptedCapture(frames))
    monkeypatch.setattr(click_flow, "game_region", lambda: {})
    monkeypatch.setattr(click_flow, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    if reclick is not None:
        monkeypatch.setattr(click_flow, "RECLICK_INTERVAL", reclick)

    flow = ExitFlow(1234, restart=False)
    flow.poll_interval = 0
    if timeout is not None:
        # An instance attribute, not the module constant — run_step reads
        # self.step_timeout, so patching STEP_TIMEOUT would do nothing.
        flow.step_timeout = timeout
    confirmed = []
    flow.confirmed.connect(confirmed.append)
    done = flow.run_step(OK_STEP[0], template, following)
    return done, clicks, confirmed


def test_a_step_clicks_the_button_centre_then_waits_for_it_to_vanish(monkeypatch):
    template = load_template(OK_STEP[1])
    h, w = template.shape[:2]
    frames = [_scene_with(template), _scene_with(template), _empty_scene()]

    done, clicks, confirmed = _run_one_step(monkeypatch, frames)

    assert done is True
    assert confirmed == [OK_STEP[0]]
    assert clicks == [(_BUTTON_X + w // 2, _BUTTON_Y + h // 2)]


def test_a_button_that_stays_on_screen_finishes_when_the_next_one_appears(monkeypatch):
    """The "Места" case: a permanent nav button never collapses, so the only
    usable proof the click worked is the screen it opened."""
    template  = load_template(OK_STEP[1])
    following = load_template(RESTART_STEPS[0][1])
    frames = [_scene_with(template),                      # clicked here
              _scene_with_both(template, following)]      # still there + next

    done, clicks, confirmed = _run_one_step(
        monkeypatch, frames, timeout=1.0, following=following)

    assert done is True            # would have timed out on the old rule
    assert confirmed == [OK_STEP[0]]
    assert len(clicks) == 1        # and it stopped prodding it


def test_a_next_button_that_was_already_there_proves_nothing(monkeypatch):
    """The Игры bug: the scroll arrow is part of the Места panel, so it was
    already on screen when the Игры step began. Counting it as proof marched
    the flow past a button it had never actually pressed."""
    template  = load_template(OK_STEP[1])
    following = load_template(RESTART_STEPS[0][1])
    frames = [_scene_with_both(template, following)]   # next up from the start

    done, clicks, confirmed = _run_one_step(
        monkeypatch, frames, timeout=0.4, following=following)

    assert confirmed == []   # never confirmed by a button that was always up
    assert done is False
    assert clicks            # and the step's own button did get clicked


# ── The measured Игры transition, driven by scripted scores ─────────────────

def _run_with_scores(monkeypatch, scores, timeout=2.0):
    """Feed run_step an exact sequence of match scores, one per poll."""
    fed = list(scores)
    clicks = []

    def fake_match(_scene, _template):
        return (fed.pop(0) if fed else fed_last[0]), (200, 300)

    fed_last = [scores[-1]]
    monkeypatch.setattr(click_flow, "best_match", fake_match)
    monkeypatch.setattr(click_flow, "grab_window",
                        _ScriptedCapture([_empty_scene()]))
    monkeypatch.setattr(click_flow, "game_region", lambda: {})
    monkeypatch.setattr(click_flow, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    monkeypatch.setattr(click_flow, "RECLICK_INTERVAL", 0)

    flow = ExitFlow(1234, restart=False)
    flow.poll_interval = 0
    flow.step_timeout  = timeout
    confirmed = []
    flow.confirmed.connect(confirmed.append)
    done = flow.run_step(OK_STEP[0], load_template(OK_STEP[1]))
    return done, clicks, confirmed


def test_the_measured_games_transition_ends_the_step(monkeypatch):
    """99.7% before the click, 93.8% after — both above the click bar, so the
    old rule kept pressing a button whose screen had already opened."""
    done, clicks, confirmed = _run_with_scores(
        monkeypatch, [0.997, 0.938, 0.938])

    assert done is True
    assert confirmed == [OK_STEP[0]]
    assert len(clicks) == 1      # clicked once, then stopped — no re-press


def test_one_dipped_frame_is_not_enough(monkeypatch):
    """A single low reading can be an animation frame; the step keeps going."""
    done, _clicks, confirmed = _run_with_scores(
        monkeypatch, [0.997, 0.938, 0.997, 0.997], timeout=0.2)

    assert done is False
    assert confirmed == []


def test_a_lingering_button_is_clicked_again_but_only_on_its_own_clock(monkeypatch):
    template = load_template(OK_STEP[1])

    # Polling flat out with re-clicks held off: one click, however many polls.
    _done, paced, _ = _run_one_step(monkeypatch, [_scene_with(template)],
                                    timeout=0.15)
    # Same run with the hold-off removed: a click per poll.
    _done, unpaced, _ = _run_one_step(monkeypatch, [_scene_with(template)],
                                      reclick=0, timeout=0.15)

    assert len(paced) == 1
    assert len(unpaced) > len(paced)


def test_a_step_does_not_finish_while_the_button_is_still_on_screen(monkeypatch):
    template = load_template(OK_STEP[1])

    done, clicks, confirmed = _run_one_step(
        monkeypatch, [_scene_with(template)], timeout=0.15)

    assert done is False       # timed out instead of moving on
    assert confirmed == []
    assert clicks              # but it did try


def test_a_step_never_clicks_a_screen_the_button_is_not_on(monkeypatch):
    done, clicks, confirmed = _run_one_step(
        monkeypatch, [_empty_scene()], timeout=0.15)

    assert done is False
    assert clicks == []
    assert confirmed == []
