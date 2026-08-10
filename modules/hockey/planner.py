# modules/hockey/planner.py
"""When to let go, and which way to pull.

The bot gets one attempt per level, so this module's job is as much to
refuse as to choose. Everything it needs is measured rather than modelled:
where each row's defenders will be comes from motion.py, which proves itself
against reality before it is believed, and when the puck reaches each row
comes from trajectory.py, which followed a real puck there.

Two things about the search are deliberate.

*Only measured aims are considered.* A right-post shot was measured swinging
88px to the *left* before hooking right (2026-08-10), so the puck's path is
nowhere near a lateral offset of the pull, and a value between two measured
aims cannot be interpolated into a plausible arc. Three aims are what a
player uses anyway — middle and the two posts.

*The release moment is planned ahead, not decided now.* The gesture takes
time, the puck takes over a second more, and a defender crosses the rink in
that. What matters is where everyone will be when the puck arrives, and the
only way to have a choice about it is to search forward over the moments a
shot could be released.

The answer is the *middle of the widest safe window*, not the first safe
instant. Window width is the direct measure of how much a mistimed release
costs, and a plan with no room either side is one bad frame from a miss.
"""
from __future__ import annotations

from dataclasses import dataclass

# The band of release moments searched, and how finely.
#
# The caller sets where it starts — the window module keeps a shot at least
# a second away, and pays for however long planning itself takes — so the
# end has to leave a full cycle of the slowest patrol measured (3.6s) beyond
# that. A patrol repeats: a window that does not exist inside one cycle does
# not exist in the next either.
#
# It used to run to eight seconds and the search cost five seconds of wall
# clock — long enough that the plan was for a moment already past by the
# time it existed (2026-08-10).
_LOOKAHEAD_FROM = 0.10
_LOOKAHEAD_TO   = 6.00
_STEP           = 0.02

# Positions are asked for on a grid this fine and cached. The same instant
# comes up over and over — every release moment sampled crosses the same
# rows — and re-deriving it each time was most of the search's cost. 5ms is
# under 3px even for the fastest patrol measured.
_CACHE_S = 0.005

# How far clear of a defender the puck has to pass, on top of the
# defender's own half width and the puck's radius.
#
# Zero: the two widths already subtracted are the whole of the collision,
# so anything above zero is a puck that misses. It was 30px of padding for
# everything not modelled — the grid the crossings were measured on, the
# model's own error, the release — and that padding was refusing shots that
# would have gone in by twenty-odd pixels (2026-08-10). The margin is now
# whatever the outline marked with "Вратарь" carries: mark it a little wider
# than the torso and the room comes back, visibly and in one place.
_MIN_CLEARANCE_PX = 0.0

# A window narrower than this is not a plan, it is a coincidence.
_MIN_WINDOW_S = 0.12

# How many moments inside a row's crossing get checked. The puck is inside
# a row's band for a stretch, not an instant — for a defender reaching above
# the goal line, most of the flight — and the defender moves throughout, so
# one sample in the middle would miss the moment they actually meet.
_CROSSING_SAMPLES = 6


@dataclass
class Plan:
    aim: float
    release_at: float     # absolute, the same clock `now` came from
    clearance: float      # px to spare at the tightest moment
    window: float         # seconds the shot stays safe either side


def plan(geom, motion, timings: dict, now: float,
         min_clearance: float = _MIN_CLEARANCE_PX,
         min_window: float = _MIN_WINDOW_S,
         lead: float = _LOOKAHEAD_FROM) -> Plan | None:
    """The best shot available, or None when there is not one.

    `timings` maps a measured aim to its list of trajectory.RowTiming.
    `lead` is how soon a release could physically happen — the pull has to
    be made first, and a plan for a moment already past is no plan.

    None means *do not shoot* — never "shoot anyway, this was the least
    bad".
    """
    best: Plan | None = None
    motion = _Remembered(motion)
    for aim, rows in timings.items():
        if not rows:
            continue
        found = _best_window(geom, motion, rows, aim, now,
                             min_clearance, min_window, lead)
        if found is None:
            continue
        if best is None or (found.window, found.clearance) > (best.window,
                                                              best.clearance):
            best = found
    return best


class _Remembered:
    """A motion model that answers the same question only once.

    The search asks where a row's defenders will be at some thousands of
    moments, and the moments repeat: every release instant considered sends
    the puck through the same rows, so the absolute times pile up on top of
    each other. Reading the model afresh each time was most of what made a
    plan take five seconds to draw up (2026-08-10).
    """

    def __init__(self, motion):
        self._motion = motion
        self._seen: dict[tuple[int, int], list | None] = {}
        self._slack: dict[int, float] = {}

    def positions(self, index: int, t: float):
        key = (index, round(t / _CACHE_S))
        if key not in self._seen:
            self._seen[key] = self._motion.positions(index, t)
        return self._seen[key]

    def slack(self, index: int) -> float:
        if index not in self._slack:
            getter = getattr(self._motion, "slack", None)
            self._slack[index] = getter(index) if getter else 0.0
        return self._slack[index]


@dataclass
class Diagnosis:
    """Why one aim was refused.

    Two numbers because there are two ways to fail, and they call for
    opposite responses. Too little room means the shot never had a line;
    enough room but too brief a window means it had one and could not be
    trusted to hit it. Reporting only the first turned the second into
    "не хватает -1 px" (2026-08-10), which says nothing at all.
    """
    aim: float
    best_clearance: float | None    # None when a row could not be judged
    tightest_row: int | None
    best_window: float = 0.0        # widest stretch that cleared, seconds


def why_not(geom, motion, timings: dict, now: float,
            lead: float = _LOOKAHEAD_FROM,
            min_clearance: float = _MIN_CLEARANCE_PX) -> list[Diagnosis]:
    """What stopped each aim, for the log.

    A refusal that says only "no window" is unarguable and therefore
    useless: it cannot be told apart from a defender genuinely blocking
    every line, a row whose model is not ready, or a body outline marked
    wider than the defender really is. The number and the row name make it
    arguable (2026-08-10 — a level with one moving defender refused every
    aim, and the cause turned out to be a 134px outline that included the
    stick).
    """
    lanes = {lane.index: lane for lane in geom.lanes}
    steps = int((_LOOKAHEAD_TO - lead) / _STEP)
    motion = _Remembered(motion)
    found = []
    for aim, rows in timings.items():
        best, blamed, unjudged = None, None, None
        run, widest = 0, 0
        for i in range(steps + 1):
            release = now + lead + i * _STEP
            clearance, row = _tightest(geom, motion, lanes, rows, release)
            if clearance is None:
                # Keep the first row that could not be judged: "nothing to
                # check this against" is a different answer from "checked
                # and too tight", and the log has to be able to say which.
                unjudged = row if unjudged is None else unjudged
                run = 0
                continue
            if best is None or clearance > best:
                best, blamed = clearance, row
            run = run + 1 if clearance >= min_clearance else 0
            widest = max(widest, run)
        found.append(Diagnosis(aim=aim, best_clearance=best,
                               tightest_row=blamed if best is not None
                               else unjudged,
                               best_window=widest * _STEP))
    return sorted(found, key=lambda d: d.aim)


def best_effort(geom, motion, timings: dict, now: float,
                lead: float = _LOOKAHEAD_FROM) -> Plan | None:
    """The least-bad shot there is, whatever the thresholds say.

    Deliberately separate from `plan`, and never reached from it. `plan`
    refusing is the whole safety story of this module, and a fallback that
    quietly fired anyway would undo it — one attempt per level. This is for
    a person who has read the shortfall, decided eight pixels is within the
    error of their own calibration, and chosen to spend the attempt finding
    out.

    It picks the single moment of highest clearance rather than the middle
    of a window, because there is no window: what is being asked for is the
    best instant that exists.
    """
    per_aim = best_effort_each(geom, motion, timings, now, lead)
    return max(per_aim.values(), key=lambda plan: plan.clearance, default=None)


def best_effort_each(geom, motion, timings: dict, now: float,
                     lead: float = _LOOKAHEAD_FROM) -> dict:
    """The least-bad shot for every aim, in one sweep.

    Separate from best_effort because the caller wants both halves of the
    same answer: which line to take, and by how much each of the three
    misses. Running the search once for the choice and again for the
    numbers would cost the plan its own moment — the whole sweep is several
    hundred milliseconds.
    """
    lanes = {lane.index: lane for lane in geom.lanes}
    steps = int((_LOOKAHEAD_TO - lead) / _STEP)
    motion = _Remembered(motion)
    found = {}
    for aim, rows in timings.items():
        if not rows:
            continue
        best = None
        for i in range(steps + 1):
            release = now + lead + i * _STEP
            clearance, _row = _tightest(geom, motion, lanes, rows, release)
            if clearance is None:
                continue
            if best is None or clearance > best.clearance:
                best = Plan(aim=aim, release_at=release,
                            clearance=clearance, window=0.0)
        if best is not None:
            found[aim] = best
    return found


def _best_window(geom, motion, rows: list, aim: float, now: float,
                 min_clearance: float, min_window: float,
                 lead: float) -> Plan | None:
    """The middle of the widest stretch of release moments that all clear."""
    lanes = {lane.index: lane for lane in geom.lanes}
    steps = int((_LOOKAHEAD_TO - lead) / _STEP)

    best_run = None          # (length, start index, end index, worst clearance)
    run_start, run_worst = None, None
    for i in range(steps + 1):
        release = now + lead + i * _STEP
        clearance = _clearance(geom, motion, lanes, rows, release,
                               floor=min_clearance)
        if clearance is not None and clearance >= min_clearance:
            if run_start is None:
                run_start, run_worst = i, clearance
            else:
                run_worst = min(run_worst, clearance)
            continue
        if run_start is not None:
            run = (i - run_start, run_start, i - 1, run_worst)
            if best_run is None or run > best_run:
                best_run = run
            run_start, run_worst = None, None
    if run_start is not None:
        run = (steps + 1 - run_start, run_start, steps, run_worst)
        if best_run is None or run > best_run:
            best_run = run

    if best_run is None:
        return None
    length, start, end, worst = best_run
    width = length * _STEP
    if width < min_window:
        return None
    middle = now + lead + (start + end) / 2 * _STEP
    return Plan(aim=aim, release_at=middle, clearance=worst, window=width)


def _slack_of(motion, index: int) -> float:
    """This row's own worst measured miss, or nothing when the model does
    not keep one — the diagnostics call in with plain stand-ins."""
    getter = getattr(motion, "slack", None)
    return float(getter(index)) if getter else 0.0


def _clearance(geom, motion, lanes: dict, rows: list,
               release: float, floor: float | None = None) -> float | None:
    return _tightest(geom, motion, lanes, rows, release, floor)[0]


def _tightest(geom, motion, lanes: dict, rows: list, release: float,
              floor: float | None = None) -> tuple[float | None, int | None]:
    """How much room the tightest moment of this shot has, in pixels.

    `floor` stops the search the moment the answer is known to be below it.
    Most release instants are blocked by the first defender they meet, and
    finishing the sweep only to confirm it was the bulk of the work.

    None means *this shot cannot be judged*, and there are two ways to get
    there. A row may have no honest prediction of where its defenders will
    be; or the flight may never have been measured through that row at all.
    Both have to stop the plan rather than be skipped over — walking past a
    row because there is no number for it plans the shot as though nobody
    were standing in it, which is exactly the mistake this module exists to
    prevent.
    """
    by_row = {timing.row: timing for timing in rows}
    worst, blamed = None, None
    for lane in lanes.values():
        timing = by_row.get(lane.index)
        if timing is None:
            # Nothing measured here. Harmless only if nobody is in it.
            occupants = motion.positions(lane.index, release)
            if occupants:
                return None, lane.index
            continue

        # The defender's own half width, the puck's own radius, and how far
        # this row's model has been known to miss by. The first two are what
        # a collision is; the third is what the collision is being predicted
        # with, and leaving less room than that is planning inside the error
        # bar (2026-08-10 — 2px of clearance on a row whose worst miss that
        # minute was 40).
        margin = (geom.half_width(lane) + geom.puck_radius
                  + _slack_of(motion, lane.index))
        for k in range(_CROSSING_SAMPLES + 1):
            share = k / _CROSSING_SAMPLES
            moment = timing.t_enter + share * (timing.t_exit - timing.t_enter)
            puck_x = timing.x_enter + share * (timing.x_exit - timing.x_enter)
            places = motion.positions(lane.index, release + moment)
            if places is None:
                return None, lane.index
            for defender_x in places:
                room = abs(puck_x - defender_x) - margin
                if worst is None or room < worst:
                    worst, blamed = room, lane.index
                    if floor is not None and worst < floor:
                        return worst, blamed
    return worst, blamed
