# app/core/restarter.py
"""Отдельный процесс, который переживает закрытие игры.

Окна помощника прицеплены к окну Аватарии как дочерние (WS_CHILD), и Windows
уничтожает их вместе с родителем — закрыть игру и продолжить работать в том
же процессе нельзя. Отцепить окна перед закрытием тоже не выход: Qt после
переприцепления показанного окна теряет отложенную перерисовку (об этом
отдельно написано в app/ui/overlay.py над attach_child).

Поэтому перезапуск делает вот этот процесс — у него нет ни одного окна, и
закрытие игры ему безразлично. Порядок: дождаться, пока помощник выйдет,
закрыть Аватарию, поднять её заново уже написанным open_game и запустить
помощник обратно.

Запускается самим помощником:  python -m app.core.restarter <pid помощника>
Консоль наследует родительскую, так что логи идут в тот же терминал.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

import win32api
import win32con
import win32gui
import win32process

from app.core.launcher import GAME_TITLE, find_window, open_game
from app.core.term_log import tlog

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HELPER_EXIT_WAIT_S = 20.0   # сколько ждём, что помощник действительно вышел
CLOSE_WAIT_S       = 10.0   # …и что игра закрылась по-хорошему
POLL_S             = 0.5

_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102


def _await_process_exit(pid: int, seconds: float) -> bool:
    """Дождаться, пока процесс pid исчезнет. True — исчез."""
    handle = ctypes.windll.kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return True   # его уже нет
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(
            handle, int(seconds * 1000))
        return result != _WAIT_TIMEOUT
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def close_game(log=tlog) -> bool:
    """Закрыть окно Аватарии. Сначала по-хорошему, потом — как придётся."""
    hwnd = find_window(GAME_TITLE)
    if not hwnd:
        log(f"{GAME_TITLE}: окно уже закрыто")
        return True

    log(f"Закрываем {GAME_TITLE}")
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pid = 0
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    deadline = time.monotonic() + CLOSE_WAIT_S
    while time.monotonic() < deadline:
        if not find_window(GAME_TITLE):
            return True
        time.sleep(POLL_S)

    # Не закрылась сама. Снимаем процесс — на этом месте выбора уже нет, но
    # сказать об этом надо: у игры общий процесс с браузером, если она
    # запущена как вкладка, и тогда закроется и он.
    if not pid:
        log(f"{GAME_TITLE} не закрылась, и процесс её неизвестен")
        return False
    log(f"{GAME_TITLE} не закрылась сама — снимаем процесс {pid}")
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
        win32api.TerminateProcess(handle, 0)
        win32api.CloseHandle(handle)
    except Exception as exc:
        log(f"Снять процесс не удалось: {exc}")
        return False

    deadline = time.monotonic() + CLOSE_WAIT_S
    while time.monotonic() < deadline:
        if not find_window(GAME_TITLE):
            return True
        time.sleep(POLL_S)
    return False


def start_helper(log=tlog) -> bool:
    """Запустить помощник заново — тем же интерпретатором, что и нас."""
    log("Запускаем помощник заново")
    try:
        subprocess.Popen([sys.executable, "main.py"], cwd=str(PROJECT_ROOT))
        return True
    except Exception as exc:
        log(f"Запустить помощник не удалось: {exc}")
        return False


def main(argv: list[str]) -> int:
    pid = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 0
    if pid:
        if not _await_process_exit(pid, HELPER_EXIT_WAIT_S):
            tlog("Помощник не завершился — перезапуск отменён")
            return 1

    if not close_game():
        tlog("Игру закрыть не удалось — перезапуск отменён")
        return 1

    if not open_game():
        tlog("Игру поднять не удалось — помощник запускаем как есть")

    start_helper()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
