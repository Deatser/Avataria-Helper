# tests/modules/test_hockey_planner.py
"""Choosing when to let go — and refusing to, which matters more."""
from app.core.config import HockeyConfig
from modules.hockey.planner import best_effort, plan, why_not
from modules.hockey.rink_area import from_config
from modules.hockey.trajectory import RowTiming


def _config(**overrides) -> HockeyConfig:
    cfg = HockeyConfig()
    cfg.rink_left, cfg.rink_top = 1000, 500
    cfg.rink_width, cfg.rink_height = 400, 600
    cfg.goal_y, cfg.goal_left, cfg.goal_right = 560, 1150, 1250
    cfg.shooter_x, cfg.shooter_y = 1200, 1050
    cfg.wall_left, cfg.wall_right = 1020, 1380
    cfg.puck_radius = 10
    # One row, body 840..940, defender 60 wide.
    cfg.lanes = [{"y": 900, "height": 60, "body_w": 60, "body_h": 100,
                  "body_dy": -60}]
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _timing(x_enter=1200.0, x_exit=1200.0, t_enter=0.5, t_exit=0.7):
    return [RowTiming(row=0, shots=3, t_enter=t_enter, t_exit=t_exit,
                      x_enter=x_enter, x_exit=x_exit, spread=0.03)]


class _Motion:
    """A stand-in for the measured model: `where(t)` gives the row's
    defenders at absolute time t, or None for "no honest answer"."""

    def __init__(self, where):
        self._where = where

    def positions(self, _row, t):
        return self._where(t)


def test_a_clear_rink_gives_a_shot():
    geom = from_config(_config())
    motion = _Motion(lambda t: [1000.0])       # parked far to the left

    found = plan(geom, motion, {0.0: _timing()}, now=100.0)

    assert found is not None
    assert found.aim == 0.0
    assert found.release_at > 100.0
    assert found.clearance > 30


def test_a_defender_parked_in_the_way_gives_nothing():
    """One attempt per level means no shot at all beats a bad one."""
    geom = from_config(_config())
    motion = _Motion(lambda t: [1200.0])       # exactly where the puck goes

    assert plan(geom, motion, {0.0: _timing()}, now=100.0) is None


def test_the_shot_is_timed_for_the_gap_not_for_now():
    """The defender is in the way at first and clears later; the plan has to
    be the later moment, because the puck takes a second to arrive."""
    geom = from_config(_config())

    def where(t):
        # Sits on the puck's line until t=103, then steps well aside.
        return [1200.0] if t < 103.0 else [1000.0]

    found = plan(geom, motion := _Motion(where), {0.0: _timing()}, now=100.0)

    assert found is not None
    # Released so that the crossing (0.5..0.7s later) lands after the move.
    assert found.release_at + 0.5 >= 103.0
    assert motion is not None


def test_the_middle_of_the_widest_window_is_chosen():
    """Not the first safe instant: window width is the direct measure of
    what a mistimed release costs."""
    geom = from_config(_config())

    def where(t):
        # A narrow gap early, a wide one later.
        if 101.0 <= t <= 101.1:
            return [1000.0]
        if 102.0 <= t <= 103.0:
            return [1000.0]
        return [1200.0]

    found = plan(geom, _Motion(where), {0.0: _timing()}, now=100.0)

    assert found is not None
    crossing = found.release_at + 0.6          # middle of the crossing
    assert 102.0 <= crossing <= 103.0
    assert found.window > 0.5


def test_a_row_with_no_prediction_stops_the_plan():
    """Skipping it would plan the shot as though that row were empty."""
    geom = from_config(_config())

    assert plan(geom, _Motion(lambda t: None), {0.0: _timing()},
                now=100.0) is None


def test_a_window_too_narrow_to_aim_at_is_refused():
    geom = from_config(_config())

    def where(t):
        return [1000.0] if 101.40 <= t <= 101.44 else [1200.0]

    assert plan(geom, _Motion(where), {0.0: _timing()}, now=100.0) is None


def test_the_clearance_counts_the_whole_defender_not_its_helmet():
    """A 60px defender plus a 10px puck needs 40px between centres before
    anything is spare at all."""
    geom = from_config(_config())
    motion = _Motion(lambda t: [1200.0 + 45.0])     # 45px away

    found = plan(geom, motion, {0.0: _timing()}, now=100.0,
                 min_clearance=1.0)

    assert found is not None
    assert abs(found.clearance - (45 - 30 - 10)) < 0.01


def test_the_aim_that_goes_round_the_defender_is_chosen():
    geom = from_config(_config())
    motion = _Motion(lambda t: [1200.0])          # parked on the centre line
    timings = {0.0: _timing(x_enter=1200.0, x_exit=1200.0),
               0.5: _timing(x_enter=1330.0, x_exit=1330.0)}

    found = plan(geom, motion, timings, now=100.0)

    assert found is not None
    assert found.aim == 0.5


def test_an_occupied_row_with_no_measured_timing_stops_the_plan():
    """Walking past a row because there is no number for it plans the shot
    as though nobody were standing in it. Measured live on 2026-08-10: the
    right-post aim had timings for three rows out of five."""
    cfg = _config()
    cfg.lanes = list(cfg.lanes) + [{"y": 750, "height": 60, "body_w": 60,
                                    "body_h": 100, "body_dy": -60}]
    geom = from_config(cfg)
    motion = _Motion(lambda t: [1000.0])       # somebody is in every row

    # Only row 0 was ever measured; row 1 exists and holds a defender.
    assert plan(geom, motion, {0.0: _timing()}, now=100.0) is None


def test_an_empty_row_with_no_timing_is_harmless():
    cfg = _config()
    cfg.lanes = list(cfg.lanes) + [{"y": 750, "height": 60, "body_w": 60,
                                    "body_h": 100, "body_dy": -60}]
    geom = from_config(cfg)

    class _Empty:
        def positions(self, row, _t):
            return [1000.0] if row == 0 else []

    assert plan(geom, _Empty(), {0.0: _timing()}, now=100.0) is not None


def test_a_refusal_names_the_row_and_the_shortfall():
    """"No window" alone cannot be argued with, and therefore cannot be
    acted on: a defender genuinely blocking every line looks exactly like an
    outline marked wider than the defender really is (2026-08-10)."""
    geom = from_config(_config())
    motion = _Motion(lambda t: [1200.0])           # parked on the puck's line

    verdicts = why_not(geom, motion, {0.0: _timing()}, now=100.0)

    assert len(verdicts) == 1
    assert verdicts[0].aim == 0.0
    assert verdicts[0].tightest_row == 0
    # Dead centre on a 60px defender plus a 10px puck: 40px short of touching.
    assert abs(verdicts[0].best_clearance + 40) < 1


def test_a_refusal_reports_the_best_the_aim_ever_managed():
    geom = from_config(_config())

    def where(t):
        return [1265.0] if t > 101.0 else [1200.0]      # steps 65px aside

    verdicts = why_not(geom, _Motion(where), {0.0: _timing()}, now=100.0)

    assert abs(verdicts[0].best_clearance - (65 - 40)) < 1


def test_room_enough_but_never_for_long_is_reported_as_such():
    """Two ways to fail, and they call for opposite responses. Reporting
    only the shortfall turned this one into "не хватает -1 px"
    (2026-08-10), which says nothing at all."""
    geom = from_config(_config())

    def where(t):
        # Clear of the line for 260ms, blocking otherwise. The crossing
        # itself takes 200ms, so only a 60ms band of release moments has
        # the whole of it inside the gap.
        return [1000.0] if 101.50 <= t <= 101.76 else [1200.0]

    verdicts = why_not(geom, _Motion(where), {0.0: _timing()}, now=100.0)

    assert verdicts[0].best_clearance > 30      # there was room...
    assert 0 < verdicts[0].best_window < 0.12   # ...but never for long


def test_a_row_that_cannot_be_judged_is_named_too():
    cfg = _config()
    cfg.lanes = list(cfg.lanes) + [{"y": 750, "height": 60, "body_w": 60,
                                    "body_h": 100, "body_dy": -60}]
    geom = from_config(cfg)

    verdicts = why_not(geom, _Motion(lambda t: [1000.0]), {0.0: _timing()},
                       now=100.0)

    assert verdicts[0].best_clearance is None
    assert verdicts[0].tightest_row == 1


def test_the_forced_shot_takes_the_least_bad_aim():
    """The one whose shortfall is smallest — 8px was judged inside the
    calibration's own error and worth spending an attempt on
    (2026-08-10)."""
    geom = from_config(_config())
    motion = _Motion(lambda t: [1200.0])           # parked on the centre line
    timings = {0.0: _timing(x_enter=1200.0, x_exit=1200.0),   # dead on it
               0.5: _timing(x_enter=1265.0, x_exit=1265.0)}   # 65px aside

    found = best_effort(geom, motion, timings, now=100.0)

    assert found is not None
    assert found.aim == 0.5
    assert abs(found.clearance - (65 - 40)) < 1


def test_the_forced_shot_still_picks_its_moment():
    geom = from_config(_config())

    def where(t):
        return [1000.0] if 102.0 <= t <= 102.2 else [1200.0]

    found = best_effort(geom, _Motion(where), {0.0: _timing()}, now=100.0)

    assert found is not None
    # The crossing runs 0.5..0.7s after release, so it has to land in the gap.
    assert 102.0 <= found.release_at + 0.5
    assert found.release_at + 0.7 <= 102.2 + 0.01


def test_the_forced_shot_refuses_what_it_cannot_judge():
    """Thresholds are what it ignores; a row it cannot see is not one."""
    cfg = _config()
    cfg.lanes = list(cfg.lanes) + [{"y": 750, "height": 60, "body_w": 60,
                                    "body_h": 100, "body_dy": -60}]
    geom = from_config(cfg)

    assert best_effort(geom, _Motion(lambda t: [1000.0]), {0.0: _timing()},
                       now=100.0) is None


def test_an_unmeasured_aim_is_never_planned():
    """A right-post shot was measured swinging 88px the *other* way before
    hooking back (2026-08-10), so nothing between two measured aims can be
    guessed at."""
    geom = from_config(_config())

    assert plan(geom, _Motion(lambda t: [1000.0]), {}, now=100.0) is None


def test_a_crossing_is_checked_all_the_way_through():
    """The puck is inside a row's band for a stretch, and the defender moves
    the whole time — one look at the middle would miss them meeting."""
    geom = from_config(_config())

    def where(t):
        # Clear at the start and end of the crossing, right in the way in
        # the middle of it.
        return [1200.0] if 100.85 <= t <= 100.95 else [1000.0]

    found = plan(geom, _Motion(where), {0.0: _timing(t_enter=0.5, t_exit=1.5)},
                 now=100.0)

    # Every release whose crossing spans that instant has to be rejected.
    assert found is None or not (found.release_at + 0.5
                                 <= 100.90 <= found.release_at + 1.5)
