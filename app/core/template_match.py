# app/core/template_match.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import mss

from app.core.capture import ScreenCapture

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

# The end-of-round reward line, one template per currency. Both are opaque
# crops of the real screen, so plain grayscale matching is enough — no mask.
# The keys are what gets stored in config as `finish_on`.
FINISH_GOLD   = "gold"
FINISH_SILVER = "silver"

LEAVE_TEMPLATES: dict[str, tuple[str, str]] = {
    FINISH_GOLD:   ("Золото",  "leave_gold.png"),
    FINISH_SILVER: ("Серебро", "leave_silver.png"),
}

# The reward line appears in stages: the coin and its number arrive before the
# green checkmark next to them. At 0.70 gold matched at 78.7% with the
# checkmark still missing — a partial line that means the round is not over
# yet. Both templates include the checkmark, so demanding 0.96 is what tells
# "fully drawn, safe to leave" apart from "still filling in".
MATCH_THRESHOLD = 0.96


@dataclass
class Match:
    key: str       # FINISH_GOLD / FINISH_SILVER
    label: str     # display name for the log
    score: float   # 0..1, TM_CCOEFF_NORMED
    x: int         # top-left corner of the best match, screen coordinates
    y: int
    w: int
    h: int

    @property
    def percent(self) -> float:
        return self.score * 100

    @property
    def found(self) -> bool:
        return self.score >= MATCH_THRESHOLD


def load_template(filename: str) -> np.ndarray | None:
    """Read a template from templates/ as grayscale, None if missing."""
    return cv2.imread(str(TEMPLATES_DIR / filename), cv2.IMREAD_GRAYSCALE)


def best_match(scene: np.ndarray, template: np.ndarray) -> tuple[float, tuple[int, int]]:
    """Best score for template anywhere in scene, plus its top-left corner.

    Both images grayscale. A template bigger than the scene is not an error
    here — the game can be windowed smaller than the crop was taken at, and a
    diagnostic button should report "no match" rather than raise.
    """
    if scene.size == 0 or template.size == 0:
        return 0.0, (0, 0)
    sh, sw = scene.shape[:2]
    th, tw = template.shape[:2]
    if th > sh or tw > sw:
        return 0.0, (0, 0)

    result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    return float(score), (int(loc[0]), int(loc[1]))


def find_all(scene: np.ndarray, template: np.ndarray, threshold: float,
             limit: int = 100) -> list[tuple[float, int, int]]:
    """Every place `template` appears in `scene`, best first.

    best_match answers "where is it", which is the wrong question when the
    same thing can be on screen several times over. Peaks are taken one at a
    time, and each one blanks out the area around itself before the next is
    looked for — without that, a single object would be reported dozens of
    times, once per pixel of the little plateau its match makes.

    Returns (score, x, y) with the corner, not the centre; `limit` is a
    stop so a threshold set too low cannot spin through the whole image.
    """
    if scene.size == 0 or template.size == 0:
        return []
    th, tw = template.shape[:2]
    sh, sw = scene.shape[:2]
    if th > sh or tw > sw:
        return []

    result = cv2.matchTemplate(scene, template, cv2.TM_CCOEFF_NORMED)
    found  = []
    while len(found) < limit:
        _, score, _, loc = cv2.minMaxLoc(result)
        if score < threshold:
            break
        x, y = int(loc[0]), int(loc[1])
        found.append((float(score), x, y))
        # Half a template in each direction: two real objects can sit close
        # together, but not overlapping by more than half.
        result[max(0, y - th // 2):y + th // 2 + 1,
               max(0, x - tw // 2):x + tw // 2 + 1] = -1.0
    return found


def primary_monitor_region() -> dict:
    with mss.mss() as sct:
        return dict(sct.monitors[1])


def search_screen(templates: dict[str, tuple[str, str]] = None,
                  screen: np.ndarray = None) -> list[Match]:
    """Grab the primary monitor once and score every template against it.

    Returns one Match per template whatever it scored — the caller decides
    what to do with a miss, and seeing the near-miss percentage is the point
    of the readout. Templates that fail to load are skipped silently; the
    caller reports the gap by comparing lengths. Pass `screen` (grayscale) to
    score an image that was already grabbed instead of taking a new one.
    """
    templates = LEAVE_TEMPLATES if templates is None else templates
    if screen is None:
        screen = cv2.cvtColor(ScreenCapture.get().grab(primary_monitor_region()),
                              cv2.COLOR_BGR2GRAY)
    matches = []
    for key, (label, filename) in templates.items():
        template = load_template(filename)
        if template is None:
            continue
        score, (x, y) = best_match(screen, template)
        h, w = template.shape[:2]
        matches.append(Match(key, label, score, x, y, w, h))
    return matches
