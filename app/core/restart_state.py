# app/core/restart_state.py
"""Записка помощника самому себе через перезапуск.

Перезапуск игры уносит с собой и помощника (его окна прицеплены к окну игры
как дочерние — см. app/core/restarter.py), поэтому «кто был включён и почему
мы вообще перезапустились» не может жить в памяти. Отдельный файлик, а не
config.json или stats.json: это не настройка и не накопленное за всё время,
а одноразовая записка, которая после прочтения удаляется.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import time

RESTART_FILE = Path(__file__).resolve().parents[2] / "restart.json"
# Логи отдельным файлом: их много, а записка должна оставаться такой, чтобы
# в неё можно было заглянуть глазами.
LOGS_FILE    = Path(__file__).resolve().parents[2] / "restart_logs.json"

# Ключ главного окна помощника в файле логов; у окон модов ключ — их
# module_name.
HELPER_LOG = "__helper__"

# Почему перезапустились. Техперерыв отличается от остальных: он длится
# часами, и вернувшийся помощник должен понимать, что заставку он сейчас
# увидит снова и это не повод перезапускаться опять.
REASON_BREAK  = "break"
REASON_STUCK  = "stuck"
REASON_PAUSE  = "pause"
REASON_MANUAL = "manual"


@dataclass
class RestartInfo:
    reason: str = REASON_MANUAL
    modules: list[str] = field(default_factory=list)
    at: float = 0.0   # time.time() момента, когда записку написали


def write_restart(reason: str, modules: list[str]) -> None:
    try:
        RESTART_FILE.write_text(
            json.dumps({"reason": reason, "modules": modules, "at": time.time()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass   # не смогли записать — перезапуск всё равно нужнее записки


def take_restart() -> RestartInfo | None:
    """Прочитать записку и сразу убрать: она годна ровно на один запуск."""
    if not RESTART_FILE.exists():
        return None
    try:
        raw = json.loads(RESTART_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    try:
        RESTART_FILE.unlink()
    except OSError:
        pass
    return RestartInfo(
        reason  = str(raw.get("reason", REASON_MANUAL)),
        modules = [str(m) for m in raw.get("modules", [])],
        at      = float(raw.get("at", 0.0) or 0.0),
    )


def write_logs(panels: dict[str, str]) -> None:
    """Сохранить логи окон (готовый HTML) на время перезапуска."""
    try:
        LOGS_FILE.write_text(json.dumps(panels, ensure_ascii=False),
                             encoding="utf-8")
    except OSError:
        pass


def take_logs() -> dict[str, str]:
    """Забрать сохранённые логи и убрать файл — они годны на один запуск."""
    if not LOGS_FILE.exists():
        return {}
    try:
        raw = json.loads(LOGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    try:
        LOGS_FILE.unlink()
    except OSError:
        pass
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
