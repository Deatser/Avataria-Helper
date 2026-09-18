# modules/janitor/trash.py
"""Finding trash in the park — the same approach Садовник's own trash.py
scans the garden with, just three kinds instead of six.
"""
from __future__ import annotations
from dataclasses import dataclass

import cv2

from app.core.capture import grab_screen_region, grab_window
from app.core.template_match import (find_all, load_template,
                                     game_region)

MATCH_THRESHOLD = 0.72


@dataclass(frozen=True)
class TrashKind:
    key: str
    plural: str        # "банок колы"
    singular: str      # "Банка колы"
    filenames: tuple[str, ...]
    colour: str = "#e2a24a"
    threshold: float = MATCH_THRESHOLD


# Add a kind here and it is scanned, counted and reported with no other
# change — the log lines are built from this list.
TRASH_KINDS: list[TrashKind] = [
    TrashKind("cola", "банок колы", "Банка колы",
              ("janitor_1.png", "janitor_11.png"), colour="#c0392b"),
    TrashKind("cone", "рожков мороженого", "Рожок мороженого",
              ("janitor_2.png", "janitor_22.png", "janitor_222.png",
               "janitor_2222.png", "janitor_22222.png",
               "janitor_222222.png"), colour="#d879c7",
              threshold=0.75),
    TrashKind("box", "коробок", "Коробка",
              ("janitor_3.png", "janitor_33.png"), colour="#c8935a"),
]

# TEMPORARY, for choosing each kind's bar — see the per-kind debug buttons.
NEAR_THRESHOLD = 0.60

# Per kind. A park with more trash than this in one screen is not a reading
# worth trusting anyway.
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
         kinds: list[TrashKind] | None = None,
         hwnd: int | None = None) -> list[TrashFind]:
    """Every piece of trash on screen, of every known kind, best first.

    Pass `screen_gray` to search an image that has already been grabbed;
    otherwise `region` is captured here, or the primary monitor if `region`
    is not given either. Either way, `region`'s corner is what turns a match
    found in that patch back into a screen coordinate.

    Pass `hwnd` to read the park through the game's own window content
    (PrintWindow) rather than the screen — the same reason grab_window
    exists at all: ScreenCapture only shows whatever is drawn on top, so
    the helper's own windows (or anything else) sitting over the game would
    otherwise read as missing trash rather than as something covering it.
    """
    if screen_gray is None:
        region = region or game_region()
        frame = (grab_window(hwnd, region) if hwnd
                 else grab_screen_region(region))
        screen_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
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
# A box a little wider than the picture itself, not exact — same reasoning
# as Садовник's own copy of this constant.
CHECK_PAD = 10


def score_at(kind: TrashKind, gray, x: int, y: int,
             origin: tuple[int, int] = (0, 0)) -> float:
    """How well that kind matches in a small box around one point.

    Confirming a pick this way — a look at just the spot it was found at
    — is both cheaper and far more direct than inferring "gone" from
    whether the character seemed to move: a box costs a fraction of a
    millisecond where the park costs hundreds, and it answers the actual
    question (is the item still there) instead of a proxy for it.

    `origin` is the top-left of `gray` in screen coordinates, so the point
    can be given the way scan() reported it.
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


def _merge_variants(hits: list) -> list[tuple[float, int, int]]:
    """One object matched by two pictures of it is still one object."""
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
