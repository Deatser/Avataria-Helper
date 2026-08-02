# tests/modules/test_cleaning.py
import time

import pytest
from PySide6.QtWidgets import QApplication

from modules.gardener import cleaning
from modules.gardener.cleaning import CleaningRun, Job, format_duration


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
        self.left    = dict(counts)
        self.marked  = {}
        self.clicks  = []
        self.per_look = per_look     # how many he clears between two counts
        self.stuck   = stuck         # kinds he never gets to

    def click(self, _hwnd, x, y):
        self.clicks.append((x, y))
        return True

    def count(self, key):
        """Counting is also when the gardener gets on with it."""
        if key not in self.stuck:
            self.left[key] = max(0, self.left.get(key, 0) - self.per_look)
        return self.left.get(key, 0)


def _start(app, monkeypatch, jobs, garden, seconds=0.6):
    monkeypatch.setattr(cleaning, "click_at", garden.click)
    run = CleaningRun(4242, jobs, garden.count)
    events = {"marking": [], "cleaned": [], "kind_done": [], "finished": []}
    run.marking.connect(lambda *a: events["marking"].append(a))
    run.cleaned.connect(lambda *a: events["cleaned"].append(a))
    run.kind_done.connect(lambda *a: events["kind_done"].append(a))
    run.finished.connect(lambda *a: events["finished"].append(a))
    run.start()
    _pump(app, seconds)
    return run, events


def _jobs(**counts):
    """Litter at coordinates no two pieces share, whatever the kind."""
    made = []
    for key, number in counts.items():
        for _ in range(number):
            made.append(Job(key, len(made), len(made)))
    return made


# ── One kind at a time ──────────────────────────────────────────────────────

def test_a_kind_is_marked_in_full_before_anything_else_is_touched(
        app, monkeypatch):
    """The complaint that started this: he was sent round the garden in
    circles because every kind was clicked at once."""
    jobs = _jobs(dry_bush=3, beetle=2)
    garden = _Garden({"dry_bush": 3, "beetle": 2})

    run, events = _start(app, monkeypatch, jobs, garden)

    # Three dry bushes, then two beetles — never interleaved
    assert [key for key, _n, _r in events["marking"]] == ["dry_bush", "beetle"]
    assert garden.clicks[:3] == [(0, 0), (1, 1), (2, 2)]


def test_the_next_kind_waits_until_this_one_is_gone(app, monkeypatch):
    """The gardener clears one bush per look, so the beetles cannot start
    until the third look at the bushes."""
    jobs = _jobs(dry_bush=3, beetle=1)
    garden = _Garden({"dry_bush": 3, "beetle": 1})

    run, events = _start(app, monkeypatch, jobs, garden)

    marked_beetles = events["marking"].index(("beetle", 1, 0))
    assert marked_beetles == 1                    # the second kind marked
    assert garden.left["dry_bush"] == 0           # and only once these had gone
    assert run.done == 4


def test_the_bar_moves_when_the_gardener_removes_it_not_when_it_is_clicked(
        app, monkeypatch):
    jobs = _jobs(dry_bush=2)
    garden = _Garden({"dry_bush": 2})

    run, events = _start(app, monkeypatch, jobs, garden)

    # Clicked twice, but credited one at a time as the count came down
    assert events["cleaned"] == [("dry_bush", 1, 1), ("dry_bush", 2, 2)]


def test_several_going_at_once_are_all_credited(app, monkeypatch):
    jobs = _jobs(dry_bush=4)
    garden = _Garden({"dry_bush": 4}, per_look=2)

    run, events = _start(app, monkeypatch, jobs, garden)

    assert events["cleaned"] == [("dry_bush", 2, 2), ("dry_bush", 4, 4)]


# ── When the gardener never gets there ──────────────────────────────────────

def test_a_kind_that_never_moves_is_marked_again_and_then_left(
        app, monkeypatch):
    monkeypatch.setattr(cleaning, "PATIENCE", 3)
    jobs = _jobs(beetle=2, dry_bush=1)
    garden = _Garden({"beetle": 2, "dry_bush": 1}, stuck={"beetle"})

    run, events = _start(app, monkeypatch, jobs, garden)

    # Marked once, then once more when nothing happened, then given up on
    assert garden.clicks.count((0, 0)) == 1 + cleaning.MARK_ROUNDS
    assert ("beetle", 0, 2) in events["kind_done"]
    assert events["finished"]                      # and the run still ends
    assert run.done == 1                           # only the bush counted


def test_a_screen_that_cannot_be_read_counts_as_nothing_removed(
        app, monkeypatch):
    monkeypatch.setattr(cleaning, "PATIENCE", 2)
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)

    def count(_key):
        raise OSError("no screen")

    run = CleaningRun(4242, _jobs(dry_bush=1), count)
    events = []
    run.cleaned.connect(lambda *a: events.append(a))
    done = []
    run.finished.connect(done.append)
    run.start()
    _pump(app)

    assert events == []
    assert len(done) == 1


# ── The rest of the bookkeeping ─────────────────────────────────────────────

def test_a_run_reports_how_long_it_took(app, monkeypatch):
    run, events = _start(app, monkeypatch, _jobs(dry_bush=1),
                         _Garden({"dry_bush": 1}))

    assert len(events["finished"]) == 1
    assert events["finished"][0][0] >= 0


def test_an_empty_garden_finishes_at_once(app, monkeypatch):
    garden = _Garden({})

    run, events = _start(app, monkeypatch, [], garden)

    assert garden.clicks == [] and events["cleaned"] == []
    assert events["finished"] == [(0.0,)]


def test_stopping_leaves_the_rest_alone(app, monkeypatch):
    garden = _Garden({"dry_bush": 50})
    monkeypatch.setattr(cleaning, "click_at", garden.click)
    run = CleaningRun(4242, _jobs(dry_bush=50), garden.count)
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
