# tests/modules/test_hockey_trajectory.py
"""Following the puck, and turning that into per-row timings."""
import numpy as np

from app.core.config import HockeyConfig
from modules.hockey.rink_area import from_config
from modules.hockey.trajectory import (PuckFlight, PuckSample, RowTiming,
                                       ShotTrack, append, arrived, averaged,
                                       crossings, load, mirror_fill, puck_at)

# Rink at (1000, 500), 400x600. Two rows whose defenders occupy known
# bands, so a crossing time can be checked against arithmetic.
_ROWS = [
    # body 640..740 on screen
    {"y": 700, "height": 60, "body_w": 80, "body_h": 100, "body_dy": -60},
    # body 840..940
    {"y": 900, "height": 60, "body_w": 80, "body_h": 100, "body_dy": -60},
]


def _config(**overrides) -> HockeyConfig:
    cfg = HockeyConfig()
    cfg.rink_left, cfg.rink_top = 1000, 500
    cfg.rink_width, cfg.rink_height = 400, 600
    cfg.lanes = list(_ROWS)
    cfg.goal_y, cfg.goal_left, cfg.goal_right = 560, 1150, 1250
    cfg.shooter_x, cfg.shooter_y = 1200, 1050
    cfg.wall_left, cfg.wall_right = 1020, 1380
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _straight_track(speed=250.0, seconds=2.0, step=0.03) -> ShotTrack:
    """Puck rising from the shooter at a constant speed."""
    samples, t = [], 0.0
    while t <= seconds:
        samples.append(PuckSample(t=t, x=1200.0, y=1050.0 - speed * t))
        t += step
    return ShotTrack(aim=0.0, samples=samples)


# ── Turning a track into crossings ───────────────────────────────────────

def test_each_row_gets_its_own_moment():
    geom = from_config(_config())

    found = crossings(geom, _straight_track())

    assert [c.row for c in found] == [0, 1]
    # Row 2 stands with its skates at 940, so the puck reaches its ice a
    # touch below that; from 1050 at 250px/s that is about 0.40s.
    assert abs(found[1].t_enter - (1050 - 950) / 250) < 0.03
    # And row 1's, 200px further on.
    assert abs(found[0].t_enter - (1050 - 750) / 250) < 0.03


def test_a_crossing_is_an_interval_not_an_instant():
    """The ice a defender stands on has depth, so the puck is at risk for a
    stretch — but a short one, nothing like the height it is drawn at."""
    geom = from_config(_config())

    found = crossings(geom, _straight_track())

    row = found[1]
    assert row.t_exit > row.t_enter
    assert (row.t_exit - row.t_enter) < 100 / 250     # far less than its height
    assert row.t_enter < row.t_middle < row.t_exit


def test_a_flight_that_stopped_short_reports_only_what_it_reached():
    """Row 2's band is fully crossed by 0.84s, row 1's not entered until
    1.24s — so a second of flight measures one row and says nothing about
    the other."""
    geom = from_config(_config())

    found = crossings(geom, _straight_track(seconds=1.0))

    assert [c.row for c in found] == [1]


def test_a_half_crossed_row_is_not_reported_at_all():
    """Entered but never left means the track ran out, not that the puck
    stopped there — an exit time invented from that would be a lie in the
    one place the planner most needs the truth."""
    geom = from_config(_config())

    assert crossings(geom, _straight_track(seconds=0.5)) == []


def test_a_defender_standing_across_the_goal_line_is_still_timed():
    """The puck scores while still inside its strip of ice, so demanding a
    real exit threw the row away entirely (live, 2026-08-09)."""
    # Outline 600..740, so skates at 740 and the strip roughly 705..750,
    # straddling a goal line at 720.
    cfg = _config(goal_y=720,
                  lanes=[{"y": 700, "height": 60, "body_w": 80,
                          "body_h": 140, "body_dy": -100}])
    geom = from_config(cfg)

    found = crossings(geom, _straight_track(seconds=2.0))

    assert len(found) == 1
    assert abs(found[0].t_enter - (1050 - 750) / 250) < 0.03
    # Risk ends at the goal line, not at the far side of the strip.
    assert abs(found[0].t_exit - (1050 - 720) / 250) < 0.03


def test_a_defender_entirely_behind_the_goal_is_not_timed():
    cfg = _config(goal_y=760,
                  lanes=[{"y": 650, "height": 60, "body_w": 80,
                          "body_h": 100, "body_dy": -50}])   # skates at 700

    assert crossings(from_config(cfg), _straight_track(seconds=2.0)) == []


def test_a_track_that_woke_up_late_does_not_claim_to_start_at_the_release():
    """"Started inside this row" and "the tracker only found it here" look
    identical from the samples alone, and the first is a fact about geometry
    while the second is a fact about the tracker."""
    cfg = _config(lanes=[{"y": 800, "height": 60, "body_w": 80,
                          "body_h": 200, "body_dy": -100}])   # body 700..900
    geom = from_config(cfg)
    late = ShotTrack(aim=0.0, samples=[
        PuckSample(t=0.5 + 0.03 * i, x=1200.0, y=850.0 - 8 * i)
        for i in range(20)])                  # begins at 850, inside the band

    assert crossings(geom, late) == []


def test_a_row_the_puck_starts_inside_is_timed_from_the_release():
    """A band reaching below the stick is one the puck is already in."""
    cfg = _config(lanes=[{"y": 1000, "height": 60, "body_w": 80,
                          "body_h": 200, "body_dy": -100}])   # body 900..1100
    geom = from_config(cfg)

    found = crossings(geom, _straight_track(seconds=2.0))

    assert len(found) == 1
    assert found[0].t_enter == 0.0


def test_a_track_of_nothing_crosses_nothing():
    assert crossings(from_config(_config()), ShotTrack(aim=0.0)) == []


# ── Following the puck ───────────────────────────────────────────────────

_GUIDE_BGR = (220, 200, 85)   # the game's own cyan aim dashes


def _fly(cfg, moving, frames=40, step=0.03) -> PuckFlight:
    """Feed a whole flight. `moving(i)` gives the extra sprites in frame i as
    (x, y, size) or (x, y, size, colour) — the puck among them, if there is
    one."""
    flight = PuckFlight(from_config(cfg), aim=0.0, start=(1200, 1050))
    t = 0.0
    for i in range(frames):
        frame = np.full((cfg.rink_height, cfg.rink_width, 3), 200, np.uint8)
        for sprite in moving(i):
            _paint(frame, cfg, *sprite)
        flight.feed(frame, t)
        t += step
    return flight


def _paint(frame, cfg, x, y, size, colour=20):
    left = int(x - cfg.rink_left - size // 2)
    top  = int(y - cfg.rink_top - size // 2)
    frame[max(0, top):top + size, max(0, left):left + size] = colour


def test_a_rising_puck_is_followed():
    cfg = _config()

    flight = _fly(cfg, lambda i: [(1200, 1050 - 8 * i, 14)])

    samples = flight.resolve().samples
    assert len(samples) > 12
    assert samples[-1].y < samples[0].y          # it went toward the goal
    assert all(abs(s.x - 1200) < 12 for s in samples)


def test_a_defender_sliding_sideways_is_not_the_puck():
    """The other moving things on the rink travel across it, never toward
    the goal — which is why the path has to rise, not merely exist."""
    cfg = _config()

    flight = _fly(cfg, lambda i: [(1100 + 8 * i, 900, 24)])

    assert flight.resolve().samples == []


def test_a_path_is_judged_by_what_it_does_not_where_it_began():
    """Requiring a start at the stick was tried and cost whole
    measurements: that is exactly where the shooter's swing swallows the
    puck into one blob too big to be a candidate, so there was no seed at
    all (2026-08-10). A chain that rises steadily all the way is a flight
    wherever it was first picked up; the caller then throws away anything
    that did not reach the goal."""
    cfg = _config()

    # Picked up two hundred pixels along, as happens when the release is
    # lost in the animation.
    samples = _fly(cfg, lambda i: [(1200, 850 - 9 * i, 14)]).resolve().samples

    assert len(samples) > 12
    assert samples[-1].y < samples[0].y


def test_the_real_puck_wins_over_a_decoy_at_the_stick():
    """The avatar animates right where the puck starts, so something puck-
    sized is always there for the first few frames. It does not go
    anywhere, and a path is judged by how far it gets."""
    cfg = _config()

    def moving(i):
        # A flicker at the stick for the first six frames, and the puck.
        decoy = [(1210, 1040 - 3 * i, 14)] if i < 6 else []
        return decoy + [(1180, 1046 - 9 * i, 14)]

    samples = _fly(cfg, moving).resolve().samples

    assert len(samples) > 12
    assert abs(samples[-1].x - 1180) < 20


def test_a_flight_stops_at_the_goal_line():
    cfg = _config()

    flight = _fly(cfg, lambda i: [(1200, 1050 - 14 * i, 14)])

    assert flight.resolve().samples[-1].y <= from_config(cfg).goal_y


def test_the_watching_window_closes_itself():
    cfg = _config()
    flight = PuckFlight(from_config(cfg), aim=0.0, start=(1200, 1050))
    frame = np.full((cfg.rink_height, cfg.rink_width, 3), 200, np.uint8)

    assert flight.feed(frame, 0.0) is True
    assert flight.feed(frame, 9.0) is False


# ── Cutting the pictures afterwards ──────────────────────────────────────

def test_the_puck_survives_being_hidden_behind_a_defender():
    """It passes behind the very defenders it is being timed against, and a
    difference image loses it for as long as that lasts. A left-post shot
    swings out through the lower rows and disappeared for long enough to
    kill two tracks out of three (2026-08-10)."""
    cfg = _config()

    def moving(i):
        if 10 <= i <= 18:      # nine frames out of sight
            return []
        return [(1200, 1050 - 9 * i, 14)]

    samples = _fly(cfg, moving).resolve().samples

    assert len(samples) > 20
    assert samples[-1].y < 700          # it carried on past the gap


def test_a_gap_too_long_to_bridge_is_not_bridged():
    """The allowance stops growing, so a jump cannot reach halfway across
    the rink and call it the same puck. Out of sight for longer than
    _MAX_GAP_S — a duration, so the same shot is judged the same way however
    fast the frames were arriving."""
    cfg = _config()
    step = 0.03

    def moving(i):
        if 0.45 <= i * step < 1.05:        # 0.63s, past the allowance
            return []
        return [(1200, 1050 - 9 * i, 14)]

    samples = _fly(cfg, moving, frames=45, step=step).resolve().samples

    assert samples, "the run before the gap is a chain in its own right"
    assert samples[-1].y > 850             # and it ends there


def test_the_games_own_aim_guide_is_not_a_candidate():
    """A column of cyan dashes runs from the stick to the goal and animates
    as it goes. Each is about puck-sized and puck-shaped, there are a dozen
    and a half of them, and they were crowding the real puck out of the
    candidate budget (2026-08-09)."""
    cfg = _config()

    def moving(i):
        dashes = [(1200, 1000 - 40 * k + (i % 2), 18, _GUIDE_BGR)
                  for k in range(10)]
        return dashes + [(1240, 1050 - 9 * i, 14)]

    flight = _fly(cfg, moving, frames=30)

    assert flight.candidates()          # the puck itself still gets through
    assert all(abs(x - 1240) < 30 for x, _y in flight.candidates())


def test_the_puck_survives_flying_over_the_guide():
    """It spends most of the flight directly over those cyan dashes, so a
    difference blob holds the dark puck and the dash it just uncovered — and
    an average of the two could go either way."""
    cfg = _config()

    def moving(i):
        dashes = [(1200, 1000 - 40 * k, 18, _GUIDE_BGR) for k in range(10)]
        return dashes + [(1200, 1050 - 9 * i, 14)]      # right on top of them

    samples = _fly(cfg, moving, frames=45).resolve().samples

    assert len(samples) > 15
    assert samples[-1].y < 700


def test_the_summary_says_which_way_a_track_broke():
    """Candidates reaching the goal while the chain stopped short means the
    chain is at fault; candidates stopping in the same place means the puck
    was never seen any higher."""
    cfg = _config()

    flight = _fly(cfg, lambda i: [(1200, 1050 - 9 * i, 14)], frames=20)
    frames, seen, highest, step = flight.summary()

    assert frames == 20
    assert seen > 10
    assert highest is not None and highest < 900
    assert abs(step - 30) < 1        # the grid every timing rests on


def test_the_summary_of_an_empty_watch_says_nothing_was_seen():
    cfg = _config()

    frames, seen, highest, _step = _fly(cfg, lambda i: [], frames=5).summary()

    assert (frames, seen, highest) == (5, 0, None)


def test_every_candidate_is_kept_for_the_picture():
    """The grey dots are what say whether a failure was the puck never being
    seen or the chain declining to follow it."""
    cfg = _config()

    flight = _fly(cfg, lambda i: [(1200, 1050 - 8 * i, 14)], frames=10)

    assert len(flight.candidates()) >= 5


def test_a_kept_frame_can_be_read_back():
    """The per-row pictures are cut once the path is known — taken live they
    photograph whatever the tracker believed at the time."""
    cfg = _config()

    flight = _fly(cfg, lambda i: [(1200, 1050 - 8 * i, 14)])
    frame = flight.frame_at(0.30)

    assert frame is not None
    assert frame.shape == (cfg.rink_height, cfg.rink_width, 3)


def test_the_puck_position_is_interpolated_to_match_the_picture():
    track = _straight_track(speed=250.0, seconds=1.0)

    where = puck_at(track, 0.4)

    assert where is not None
    assert abs(where[1] - (1050 - 250 * 0.4)) < 2


def test_no_position_outside_the_measured_flight():
    track = _straight_track(seconds=0.5)

    assert puck_at(track, 2.0) is None


# ── Was it followed all the way? ─────────────────────────────────────────

def test_a_flight_that_reached_the_goal_counts():
    geom = from_config(_config())

    assert arrived(geom, _straight_track(seconds=2.0)) is True


def test_a_flight_that_died_halfway_does_not():
    """Measured live on 2026-08-09: three identical centre shots reported
    1.38s, 0.91s and 1.38s. The odd one was not a faster flight, it was a
    lost one — and it went into the table looking like a measurement."""
    geom = from_config(_config())

    assert arrived(geom, _straight_track(seconds=0.9)) is False


def test_a_flight_stopping_just_short_of_the_goal_does_not_count():
    """Slack here was tried and made the module disagree with itself: rows
    whose defenders reach above the goal need a crossing *at* that line, so
    a flight accepted 40px short measured nothing at all."""
    geom = from_config(_config())          # goal at y=560
    # Ends at 570 — ten pixels short.
    track = ShotTrack(aim=0.0, samples=[
        PuckSample(t=0.03 * i, x=1200.0, y=1050.0 - 20 * i) for i in range(25)])

    assert track.samples[-1].y > geom.goal_y
    assert arrived(geom, track) is False


def test_nothing_tracked_did_not_arrive():
    assert arrived(from_config(_config()), ShotTrack(aim=0.0)) is False


# ── Averaging repeats ────────────────────────────────────────────────────

def test_repeats_at_one_aim_are_averaged():
    """A single crossing time is only ever as sharp as the capture rate,
    because it is interpolated across one frame's gap. Repeats are not
    limited by it."""
    geom = from_config(_config())
    slow = _straight_track(speed=240.0, seconds=2.0)
    fast = _straight_track(speed=260.0, seconds=2.0)

    timings = averaged(geom, [slow, fast], aim=0.0)

    assert [t.row for t in timings] == [0, 1]
    assert all(t.shots == 2 for t in timings)
    # Row 2's ice strip starts at 950: 100px at 240 and at 260 px/s.
    assert abs(timings[1].t_enter - (100 / 240 + 100 / 260) / 2) < 0.01


def test_a_row_that_disagrees_with_itself_says_so():
    """Hidden inside a mean it would look like a measurement."""
    geom = from_config(_config())

    timings = averaged(geom, [_straight_track(speed=200.0, seconds=2.0),
                              _straight_track(speed=320.0, seconds=2.0)],
                       aim=0.0)

    assert timings[1].spread > 0.1


def test_only_flights_at_the_same_aim_are_pooled():
    geom = from_config(_config())
    centre = _straight_track(seconds=2.0)
    left = _straight_track(seconds=2.0)
    left.aim = -0.5

    timings = averaged(geom, [centre, left], aim=0.0)

    assert all(t.shots == 1 for t in timings)


def test_nothing_measured_averages_to_nothing():
    assert averaged(from_config(_config()), [], aim=0.0) == []


# ── Filling a gap from the opposite aim ──────────────────────────────────

def _row_timing(row, t_enter, x_enter):
    return RowTiming(row=row, shots=3, t_enter=t_enter, t_exit=t_enter + 0.4,
                     x_enter=x_enter, x_exit=x_enter + 10, spread=0.03)


def test_a_missing_row_is_taken_from_the_opposite_aim():
    """The two side shots were measured independently and, folded about the
    shooter, agreed to a pixel or two on every row (2026-08-09). A row goes
    unmeasured because the puck passed behind somebody, which says nothing
    about the shot itself."""
    geom = from_config(_config())        # shooter x = 1200
    timings = {-0.5: [_row_timing(0, 1.2, 1150.0), _row_timing(1, 0.5, 1090.0)],
               0.5:  [_row_timing(0, 1.2, 1250.0)]}

    filled = mirror_fill(geom, timings)

    assert filled == [(0.5, 1)]
    added = [t for t in timings[0.5] if t.row == 1][0]
    assert added.x_enter == 2 * 1200 - 1090.0
    assert added.mirrored is True
    assert added.t_enter == 0.5


def test_a_row_that_was_measured_is_left_alone():
    geom = from_config(_config())
    timings = {-0.5: [_row_timing(0, 1.2, 1150.0)],
               0.5:  [_row_timing(0, 1.3, 1250.0)]}

    assert mirror_fill(geom, timings) == []
    assert timings[0.5][0].t_enter == 1.3


def test_the_centre_has_no_opposite_to_borrow_from():
    geom = from_config(_config())
    timings = {0.0: [_row_timing(0, 1.2, 1200.0)]}

    assert mirror_fill(geom, timings) == []


# ── Storage ──────────────────────────────────────────────────────────────

def test_a_measured_flight_survives_a_round_trip(tmp_path):
    path = tmp_path / "shots.json"
    track = _straight_track(seconds=0.3)

    assert append(track, path) == 1
    assert append(_straight_track(seconds=0.3), path) == 2

    stored = load(path)
    assert [shot.aim for shot in stored] == [0.0, 0.0]
    assert len(stored[0].samples) == len(track.samples)
    assert abs(stored[0].samples[-1].y - track.samples[-1].y) < 0.5


def test_a_missing_file_is_an_empty_history(tmp_path):
    assert load(tmp_path / "nothing.json") == []


def test_a_mangled_file_costs_the_history_not_the_run(tmp_path):
    path = tmp_path / "shots.json"
    path.write_text("{ this is not json", encoding="utf-8")

    assert load(path) == []


# ── Pulls between the measured ones ──────────────────────────────────────

def test_a_pull_between_two_measured_ones_scales_the_arc():
    """A straight shot holds its x to within half a pixel over the whole
    flight, and the two full pulls deviate from it by mirror-image amounts
    at every moment — 7px of disagreement against an 84px swing, correlated
    0.978 (2026-08-10). One shape, then, scaled by how hard the pull was."""
    from modules.hockey.trajectory import RowTiming, blend

    straight = [RowTiming(row=0, shots=3, t_enter=1.0, t_exit=1.1,
                          x_enter=1280.0, x_exit=1280.0, spread=0.01)]
    full = [RowTiming(row=0, shots=3, t_enter=1.04, t_exit=1.16,
                      x_enter=1360.0, x_exit=1340.0, spread=0.02)]

    half = blend(straight, full, 0.25)[0]

    assert half.x_enter == 1320.0          # half of the 80px swing
    assert half.x_exit == 1310.0
    assert abs(half.t_enter - 1.02) < 1e-9
    assert blend(straight, full, 0.5)[0].x_enter == 1360.0
    assert blend(straight, full, 0.0)[0].x_enter == 1280.0


def test_the_in_between_pulls_never_replace_a_measured_one():
    from modules.hockey.trajectory import RowTiming, spread_aims

    def rows(x):
        return [RowTiming(row=0, shots=3, t_enter=1.0, t_exit=1.1,
                          x_enter=x, x_exit=x, spread=0.01)]

    measured = {-0.5: rows(1200.0), 0.0: rows(1280.0), 0.5: rows(1360.0)}

    dense = spread_aims(measured)

    assert len(dense) == 21
    assert dense[0.0] is measured[0.0]
    assert dense[0.5] is measured[0.5]
    assert round(dense[0.25][0].x_enter) == 1320
    assert round(dense[-0.25][0].x_enter) == 1240


def test_and_nothing_is_spread_without_a_straight_shot_to_spread_from():
    from modules.hockey.trajectory import RowTiming, spread_aims

    only_side = {0.5: [RowTiming(row=0, shots=1, t_enter=1.0, t_exit=1.1,
                                 x_enter=1360.0, x_exit=1360.0, spread=0.0)]}

    assert spread_aims(only_side) == only_side


# ── However fast the frames arrive ───────────────────────────────────────
# The chain used to be built in frame numbers: how far the puck must rise
# per frame, how many frames it may go unseen, how many of them make a
# flight rather than a coincidence. All of it was tuned against the ~13 a
# second PrintWindow managed, and none of it is a fact about a puck. A
# flight is the one measurement here that the faster capture exists for —
# every crossing time is interpolated across one frame gap, and there were
# 22 of them in a whole flight.

_SLOW_STEP = 0.077     # PrintWindow: 44.6ms a grab, plus the encode
_FAST_STEP = 0.025     # WGC, paced by the game's own redraws


def _rising(step, speed=300.0, hidden=None):
    """A puck leaving the stick at `speed` px/s, optionally invisible over
    the (from, to) seconds it spends behind somebody."""
    def moving(i):
        t = i * step
        if hidden is not None and hidden[0] <= t < hidden[1]:
            return []
        return [(1200, 1050 - speed * t, 14)]
    return moving


def test_a_flight_measures_the_same_at_either_capture_rate():
    cfg = _config()
    geom = from_config(cfg)

    both = [crossings(geom, _fly(cfg, _rising(step), frames=int(1.5 / step),
                                 step=step).resolve())
            for step in (_SLOW_STEP, _FAST_STEP)]

    slow, fast = both
    assert len(slow) == len(fast) == 2
    for one, other in zip(slow, fast):
        assert abs(one.t_enter - other.t_enter) < 0.08
        assert abs(one.t_exit - other.t_exit) < 0.08


def test_the_puck_may_be_hidden_for_as_long_however_many_frames_that_is():
    """It passes behind the defenders it is being timed against and a
    difference image loses it for as long as that lasts. Ten frames was half
    a second of PrintWindow and is a quarter of one at WGC's rate — and two
    left-post shots in three already died in exactly this gap when the
    allowance was measured in frames (2026-08-10)."""
    cfg = _config()

    for step in (_SLOW_STEP, _FAST_STEP):
        flight = _fly(cfg, _rising(step, hidden=(0.5, 0.95)),
                      frames=int(1.5 / step), step=step)

        samples = flight.resolve().samples

        assert samples, f"nothing followed at {step}s a frame"
        assert min(s.y for s in samples) < 750, (
            f"the chain stopped at the gap at {step}s a frame")
