# modules/gardener/trash.py
"""Finding litter in the garden.

Each kind of litter is one template, and any of them can be on screen more
than once, so every match matters rather than just the best one.
"""
from __future__ import annotations
from dataclasses import dataclass

import cv2

from app.core.capture import grab_screen_region
from app.core.template_match import (find_all, load_template,
                                     game_region)


# The default bar. Lower than the one the game's own buttons are held to,
# and deliberately so: a button is a fixed piece of chrome that renders
# identically every time, while these sit on grass, on paths and half behind
# scenery, and the same bush is never quite the same pixels twice.
MATCH_THRESHOLD = 0.72


@dataclass(frozen=True)
class TrashKind:
    key: str
    plural: str        # how it reads in the count line: "голубых кустов"
    singular: str      # how one of them is listed: "Голубой куст №1"
    # One kind, one or more pictures of it. A dry bush is drawn differently
    # depending on which one it is, and those are still dry bushes — merged
    # here rather than split into kinds nobody wants counted apart.
    filenames: tuple[str, ...]
    # What its dots and its coordinates are drawn in. One colour per kind,
    # not per find: only one group is ever highlighted at a time, so the
    # colour says which kind, and the dot being filled or hollow says
    # whether it cleared the bar.
    colour: str = "#a8bd4f"
    # Its own bar where the shared one does not fit. The blue bush was
    # matching clean bushes — ones that are not litter at all — at 81%, so it
    # was given a stricter one than a kind whose real finds sit at 76%. That
    # was measured against its previous picture; worth re-reading off the log
    # now that the picture has changed.
    threshold: float = MATCH_THRESHOLD


# Add a kind here and it is scanned, counted and reported with no other
# change — the log lines are built from this list.
TRASH_KINDS: list[TrashKind] = [
    TrashKind("dry_bush", "сухих кустов", "Сухой куст",
              ("gardener_-1.png", "gardener_0.png", "gardener_1.png",
               "gardener_2.png", "gardener_3.png"),
              colour="#c08a3e", threshold=0.65),
    TrashKind("blue_bush", "голубых кустов", "Голубой куст",
              ("gardener_4.png",), colour="#4cc9f0", threshold=0.85),
    TrashKind("yellow_bush", "жёлтых кустов", "Жёлтый куст",
              ("gardener_5.png", "gardener_51.png", "gardener_52.png"),
              colour="#ffe14d", threshold=0.80),
    TrashKind("pink_bush", "розовых кустов", "Розовый куст",
              ("gardener_6.png",), colour="#ff6ec7", threshold=0.65),
    TrashKind("beetle", "жуков", "Жук",
              ("gardener_7.png", "gardener_8.png",
               "gardener_71.png", "gardener_81.png"),   # mirrored the same way
              colour="#9944ff", threshold=0.65),
    TrashKind("butterfly", "бабочек", "Бабочка",
              ("gardener_9.png", "gardener_10.png",
               # Butterflies never sit still and never face the same way
               # twice — one picture each of both colours was never going to
               # match a live one turned even a little. Twelve copies of
               # each, rotated 30° apart, cover the whole circle instead.
               "gardener_91.png", "gardener_92.png", "gardener_93.png",
               "gardener_94.png", "gardener_95.png", "gardener_96.png",
               "gardener_97.png", "gardener_98.png", "gardener_99.png",
               "gardener_910.png", "gardener_911.png", "gardener_912.png",
               "gardener_101.png", "gardener_102.png", "gardener_103.png",
               "gardener_104.png", "gardener_105.png", "gardener_106.png",
               "gardener_107.png", "gardener_108.png", "gardener_109.png",
               "gardener_1010.png", "gardener_1011.png", "gardener_1012.png"),
              colour="#00ffa3", threshold=0.65),
]

# Picked out for the butterfly hunt, which searches for this one kind alone
# many times a second and has no reason to spell out which kind that is
# everywhere it is used.
BUTTERFLY = next(kind for kind in TRASH_KINDS if kind.key == "butterfly")

# TEMPORARY, for choosing the bar above. Everything down to here is reported
# too, marked as a near miss: without seeing what the misses actually scored
# there is no way to tell a threshold that is slightly too strict from one
# that is about right.
#
# Raised from 0.45 (2026-08-03): with the butterfly hunt running find_all
# across 26 rotated templates several times a second, a bar this low was
# turning up 100+ near misses a tick on a real screen — flooding the debug
# log and, far worse, slow enough to make the hunt itself visibly lag
# behind a butterfly that had already moved on by the time a tick finished.
NEAR_THRESHOLD = 0.60

# Per kind. A garden with more litter than this in one screen is not a
# reading worth trusting anyway.
MAX_PER_KIND = 60


@dataclass
class TrashFind:
    kind: TrashKind
    score: float
    x: int          # centre, screen coordinates
    y: int
    accepted: bool  # cleared MATCH_THRESHOLD, rather than merely NEAR


def scan(screen_gray=None, region: dict | None = None,
         threshold: float | None = None,
         near: float = NEAR_THRESHOLD,
         kinds: list[TrashKind] | None = None) -> list[TrashFind]:
    """Every piece of litter on screen, of every known kind, best first.

    Searching down to `near` and marking the difference costs nothing extra
    — it is the same pass — and it is what makes the threshold arguable
    from the log rather than by guesswork.

    Pass `screen_gray` to search an image that has already been grabbed;
    otherwise `region` is captured here, or the primary monitor if `region`
    is not given either. Either way, `region`'s corner is what turns a match
    found in that patch back into a screen coordinate — pass it even
    alongside an already-grabbed `screen_gray` if that image is not itself
    the whole screen.

    Pass `kinds` to search only some of them — the butterfly hunt runs this
    many times a second and has no use for re-checking bushes and beetles
    that are already long since cleared, on top of butterflies now costing
    26 templates apiece to check instead of one or two.
    """
    if screen_gray is None:
        region = region or game_region()
        screen_gray = cv2.cvtColor(
            grab_screen_region(region), cv2.COLOR_BGR2GRAY)
    origin = (region["left"], region["top"]) if region else (0, 0)

    found: list[TrashFind] = []
    for kind in (kinds if kinds is not None else TRASH_KINDS):
        bar = kind.threshold if threshold is None else threshold
        hits = []
        for filename in kind.filenames:
            template = load_template(filename)
            if template is None:
                continue
            h, w = template.shape[:2]
            hits += [(score, x + w // 2, y + h // 2, max(w, h))
                     for score, x, y in find_all(screen_gray, template,
                                                 min(near, bar),
                                                 limit=MAX_PER_KIND)]
        for score, x, y in _merge_variants(hits):
            found.append(TrashFind(kind, score, origin[0] + x, origin[1] + y,
                                   accepted=score >= bar))
    return found


# How far around a mark to look when checking whether it is still there.
# The character walks past and over the litter, and the game shifts a bush a
# pixel or two as it animates, so the box has to be a little wider than the
# picture — but only a little: a wide box catches the *next* bush along and
# reports the cleared one as still standing.
CHECK_PAD = 10


def score_at(kind: TrashKind, gray, x: int, y: int,
             origin: tuple[int, int] = (0, 0)) -> float:
    """How well that kind matches in a small box around one point.

    This is what a running job checks, several times a second, instead of
    searching the whole screen: the run already knows where every piece of
    litter is — it put a mark on each of them — so the only question left is
    whether each mark still has something under it. A box costs a fraction of
    a millisecond where the screen costs 200.

    `origin` is the top-left of `gray` in screen coordinates, so the point
    can be given the way the scan reported it.
    """
    best = 0.0
    for filename in kind.filenames:
        template = load_template(filename)
        if template is None:
            continue
        h, w = template.shape[:2]
        left = int(x - origin[0] - w / 2 - CHECK_PAD)
        top  = int(y - origin[1] - h / 2 - CHECK_PAD)
        box  = gray[max(0, top):top + h + 2 * CHECK_PAD,
                    max(0, left):left + w + 2 * CHECK_PAD]
        if box.shape[0] < h or box.shape[1] < w:
            continue
        best = max(best, float(cv2.matchTemplate(
            box, template, cv2.TM_CCOEFF_NORMED).max()))
    return best


def still_there(kind: TrashKind, gray, x: int, y: int,
                origin: tuple[int, int] = (0, 0)) -> bool:
    """Is that piece of litter still where the scan found it?"""
    return score_at(kind, gray, x, y, origin) >= kind.threshold


def _merge_variants(hits: list) -> list[tuple[float, int, int]]:
    """One object matched by two pictures of it is still one object.

    The variants overlap — a bush that looks like both scores on both — so
    the hits are taken best first and anything sitting on top of one already
    kept is dropped. Without this a bush drawn either way would be counted
    twice and marked twice on screen.
    """
    kept: list[tuple[float, int, int]] = []
    for score, x, y, span in sorted(hits, key=lambda hit: -hit[0]):
        near_enough = span * 0.5
        if any(abs(x - kx) < near_enough and abs(y - ky) < near_enough
               for _s, kx, ky in kept):
            continue
        kept.append((score, x, y))
    return kept


def accepted(found: list[TrashFind]) -> list[TrashFind]:
    return [item for item in found if item.accepted]


def count_by_kind(found: list[TrashFind]) -> dict[str, int]:
    """How many of each kind, counting only what cleared the threshold."""
    counts = {kind.key: 0 for kind in TRASH_KINDS}
    for item in found:
        if item.accepted:
            counts[item.kind.key] = counts.get(item.kind.key, 0) + 1
    return counts
