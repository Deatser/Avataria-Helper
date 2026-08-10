# tests/modules/test_hockey_motion.py
"""Predicting where a defender will be, and knowing when not to trust it."""
import math

from modules.hockey.motion import RowMotion

_TICK = 0.03      # the scan cadence the real loop runs at
_HORIZON = 1.5    # what a shot takes to fly


def _triangle(t: float, period: float, low: float, high: float) -> float:
    """A patrol bouncing between two boards at constant speed."""
    span = high - low
    offset = (t % period) / period * 2 * span
    return low + (offset if offset <= span else 2 * span - offset)


def _sine(t: float, period: float, low: float, high: float) -> float:
    """The same patrol easing into its turns instead of snapping round."""
    mid, half = (low + high) / 2, (high - low) / 2
    return mid + half * math.sin(2 * math.pi * t / period)


def _run(shape, seconds: float, horizon: float = _HORIZON, standers=(),
         **kwargs):
    """One row watched for `seconds`: a defender following `shape`, plus any
    number of others parked at fixed positions."""
    row = RowMotion(horizon)
    t = 0.0
    while t < seconds:
        row.feed(t, [shape(t, **kwargs), *standers])
        t += _TICK
    return row


# ── Finding the period ───────────────────────────────────────────────────

def test_a_triangle_patrol_gives_up_its_period():
    row = _run(_triangle, seconds=20, period=4.0, low=1000, high=1400)

    report = row.report(0)
    assert abs(report.period - 4.0) < 0.1
    assert report.confidence > 0.95


def test_a_patrol_that_eases_into_its_turns_works_the_same():
    """Nothing here assumes a shape — the point of reading history back
    instead of extrapolating a velocity."""
    row = _run(_sine, seconds=20, period=3.0, low=1000, high=1400)

    report = row.report(0)
    assert abs(report.period - 3.0) < 0.1
    assert report.confidence > 0.95


def test_too_little_history_gives_no_period_rather_than_a_guess():
    row = _run(_triangle, seconds=1.5, period=4.0, low=1000, high=1400)

    assert row.report(0).period is None or row.report(0).confidence < 0.85


# ── Predicting ───────────────────────────────────────────────────────────

def test_a_prediction_a_whole_shot_ahead_lands_on_the_defender():
    period, low, high = 4.0, 1000.0, 1400.0
    row = _run(_triangle, seconds=20, period=period, low=low, high=high)

    now = 20 - _TICK
    predicted = row.predict(now + _HORIZON)

    assert predicted is not None
    assert abs(predicted - _triangle(now + _HORIZON, period, low, high)) < 6


def test_the_prediction_holds_across_a_turn_at_the_boards():
    """Where straight-line extrapolation is most wrong: the defender
    reverses inside the horizon, so a velocity carried forward would put it
    outside the rink entirely."""
    period, low, high = 4.0, 1000.0, 1400.0
    row = _run(_triangle, seconds=19.0, period=period, low=low, high=high)

    now = 19.0 - _TICK
    # Mid-sweep now, past the board and coming back a shot's flight later.
    predicted = row.predict(now + _HORIZON)

    assert predicted is not None
    assert abs(predicted - _triangle(now + _HORIZON, period, low, high)) < 6
    assert low - 1 <= predicted <= high + 1


def test_a_defender_standing_still_needs_no_period():
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 8:
        row.feed(t, [1200.0])
        t += _TICK

    report = row.report(0)
    assert report.has_mover is False
    assert [round(x) for x in report.standing] == [1200]
    assert row.positions(t + _HORIZON) == report.standing
    assert row.ready() is True


def test_nothing_observed_predicts_nothing():
    assert RowMotion(_HORIZON).predict(5.0) is None


def test_an_empty_row_reports_itself_empty():
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 8:
        row.feed(t, [])
        t += _TICK

    assert row.report(0).empty is True
    assert row.positions(t + _HORIZON) == []


def test_an_unfitted_patrol_predicts_nothing_rather_than_extrapolating():
    """The one rule the game's single attempt per level forces: no honest
    answer means no answer, not a plausible-looking one."""
    row = _run(_triangle, seconds=1.0, period=4.0, low=1000, high=1400)

    assert row.predict(1.0 + _HORIZON) is None


# ── Knowing whether to believe it ────────────────────────────────────────

def test_a_patrol_it_has_learnt_reports_a_small_error():
    row = _run(_triangle, seconds=25, period=4.0, low=1000, high=1400)

    report = row.report(0)
    assert report.checks >= 15
    assert report.error < 12
    assert report.ready is True


def test_a_row_is_not_ready_before_it_has_checked_itself():
    row = _run(_triangle, seconds=3, period=4.0, low=1000, high=1400)

    assert row.ready() is False


def test_a_row_that_has_proved_itself_stays_proved():
    """Latching, and deliberately. A defender's speed is a property of the
    level, so a row reading green, then amber, then red, then green again is
    the measurement wobbling with what the camera can see — not the rink.
    Re-deciding every five seconds meant a row could be lost right after the
    model announced it had converged, and no shot was ever available on all
    five rows at the same moment (2026-08-10)."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 20:                       # settle on a 4s patrol
        row.feed(t, [_triangle(t, 4.0, 1000, 1400)])
        t += _TICK
    assert row.ready()

    while t < 23:                       # then something else entirely
        row.feed(t, [_triangle(t, 2.0, 1000, 1400)])
        t += _TICK

    assert row.ready() is True


def test_and_only_a_new_level_takes_that_back():
    """The one thing that really does change the defenders."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 20:
        row.feed(t, [_triangle(t, 4.0, 1000, 1400)])
        t += _TICK
    assert row.ready()

    row.relearn()

    assert row.ready() is False
    assert row.report(0).occupants == 0


def test_and_earns_that_trust_back_once_it_has_relearnt():
    """Distrust is meant to be temporary. A row that stayed refused after
    the patrol settled into its new speed would be as wrong as one that
    never noticed."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 20:
        row.feed(t, [_triangle(t, 4.0, 1000, 1400)])
        t += _TICK
    while t < 40:
        row.feed(t, [_triangle(t, 2.0, 1000, 1400)])
        t += _TICK

    report = row.report(0)
    # The buffer still holds a stretch of the old patrol, so the lag that
    # fits all of it is 4s — which is two of the new cycles and predicts the
    # new one exactly. Any whole multiple is a correct answer here, and the
    # fit lands a hair under one as readily as a hair over.
    off_multiple = report.period % 2.0
    assert min(off_multiple, 2.0 - off_multiple) < 0.1
    assert report.ready is True
    assert report.error < 12


def test_a_hole_in_the_record_is_not_interpolated_across():
    """A row seen in three frames out of five leaves gaps a defender can
    turn round inside, and a straight line drawn over one cuts the corner
    off. Rows at 60% and 73% detection threw errors of 74 and 136 pixels
    against a 450px patrol while a row at 98% held to 5 (2026-08-10)."""
    row = RowMotion(_HORIZON)
    period, low, high = 3.0, 1000.0, 1400.0
    t = 0.0
    while t < 20:
        # A long blind spell right where the patrol reverses.
        blind = 1.4 <= (t % period) <= 1.9
        row.feed(t, [] if blind else [_triangle(t, period, low, high)])
        t += _TICK

    # Whatever it answers, it must not answer with the corner cut off.
    for ahead in [x * 0.05 for x in range(1, 40)]:
        guess = row.predict(t + ahead)
        if guess is not None:
            assert low - 5 <= guess <= high + 5


def test_a_small_gap_is_still_bridged():
    """Refusing every gap would refuse every row: a frame or two goes
    missing constantly."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 20:
        value = _triangle(t, 4.0, 1000, 1400)
        row.feed(t, [] if int(t / _TICK) % 6 == 0 else [value])
        t += _TICK

    assert row.ready() is True
    assert row.predict(t + _HORIZON) is not None


def test_a_missed_detection_is_a_gap_not_a_position():
    """An occluded helmet must not be recorded as a defender at zero."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 20:
        value = _triangle(t, 4.0, 1000, 1400)
        row.feed(t, [] if int(t / _TICK) % 7 == 0 else [value])
        t += _TICK

    report = row.report(0)
    assert 0.8 < report.seen_share < 0.9
    assert abs(report.period - 4.0) < 0.1
    assert report.ready is True


# ── Several defenders in one row ─────────────────────────────────────────
# A row can hold more than one, and at most one of them ever moves
# (2026-08-09). Telling them apart is by occupancy over time, not by
# tracking anybody across frames: the mover walks straight through the
# standers, and the two merge whenever they overlap.

def test_a_stander_is_told_from_the_mover():
    row = _run(_triangle, seconds=20, standers=(1250.0,),
               period=4.0, low=1000, high=1200)

    report = row.report(0)
    assert [round(x) for x in report.standing] == [1250]
    assert report.occupants == 2
    assert abs(report.period - 4.0) < 0.1


def test_the_mover_is_still_predicted_with_company():
    period, low, high = 4.0, 1000.0, 1200.0
    row = _run(_triangle, seconds=25, standers=(1250.0,),
               period=period, low=low, high=high)

    now = 25 - _TICK
    predicted = row.predict(now + _HORIZON)

    assert predicted is not None
    assert abs(predicted - _triangle(now + _HORIZON, period, low, high)) < 8
    assert row.report(0).ready is True


def test_everyone_in_the_row_is_predicted_not_just_the_mover():
    """A shot has to clear the standers too."""
    row = _run(_triangle, seconds=25, standers=(1250.0, 1400.0),
               period=4.0, low=1000, high=1200)

    places = row.positions(25 + _HORIZON)

    assert places is not None
    assert len(places) == 3
    assert any(abs(x - 1250) < 6 for x in places)
    assert any(abs(x - 1400) < 6 for x in places)


def test_a_stray_blob_does_not_become_a_patrol():
    """Anything not parked on a known stander becomes the mover, and a
    neighbouring row's helmet bobbing over the boundary is exactly that. It
    fits no patrol, so the row makes no predictions, gathers no checks and
    hangs at "не готов" for ever (2026-08-10)."""
    row = RowMotion(_HORIZON)
    t, step = 0.0, 0
    while t < 15:
        seen = [1409.0]                     # the real, motionless defender
        if step % 9 == 0:                   # a flicker every ninth frame
            seen.append(1120.0 + (step % 200))
        row.feed(t, seen)
        t += _TICK
        step += 1

    report = row.report(0)
    assert report.has_mover is False
    assert [round(x) for x in report.standing] == [1409]
    assert report.ready is True             # nothing left to predict


def test_a_badly_occluded_mover_still_counts():
    """The worst real detection rate measured was 60%, so the bar has to sit
    well below it."""
    row = RowMotion(_HORIZON)
    t, step = 0.0, 0
    while t < 20:
        value = _triangle(t, 4.0, 1000, 1400)
        row.feed(t, [] if step % 5 < 2 else [value])   # seen 60% of frames
        t += _TICK
        step += 1

    assert row.report(0).has_mover is True


def test_two_defenders_patrolling_one_row_are_predicted_together():
    """Measured live on 2026-08-10: one slow, one fast, crossing and hiding
    each other. There is no saying which detection is "the mover", and a
    series built by picking one each frame is a splice of two patrols with
    no period at all — that row never converged."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 25:
        row.feed(t, [_triangle(t, 4.0, 1000, 1250),
                     _triangle(t, 2.0, 1300, 1550)])
        t += _TICK

    report = row.report(0)
    assert report.ready is True
    assert report.error < 12

    now = 25 - _TICK
    places = row.positions(now + _HORIZON)
    assert places is not None and len(places) == 2
    expected = sorted([_triangle(now + _HORIZON, 4.0, 1000, 1250),
                       _triangle(now + _HORIZON, 2.0, 1300, 1550)])
    for guess, real in zip(sorted(places), expected):
        assert abs(guess - real) < 12


def test_two_patrols_that_never_line_up_are_judged_by_the_measured_error():
    """Their picture only repeats at a common multiple far outside any
    buffer, so the best lag matches one of them and scores about what that
    one is worth — 77% on the real rink. Refusing on that number threw away
    a row whose predictions might have been fine; the measured error is a
    fact about the future rather than a heuristic about the past, so it
    decides (2026-08-10)."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 25:
        row.feed(t, [_triangle(t, 4.0, 1000, 1250),
                     _triangle(t, 2.9, 1300, 1550)])
        t += _TICK

    report = row.report(0)
    # Whatever it decides, it must agree with itself: ready only if the
    # predictions really did land, every one of them.
    assert report.ready is (report.error is not None and report.error < 12
                            and report.worst is not None
                            and report.worst < 30
                            and report.checks >= 15)


def test_one_wild_miss_is_enough_to_withhold_a_row():
    """With the confidence gate loosened, a wrong period can still average
    well by luck for a stretch. One prediction landing a third of a patrol
    away is proof it is wrong, however good the mean looks."""
    row = RowMotion(_HORIZON)
    t = 0.0
    # Never asked whether it is ready, so it never latches — this is about
    # the gate a row has to pass before it may latch at all.
    while t < 20:                       # a clean patrol...
        row.feed(t, [_triangle(t, 4.0, 1000, 1400)])
        t += _TICK
    while t < 21.6:                     # ...then one cycle of something else
        row.feed(t, [_triangle(t, 1.3, 1000, 1400)])
        t += _TICK

    report = row.report(0)
    assert report.worst > 30
    assert report.ready is False


def test_a_row_where_everybody_stands_is_ready_at_once():
    """Nothing left to predict, so nothing left to prove."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 8:
        row.feed(t, [1100.0, 1300.0])
        t += _TICK

    report = row.report(0)
    assert report.has_mover is False
    assert len(report.standing) == 2
    assert report.ready is True


def test_a_mover_passing_a_stander_does_not_become_one():
    """The moment they overlap there is one detection, not two, and it sits
    on the stander — the mover must not be redefined as parked there."""
    row = _run(_triangle, seconds=25, standers=(1200.0,),
               period=4.0, low=1000, high=1400)

    report = row.report(0)
    assert [round(x) for x in report.standing] == [1200]
    assert abs(report.period - 4.0) < 0.2
    assert report.min_x < 1050 and report.max_x > 1350


def test_the_report_measures_the_patrol_it_watched():
    row = _run(_triangle, seconds=20, period=4.0, low=1000, high=1400)

    report = row.report(0)
    assert abs(report.min_x - 1000) < 6
    assert abs(report.max_x - 1400) < 6
    # Out and back in one period: 800px in 4s.
    assert abs(report.speed - 200) < 15


# ── Two defenders in one row ─────────────────────────────────────────────

def _two_movers(seconds: float, merge_px: float = 0.0, horizon=_HORIZON):
    """Row 3 of the real game: a medium patrol and a fast one sharing the
    ice. With `merge_px` the detector returns a single blob whenever they
    come that close, which is what actually happens on screen."""
    row = RowMotion(horizon)
    t = 0.0
    while t < seconds:
        xs = sorted([_triangle(t, 4.0, 1000, 1400),
                     _triangle(t, 1.3, 1020, 1380)])
        if merge_px and xs[1] - xs[0] < merge_px:
            xs = [(xs[0] + xs[1]) / 2]
        row.feed(t, xs)
        t += _TICK
    return row


def test_two_patrols_in_one_row_are_followed_apart():
    """Two incommensurate periods have no combined period, so the row as a
    whole cannot be fitted — only each defender separately."""
    row = _two_movers(seconds=20)

    report = row.report(2)
    assert report.occupants == 2
    assert report.error < 12
    assert report.ready is True


def test_and_survive_covering_each_other():
    """They cross constantly, and each crossing costs one of them: the
    detector suppresses the overlap and returns one helmet where there are
    two. Handing that blob to whichever track is nearer dragged it half a
    helmet off its patrol and gave it back to the wrong defender on the way
    out — six tracks and a 260px error, before the merge was recognised for
    what it is."""
    row = _two_movers(seconds=20, merge_px=55)

    report = row.report(2)
    assert report.occupants == 2
    assert report.error < 12
    assert report.worst < 30
    assert report.ready is True


def test_red_that_only_flashes_through_leaves_the_row_clear():
    """The puck crossing an empty row, the flash of a goal. Judging
    occupancy by the most ever seen in the last two seconds left rows the
    puck had flown through occupied for the rest of the session, refusing
    every shot from then on (2026-08-10)."""
    row = RowMotion(_HORIZON)
    t = 0.0
    step = 0
    while t < 12:
        # Red for three ticks out of every forty, and gone in between.
        flash = step % 40 < 3
        row.feed(t, [1200.0] if flash else [])
        t += _TICK
        step += 1

    report = row.report(0)
    assert report.occupants == 0
    assert report.empty is True
    assert row.positions(12.0 + _HORIZON) == []


def test_but_somebody_always_there_and_never_followed_is_refused():
    """A defender in nearly every frame whose track keeps dying. Reading
    that as "nothing moves, so nothing to predict" is how the model
    announced it had converged with two rows never modelled at all
    (2026-08-10)."""
    row = RowMotion(_HORIZON)
    t = 0.0
    step = 0
    while t < 12:
        # Somewhere different every time, so no track can accumulate.
        row.feed(t, [1000.0 + (step * 337) % 400])
        t += _TICK
        step += 1

    report = row.report(0)
    assert report.occupants == 1
    assert report.tracked == 0
    assert report.ready is False
    assert row.positions(12.0 + _HORIZON) is None


# ── Keeping the best verdict a row has earned ────────────────────────────

def _watch(row: RowMotion, start: float, seconds: float, period=4.0,
           low=1000.0, high=1400.0, blind_near_boards=False):
    """Feed `row` for a stretch. With `blind_near_boards` the defender is
    hidden wherever the patrol reverses — the same patrol, seen worse, which
    is what a neighbouring defender standing in the way actually costs."""
    t = start
    while t < start + seconds:
        x = _triangle(t, period, low, high)
        hidden = blind_near_boards and (x - low < 70 or high - x < 70)
        row.feed(t, [] if hidden else [x])
        t += _TICK
    return t


def test_a_row_keeps_the_best_reading_it_has_earned():
    """The patrol's speed is a property of the level. Once a row has shown
    it can be predicted to a few pixels, a stretch of bad visibility is a
    fact about the camera and not about the model — and rows taking turns
    being trustworthy meant the model never declared itself ready with all
    of them at once."""
    row = RowMotion(_HORIZON)
    t = _watch(row, 0.0, 25.0)
    best = row.report(0).error
    assert row.report(0).ready is True

    # The same patrol at the same speed, now hidden at both turns.
    _watch(row, t, 20.0, blind_near_boards=True)

    report = row.report(0)
    assert report.error <= best               # the verdict never goes back up
    assert report.ready is True


def test_a_row_never_asked_still_drops_a_verdict_that_stops_fitting():
    """Before anything latches, the remembered best belongs to the fit that
    earned it: a 3px average carried across a change of patrol would be the
    model vouching for a prediction it no longer makes. The period cannot
    raise the alarm on its own — a 30s buffer still reads 4s long after the
    level handed the row something faster — so the misses have to."""
    row = RowMotion(_HORIZON)
    t = _watch(row, 0.0, 25.0)          # never asked, so never latched

    _watch(row, t, 6.0, period=1.5)

    assert row.ready() is False


def test_and_improves_on_it_when_it_does_better():
    """Only downwards. A row that has settled further should say so."""
    row = RowMotion(_HORIZON)
    t = _watch(row, 0.0, 20.0, blind_near_boards=True)
    rough = row.report(0).error

    _watch(row, t, 25.0)

    assert row.report(0).error < rough


# ── A patrol crossing somebody who stands still ──────────────────────────

def test_a_patrol_passing_a_stander_takes_neither_of_them_out():
    """They meet constantly and the detector returns one helmet where there
    are two. Blinding the pair through the overlap starved the stander as
    well as the patrol, and the row could empty itself entirely — at exactly
    the spot a shot most needs to know better (2026-08-10)."""
    row = RowMotion(_HORIZON)
    period, low, high = 4.0, 1000.0, 1400.0
    stander = 1200.0
    t = 0.0
    merges = 0
    while t < 25:
        moving = _triangle(t, period, low, high)
        if abs(moving - stander) < 55:      # one blob, between the two
            row.feed(t, [(moving + stander) / 2])
            merges += 1
        else:
            row.feed(t, sorted([moving, stander]))
        t += _TICK

    assert merges > 40, "the two were meant to keep meeting"
    report = row.report(0)
    assert report.occupants == 2
    assert [round(x) for x in report.standing] == [1200]
    assert abs(report.period - period) < 0.2
    assert report.ready is True


def test_a_stander_outlives_being_hidden_far_longer_than_a_patrol():
    """There is nothing to lose track of: he is where he has always been.
    Only a level change should take him off the board."""
    row = RowMotion(_HORIZON)
    t = 0.0
    while t < 8:                        # long enough to be taken on trust
        row.feed(t, [1200.0])
        t += _TICK
    assert row.report(0).occupants == 1

    while t < 11:                       # three seconds behind somebody
        row.feed(t, [])
        t += _TICK

    assert [round(x) for x in row.report(0).standing] == [1200]
    assert row.positions(t + _HORIZON) == [1200.0]
