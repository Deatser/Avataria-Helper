# tests/modules/test_hockey_rink.py
"""The rink's geometry — the config numbers turned into rows and regions."""
from modules.hockey.rink_area import from_config
from tests.modules.hockey_legacy_config import HockeyConfig


def _lane(y: int, height: int = 120, body_w: int = 90, body_h: int = 140,
          body_dy: int = -60) -> dict:
    """A fully marked row — strip, boards and the defender's own outline —
    so a test about one of them is not also a test about the others."""
    return {"y": y, "height": height, "body_w": body_w, "body_h": body_h,
            "body_dy": body_dy}


def _config(**overrides) -> HockeyConfig:
    cfg = HockeyConfig()
    cfg.rink_left, cfg.rink_top = 1000, 500
    cfg.rink_width, cfg.rink_height = 800, 600
    cfg.wall_left, cfg.wall_right = 1050, 1750
    cfg.shooter_x, cfg.shooter_y = 1400, 1050
    cfg.goal_y, cfg.goal_left, cfg.goal_right = 560, 1300, 1500
    cfg.lanes = [_lane(700)]
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_the_region_is_the_mss_shape_a_grab_takes():
    geom = from_config(_config())

    assert geom.region == {"left": 1000, "top": 500,
                           "width": 800, "height": 600}


def test_rows_come_out_top_to_bottom_however_they_were_written():
    geom = from_config(_config(lanes=[{"y": 900, "height": 120},
                                      {"y": 620, "height": 120},
                                      {"y": 760, "height": 120}]))

    assert [lane.y for lane in geom.lanes] == [620, 760, 900]
    assert [lane.index for lane in geom.lanes] == [0, 1, 2]


def test_a_row_becomes_frame_rows_relative_to_the_rink():
    geom = from_config(_config(lanes=[{"y": 700, "height": 120}]))

    # Strip 640..760 on screen, rink starts at y=500.
    assert geom.lane_rows(geom.lanes[0]) == (140, 260)


def test_a_row_hanging_off_the_rink_is_clamped_to_it():
    geom = from_config(_config(lanes=[{"y": 520, "height": 200}]))

    top, bottom = geom.lane_rows(geom.lanes[0])
    assert top == 0            # strip would start at 420, above the rink
    assert bottom == 120       # 620 on screen


def test_a_row_entirely_off_the_rink_gives_an_empty_range():
    geom = from_config(_config(lanes=[{"y": 200, "height": 60}]))

    top, bottom = geom.lane_rows(geom.lanes[0])
    assert bottom == top       # nothing to slice, and never negative


def test_a_row_missing_its_y_costs_that_row_not_the_geometry():
    geom = from_config(_config(lanes=[{"height": 120},
                                      {"y": 700, "height": 120}]))

    assert [lane.y for lane in geom.lanes] == [700]


def test_a_sound_calibration_has_no_complaints():
    assert from_config(_config()).problems() == []


def test_an_empty_rink_is_the_only_complaint_worth_making():
    problems = from_config(_config(rink_width=0)).problems()

    assert len(problems) == 1
    assert "каток" in problems[0].lower()


def test_missing_rows_are_reported():
    problems = from_config(_config(lanes=[])).problems()

    assert any("ряды не заданы" in p for p in problems)


def test_a_goal_line_below_the_shooter_is_reported():
    problems = from_config(_config(goal_y=1080)).problems()

    assert any("не выше точки броска" in p for p in problems)


def test_a_helmet_above_the_goal_line_is_not_a_complaint():
    """Measured live on 2026-08-09: rows whose helmets sit above the goal
    line while their bodies reach well below it. Judging by the row's own
    line — which is the helmet — flagged three perfectly good rows."""
    # Body hangs from 60px above the row line down to 80px below it, so it
    # crosses a goal line 20px above the helmet.
    lanes = [_lane(700, body_h=140, body_dy=-60)]

    assert from_config(_config(goal_y=680, lanes=lanes)).problems() == []


def test_a_defender_entirely_outside_the_band_is_reported():
    """Its whole outline, head to skates, sits behind the goal — it cannot
    touch a shot however it moves."""
    lanes = [_lane(500, body_h=100, body_dy=-50)]   # body 450..550

    problems = from_config(_config(goal_y=600, lanes=lanes)).problems()

    assert any("целиком вне полосы" in p for p in problems)


def test_the_puck_below_every_row_is_not_a_complaint():
    assert from_config(_config(lanes=[_lane(700, 60)])).problems() == []


def test_a_defender_blocks_the_ice_it_stands_on_not_its_own_height():
    """A sprite is a standing person seen at an angle: the puck slides past
    it when it reaches the skates, not when it first slides behind the drawn
    body. Taking the whole outline made near rows block almost a whole
    flight (2026-08-10)."""
    # Outline 640..780 — 140 tall, feet at 780.
    geom = from_config(_config(lanes=[_lane(700, body_h=140, body_dy=-60)]))

    top, bottom = geom.block_band(geom.lanes[0])

    assert bottom > 780 >= top          # the strip sits at the feet
    assert bottom - top < 60            # and is nowhere near 140 deep


def test_a_short_outline_still_gets_a_usable_depth():
    geom = from_config(_config(lanes=[_lane(700, body_h=40, body_dy=-20)]))

    top, bottom = geom.block_band(geom.lanes[0])

    assert bottom - top >= 20


def test_an_unmarked_defender_outline_is_reported():
    """Until it is marked the planner falls back to a guessed width, and a
    guessed width is a guessed clearance."""
    problems = from_config(_config(lanes=[{"y": 700, "height": 60}])).problems()

    assert any("габарит вратаря ряда 1" in p for p in problems)


def test_overlapping_strips_are_not_a_complaint():
    """The defenders overlap each other, so a strip tight enough to clear
    its neighbours would clip the helmets it exists to hold (2026-08-09).
    One helmet being claimed by two rows is settled in the detector, not by
    forbidding the shape."""
    assert from_config(_config(lanes=[_lane(700), _lane(752)])).problems() == []


def test_rows_that_merely_sit_close_are_fine():
    assert from_config(
        _config(lanes=[_lane(700, 50), _lane(752, 50)])).problems() == []


# ── Per-row boards ───────────────────────────────────────────────────────

def test_a_row_keeps_its_own_turning_points():
    """Each row's defender turns round at its own place, so there is no one
    pair of boards for the rink."""
    geom = from_config(_config(lanes=[
        {"y": 620, "height": 50, "wall_left": 1100, "wall_right": 1400},
        {"y": 700, "height": 50, "wall_left": 1200, "wall_right": 1700}]))

    assert [(lane.wall_left, lane.wall_right) for lane in geom.lanes] == [
        (1100, 1400), (1200, 1700)]
    assert geom.lanes[0].span == 300


def test_a_row_without_its_own_boards_borrows_the_rink_wide_pair():
    geom = from_config(_config(lanes=[{"y": 700, "height": 50}]))

    assert (geom.lanes[0].wall_left, geom.lanes[0].wall_right) == (1050, 1750)


def test_zero_boards_count_as_never_marked_not_as_a_row_pinned_to_zero():
    """_write_row seeds a new row with 0/0, which has to read as "not set"
    rather than as boards at the left edge of the screen."""
    geom = from_config(_config(lanes=[
        {"y": 700, "height": 50, "wall_left": 0, "wall_right": 0}]))

    assert (geom.lanes[0].wall_left, geom.lanes[0].wall_right) == (1050, 1750)


def test_a_row_with_collapsed_boards_is_reported():
    problems = from_config(_config(lanes=[
        {"y": 700, "height": 50, "wall_left": 1200, "wall_right": 1210}
    ])).problems()

    assert any("борта ряда 1" in p for p in problems)
