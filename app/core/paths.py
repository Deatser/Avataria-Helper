# app/core/paths.py
"""Где лежат картинки и где лежат данные.

Из исходников это одна и та же папка проекта: `templates/` соседствует с
`config.json`. В собранном приложении они разъезжаются:

- картинки и шрифты PyInstaller кладёт внутрь сборки (`sys._MEIPASS`), и
  писать туда нельзя - при onefile папка вообще временная;
- `config.json`, `stats.json`, `logs/` - наоборот, только на запись, а
  установленное приложение лежит там, куда Windows писать не даёт. Их место
  в `%APPDATA%\\Avataria Helper`.

Из исходников DATA остаётся относительным путём - ровно тем, что был до
сборки: рабочая папка помощника и есть папка проекта.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Avataria Helper"

_BUNDLE = getattr(sys, "_MEIPASS", None)

# Только чтение: templates/, assets/.
RESOURCES = Path(_BUNDLE) if _BUNDLE else Path(__file__).resolve().parents[2]
TEMPLATES = RESOURCES / "templates"
ASSETS    = RESOURCES / "assets"

# Только запись: настройки, накопленная статистика, подённые логи.
if _BUNDLE:
    DATA = Path(os.environ.get("APPDATA") or Path.home()) / APP_DIR_NAME
    DATA.mkdir(parents=True, exist_ok=True)
else:
    DATA = Path()


def data_path(*parts: str) -> Path:
    """Путь к файлу данных: относительный в исходниках, %APPDATA% в сборке."""
    return DATA.joinpath(*parts)
