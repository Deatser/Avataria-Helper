# tests/modules/test_butterfly.py
"""Following a butterfly: the bookkeeping, with no screen involved."""
import pytest

from modules.gardener.butterfly import (MATCH_RADIUS, MAX_MISSED,
                                        MAX_TRACKS, Track, Tracks,
                                        colour_for, dedupe)


def test_something_new_becomes_a_track():
    tracks = Tracks()

    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)

    assert tracks.positions() == [(1, 100, 100, 0.8)]


def test_a_track_follows_its_butterfly():
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)

    tracks.step([(140.0, 100.0, 0.8)], dt=0.1)

    assert tracks.positions() == [(1, 140, 100, 0.8)]
    assert len(tracks.items) == 1        # followed, not counted twice


def test_speed_is_learnt_and_used_to_predict():
    """The point of the whole exercise: the dot leads rather than lags."""
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)
    for step in range(1, 6):             # 400 px/s to the right
        tracks.step([(100.0 + 40 * step, 100.0, 0.8)], dt=0.1)

    moving = tracks.items[0]
    assert moving.vx > 250               # smoothing lags the true 400
    assert moving.predicted(0.1)[0] > moving.x


def test_a_missed_frame_keeps_the_track_moving():
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)
    tracks.step([(140.0, 100.0, 0.8)], dt=0.1)

    tracks.step([], dt=0.1)              # passed behind something

    assert len(tracks.items) == 1
    assert tracks.items[0].missed == 1
    assert tracks.items[0].x > 140       # carried on by its own speed


def test_a_butterfly_gone_for_good_is_dropped():
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)

    for _ in range(MAX_MISSED + 1):
        tracks.step([], dt=0.1)

    assert tracks.items == []


def test_a_detection_too_far_away_is_a_different_butterfly():
    """Otherwise one track would teleport across the garden between frames."""
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)

    tracks.step([(100.0 + MATCH_RADIUS * 2, 100.0, 0.8)], dt=0.1)

    assert len(tracks.items) == 2


def test_two_butterflies_keep_their_own_identities():
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8), (600.0, 400.0, 0.7)], dt=0.0)

    tracks.step([(620.0, 400.0, 0.7), (120.0, 100.0, 0.8)], dt=0.1)

    assert sorted(x for _n, x, _y, _s in tracks.positions()) == [120, 620]
    assert len(tracks.items) == 2


def test_the_nearer_detection_wins():
    tracks = Tracks()
    tracks.items = [Track(100.0, 100.0)]

    tracks.step([(180.0, 100.0, 0.5), (110.0, 100.0, 0.5)], dt=0.1)

    assert tracks.items[0].x == 110      # the close one is this butterfly…
    assert len(tracks.items) == 2        # …and the far one is a new track


def test_only_the_best_are_followed():
    """The template is tiny, so a low bar finds hundreds on an empty screen —
    and each one followed costs a screen grab per tick."""
    tracks = Tracks()

    tracks.step([(i * 200.0, 100.0, 0.5 + i / 1000) for i in range(40)], dt=0.0)

    assert len(tracks.items) == MAX_TRACKS
    assert min(t.score for t in tracks.items) > 0.5   # the weakest were cut


def test_the_same_butterfly_seen_by_two_boxes_is_one_detection():
    """Search boxes overlap; without this every duplicate spawns a track."""
    found = [(100.0, 100.0, 0.7), (104.0, 102.0, 0.6), (400.0, 100.0, 0.65)]

    assert dedupe(found, radius=20.0) == [(100.0, 100.0, 0.7),
                                          (400.0, 100.0, 0.65)]


def test_each_butterfly_keeps_its_number_and_colour():
    """The number is the identity: №1 is the same butterfly, and the same
    colour, from one frame to the next."""
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)
    first = tracks.items[0]

    tracks.step([(140.0, 100.0, 0.8), (900.0, 500.0, 0.7)], dt=0.1)

    assert first.number == 1
    assert [t.number for t in tracks.items] == [1, 2]
    assert first.colour == colour_for(1) != colour_for(2)


def test_a_new_butterfly_never_reuses_a_number():
    tracks = Tracks()
    tracks.step([(100.0, 100.0, 0.8)], dt=0.0)
    for _ in range(MAX_MISSED + 1):
        tracks.step([], dt=0.1)          # the first one leaves

    tracks.step([(700.0, 700.0, 0.8)], dt=0.1)

    assert tracks.items[0].number == 2   # not 1 again


def test_the_drawing_that_matched_is_remembered():
    """It is tried first next time; that is what keeps sixteen pictures
    costing about one."""
    tracks = Tracks()

    tracks.step([(100.0, 100.0, 0.8, 5)], dt=0.0)

    assert tracks.items[0].picture == 5
