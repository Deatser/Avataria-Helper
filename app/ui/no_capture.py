# app/ui/no_capture.py
"""Keep our own windows out of our own screenshots.

Everything this program knows about the game it reads off the screen — and
its windows sit on top of that screen. The gardener's marker dots are drawn
exactly on the litter they point at, so a screenshot taken with them up shows
a garden with no litter in it: the run counted every bush as cleared within
seconds of marking it, having in fact hidden them all from itself.

Windows can be told to stay out of captures. The flag is not a drawing
change — the window still looks the same — it only makes the compositor leave
it out of anything that copies the screen, ours included. Screen sharing and
recording software will not see these windows either, which for a bot's
overlay is no loss.

Windows 10 2004 and later. Anywhere else the call simply fails and the
overlays go back to being visible to the grabber.
"""
from __future__ import annotations

import ctypes

WDA_NONE              = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(widget) -> bool:
    """Take this widget's window out of screen captures. True if it worked."""
    try:
        handle = int(widget.winId())
    except Exception:
        return False
    if not handle:
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
            handle, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False
