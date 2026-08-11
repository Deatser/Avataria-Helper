"""Tracker tests.

The scenarios are the ones the old strip detector got wrong: two notes close
enough together to merge into one reading, and a lane blinded by the hit
flash at exactly the wrong moment. Both are built here as synthetic frames
so they can be asserted on rather than waited for in a real round.
"""
import numpy as np
import pytest

from modules.ava_dancers.tracker import (
    DEFAULT_GEOMETRY, Geometry, LaneTracker,
    LANES, MIN_BLOB_H, ROW_ON,
    close_profile, find_blobs, fit_line, lane_profiles, locate_field,
    region_masks,
)

GEO = DEFAULT_GEOMETRY
REGION = GEO.region          # the whole capture: tracked zone + flash below
ZONE_BOTTOM = GEO.zone_top + GEO.zone_height
NOTE_H = 100            # a real note measures 116-122px tall
NOTE_W = 120            # inside a 178px lane

CYAN  = (255, 255, 0)   # BGR — H=90  S=255 V=255, the plain arrow
GREEN = (0, 255, 0)     #       H=60,             the bonus star
RED   = (0, 0, 255)     #       H=0,              the bad tile
COLOURS = {"nice": CYAN, "bonus": GREEN, "bad": RED}


def frame(notes=(), flooded=()):
    """One capture of the tracked zone.

    notes:   (lane, bottom_edge_screen_y, kind) — drawn where they would be
    flooded: lanes lit end to end, standing in for the hit flash
    """
    img = np.zeros((REGION["height"], REGION["width"], 3), np.uint8)
    for lane in flooded:
        img[:, lane * GEO.lane_w:(lane + 1) * GEO.lane_w] = CYAN
    for lane, bottom_y, kind in notes:
        bottom = int(bottom_y) - REGION["top"]
        top = max(0, bottom - NOTE_H)
        bottom = min(GEO.zone_height, bottom)
        if bottom <= top:
            continue
        x0 = lane * GEO.lane_w + (GEO.lane_w - NOTE_W) // 2
        img[top:bottom, x0:x0 + NOTE_W] = COLOURS[kind]
    return img


def descend(tracker, notes, speed=600.0, hz=24.0, seconds=2.2, start=100.0):
    """Run notes down the zone at a constant speed and collect the presses.

    notes: (lane, bottom_edge_at_t0, kind)
    """
    presses, t, dt = [], start, 1.0 / hz
    for step in range(int(seconds * hz)):
        moved = [(lane, y + speed * (t - start), kind) for lane, y, kind in notes]
        presses += tracker.update(t, frame(moved))
        t += dt
    return presses


# ── geometry ─────────────────────────────────────────────────────────────

def test_lane_width_divides_the_field_exactly():
    assert GEO.lane_w * LANES == GEO.width


def test_tracking_stops_above_the_hit_flash():
    """The flash saturates the lane from about y=835 down. Notes are tracked
    strictly above it; the capture reaches further only to watch for the
    flash itself."""
    assert ZONE_BOTTOM <= 835
    assert REGION["top"] + GEO.flash_row >= ZONE_BOTTOM


def test_the_capture_reaches_the_flash():
    assert REGION["top"] + REGION["height"] > 835 + 200


def test_commit_line_sits_inside_the_tracked_zone():
    assert REGION["top"] < GEO.commit_y < ZONE_BOTTOM


def test_hit_line_is_below_the_tracked_zone():
    assert GEO.hit_y > ZONE_BOTTOM


def test_geometry_scales_with_the_field():
    half = Geometry(GEO.left, GEO.top, GEO.width, GEO.height // 2)
    assert half.zone_height == pytest.approx(GEO.zone_height / 2, abs=2)
    assert half.hit_y - half.top == pytest.approx((GEO.hit_y - GEO.top) / 2, abs=2)


# ── pure helpers ─────────────────────────────────────────────────────────

def test_close_profile_bridges_the_gaps_between_an_arrows_bars():
    on = np.zeros(200, bool)
    on[50:100] = True
    on[70:73] = False           # the comb a <- or -> glyph leaves
    assert find_blobs(close_profile(on)) == [(50, 100)]


def test_close_profile_keeps_two_notes_apart():
    on = np.zeros(400, bool)
    on[20:120] = True
    on[260:360] = True          # 140 rows of clear air, as measured
    assert len(find_blobs(close_profile(on))) == 2


def test_find_blobs_ignores_specks():
    on = np.zeros(200, bool)
    on[10:10 + MIN_BLOB_H - 1] = True
    assert find_blobs(on) == []


def test_fit_line_reads_a_constant_descent():
    samples = [(i * 0.04, 200 + 600 * i * 0.04) for i in range(8)]
    speed, position = fit_line(samples)
    assert speed == pytest.approx(600, rel=1e-6)
    assert position == pytest.approx(samples[-1][1], abs=1e-6)


def test_fit_line_smooths_a_noisy_reading():
    """One frame's edge measurement carries ~5px of jitter; the fitted
    position must not inherit the last frame's share of it."""
    clean = [(i * 0.04, 200 + 600 * i * 0.04) for i in range(12)]
    noisy = clean[:-1] + [(clean[-1][0], clean[-1][1] + 30)]
    _, position = fit_line(noisy)
    assert abs(position - clean[-1][1]) < 12


def test_fit_line_needs_two_points():
    assert fit_line([(0.0, 100.0)]) == (0.0, 100.0)


def test_lane_profiles_counts_each_lane_separately():
    img = frame([(2, REGION["top"] + 300, "nice")])
    profiles = lane_profiles(region_masks(img))
    assert profiles.shape[0] == LANES
    lit_per_lane = profiles[:, 0, :].sum(axis=1)
    assert lit_per_lane[2] > 0
    assert list(lit_per_lane[[0, 1, 3]]) == [0, 0, 0]


def test_locate_field_finds_a_drawn_border():
    img = np.zeros((1400, 2000, 3), np.uint8)
    img[200:1200, 500:1400] = 0                 # inside stays dark
    img[200:208, 500:1400] = CYAN               # top rail
    img[1192:1200, 500:1400] = CYAN             # bottom rail
    img[200:1200, 500:508] = CYAN               # left rail
    img[200:1200, 1392:1400] = CYAN             # right rail
    found = locate_field(img, 10, 20)
    assert found is not None
    assert found.left == 508 + 10
    assert found.top == 208 + 20


def test_locate_field_returns_none_without_a_playfield():
    assert locate_field(np.zeros((600, 800, 3), np.uint8), 0, 0) is None


# ── tracking ─────────────────────────────────────────────────────────────

def test_one_note_gives_exactly_one_press():
    presses = descend(LaneTracker(), [(1, REGION["top"] + 20, "nice")])
    assert len(presses) == 1
    assert presses[0].lane == 1
    assert presses[0].kind == "nice"


def test_press_is_scheduled_for_when_the_note_reaches_the_hit_line():
    speed, start_y, t0 = 600.0, REGION["top"] + 20, 100.0
    presses = descend(LaneTracker(), [(0, start_y, "nice")], speed=speed, start=t0)
    expected = t0 + (GEO.hit_y - start_y) / speed
    assert presses[0].arrival == pytest.approx(expected, abs=0.02)
    assert presses[0].speed == pytest.approx(speed, rel=0.02)


def test_a_slow_note_at_the_start_of_a_round_is_pressed():
    """The regression that made the first live run press nothing at all.

    A round opens at 363 px/s. The "has it travelled far enough to be a real
    note" check was reading the rolling speed-fit window instead of the
    track's whole descent, and at that speed the window only ever covers
    ~106px — under the floor, so every note was refused as "мало прошло".
    """
    presses = descend(LaneTracker(), [(0, REGION["top"] + 20, "nice")],
                      speed=363.0, seconds=3.2)
    assert len(presses) == 1


def test_speed_is_measured_not_assumed():
    fast = descend(LaneTracker(), [(0, REGION["top"] + 20, "nice")],
                   speed=900.0, seconds=1.6)
    assert fast[0].speed == pytest.approx(900.0, rel=0.03)


def test_two_notes_a_flash_apart_both_get_pressed():
    """The old detector's central failure: two notes close enough that the
    lane never reads empty between them, so the second never fired."""
    top = REGION["top"] + 20
    presses = descend(LaneTracker(),
                      [(3, top, "nice"), (3, top - 250, "nice")],
                      speed=600.0, seconds=2.8)
    assert len(presses) == 2
    gap = abs(presses[1].arrival - presses[0].arrival)
    assert gap == pytest.approx(250 / 600, abs=0.05)


def test_a_note_is_still_pressed_when_the_flash_blinds_the_lane():
    """A track coasts on its own fitted line, so being unable to see the
    note for a few frames must not cost the press."""
    tracker = LaneTracker()
    speed, start_y, t0 = 600.0, REGION["top"] + 20, 100.0
    presses, t = [], t0
    for step in range(53):
        y = start_y + speed * (t - t0)
        blind = 30 <= step <= 33          # the lane washes out mid-descent
        presses += tracker.update(
            t, frame([] if blind else [(2, y, "nice")], flooded=[2] if blind else []))
        t += 1 / 24
    assert len(presses) == 1
    assert presses[0].arrival == pytest.approx(
        t0 + (GEO.hit_y - start_y) / speed, abs=0.05)


def test_bad_note_is_never_pressed():
    assert descend(LaneTracker(), [(0, REGION["top"] + 20, "bad")]) == []


def test_bonus_note_is_pressed():
    presses = descend(LaneTracker(), [(2, REGION["top"] + 20, "bonus")])
    assert [p.kind for p in presses] == ["bonus"]


def test_all_four_lanes_are_independent():
    top = REGION["top"] + 20
    presses = descend(LaneTracker(), [(i, top, "nice") for i in range(LANES)])
    assert sorted(p.lane for p in presses) == [0, 1, 2, 3]


def test_a_blob_stuck_on_the_zone_floor_never_presses():
    """The tail of a note already on its way out. It cannot be measured and
    would only ever produce a second press for a note that has had one."""
    tracker = LaneTracker()
    floor = ZONE_BOTTOM
    presses = []
    for step in range(20):
        presses += tracker.update(100.0 + step / 24, frame([(1, floor, "nice")]))
    assert presses == []


def test_an_empty_zone_presses_nothing():
    tracker = LaneTracker()
    assert [p for step in range(20)
            for p in tracker.update(100.0 + step / 24, frame())] == []


def test_reset_forgets_everything_in_flight():
    tracker = LaneTracker()
    descend(tracker, [(0, REGION["top"] + 20, "nice")], seconds=0.5)
    assert tracker.live_tracks()
    tracker.reset()
    assert tracker.live_tracks() == []


def test_row_on_is_reachable_by_a_real_note():
    """A note has to light enough of a row to register at all."""
    assert NOTE_W >= ROW_ON


# ── checking the bot's own work ──────────────────────────────────────────

def flash_frame(lanes):
    """A capture where the named lanes are washed out below the flash line —
    what the game draws when a press scores."""
    img = np.zeros((REGION["height"], REGION["width"], 3), np.uint8)
    for lane in lanes:
        img[GEO.flash_row:, lane * GEO.lane_w:(lane + 1) * GEO.lane_w] = CYAN
    return img


def test_a_press_followed_by_its_flash_counts_as_scored():
    tracker = LaneTracker()
    tracker.expect_hit(2, 100.0)
    tracker.update(100.15, flash_frame([2]))     # the flash, ~145ms later
    tracker.update(100.60, frame())              # past the window: tally it
    assert (tracker.scored, tracker.missed) == (1, 0)


def test_a_press_with_no_flash_counts_as_missed():
    tracker = LaneTracker()
    tracker.expect_hit(2, 100.0)
    tracker.update(100.15, frame())
    tracker.update(100.60, frame())
    assert (tracker.scored, tracker.missed) == (0, 1)


def test_a_flash_in_another_lane_does_not_count():
    tracker = LaneTracker()
    tracker.expect_hit(1, 100.0)
    tracker.update(100.15, flash_frame([3]))
    tracker.update(100.60, frame())
    assert (tracker.scored, tracker.missed) == (0, 1)


def test_a_flash_too_early_does_not_count():
    """The lane can still be lit from the previous note when this one is
    pressed; only a flash inside the window belongs to this press."""
    tracker = LaneTracker()
    tracker.expect_hit(0, 100.0)
    tracker.update(100.01, flash_frame([0]))
    tracker.update(100.60, frame())
    assert (tracker.scored, tracker.missed) == (0, 1)


def test_verification_leaves_tracking_alone():
    """The rows the flash lives in must never become notes."""
    tracker = LaneTracker()
    presses = [p for _ in range(20)
               for p in tracker.update(100.0, flash_frame([0, 1, 2, 3]))]
    assert presses == []


def test_the_field_lands_in_the_same_place_whoever_framed_it():
    """The same window through two backends: PrintWindow includes the
    invisible resize border, a WGC frame starts 8px in. Told the right
    origin, both have to place the playfield identically — getting this
    wrong shifted every lane into its neighbour and cost a round."""
    border = 8
    outer = np.zeros((1400, 2560, 3), np.uint8)
    outer[300:1300, 900:1600] = 0
    outer[300:308, 900:1600] = CYAN
    outer[1292:1300, 900:1600] = CYAN
    outer[300:1300, 900:908] = CYAN
    outer[300:1300, 1592:1600] = CYAN

    as_printwindow = locate_field(outer, -border, -border)
    inner = outer[border:, border:]                   # what WGC hands over
    as_wgc = locate_field(inner, 0, 0)

    assert as_printwindow is not None and as_wgc is not None
    assert (as_wgc.left, as_wgc.top) == (as_printwindow.left, as_printwindow.top)


def test_a_missed_press_is_recorded_with_what_it_was_aimed_at():
    """One line per miss, with the note's speed: several at once at one
    speed is a different problem from one every few minutes."""
    tracker = LaneTracker()
    tracker.expect_hit(2, 100.0, speed=655.0)
    tracker.update(100.15, frame())
    tracker.update(100.60, frame())
    assert tracker.missed == 1
    assert tracker.miss_log == [{"lane": 2, "at": 100.0, "v": 655.0}]


def test_a_scored_press_is_not_recorded_as_a_miss():
    tracker = LaneTracker()
    tracker.expect_hit(2, 100.0, speed=655.0)
    tracker.update(100.15, flash_frame([2]))
    tracker.update(100.60, frame())
    assert tracker.miss_log == []


def test_note_speed_reports_what_the_lanes_have_measured():
    """Printed next to the poll rate so a speed wave shows up in the log as
    a step — the thing a burst of misses would have to line up with."""
    tracker = LaneTracker()
    assert tracker.note_speed() == 0.0, "a seeded default is not a measurement"
    descend(tracker, [(0, REGION["top"] + 20, "nice")], speed=600.0)
    assert tracker.note_speed() == pytest.approx(600, rel=0.05)

