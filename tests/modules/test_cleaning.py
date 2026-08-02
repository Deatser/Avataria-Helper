# tests/modules/test_cleaning.py
import time

import pytest
from PySide6.QtWidgets import QApplication

from modules.gardener import cleaning
from modules.gardener.cleaning import CleaningRun, Job, format_duration


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


class _Garden:
    """A garden that loses one piece of the clicked kind per click.

    Standing in for the screen: the run asks it how many are left, which is
    the only thing it treats as evidence that anything was cleaned.
    """

    def __init__(self, jobs, deaf: set[str] = frozenset()):
        self.left = {}
        for job in jobs:
            self.left[job.key] = self.left.get(job.key, 0) + 1
        self.deaf = deaf          # kinds that ignore clicks entirely
        self.clicks = []

    def click(self, _hwnd, x, y, key=None):
        self.clicks.append((x, y))
        return True

    def clicked(self, key):
        if key not in self.deaf:
            self.left[key] = max(0, self.left.get(key, 0) - 1)

    def count(self, key):
        return self.left.get(key, 0)


def _run(app, monkeypatch, jobs, step_ms=1, deaf=frozenset(), seconds=None):
    monkeypatch.setattr(cleaning, "STEP_MS", step_ms)
    monkeypatch.setattr(cleaning, "VERIFY_MS", step_ms)
    garden = _Garden(jobs, deaf)

    order = iter(jobs)
    def click(hwnd, x, y):
        garden.clicks.append((x, y))
        job = next((j for j in jobs if (j.x, j.y) == (x, y)), None)
        if job is not None:
            garden.clicked(job.key)
        return True

    monkeypatch.setattr(cleaning, "click_at", click)

    run = CleaningRun(4242, jobs, garden.count)
    run._timer.setInterval(step_ms)
    run._check.setInterval(step_ms)
    progress, done = [], []
    run.cleaned.connect(lambda key, k, total: progress.append((key, k, total)))
    run.finished.connect(done.append)
    run.start()
    _pump(app, seconds if seconds else 0.3 + step_ms * len(jobs) * 3 / 1000)
    return garden, progress, done


def test_every_piece_of_litter_is_clicked_once(app, monkeypatch):
    jobs = [Job("dry_bush", 10, 10), Job("beetle", 20, 20),
            Job("dry_bush", 30, 30)]

    garden, _progress, _done = _run(app, monkeypatch, jobs)

    assert garden.clicks == [(10, 10), (20, 20), (30, 30)]


def test_progress_counts_per_kind_and_overall(app, monkeypatch):
    """Each kind fills its own bar while the total fills the one above."""
    jobs = [Job("dry_bush", 1, 1), Job("beetle", 2, 2), Job("dry_bush", 3, 3)]

    _garden, progress, _done = _run(app, monkeypatch, jobs)

    assert progress == [("dry_bush", 1, 1),
                        ("beetle", 1, 2),
                        ("dry_bush", 2, 3)]


# ── Nothing is counted that was not seen to go ──────────────────────────────

def test_a_click_that_changes_nothing_does_not_move_the_bar(app, monkeypatch):
    """The beetle ignores every click: its bar stays empty, and stays there."""
    jobs = [Job("dry_bush", 1, 1), Job("beetle", 2, 2)]

    _garden, progress, done = _run(app, monkeypatch, jobs, deaf={"beetle"})

    assert progress == [("dry_bush", 1, 1)]
    assert len(done) == 1                      # and the run still ends


def test_an_unclickable_piece_is_retried_and_then_left(app, monkeypatch):
    jobs = [Job("beetle", 2, 2)]

    garden, progress, _done = _run(app, monkeypatch, jobs, deaf={"beetle"})

    assert garden.clicks == [(2, 2)] * cleaning.MAX_TRIES
    assert progress == []


def test_progress_follows_the_count_even_when_two_go_at_once(app, monkeypatch):
    """Whatever disappeared is credited, not one per click."""
    jobs = [Job("dry_bush", 1, 1), Job("dry_bush", 2, 2)]
    monkeypatch.setattr(cleaning, "STEP_MS", 1)
    monkeypatch.setattr(cleaning, "VERIFY_MS", 1)
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)

    left = {"n": 2}
    def count(_key):
        left["n"] = 0          # both went with the first click
        return left["n"]

    run = CleaningRun(4242, jobs, count)
    run._timer.setInterval(1)
    run._check.setInterval(1)
    progress = []
    run.cleaned.connect(lambda key, k, total: progress.append((key, k, total)))
    run.start()
    _pump(app, 0.2)

    assert progress == [("dry_bush", 2, 2)]


def test_a_screen_that_cannot_be_read_counts_as_no_progress(app, monkeypatch):
    monkeypatch.setattr(cleaning, "STEP_MS", 1)
    monkeypatch.setattr(cleaning, "VERIFY_MS", 1)
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)

    def count(_key):
        raise OSError("no screen")

    run = CleaningRun(4242, [Job("dry_bush", 1, 1)], count)
    run._timer.setInterval(1)
    run._check.setInterval(1)
    progress, done = [], []
    run.cleaned.connect(lambda *a: progress.append(a))
    run.finished.connect(done.append)
    run.start()
    _pump(app, 0.2)

    assert progress == []
    assert len(done) == 1


def test_a_run_reports_how_long_it_took(app, monkeypatch):
    _garden, _progress, done = _run(app, monkeypatch, [Job("beetle", 1, 1)])

    assert len(done) == 1
    assert done[0] >= 0


def test_an_empty_garden_finishes_at_once(app, monkeypatch):
    garden, progress, done = _run(app, monkeypatch, [])

    assert garden.clicks == [] and progress == []
    assert done == [0.0]


def test_stopping_leaves_the_rest_alone(app, monkeypatch):
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)
    run = CleaningRun(4242, [Job("dry_bush", i, i) for i in range(50)],
                      lambda key: 0)
    run._timer.setInterval(1)
    run._check.setInterval(1)
    run.start()
    _pump(app, 0.02)

    run.stop()
    counted = run._index
    _pump(app, 0.1)

    assert run.running is False
    assert run._index == counted        # nothing carried on after the stop


# ── How the time reads ──────────────────────────────────────────────────────

def test_durations_read_the_way_they_are_spoken():
    assert format_duration(45) == "45 сек"
    assert format_duration(120) == "2 мин"
    assert format_duration(187) == "3 мин 7 сек"
