# modules/hockey/rink_area.py
"""The rink's own geometry.

Every number here lives in `config.json` under `hockey` and is written there
by the module's own "Область экрана" button, so this file does not define
any coordinates of its own — it only shapes the configured ones into
something the detector and the planner can ask questions of.

The one conversion worth naming: a frame is grabbed once per tick for the
whole rink (`Geometry.region`), and everything downstream works in *frame*
coordinates inside that grab, while the config, the overlays and the mouse
all work in absolute *screen* coordinates. `lane_rows` and `to_screen_x`
are the two crossings between those, and they are the only places the
offset appears.
"""
from __future__ import annotations

from dataclasses import dataclass

# The shallowest strip of ice a defender is taken to occupy, whatever its
# outline says — see Geometry.block_band.
_MIN_BLOCK_DEPTH = 20


@dataclass(frozen=True)
class Lane:
    """One row a defender patrols.

    `y` is the centre of the strip that gets searched for a helmet, in screen
    coordinates; `height` is how tall that strip is.

    `wall_left`/`wall_right` are this row's *own* turning points, marked at
    the defender's **edges**: wall_left is how far left its left side gets,
    wall_right how far right its right side gets (2026-08-09). Detection
    reports the centre of a helmet, so the two are half a body apart — see
    Geometry.centre_bounds, which is what anything comparing a marked wall
    to a measured position must use.

    Each row turns round in its own place, so there is no single pair of
    boards for the rink: a shot planned against a shared pair would be
    planned against a patrol nobody actually runs.
    """
    index: int
    y: int
    height: int
    wall_left: int
    wall_right: int
    # The defender's whole outline. Detection finds a helmet, but a shot is
    # blocked by the body under it (2026-08-09), so the two are separate
    # measurements: the strip above is where to *look*, this is what to
    # *dodge*. body_dy is the outline's top relative to this row's own line,
    # which is the helmet line, so the outline can be hung off a helmet
    # wherever one turns up.
    body_w: int = 0
    body_h: int = 0
    body_dy: int = 0

    @property
    def top(self) -> int:
        return self.y - self.height // 2

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def span(self) -> int:
        return self.wall_right - self.wall_left

    @property
    def has_body(self) -> bool:
        return self.body_w > 0 and self.body_h > 0


@dataclass(frozen=True)
class Geometry:
    left: int
    top: int
    width: int
    height: int
    lanes: tuple[Lane, ...]
    wall_left: int
    wall_right: int
    shooter_x: int
    shooter_y: int
    goal_y: int
    goal_left: int
    goal_right: int
    goalie_half_w: int
    puck_radius: int

    # ── The rink itself ──────────────────────────────────────────────────

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def region(self) -> dict:
        """The rectangle in the mss shape every grab call takes."""
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}

    @property
    def shooter(self) -> tuple[int, int]:
        return self.shooter_x, self.shooter_y

    @property
    def goal_centre(self) -> float:
        return (self.goal_left + self.goal_right) / 2

    # ── Screen ↔ frame ───────────────────────────────────────────────────

    def to_screen_x(self, frame_x: float) -> float:
        return self.left + frame_x

    def to_screen_y(self, frame_y: float) -> float:
        return self.top + frame_y

    def lane_rows(self, lane: Lane) -> tuple[int, int]:
        """The lane's strip as row indices inside a frame grabbed for
        `region`, clamped to that frame. An empty range (top >= bottom)
        means the lane sits outside the rink and cannot be searched — the
        detector skips it rather than slicing a zero-height array."""
        top    = max(0, lane.top - self.top)
        bottom = min(self.height, lane.bottom - self.top)
        return top, max(top, bottom)

    def half_width(self, lane: Lane) -> int:
        """How far a defender in this row reaches either side of its own
        centre. Half the marked outline where there is one; the configured
        fallback until then."""
        return lane.body_w // 2 if lane.has_body else self.goalie_half_w

    def body_box(self, lane: Lane, x: float, y: float) -> tuple[int, int, int, int]:
        """The rectangle a defender actually blocks with, given where its
        helmet was found — left, top, width, height, screen coordinates.

        Hung off the helmet rather than off the row's own line, because the
        helmet is the thing that was measured; the row line is only where it
        is expected. Falls back to a box the width of `goalie_half_w` and
        the height of the strip when the outline has never been marked, so
        an uncalibrated row still dodges something rather than nothing.
        """
        half = self.half_width(lane)
        if lane.has_body:
            height = lane.body_h
            top = int(round(y + lane.body_dy))
        else:
            height = lane.height
            top = int(round(y - height / 2))
        return int(round(x - half)), top, half * 2, height

    def block_band(self, lane: Lane) -> tuple[int, int]:
        """The depth of ice a defender in this row actually occupies.

        Not its drawn height. A sprite is a standing person seen at an
        angle: its outline runs from the skates up past the head, but the
        ice it stands on is only the strip under its feet. The puck slides
        along that ice and passes the defender when it reaches that strip,
        not when it first slides behind the drawn body.

        Taking the whole outline made the near rows block almost the entire
        flight — row 4's outline reached from 659 to 822 with the goal line
        at 665, so a puck was "inside" it from the moment it arrived until
        it scored, and its own curve carried it across every x in between
        (2026-08-10). Feet lines across five rows came out at 681, 721, 762,
        822 and 894: evenly spaced and in order, which drawn heights are
        not.
        """
        _left, top, _width, height = self.body_box(lane, lane.wall_left,
                                                   lane.y)
        feet = top + height
        depth = max(_MIN_BLOCK_DEPTH, height // 4)
        return feet - depth, feet + self.puck_radius

    def centre_bounds(self, lane: Lane) -> tuple[float, float]:
        """The leftmost and rightmost a defender's own centre reaches in
        this row.

        Not the marked boards themselves: those are where its left and right
        *edges* stop, so the centre turns half a body short of each. Every
        comparison between a marked wall and a detected position goes
        through here — the detector reports helmet centres, and mixing the
        two up is worth half a defender of silent error at both ends.
        """
        half = self.half_width(lane)
        return lane.wall_left + half, lane.wall_right - half

    def lane_region(self, lane: Lane) -> dict:
        """The lane's strip in screen coordinates — for the overlays."""
        top, bottom = self.lane_rows(lane)
        return {"left": self.left, "top": self.top + top,
                "width": self.width, "height": bottom - top}

    # ── Sanity ───────────────────────────────────────────────────────────

    def problems(self) -> list[str]:
        """Everything wrong with the current calibration, in Russian, ready
        to go straight into the log. Empty list means it is usable.

        Checked rather than assumed because every one of these numbers
        starts life as a guess off a reference screenshot, and a silently
        wrong one shows up much later as "бот почему-то мажет" rather than
        as an obvious mistake.
        """
        issues: list[str] = []
        if self.width <= 0 or self.height <= 0:
            issues.append("область катка пустая — откалибруйте «Каток»")
            return issues   # nothing else can be judged without it

        if not self.lanes:
            issues.append("ряды не заданы — нажмите «Найти ряды»")
        for lane in self.lanes:
            top, bottom = self.lane_rows(lane)
            if bottom - top < 10:
                issues.append(
                    f"ряд {lane.index + 1} (y={lane.y}) почти не попадает "
                    f"в каток")

        # Only a defender overlapping the puck-to-goal band can block a
        # shot. Judged by the *body*, not by the row's own line: that line
        # is the helmet, and a defender whose head is above the goal still
        # reaches into the band with everything below it (2026-08-09) —
        # comparing helmets flagged three perfectly good rows.
        outside = []
        for lane in self.lanes:
            _left, top, _width, height = self.body_box(lane, lane.wall_left,
                                                       lane.y)
            if top + height <= self.goal_y or top >= self.shooter_y:
                outside.append(lane.index + 1)
        if outside:
            issues.append(
                f"вратари рядов {', '.join(map(str, outside))} целиком вне "
                f"полосы «ворота (y={self.goal_y}) … шайба "
                f"(y={self.shooter_y})» — они не могут помешать броску, "
                f"проверьте «Ворота», «Шайба» и габарит этих рядов")

        # Strips overlapping is not a fault and is not reported. The
        # defenders themselves overlap — the one nearer the puck is drawn in
        # front of the one behind — so a strip tight enough to clear its
        # neighbours would clip the helmets it exists to hold. What used to
        # be the harm in it, one helmet counted as two defenders, is settled
        # in the detector instead: components are found once over the whole
        # band and each is assigned to a single row.

        for lane in self.lanes:
            # Boards are marked at the defender's edges, so a row narrower
            # than the defender itself leaves its centre nowhere to go.
            if lane.span < 2 * self.half_width(lane) + 20:
                issues.append(
                    f"борта ряда {lane.index + 1} заданы неверно "
                    f"({lane.wall_left}…{lane.wall_right}, ширина "
                    f"{lane.span}) — выберите этот ряд и откалибруйте «Борт»")
            if not lane.has_body:
                issues.append(
                    f"габарит вратаря ряда {lane.index + 1} не отмечен — "
                    f"пока считаю его шириной {2 * self.goalie_half_w} px; "
                    f"откалибруйте «Вратарь»")

        if self.wall_right - self.wall_left < 50:
            issues.append("запасные борта заданы неверно")
        if self.goal_right - self.goal_left < 20:
            issues.append("створ ворот пустой — откалибруйте «Ворота»")
        if not self.left <= self.shooter_x <= self.right:
            issues.append("точка броска вне катка — откалибруйте «Шайба»")
        if not self.top <= self.goal_y <= self.bottom:
            issues.append("линия ворот вне катка — откалибруйте «Ворота»")
        if self.goal_y >= self.shooter_y:
            issues.append("ворота не выше точки броска — проверьте калибровку")
        return issues


def from_config(cfg) -> Geometry:
    """Build the geometry from a HockeyConfig.

    Rows come out sorted top-to-bottom and renumbered from that order, so a
    lane's index always means "how far up the rink it is" no matter what
    order the calibration happened to discover them in — the planner reports
    rows by that number and it should not shuffle between runs.
    """
    raw = []
    for entry in getattr(cfg, "lanes", None) or []:
        try:
            y      = int(entry["y"])
            height = int(entry.get("height", cfg.lane_height))
            # A row that has never had its own boards marked borrows the
            # rink-wide pair, which is a seed rather than the truth — see
            # Lane. Better a stated fallback than a row with no walls at all.
            left   = int(entry.get("wall_left")  or cfg.wall_left)
            right  = int(entry.get("wall_right") or cfg.wall_right)
            body   = (int(entry.get("body_w")  or 0),
                      int(entry.get("body_h")  or 0),
                      int(entry.get("body_dy") or 0))
        except (KeyError, TypeError, ValueError):
            continue   # a hand-edited row that lost a field costs that row,
                      # not the whole geometry
        raw.append((y, max(1, height), left, right, body))

    raw.sort(key=lambda row: row[0])
    lanes = tuple(
        Lane(index=i, y=y, height=h, wall_left=left, wall_right=right,
             body_w=body[0], body_h=body[1], body_dy=body[2])
        for i, (y, h, left, right, body) in enumerate(raw))

    return Geometry(
        left=cfg.rink_left, top=cfg.rink_top,
        width=cfg.rink_width, height=cfg.rink_height,
        lanes=lanes,
        wall_left=cfg.wall_left, wall_right=cfg.wall_right,
        shooter_x=cfg.shooter_x, shooter_y=cfg.shooter_y,
        goal_y=cfg.goal_y, goal_left=cfg.goal_left, goal_right=cfg.goal_right,
        goalie_half_w=cfg.goalie_half_w, puck_radius=cfg.puck_radius,
    )
