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


def _run(app, monkeypatch, jobs, step_ms=1):
    monkeypatch.setattr(cleaning, "STEP_MS", step_ms)
    clicks = []
    monkeypatch.setattr(cleaning, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    run = CleaningRun(4242, jobs)
    run._timer.setInterval(step_ms)
    progress, done = [], []
    run.cleaned.connect(lambda key, k, total: progress.append((key, k, total)))
    run.finished.connect(done.append)
    run.start()
    _pump(app, 0.2 + step_ms * len(jobs) / 1000)
    return clicks, progress, done


def test_every_piece_of_litter_is_clicked_once(app, monkeypatch):
    jobs = [Job("dry_bush", 10, 10), Job("beetle", 20, 20),
            Job("dry_bush", 30, 30)]

    clicks, _progress, _done = _run(app, monkeypatch, jobs)

    assert clicks == [(10, 10), (20, 20), (30, 30)]


def test_progress_counts_per_kind_and_overall(app, monkeypatch):
    """Each kind fills its own bar while the total fills the one above."""
    jobs = [Job("dry_bush", 1, 1), Job("beetle", 2, 2), Job("dry_bush", 3, 3)]

    _clicks, progress, _done = _run(app, monkeypatch, jobs)

    assert progress == [("dry_bush", 1, 1),
                        ("beetle", 1, 2),
                        ("dry_bush", 2, 3)]


def test_a_run_reports_how_long_it_took(app, monkeypatch):
    _clicks, _progress, done = _run(app, monkeypatch, [Job("beetle", 1, 1)])

    assert len(done) == 1
    assert done[0] >= 0


def test_an_empty_garden_finishes_at_once(app, monkeypatch):
    clicks, progress, done = _run(app, monkeypatch, [])

    assert clicks == [] and progress == []
    assert done == [0.0]


def test_stopping_leaves_the_rest_alone(app, monkeypatch):
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)
    run = CleaningRun(4242, [Job("dry_bush", i, i) for i in range(50)])
    run._timer.setInterval(1)
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
