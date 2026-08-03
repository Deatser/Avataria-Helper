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
    monkeypatch.setattr(cleaning, "SETTLE_S", 0.0)


def _pump(app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


class _Garden:
    """A garden where the gardener works through the marks in his own time.

    Standing in for the screen: the run asks which marks have nothing under
    them any more, and that answer is the only thing it treats as progress.
    """

    def __init__(self, per_look: int = 1, stuck: set[str] = frozenset()):
        self.clicks   = []
        self.per_look = per_look      # how many he clears between two looks
        self.stuck    = stuck         # kinds he never gets to
        self.cleared  = []

    def click(self, _hwnd, x, y):
        self.clicks.append((x, y))
        return True

    def gone(self, jobs):
        """Looking is also when the gardener gets on with it."""
        fresh = [job for job in jobs
                 if job not in self.cleared and job.key not in self.stuck]
        self.cleared += fresh[:self.per_look]
        return [job for job in jobs if job in self.cleared]


def _start(app, monkeypatch, jobs, garden, seconds=0.6):
    monkeypatch.setattr(cleaning, "click_at", garden.click)
    run = CleaningRun(4242, jobs, garden.gone)
    events = {"marking": [], "cleaned": [], "kind_done": [], "finished": []}
    run.marking.connect(lambda *a: events["marking"].append(a))
    run.cleaned.connect(lambda *a: events["cleaned"].append(a))
    run.kind_done.connect(lambda *a: events["kind_done"].append(a))
    run.finished.connect(lambda *a: events["finished"].append(a))
    run.start()
    _pump(app, seconds)
    return run, events


# ── The order the garden is walked in ───────────────────────────────────────

def test_the_marks_go_down_left_to_right():
    """A vertical line sweeping right: every x in turn, whatever the kind."""
    jobs = [Job("beetle", 800, 100), Job("dry_bush", 120, 700),
            Job("blue_bush", 450, 300), Job("dry_bush", 120, 200)]

    assert [(j.x, j.y) for j in sweep_order(jobs)] == [
        (120, 200), (120, 700), (450, 300), (800, 100)]


def test_kind_does_not_decide_the_order(app, monkeypatch):
    """Marking each kind in turn sent him back across the map every time."""
    jobs = [Job("dry_bush", 900, 10), Job("beetle", 100, 10),
            Job("dry_bush", 500, 10)]
    garden = _Garden()

    _run, _events = _start(app, monkeypatch, jobs, garden)

    assert garden.clicks[:3] == [(100, 10), (500, 10), (900, 10)]


def test_everything_is_marked_before_anything_is_watched(app, monkeypatch):
    jobs = [Job("dry_bush", x, 0) for x in (10, 20, 30)]
    garden = _Garden()

    _run, events = _start(app, monkeypatch, jobs, garden)

    assert events["marking"][0] == (3,)
    assert garden.clicks[:3] == [(10, 0), (20, 0), (30, 0)]


# ── Progress is what the screen says, not what was clicked ──────────────────

def test_a_mark_counts_when_it_is_empty_not_when_it_is_clicked(
        app, monkeypatch):
    jobs = [Job("dry_bush", 10, 0), Job("dry_bush", 20, 0)]
    garden = _Garden()

    run, events = _start(app, monkeypatch, jobs, garden)

    assert events["cleaned"] == [("dry_bush", 1, 1), ("dry_bush", 2, 2)]
    assert run.done == 2


def test_a_fresh_mark_is_left_alone_until_it_has_had_time_to_settle(
        app, monkeypatch):
    """A click gets its own flourish on screen; asked about too soon, that
    flourish alone would read as the litter already being gone."""
    monkeypatch.setattr(cleaning, "SETTLE_S", 0.15)
    monkeypatch.setattr(cleaning, "PATIENCE", 10_000)   # only settling is timed here
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)
    job = Job("dry_bush", 10, 0)
    calls = []

    def always_gone(jobs):
        calls.append(list(jobs))
        return list(jobs)

    run = CleaningRun(4242, [job], always_gone)
    events = []
    run.cleaned.connect(lambda *a: events.append(a))
    run.start()
    _pump(app, 0.05)              # well before SETTLE_S has passed

    assert events == []
    assert calls == []            # never even asked about yet

    _pump(app, 0.6)                # now it has had time to settle

    assert events == [("dry_bush", 1, 1)]


def test_one_empty_look_is_not_enough(app, monkeypatch):
    """The gardener stands in front of the litter he is about to clear."""
    monkeypatch.setattr(cleaning, "PATIENCE", 3)
    job = Job("dry_bush", 10, 0)
    looks = {"n": 0}

    def blink(jobs):
        looks["n"] += 1
        return list(jobs) if looks["n"] == 1 else []    # gone, then back

    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)
    run = CleaningRun(4242, [job], blink)
    events = []
    run.cleaned.connect(lambda *a: events.append(a))
    run.start()
    _pump(app, 0.2)

    assert events == []


def test_progress_is_per_mark_across_kinds(app, monkeypatch):
    jobs = [Job("dry_bush", 10, 0), Job("beetle", 20, 0)]
    garden = _Garden()

    run, events = _start(app, monkeypatch, jobs, garden)

    assert {key for key, _d, _t in events["cleaned"]} == {"dry_bush", "beetle"}
    assert run.done == 2


def test_a_kind_finished_is_announced(app, monkeypatch):
    jobs = [Job("dry_bush", 10, 0), Job("dry_bush", 20, 0)]
    garden = _Garden()

    _run, events = _start(app, monkeypatch, jobs, garden)

    assert events["kind_done"] == [("dry_bush", 2)]


# ── When the gardener never gets there ──────────────────────────────────────

def test_marks_that_never_clear_are_left_standing_not_clicked_again(
        app, monkeypatch):
    """Clicking an already-queued spot again does not help the game along
    — it might knock it out of the queue — so a stuck mark is reported and
    left alone, not marked a second time."""
    monkeypatch.setattr(cleaning, "PATIENCE", 3)
    jobs = [Job("beetle", 10, 0), Job("beetle", 20, 0)]
    garden = _Garden(stuck={"beetle"})   # never comes up empty

    run, events = _start(app, monkeypatch, jobs, garden)

    assert garden.clicks == [(10, 0), (20, 0)]   # each mark clicked once, ever
    assert len(events["marking"]) == 1           # one sweep, never a second
    assert events["finished"]                    # and the run still ends
    assert run.done == 0                         # nothing was seen to go


def test_a_screen_that_cannot_be_read_counts_as_nothing_removed(
        app, monkeypatch):
    monkeypatch.setattr(cleaning, "PATIENCE", 2)
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)

    def look(_jobs):
        raise OSError("no screen")

    run = CleaningRun(4242, [Job("dry_bush", 1, 1)], look)
    events, done = [], []
    run.cleaned.connect(lambda *a: events.append(a))
    run.finished.connect(done.append)
    run.start()
    _pump(app)

    assert events == []
    assert len(done) == 1


# ── The rest of the bookkeeping ─────────────────────────────────────────────

def test_a_run_reports_how_long_it_took(app, monkeypatch):
    _run, events = _start(app, monkeypatch, [Job("dry_bush", 1, 1)], _Garden())

    assert len(events["finished"]) == 1
    assert events["finished"][0][0] >= 0


def test_an_empty_garden_finishes_at_once(app, monkeypatch):
    garden = _Garden()

    _run, events = _start(app, monkeypatch, [], garden)

    assert garden.clicks == [] and events["cleaned"] == []
    assert events["finished"] == [(0.0,)]


def test_stopping_leaves_the_rest_alone(app, monkeypatch):
    garden = _Garden()
    monkeypatch.setattr(cleaning, "click_at", garden.click)
    run = CleaningRun(4242, [Job("dry_bush", x, 0) for x in range(50)],
                      garden.gone)
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
