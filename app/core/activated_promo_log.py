# app/core/activated_promo_log.py
"""The running list of promo codes this helper has already submitted
in-game — its own file, not stats.json or config.json: it is neither a
running count (stats.py) nor a setting (config.py), just the record both
the manual "Активировать" button and the autonomous loop check against so
a code already handled is never entered a second time.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

LOG_PATH = Path("logs") / "Активированные промокоды.json"


def _load() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def is_activated(code: str) -> bool:
    """Только по-настоящему принятые коды. Записи с "ok": false — это
    отказы игры, они здесь не считаются: код не активирован, и помечать его
    как использованный (жёлтым в окне) нельзя. Старые записи поля "ok" не
    имеют вовсе и считаются успешными — какими они и были."""
    return any(entry.get("code") == code and entry.get("ok", True)
               for entry in _load())


def is_rejected(code: str) -> bool:
    """Игра отказала по этому коду хотя бы раз («OK» вместо «забрать»):
    не найден, уже использован, просрочен. Нужно автодетекту, чтобы не
    долбиться в один и тот же мёртвый код каждую минуту — на ручное
    нажатие «активировать» это не влияет, там попытка повторится."""
    return any(entry.get("code") == code and not entry.get("ok", True)
               for entry in _load())


def record(code: str, title: str | None, ok: bool = True):
    """Appends one entry — idempotent: recording the same code twice
    only adds a second timestamped line, never de-duplicates itself, since
    is_activated() already stops a code being submitted twice in the first
    place."""
    LOG_PATH.parent.mkdir(exist_ok=True)
    entries = _load()
    entries.append({
        "code": code,
        "title": title or "",
        "ok": ok,
        "activated_at": datetime.now().isoformat(timespec="seconds"),
    })
    LOG_PATH.write_text(
        json.dumps(entries, indent=4, ensure_ascii=False), encoding="utf-8")
