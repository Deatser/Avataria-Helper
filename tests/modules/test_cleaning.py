# tests/modules/test_cleaning.py
import time

import pytest
from PySide6.QtWidgets import QApplication

from modules.gardener import cleaning
from modules.gardener.cleaning import (CleaningRun, Job, format_duration,
                                       sweep_order)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    """The real run is paced in seconds; the tests are not."""
    monkeypatch.setattr(cleaning, "MARK_MS", 1)
    monkeypatch.setattr(cleaning, "WATCH_MS", 1)


def _pump(app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


class _Garden:
    """A garden where the gardener clears what has been marked, in his time.

    Nothing goes on the click, which is the whole point: a run may only count
    what the count says has gone.
    """

    def __init__(self, counts: dict, per_look: int = 1,
                 stuck: set[str] = frozenset()):
        self.left     = dict(counts)
        self.clicks   = []
        self.per_look = per_look     # how many he clears between two counts
        self.stuck    = stuck        # kinds he never gets to

    def click(self, _hwnd, x, y):
        self.clicks.append((x, y))
        return True

    def count(self, key):
        """Counting is also when the gardener gets on with it."""
        if key not in self.stuck:
            self.left[key] = max(0, self.left.get(key, 0) - self.per_look)
        return self.left.get(key, 0)


def _start(app, monkeypatch, jobs, garden, seconds=0.6, rescan=None):
    monkeypatch.setattr(cleaning, "click_at", garden.click)
    run = CleaningRun(4242, jobs, garden.count, rescan)
    events = {"marking": [], "cleaned": [], "kind_done": [], "finished": []}
    run.marking.connect(lambda *a: events["marking"].append(a))
    run.cleaned.connect(lambda *a: events["cleaned"].append(a))
    run.kind_done.connect(lambda *a: events["kind_done"].append(a))
    run.finished.connect(lambda *a: events["finished"].append(a))
    run.start()
    _pump(app, seconds)
    return run, events


# ── The order the garden is walked in ───────────────────────────────────────

def test_the_marks_go_down_left_to_right(app, monkeypatch):
    """A vertical line sweeping right: every x in turn, whatever the kind."""
    jobs = [Job("beetle", 800, 100), Job("dry_bush", 120, 700),
            Job("blue_bush", 450, 300), Job("dry_bush", 120, 200)]

    assert [(j.x, j.y) for j in sweep_order(jobs)] == [
        (120, 200), (120, 700), (450, 300), (800, 100)]


def test_kind_does_not_decide_the_order(app, monkeypatch):
    """Marking each kind in turn sent him back across the map every time."""
    jobs = [Job("dry_bush", 900, 10), Job("beetle", 100, 10),
            Job("dry_bush", 500, 10)]
    garden = _Garden({"dry_bush": 2, "beetle": 1})

    _run, _events = _start(app, monkeypatch, jobs, garden)

    assert garden.clicks[:3] == [(100, 10), (500, 10), (900, 10)]


def test_everything_is_marked_before_anything_is_waited_for(app, monkeypatch):
    jobs = [Job("dry_bush", x, 0) for x in (10, 20, 30)]
    garden = _Garden({"dry_bush": 3})

    _run, events = _start(app, monkeypatch, jobs, garden)

    assert events["marking"][0] == (3, 0)      # all three, on the first round
    assert garden.clicks[:3] == [(10, 0), (20, 0), (30, 0)]


# ── Progress is what the screen says, not what was clicked ──────────────────

def test_the_bar_moves_when_the_gardener_removes_it_not_when_it_is_clicked(
        app, monkeypatch):
    jobs = [Job("dry_bush", 10, 0), Job("dry_bush", 20, 0)]
    garden = _Garden({"dry_bush": 2})

    _run, events = _start(app, monkeypatch, jobs, garden)

    assert events["cleaned"] == [("dry_bush", 1, 1), ("dry_bush", 2, 2)]


def test_every_kind_is_counted_in_turn(app, monkeypatch):
    """One kind per look — counting all of them every time is half a second
    of the interface's own time for nothing."""
    jobs = [Job("dry_bush", 10, 0), Job("beetle", 20, 0)]
    garden = _Garden({"dry_bush": 1, "beetle": 1})

    run, events = _start(app, monkeypatch, jobs, garden)

    assert {key for key, _d, _t in events["cleaned"]} == {"dry_bush", "beetle"}
    assert run.done == 2


def test_a_kind_finished_is_announced_once(app, monkeypatch):
    jobs = [Job("dry_bush", 10, 0), Job("dry_bush", 20, 0)]
    garden = _Garden({"dry_bush": 2})

    _run, events = _start(app, monkeypatch, jobs, garden)

    assert events["kind_done"] == [("dry_bush", 2)]


# ── When the gardener never gets there ──────────────────────────────────────

def test_what_is_left_standing_is_marked_again_from_a_fresh_look(
        app, monkeypatch):
    monkeypatch.setattr(cleaning, "PATIENCE", 3)
    jobs = [Job("beetle", 10, 0), Job("beetle", 20, 0)]
    garden = _Garden({"beetle": 2}, stuck={"beetle"})
    # The second look finds only one of them still there, and somewhere else
    rescan = lambda: [Job("beetle", 55, 5)]

    run, events = _start(app, monkeypatch, jobs, garden, rescan=rescan)

    assert (55, 5) in garden.clicks
    assert len(events["marking"]) == 2 and events["marking"][1] == (1, 1)
    assert events["finished"]                  # and the run still ends
    assert run.done == 0                       # nothing was seen to go


def test_a_garden_that_is_already_clear_stops_marking(app, monkeypatch):
    monkeypatch.setattr(cleaning, "PATIENCE", 2)
    garden = _Garden({"beetle": 1}, stuck={"beetle"})

    _run, events = _start(app, monkeypatch, [Job("beetle", 10, 0)], garden,
                          rescan=lambda: [])

    assert len(events["marking"]) == 1
    assert events["finished"]


def test_a_screen_that_cannot_be_read_counts_as_nothing_removed(
        app, monkeypatch):
    monkeypatch.setattr(cleaning, "PATIENCE", 2)
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)

    def count(_key):
        raise OSError("no screen")

    run = CleaningRun(4242, [Job("dry_bush", 1, 1)], count)
    events, done = [], []
    run.cleaned.connect(lambda *a: events.append(a))
    run.finished.connect(done.append)
    run.start()
    _pump(app)

    assert events == []
    assert len(done) == 1


# ── The rest of the bookkeeping ─────────────────────────────────────────────

def test_a_run_reports_how_long_it_took(app, monkeypatch):
    _run, events = _start(app, monkeypatch, [Job("dry_bush", 1, 1)],
                          _Garden({"dry_bush": 1}))

    assert len(events["finished"]) == 1
    assert events["finished"][0][0] >= 0


def test_an_empty_garden_finishes_at_once(app, monkeypatch):
    garden = _Garden({})

    _run, events = _start(app, monkeypatch, [], garden)

    assert garden.clicks == [] and events["cleaned"] == []
    assert events["finished"] == [(0.0,)]


def test_stopping_leaves_the_rest_alone(app, monkeypatch):
    garden = _Garden({"dry_bush": 50})
    monkeypatch.setattr(cleaning, "click_at", garden.click)
    run = CleaningRun(4242, [Job("dry_bush", x, 0) for x in range(50)],
                      garden.count)
    run.start()
    _pump(app, 0.02)

    run.stop()
    clicked = len(garden.clicks)
    _pump(app, 0.1)

    assert run.running is False
    assert len(garden.clicks) == clicked      # nothing carried on after the stop


# ── How the time reads ──────────────────────────────────────────────────────

def test_durations_read_the_way_they_are_spoken():
    assert format_duration(45) == "45 сек"
    assert format_duration(120) == "2 мин"
    assert format_duration(187) == "3 мин 7 сек"
