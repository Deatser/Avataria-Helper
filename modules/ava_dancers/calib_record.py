# modules/ava_dancers/calib_record.py
"""Записывает то, чего боту сейчас не видно: всю колонку ряда целиком,
сильно выше полоски детекта.

Нужно ровно для одного — спроектировать трекинг нот (уровень 2): на какой
высоте нота вообще появляется, с какой скоростью едет, накрывает ли вспышка
попадания всю колонку или только низ, и где настоящая линия удара.

Запускается отдельным процессом рядом с ботом, ничего в нём не меняет.
Сам находит окно игры, сам ждёт начала раунда, сам останавливается.

Пишет в testlogs/calib_<дата>/:
    meta.json     — геометрия окна, где оказалась полоска бота, тайминги
    profiles.npz  — по каждому кадру и каждому из 4 рядов: сколько пикселей
                    в каждой строке колонки попало в маску lit/cyan/green/
                    red/magenta. Это количественная часть: из неё считается
                    скорость нот, плотность и вертикальный охват вспышки.
    frames.csv    — время каждого кадра и прямоугольник окна (ловит переезд
                    или ресайз окна посреди записи)
    band/         — PNG колонок всех 4 рядов, короткими очередями
    full/         — несколько кадров окна целиком, для общей геометрии

Запуск:
    python modules/ava_dancers/calib_record.py
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import win32gui

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.capture import grab_window, release_window_capture   # noqa: E402
from modules.ava_dancers.tiles import _LIT_V                       # noqa: E402
from modules.ava_dancers.tracker import DEFAULT_GEOMETRY           # noqa: E402

# The anchor everything below is measured from. Was the old strip detector's
# TILE_REGION; now it is the playfield the tracker works off, so a recording
# lines up with what the bot actually reads.
TILE_REGION = {"left": DEFAULT_GEOMETRY.left,
               "top": DEFAULT_GEOMETRY.top + 884,   # the old hit line, y=1070
               "width": DEFAULT_GEOMETRY.width,
               "height": 30}

GAME_TITLE = "Аватария"

# ── Что снимаем ──────────────────────────────────────────────────────────
# Полоска бота стоит на y=1070. Нас интересует всё, что выше неё: там нота
# видна чисто, задолго до вспышки от предыдущей. LOOKUP взят с запасом —
# лишнее обрежется по краю окна, и по профилям сразу будет видно, сколько
# из этого запаса реально занимает игровое поле.
LOOKUP_PX   = 700   # сколько строк захватить ВЫШЕ полоски бота
LOOKDOWN_PX = 240   # и сколько НИЖЕ (там живёт вспышка попадания)

_LANE_W = DEFAULT_GEOMETRY.lane_w   # 178 — полная ширина ряда

# ── Темп ─────────────────────────────────────────────────────────────────
# Скрипт делает свой PrintWindow параллельно с ботом, так что темп нарочно
# низкий: профили нужны непрерывно, но 8 Гц хватает (нота проходит колонку
# ~1.9 с, это ~15 замеров), а полную частоту берём только очередями.
PROFILE_HZ   = 8.0
DURATION_S   = 330.0    # от начала раунда; отметка 4 минуты попадает внутрь
WAIT_ROUND_S = 150.0    # сколько ждать первую ноту, прежде чем писать «как есть»

# (старт в секундах от начала раунда, длительность) — очереди полнокадровой
# записи PNG. Последние три специально в плотной части забега.
BURSTS = [(4, 3), (70, 3), (150, 3), (215, 3), (255, 4), (300, 4)]
BURST_EVERY_NTH = 2     # в очереди сохраняем каждый 2-й кадр (~12 fps)

FULL_SHOTS_S = [1, 6, 80, 220, 262, 305]   # когда снять окно целиком


def _find_game() -> int | None:
    """Окно игры — точное совпадение заголовка, иначе вхождение.

    Та же логика, что в WindowManager.find_game: вкладка браузера про
    Аватарию содержит то же слово, что и окно самой игры.
    """
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


def _band_rect(img: np.ndarray, hwnd: int) -> tuple[int, int, int, int, int, int]:
    """Полоса захвата в координатах кадра окна.

    TILE_REGION задан в абсолютных экранных координатах и переводится в
    оконные по текущему положению окна — ровно как это делает grab_window,
    поэтому полоса всегда стоит там же, где полоска бота.
    """
    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    h, w = img.shape[:2]
    rel_left = TILE_REGION["left"] - left
    rel_top  = TILE_REGION["top"] - top
    x0 = max(0, min(w, rel_left))
    x1 = max(0, min(w, rel_left + TILE_REGION["width"]))
    y0 = max(0, min(h, rel_top - LOOKUP_PX))
    y1 = max(0, min(h, rel_top + LOOKDOWN_PX))
    return x0, y0, x1, y1, rel_left, rel_top


def _profiles(band: np.ndarray) -> np.ndarray:
    """(4 ряда, 5 масок, строк) — сколько пикселей строки попало в маску.

    Пороги те же, что у classify_hsv, чтобы цифры отсюда напрямую
    сравнивались с тем, что видит боевой детектор. uint8 хватает: в строке
    ряда 175 пикселей.
    """
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    lit  = val >= _LIT_V
    neon = lit & (sat >= 180)
    masks = [
        lit,
        neon & (hue >= 79) & (hue <= 125),                      # cyan  — nice
        neon & (hue >= 35) & (hue <= 78),                       # green — bonus
        (val >= 110) & (sat >= 90) & ((hue <= 14) | (hue >= 160)),   # red — bad
        lit & (sat >= 40) & (hue >= 126) & (hue <= 159),        # magenta — bomb
    ]
    rows = band.shape[0]
    out = np.zeros((4, len(masks), rows), np.uint8)
    for lane in range(4):
        sl = slice(lane * _LANE_W, (lane + 1) * _LANE_W)
        for m, mask in enumerate(masks):
            out[lane, m] = np.minimum(mask[:, sl].sum(axis=1), 255).astype(np.uint8)
    return out


def _in_burst(t: float) -> bool:
    return any(start <= t < start + length for start, length in BURSTS)


def main() -> int:
    hwnd = _find_game()
    if not hwnd:
        print(f"Окно «{GAME_TITLE}» не найдено — запусти игру и повтори.")
        return 1
    print(f"Окно игры: {hwnd}  {win32gui.GetWindowRect(hwnd)}")

    out_dir = _ROOT / "testlogs" / f"calib_{time.strftime('%Y%m%d_%H%M%S')}"
    (out_dir / "band").mkdir(parents=True, exist_ok=True)
    (out_dir / "full").mkdir(parents=True, exist_ok=True)

    period = 1.0 / PROFILE_HZ
    prof_frames: list[np.ndarray] = []
    rows_csv: list[tuple] = []
    errors = 0
    band_saved = full_saved = 0
    geom: dict | None = None

    # ── Фаза 1: ждём первую ноту ────────────────────────────────────────
    # Отсчёт ведём от начала раунда, а не от запуска скрипта: до раунда ещё
    # идёт проход по меню, и он занимает каждый раз разное время — очереди
    # съехали бы относительно отметки 4 минуты.
    print(f"Жду начала раунда (до {WAIT_ROUND_S:.0f} с)…  Ctrl+C — остановить.")
    wait_start = time.monotonic()
    lit_streak = 0
    while time.monotonic() - wait_start < WAIT_ROUND_S:
        try:
            img = grab_window(hwnd)
            x0, y0, x1, y1, _, _ = _band_rect(img, hwnd)
            band = img[y0:y1, x0:x1]
            val = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 2]
            lit_streak = lit_streak + 1 if (val >= _LIT_V).mean() > 0.002 else 0
            if lit_streak >= 3:
                break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        print("Ноты так и не появились — пишу как есть, разберусь по кадрам.")
    print("Пошла запись.")

    # ── Фаза 2: запись ──────────────────────────────────────────────────
    t0 = time.monotonic()
    frame_no = 0
    try:
        while True:
            t = time.monotonic() - t0
            if t >= DURATION_S:
                break
            burst = _in_burst(t)
            try:
                img = grab_window(hwnd)
                x0, y0, x1, y1, rel_left, rel_top = _band_rect(img, hwnd)
                band = img[y0:y1, x0:x1]
                if band.size == 0:
                    raise RuntimeError("полоса захвата вне окна")

                if geom is None:
                    geom = {
                        "window_rect": list(win32gui.GetWindowRect(hwnd)),
                        "window_size": [img.shape[1], img.shape[0]],
                        "tile_region": TILE_REGION,
                        "strip_rel_left": rel_left, "strip_rel_top": rel_top,
                        "band_rect_in_window": [x0, y0, x1, y1],
                        "band_size": [band.shape[1], band.shape[0]],
                        "lane_width": _LANE_W,
                        # строка полосы, на которой стоит верх полоски бота —
                        # точка отсчёта для всех вертикальных координат
                        "strip_top_row_in_band": rel_top - y0,
                        "lookup_px": LOOKUP_PX, "lookdown_px": LOOKDOWN_PX,
                        "profile_hz": PROFILE_HZ,
                    }
                    print(f"Полоса {band.shape[1]}x{band.shape[0]}, "
                          f"полоска бота на строке {rel_top - y0} внутри неё")

                prof_frames.append(_profiles(band))
                rows_csv.append((frame_no, round(t * 1000), *win32gui.GetWindowRect(hwnd)))

                if burst and frame_no % BURST_EVERY_NTH == 0:
                    cv2.imwrite(str(out_dir / "band" / f"b{int(t*1000):06d}.png"),
                                band, [cv2.IMWRITE_PNG_COMPRESSION, 6])
                    band_saved += 1
                if FULL_SHOTS_S and t >= FULL_SHOTS_S[0]:
                    FULL_SHOTS_S.pop(0)
                    cv2.imwrite(str(out_dir / "full" / f"w{int(t*1000):06d}.png"), img)
                    full_saved += 1

            except Exception as exc:
                errors += 1
                if errors <= 3:
                    print(f"  кадр пропущен: {exc}")

            frame_no += 1
            if frame_no % 40 == 0:
                print(f"\r  {t:5.0f} с / {DURATION_S:.0f}   кадров {frame_no}"
                      f"   PNG полос {band_saved}   ошибок {errors}   ", end="")
            if not burst:
                time.sleep(max(0.0, period - ((time.monotonic() - t0) - t)))

    except KeyboardInterrupt:
        print("\nОстановлено вручную — сохраняю то, что успел.")
    finally:
        release_window_capture()

    # ── Результаты ──────────────────────────────────────────────────────
    if prof_frames:
        np.savez_compressed(out_dir / "profiles.npz",
                            profiles=np.stack(prof_frames),
                            t_ms=np.array([r[1] for r in rows_csv], np.int32))
    with (out_dir / "frames.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "t_ms", "win_left", "win_top", "win_right", "win_bottom"])
        w.writerows(rows_csv)
    (out_dir / "meta.json").write_text(json.dumps({
        "geometry": geom,
        "frames": len(prof_frames),
        "duration_s": round(rows_csv[-1][1] / 1000, 1) if rows_csv else 0,
        "errors": errors,
        "band_pngs": band_saved,
        "full_pngs": full_saved,
        "mask_order": ["lit", "cyan", "green", "red", "magenta"],
        "bursts_s": BURSTS,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nГотово: {out_dir}")
    print(f"  кадров {len(prof_frames)}, PNG полос {band_saved}, "
          f"окон целиком {full_saved}, ошибок {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
