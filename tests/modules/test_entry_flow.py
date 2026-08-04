# tests/modules/test_entry_flow.py
import cv2
import numpy as np

from app.core.template_match import load_template
from modules.ava_dancers import click_flow, entry_flow
from modules.ava_dancers.entry_flow import (ENTRY_STEPS, EXIT_BUTTON,
                                            EXIT_PRESENT, LOBBY_STEPS,
                                            REPEAT_STEP, RESUME_POINTS,
                                            SOLO_STEP, START_STEP, EntryFlow)
from modules.ava_dancers.exit_flow import OK_STEP, RESTART_STEPS


def _screen_showing(*templates, size=(1000, 1600)):
    """A dark screen with each template pasted in at its own spot."""
    scene = np.zeros(size, np.uint8)
    y = 60
    for template in templates:
        h, w = template.shape[:2]
        scene[y:y + h, 400:400 + w] = template
        y += h + 60
    return cv2.cvtColor(scene, cv2.COLOR_GRAY2BGR)


def _flow_over(monkeypatch, frame):
    """An EntryFlow whose screen is always `frame`, with steps stubbed out."""
    monkeypatch.setattr(click_flow, "grab_window", lambda _hwnd, _region: frame)
    monkeypatch.setattr(entry_flow, "primary_monitor_region", lambda: {})
    monkeypatch.setattr(click_flow, "primary_monitor_region", lambda: {})

    flow = EntryFlow(1234)
    seen = {"in_game": False, "in_lobby": None, "score": None,
            "ran": None, "done": False}
    flow.in_game.connect(lambda: seen.__setitem__("in_game", True))
    flow.in_lobby.connect(lambda label: seen.__setitem__("in_lobby", label))
    flow.exit_checked.connect(lambda s: seen.__setitem__("score", s))
    flow.flow_done.connect(lambda: seen.__setitem__("done", True))
    monkeypatch.setattr(
        flow, "run_all_steps",
        lambda steps=None: seen.__setitem__("ran", steps or ENTRY_STEPS) or True)
    return flow, seen


def test_no_exit_sign_means_walking_the_whole_chain(monkeypatch):
    flow, seen = _flow_over(monkeypatch, _screen_showing())

    flow._execute()

    assert seen["score"] < EXIT_PRESENT
    assert seen["ran"] == ENTRY_STEPS
    assert seen["in_game"] is False and seen["in_lobby"] is None
    assert seen["done"] is True


def test_the_exit_sign_alone_means_a_round_is_running(monkeypatch):
    flow, seen = _flow_over(
        monkeypatch, _screen_showing(load_template(EXIT_BUTTON[1])))

    flow._execute()

    assert seen["score"] >= EXIT_PRESENT
    assert seen["in_game"] is True
    assert seen["ran"] is None      # no clicking while a round is up
    assert seen["done"] is True     # but the detector still gets started


def test_exit_plus_solo_means_the_lobby_not_a_round(monkeypatch):
    """Both on screen: Ava Dancers is open but parked before the round, so
    the menus are behind us and only the mode and start remain."""
    flow, seen = _flow_over(monkeypatch, _screen_showing(
        load_template(EXIT_BUTTON[1]), load_template(SOLO_STEP[1])))

    flow._execute()

    assert seen["in_lobby"] == SOLO_STEP[0]
    assert seen["in_game"] is False   # not a running round
    assert seen["ran"] == LOBBY_STEPS
    assert seen["done"] is True


def test_exit_plus_ok_means_the_results_screen(monkeypatch):
    """A round that just ended: ОК first, then ЗАНОВО, then НАЧАТЬ."""
    flow, seen = _flow_over(monkeypatch, _screen_showing(
        load_template(EXIT_BUTTON[1]), load_template(OK_STEP[1])))

    flow._execute()

    assert seen["in_lobby"] == OK_STEP[0]
    assert seen["ran"] == [OK_STEP] + RESTART_STEPS
    assert seen["done"] is True


def test_exit_plus_repeat_picks_up_from_repeat(monkeypatch):
    """ОК already dealt with — resume at ЗАНОВО rather than hunting for a
    button that has gone."""
    flow, seen = _flow_over(monkeypatch, _screen_showing(
        load_template(EXIT_BUTTON[1]), load_template(REPEAT_STEP[1])))

    flow._execute()

    assert seen["in_lobby"] == REPEAT_STEP[0]
    assert seen["ran"] == RESTART_STEPS
    assert seen["done"] is True


def test_ok_wins_when_the_results_screen_shows_both_buttons(monkeypatch):
    """ОК and ЗАНОВО share the results screen; the earlier one goes first."""
    flow, seen = _flow_over(monkeypatch, _screen_showing(
        load_template(EXIT_BUTTON[1]), load_template(OK_STEP[1]),
        load_template(REPEAT_STEP[1])))

    flow._execute()

    assert seen["in_lobby"] == OK_STEP[0]
    assert seen["ran"][0] == OK_STEP


def test_every_resume_point_ends_on_the_button_that_starts_a_round():
    for _probe, steps in RESUME_POINTS:
        assert steps[-1] == START_STEP


def test_the_lobby_steps_are_the_tail_of_the_full_chain(monkeypatch):
    # One source of truth: reaching the lobby the long way ends the same way.
    assert ENTRY_STEPS[-len(LOBBY_STEPS):] == LOBBY_STEPS
    assert [f for _l, f in LOBBY_STEPS] == ["button_solo.png",
                                            "leave_start.png"]


def test_a_chain_that_fails_does_not_start_the_detector(monkeypatch):
    flow, seen = _flow_over(monkeypatch, _screen_showing())
    monkeypatch.setattr(flow, "run_all_steps", lambda steps=None: False)

    flow._execute()

    assert seen["done"] is False


def test_the_entry_chain_polls_faster_than_the_default():
    # It runs while the user is waiting for the bot to come up.
    assert EntryFlow(0).poll_interval < click_flow.POLL_INTERVAL
