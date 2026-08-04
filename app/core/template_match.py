# app/core/template_match.py
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import mss

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

# Which reward a run is farmed for — stored in config as `finish_on` and
# used to work out the payout once GameOverWatch sees the round end. See
# modules.ava_dancers.window.AvaDancersWindow._on_game_over.
FINISH_GOLD   = "gold"
FINISH_SILVER = "silver"


@lru_cache(maxsize=128)
def load_template(filename: str) -> np.ndarray | None:
    """Read a template from templates/ as grayscale, None if missing.

    Cached: a cleaning run checks its marks several times a second, and each
    check would otherwise re-read the same handful of files off disk and
    decode them again. Callers only ever match against these — none of them
    writes into the array — so one copy is enough for everybody.
    """
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
