# modules/gardener/rotations.py
"""Turned copies of a template, written next to the original.

A butterfly banks and rolls as it flies, so the one picture taken of it
matches only while it happens to be at that angle — which is most of why it
was caught for a second and then lost. Rather than one picture per angle by
hand, the turned copies are made once from the originals and saved beside
them, so they can be looked at and thrown away like any other template.

Edges are replicated rather than filled with black: a black corner is
structure that is not on the screen, and it costs more correlation than the
turn itself.
"""
from __future__ import annotations

from pathlib import Path

import cv2

from app.core.template_match import TEMPLATES_DIR

# Every 45 degrees. Finer steps cost a template each — and the whole set is
# swept over the screen — while a butterfly between two of them still
# matches the nearer one well enough to be picked up.
STEP_DEGREES = 45
ANGLES = tuple(range(STEP_DEGREES, 360, STEP_DEGREES))


def variants(filename: str) -> list[str]:
    """Names of `filename` and its turned copies, making any that are missing.

    The copies are named after the original: gardener_9.png turns into
    gardener_91.png, gardener_92.png and so on, one per angle.
    """
    stem = Path(filename).stem
    names = [filename]
    source = None

    for index, angle in enumerate(ANGLES, start=1):
        name = f"{stem}{index}.png"
        names.append(name)
        path = TEMPLATES_DIR / name
        if path.exists():
            continue
        if source is None:
            source = cv2.imread(str(TEMPLATES_DIR / filename),
                                cv2.IMREAD_GRAYSCALE)
            if source is None:
                return [filename]
        cv2.imwrite(str(path), _turned(source, angle))
    return names


def _turned(image, angle: int):
    h, w = image.shape[:2]
    centre = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
    return cv2.warpAffine(image, matrix, (w, h),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
