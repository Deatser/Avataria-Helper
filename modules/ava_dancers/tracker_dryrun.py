# modules/ava_dancers/tracker_dryrun.py
"""Runs the real tracker against the live game and prints what it WOULD do.

Not one key is sent. Exists so a broken detector can be diagnosed without
spending a round on it: start a game, run this, read the output.

It uses AvaBot's own code path — the same capture region, the same
LaneTracker, the same geometry lookup — so anything it reports is what the
bot would have done at that moment.

Запуск (во время раунда):
    python modules/ava_dancers/tracker_dryrun.py [секунды]
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

import win32gui

# The console here is cp1251, which has no box-drawing or warning glyphs and
# raises on them mid-print — force UTF-8 so a diagnostic never dies of its
# own output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.capture import grab_window, release_window_capture   # noqa: E402
from modules.ava_dancers.tracker import (                          # noqa: E402
    DEFAULT_GEOMETRY, LANES, LaneTracker, locate_field,
)

GAME_TITLE = "Аватария"
DEFAULT_SECONDS = 25.0


def _find_game() -> int | None:
    exact = partial = None

    def cb(hwnd, _):
        nonlocal exact, partial
        if not win32gui.IsWindowVisible(hwnd):
            return
        text = win32gui.GetWindowText(hwnd)
        if text == GAME_TITLE:
            exact = hwnd
        elif GAME_TITLE in text:
            partial = hwnd

    win32gui.EnumWindows(cb, None)
    return exact if exact is not None else partial


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    hwnd = _find_game()
    if not hwnd:
        print(f"Окно «{GAME_TITLE}» не найдено.")
        return 1

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    print(f"Окно {hwnd}  {right - left}x{bottom - top} в ({left}, {top})")

    found = locate_field(grab_window(hwnd), left, top)
    print(f"Поле найдено по рамке : {found}")
    print(f"Поле по умолчанию     : {DEFAULT_GEOMETRY}")
    geo = found or DEFAULT_GEOMETRY
    if found is None:
        print("  !! рамка поля не видна — раунд не идёт? Беру значения по умолчанию.")
    print(f"Зона захвата {geo.region}  ряд {geo.lane_w}px  "
          f"коммит y={geo.commit_y}  удар y={geo.hit_y}")
    print(f"\nСмотрю {seconds:.0f} с. НИ ОДНА КЛАВИША НЕ НАЖИМАЕТСЯ.\n")

    tracker = LaneTracker(geometry=geo)
    region, polls, errors = geo.region, 0, 0
    presses: list = []
    born = [0] * LANES
    t0 = time.monotonic()

    try:
        while time.monotonic() - t0 < seconds:
            try:
                img = grab_window(hwnd, region)
                now = time.monotonic()
                if img.shape[:2] != (region["height"], region["width"]):
                    errors += 1
                    continue
                before = sum(len(t) for t in tracker._tracks)
                for p in tracker.update(now, img):
                    presses.append(p)
                    print(f"  [{now - t0:6.2f}s] ряд {p.lane} {p.kind:>6}  "
                          f"{p.speed:4.0f} px/s  нажатие через {p.delay_s * 1000:4.0f} мс")
                after = sum(len(t) for t in tracker._tracks)
                if after > before:
                    born[0] += after - before
                polls += 1
            except Exception as exc:
                errors += 1
                if errors <= 3:
                    print(f"  ошибка: {exc}")
            time.sleep(0.001)
    except KeyboardInterrupt:
        print("\nОстановлено вручную.")
    finally:
        release_window_capture()

    span = time.monotonic() - t0
    print(f"\n── итог за {span:.0f} с ───────────────────────────────")
    print(f"  тактов захвата     : {polls}  ({polls / span:.1f} Гц)")
    print(f"  ошибок захвата     : {errors}")
    print(f"  треков родилось    : {born[0]}")
    print(f"  дошло до коммита   : {tracker.committed}")
    print(f"  нажало бы          : {len(presses)}")
    print(f"  отказы             : {dict(tracker.refusals) or 'нет'}")
    if presses:
        by_lane = Counter(p.lane for p in presses)
        speeds = sorted(p.speed for p in presses)
        print(f"  по рядам           : {dict(sorted(by_lane.items()))}")
        print(f"  скорость нот       : медиана {speeds[len(speeds) // 2]:.0f} px/s "
              f"(от {speeds[0]:.0f} до {speeds[-1]:.0f})")
        gaps = []
        for lane in range(LANES):
            ts = sorted(p.arrival for p in presses if p.lane == lane)
            gaps += [round((b - a) * 1000) for a, b in zip(ts, ts[1:])]
        if gaps:
            print(f"  интервал в ряду мс : минимум {min(gaps)}  медиана "
                  f"{sorted(gaps)[len(gaps) // 2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
