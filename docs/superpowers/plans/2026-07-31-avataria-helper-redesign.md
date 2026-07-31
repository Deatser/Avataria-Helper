# Avataria Helper — Full Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Полный рефактор Avataria Helper — чистая модульная архитектура, Nothing Phone UI, надёжный ввод клавиш через PostMessage, QThread вместо threading.Thread.

**Architecture:** Три слоя: `app/core` (win32, захват экрана, конфиг, ввод клавиш — без UI), `app/ui` (Nothing-стиль виджеты и overlay — без win32), `modules/` (логика игровых ботов — использует core через интерфейсы). Слои общаются только через Qt сигналы и явные аргументы функций.

**Tech Stack:** Python 3.10+, PySide6 6.x, pywin32, mss, numpy, opencv-python. Без pyautogui, без threading.Thread.

## Global Constraints

- Windows only — все win32 вызовы допустимы
- Никакого `import pyautogui` нигде в кодовой базе
- Никакого `threading.Thread` для ботов — только `QThread`
- Шрифт: JetBrains Mono (с fallback на Courier New если не установлен)
- Цвета строго из `app/ui/theme.py` — никаких хардкоженных hex в других файлах
- Все конфигурационные параметры читаются из `ConfigManager`, не хардкодятся
- `app/core/` не импортирует ничего из `app/ui/` или `modules/`
- Проект пока без git; перед первым коммитом выполнить `git init`

---

## File Map

```
Создать:
  app/__init__.py
  app/core/__init__.py
  app/core/config.py
  app/core/window_manager.py
  app/core/input_sender.py
  app/core/capture.py
  app/ui/__init__.py
  app/ui/theme.py
  app/ui/widgets/__init__.py
  app/ui/widgets/nt_button.py
  app/ui/widgets/nt_panel.py
  app/ui/widgets/nt_status_dot.py
  app/ui/widgets/log_panel.py
  app/ui/module_window.py
  app/ui/overlay.py
  app/module_registry.py
  modules/__init__.py
  modules/base.py
  modules/ava_dancers/__init__.py
  modules/ava_dancers/bot.py
  modules/ava_dancers/window.py
  tests/__init__.py
  tests/core/__init__.py
  tests/core/test_config.py
  tests/core/test_input_sender.py
  tests/modules/__init__.py
  tests/modules/test_ava_bot.py

Переписать:
  main.py

Удалить (Task 9):
  config.py
  overlay.py
  ava_dancers.py
  window_bind.py
  logger.py
  circle_button.py
  test.py
  dancebot/dance_bot.py
  dancebot/__init__.py (если есть)
```

---

### Task 1: Scaffolding + ConfigManager

**Files:**
- Create: `app/__init__.py`, `app/core/__init__.py`, `modules/__init__.py`, `tests/__init__.py`, `tests/core/__init__.py`
- Create: `app/core/config.py`
- Create: `tests/core/test_config.py`

**Interfaces:**
- Produces:
  - `ConfigManager()` — класс с полем `data: AppConfig` и методом `save()`
  - `AppConfig` — датакласс с полями `overlay: OverlayConfig`, `ava_dancers: AvaDancersConfig`
  - `OverlayConfig` — датакласс: `x=10, y=10, width=220, height=420, opacity=220`
  - `AvaDancersConfig` — датакласс: `favorite=False, position_saved=False, x=250, y=10, min_active=3500`

- [ ] **Step 1: Создать пустые `__init__.py` файлы**

```
app/__init__.py          (пустой)
app/core/__init__.py     (пустой)
modules/__init__.py      (пустой)
tests/__init__.py        (пустой)
tests/core/__init__.py   (пустой)
```

- [ ] **Step 2: Написать тесты для ConfigManager**

```python
# tests/core/test_config.py
import json
import pytest
from app.core.config import ConfigManager, AppConfig


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 10
    assert cfg.data.overlay.width == 220
    assert cfg.data.ava_dancers.min_active == 3500
    assert cfg.data.ava_dancers.favorite is False


def test_load_partial_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"overlay": {"x": 50, "y": 100}}), encoding="utf-8"
    )
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 50
    assert cfg.data.overlay.y == 100
    assert cfg.data.overlay.width == 220   # missing key → default


def test_corrupted_config_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("not valid json {{{", encoding="utf-8")
    cfg = ConfigManager()
    assert isinstance(cfg.data, AppConfig)
    assert cfg.data.overlay.x == 10


def test_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    cfg.data.overlay.x = 99
    cfg.data.ava_dancers.min_active = 4000
    cfg.save()
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert raw["overlay"]["x"] == 99
    assert raw["ava_dancers"]["min_active"] == 4000


def test_type_coercion_on_load(tmp_path, monkeypatch):
    # JSON может хранить числа как float после редактирования вручную
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"overlay": {"x": 50.9}}), encoding="utf-8"
    )
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 50   # int, не float
```

- [ ] **Step 3: Запустить тесты — убедиться что FAIL**

```
pytest tests/core/test_config.py -v
```

Ожидаемо: `ModuleNotFoundError` или `ImportError` — файл ещё не создан.

- [ ] **Step 4: Написать `app/core/config.py`**

```python
# app/core/config.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
from pathlib import Path


@dataclass
class OverlayConfig:
    x: int = 10
    y: int = 10
    width: int = 220
    height: int = 420
    opacity: int = 220


@dataclass
class AvaDancersConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 250
    y: int = 10
    min_active: int = 3500


@dataclass
class AppConfig:
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    ava_dancers: AvaDancersConfig = field(default_factory=AvaDancersConfig)


class ConfigManager:
    CONFIG_FILE = Path("config.json")

    def __init__(self):
        self.data = self._load()

    def _load(self) -> AppConfig:
        if not self.CONFIG_FILE.exists():
            return AppConfig()
        try:
            raw = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
            default = AppConfig()
            self._merge(default, raw)
            return default
        except Exception:
            return AppConfig()

    def _merge(self, instance, raw: dict):
        """Recursively merge raw dict into dataclass, coercing types."""
        for key, val in raw.items():
            if not hasattr(instance, key):
                continue
            current = getattr(instance, key)
            if hasattr(current, "__dataclass_fields__") and isinstance(val, dict):
                self._merge(current, val)
            else:
                try:
                    setattr(instance, key, type(current)(val))
                except (TypeError, ValueError):
                    pass  # keep default on type mismatch

    def save(self):
        self.CONFIG_FILE.write_text(
            json.dumps(asdict(self.data), indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
```

- [ ] **Step 5: Запустить тесты — убедиться что PASS**

```
pytest tests/core/test_config.py -v
```

Ожидаемо: 5 PASSED.

- [ ] **Step 6: git init + первый коммит**

```bash
git init
git add app/core/config.py tests/core/test_config.py app/__init__.py app/core/__init__.py modules/__init__.py tests/__init__.py tests/core/__init__.py
git commit -m "feat: add ConfigManager with dataclass schema and merge logic"
```

---

### Task 2: Core layer — InputSender, WindowManager, ScreenCapture

**Files:**
- Create: `app/core/input_sender.py`
- Create: `app/core/window_manager.py`
- Create: `app/core/capture.py`
- Create: `tests/core/test_input_sender.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач
- Produces:
  - `press_key(hwnd: int, key: str) -> bool` из `app.core.input_sender`
  - `VK_MAP: dict[str, int]` из `app.core.input_sender`
  - `WindowManager` с методами: `find_game(title: str) -> bool`, `get_game_hwnd() -> int | None`, `attach_overlay(hwnd, x, y, w, h)`, `attach_child(hwnd)`, `move_window(hwnd, x, y, w, h)`
  - `ScreenCapture.get() -> ScreenCapture` с методом `grab(region: dict) -> np.ndarray`

- [ ] **Step 1: Написать тесты для input_sender**

```python
# tests/core/test_input_sender.py
from app.core.input_sender import VK_MAP, press_key


def test_vk_map_has_dance_keys():
    assert VK_MAP["a"] == 0x41
    assert VK_MAP["s"] == 0x53
    assert VK_MAP["w"] == 0x57
    assert VK_MAP["d"] == 0x44


def test_vk_map_has_arrow_keys():
    assert VK_MAP["left"]  == 0x25
    assert VK_MAP["up"]    == 0x26
    assert VK_MAP["right"] == 0x27
    assert VK_MAP["down"]  == 0x28


def test_press_key_returns_false_for_zero_hwnd():
    assert press_key(0, "a") is False


def test_press_key_returns_false_for_unknown_key():
    # hwnd=0 → returns False before PostMessage is called
    assert press_key(0, "xyz_not_a_key") is False


def test_press_key_case_insensitive():
    # lookup should be lowercase
    assert VK_MAP.get("a") == VK_MAP.get("A".lower())
```

- [ ] **Step 2: Запустить тесты — убедиться что FAIL**

```
pytest tests/core/test_input_sender.py -v
```

- [ ] **Step 3: Написать `app/core/input_sender.py`**

```python
# app/core/input_sender.py
import win32api
import win32con

VK_MAP: dict[str, int] = {
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
    "z": 0x5A,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "space": 0x20, "enter": 0x0D, "escape": 0x1B,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38,
}


def press_key(hwnd: int, key: str) -> bool:
    """Send WM_KEYDOWN + WM_KEYUP to hwnd. No window focus required."""
    vk = VK_MAP.get(key.lower())
    if not vk or not hwnd:
        return False
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP,   vk, 0)
    return True
```

- [ ] **Step 4: Написать `app/core/window_manager.py`**

```python
# app/core/window_manager.py
from __future__ import annotations
from dataclasses import dataclass
import win32gui
import win32con


@dataclass
class WindowRect:
    left: int
    top: int
    width: int
    height: int


class WindowManager:
    def __init__(self):
        self._game_hwnd: int | None = None

    def find_game(self, title_contains: str) -> bool:
        """Scan all visible windows for one whose title contains title_contains."""
        result: int | None = None

        def callback(hwnd: int, _):
            nonlocal result
            if win32gui.IsWindowVisible(hwnd):
                if title_contains in win32gui.GetWindowText(hwnd):
                    result = hwnd

        win32gui.EnumWindows(callback, None)
        self._game_hwnd = result
        return result is not None

    def get_game_hwnd(self) -> int | None:
        return self._game_hwnd

    def attach_overlay(self, overlay_hwnd: int, x: int, y: int, w: int, h: int):
        """Make overlay_hwnd a WS_CHILD of the game window at position (x, y)."""
        if not self._game_hwnd:
            return
        win32gui.SetParent(overlay_hwnd, self._game_hwnd)
        style = win32gui.GetWindowLong(overlay_hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_STYLE, style | win32con.WS_CHILD)
        self.move_window(overlay_hwnd, x, y, w, h)

    def attach_child(self, child_hwnd: int):
        """Make child_hwnd a WS_CHILD of the game window, keep current position."""
        if not self._game_hwnd:
            return
        win32gui.SetParent(child_hwnd, self._game_hwnd)
        style = win32gui.GetWindowLong(child_hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(child_hwnd, win32con.GWL_STYLE, style | win32con.WS_CHILD)
        win32gui.SetWindowPos(
            child_hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )

    def move_window(self, hwnd: int, x: int, y: int, w: int, h: int):
        """Move hwnd to (x, y) clamped to game client area."""
        if not self._game_hwnd or not hwnd:
            return
        x, y = self._clamp(x, y, w, h)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, x, y, w, h, win32con.SWP_SHOWWINDOW
        )

    def _clamp(self, x: int, y: int, w: int, h: int) -> tuple[int, int]:
        if not self._game_hwnd:
            return x, y
        l, t, r, b = win32gui.GetClientRect(self._game_hwnd)
        gw, gh = r - l, b - t
        return max(0, min(x, gw - w)), max(0, min(y, gh - h))

    def get_game_rect(self) -> WindowRect | None:
        if not self._game_hwnd:
            return None
        l, t, r, b = win32gui.GetClientRect(self._game_hwnd)
        return WindowRect(l, t, r - l, b - t)
```

- [ ] **Step 5: Написать `app/core/capture.py`**

```python
# app/core/capture.py
from __future__ import annotations
from threading import Lock
import mss
import numpy as np
import cv2


class ScreenCapture:
    """Thread-safe mss singleton. One instance for the entire app lifetime."""

    _instance: ScreenCapture | None = None
    _init_lock = Lock()

    @classmethod
    def get(cls) -> ScreenCapture:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    obj = cls.__new__(cls)
                    obj._sct = mss.mss()
                    obj._mutex = Lock()
                    cls._instance = obj
        return cls._instance

    def grab(self, region: dict) -> np.ndarray:
        """Capture region and return BGR numpy array."""
        with self._mutex:
            raw = self._sct.grab(region)
        img = np.array(raw)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
```

- [ ] **Step 6: Запустить тесты — убедиться что PASS**

```
pytest tests/core/ -v
```

Ожидаемо: 9 PASSED (5 из task 1 + 4 из task 2).

- [ ] **Step 7: Коммит**

```bash
git add app/core/input_sender.py app/core/window_manager.py app/core/capture.py tests/core/test_input_sender.py
git commit -m "feat: add core layer — InputSender (PostMessage), WindowManager, ScreenCapture singleton"
```

---

### Task 3: Nothing design system — тема и базовые виджеты

**Files:**
- Create: `app/ui/__init__.py` (пустой)
- Create: `app/ui/widgets/__init__.py` (пустой)
- Create: `app/ui/theme.py`
- Create: `app/ui/widgets/nt_button.py`
- Create: `app/ui/widgets/nt_panel.py`
- Create: `app/ui/widgets/nt_status_dot.py`
- Create: `app/ui/widgets/log_panel.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач
- Produces:
  - `theme.get_mono_font(size, bold) -> QFont`
  - `NtPanel(parent)` — QWidget с dot-grid текстурой
  - `NtButton(text, parent)` с методом `set_active(bool)`
  - `NtStatusDot(parent)` с методами `set_offline()`, `set_running()`, `set_error()`
  - `LogPanel(parent)` с методами `add_log(msg, level)`, `clear_logs()`

Тесты для UI виджетов в рамках этой задачи — ручная верификация (запуск preview скрипта). Автоматические тесты Qt виджетов требуют `QApplication` и увеличивают сложность без пропорциональной пользы.

- [ ] **Step 1: Написать `app/ui/theme.py`**

```python
# app/ui/theme.py
from PySide6.QtGui import QColor, QFont, QFontDatabase

# --- Backgrounds ---
BG_BASE     = "#080808"
BG_SURFACE  = "#111111"
BG_ELEVATED = "#1a1a1a"

# --- Borders ---
BORDER     = "#222222"
BORDER_DIM = "#161616"

# --- Text ---
TEXT_PRIMARY   = "#ffffff"
TEXT_SECONDARY = "#555555"
TEXT_DIM       = "#2a2a2a"

# --- Accents ---
ACCENT_RED   = "#ff3b3b"
ACCENT_WHITE = "#ffffff"
ACCENT_GREEN = "#39ff84"

# --- Dot grid ---
DOT_COLOR   = "#1a1a1a"
DOT_SPACING = 8
DOT_RADIUS  = 1

# --- Geometry ---
RADIUS   = 0
PADDING  = 12
SPACING  = 6

# --- Typography ---
FONT_MONO          = "JetBrains Mono"
FONT_MONO_FALLBACK = "Courier New"
FONT_SIZE_S = 11
FONT_SIZE_M = 13
FONT_SIZE_L = 16


def get_mono_font(size: int = FONT_SIZE_M, bold: bool = False) -> QFont:
    families = QFontDatabase.families()
    name = FONT_MONO if FONT_MONO in families else FONT_MONO_FALLBACK
    font = QFont(name, size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
    return font
```

- [ ] **Step 2: Написать `app/ui/widgets/nt_panel.py`**

```python
# app/ui/widgets/nt_panel.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt
from app.ui import theme


class NtPanel(QWidget):
    """Base panel with dot-grid texture and border. Use as background widget."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # 1. Solid background
        painter.fillRect(self.rect(), QColor(theme.BG_SURFACE))

        # 2. Dot grid overlay
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.DOT_COLOR))
        sp = theme.DOT_SPACING
        r  = theme.DOT_RADIUS
        w, h = self.width(), self.height()
        for x in range(sp, w - sp, sp):
            for y in range(sp, h - sp, sp):
                painter.drawEllipse(x - r, y - r, r * 2, r * 2)

        # 3. 1px border
        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        painter.end()
```

- [ ] **Step 3: Написать `app/ui/widgets/nt_button.py`**

```python
# app/ui/widgets/nt_button.py
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt, QRect
from app.ui import theme


class NtButton(QPushButton):
    """Nothing-styled flat button. Call set_active(True) for red accent state."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._active = False
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        # Transparent base so our paintEvent controls everything
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()

        # Background
        if self._active:
            bg = QColor("#180000")
        elif self.underMouse():
            bg = QColor("#242424")
        else:
            bg = QColor(theme.BG_ELEVATED)
        painter.fillRect(rect, bg)

        # Active: 3px red left bar
        if self._active:
            painter.fillRect(QRect(0, 0, 3, rect.height()), QColor(theme.ACCENT_RED))

        # Text
        text_color = QColor(theme.ACCENT_RED) if self._active else QColor(theme.TEXT_PRIMARY)
        painter.setPen(text_color)
        painter.setFont(self.font())
        text_rect = rect.adjusted(14 if self._active else 10, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text().upper())

        # 1px border
        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        painter.end()
```

- [ ] **Step 4: Написать `app/ui/widgets/nt_status_dot.py`**

```python
# app/ui/widgets/nt_status_dot.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt
from app.ui import theme

_OFFLINE = "#2a2a2a"
_RUNNING = theme.ACCENT_GREEN
_ERROR   = theme.ACCENT_RED


class NtStatusDot(QWidget):
    """8×8 colored status indicator dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = _OFFLINE
        self.setFixedSize(8, 8)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_offline(self): self._set(_OFFLINE)
    def set_running(self): self._set(_RUNNING)
    def set_error(self):   self._set(_ERROR)

    def _set(self, color: str):
        self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(0, 0, 8, 8)
        painter.end()
```

- [ ] **Step 5: Написать `app/ui/widgets/log_panel.py`**

```python
# app/ui/widgets/log_panel.py
from datetime import datetime
from PySide6.QtWidgets import QTextEdit
from app.ui import theme


class LogPanel(QTextEdit):
    """Scrolling log area in Nothing style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {theme.BG_BASE};
                color: {theme.TEXT_SECONDARY};
                border: 1px solid {theme.BORDER};
                padding: 6px;
                selection-background-color: #2a2a2a;
            }}
            QScrollBar:vertical {{
                background: {theme.BG_BASE};
                width: 4px;
                border: none;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {theme.BORDER};
                min-height: 16px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

    def add_log(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        if level == "error":
            msg_color = theme.ACCENT_RED
        elif level == "success":
            msg_color = theme.ACCENT_GREEN
        else:
            msg_color = theme.TEXT_SECONDARY
        html = (
            f'<span style="color:{theme.TEXT_DIM}">[{ts}]</span> '
            f'<span style="color:{msg_color}">{message}</span>'
        )
        self.append(html)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear_logs(self):
        self.clear()
```

- [ ] **Step 6: Визуальная проверка — запустить preview скрипт**

Создай временный файл `preview_widgets.py` в корне (удалишь в Task 9):

```python
# preview_widgets.py  (временный файл для ручной проверки виджетов)
import sys
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_panel import LogPanel

app = QApplication(sys.argv)
win = QWidget()
win.resize(260, 400)
win.setWindowTitle("Widget Preview")

panel = NtPanel(win)
panel.setGeometry(0, 0, 260, 400)

layout = QVBoxLayout(panel)

layout.addWidget(NtButton("START BOT"))
b = NtButton("ACTIVE BUTTON")
b.set_active(True)
layout.addWidget(b)

dot_row = QWidget()
from PySide6.QtWidgets import QHBoxLayout
row = QHBoxLayout(dot_row)
for state in ["offline", "running", "error"]:
    dot = NtStatusDot()
    if state == "running": dot.set_running()
    elif state == "error": dot.set_error()
    row.addWidget(dot)
layout.addWidget(dot_row)

log = LogPanel()
log.add_log("bot started", "success")
log.add_log("pressed D")
log.add_log("game window not found", "error")
layout.addWidget(log)

win.show()
sys.exit(app.exec())
```

Запусти: `python preview_widgets.py`

Ожидаемо: тёмное окно, dot-grid текстура, кнопки в Nothing-стиле, красная левая полоска на активной кнопке, цветной лог.

- [ ] **Step 7: Коммит**

```bash
git add app/ui/ 
git commit -m "feat: add Nothing design system — theme tokens and base widgets (NtPanel, NtButton, NtStatusDot, LogPanel)"
```

---

### Task 4: ModuleBase + ModuleWindow + module_registry

**Files:**
- Create: `modules/base.py`
- Create: `app/ui/module_window.py`
- Create: `app/module_registry.py`
- Create: `modules/ava_dancers/__init__.py` (заглушка — заполнится в Task 7)

**Interfaces:**
- Consumes:
  - `ConfigManager` из `app.core.config`
  - `WindowManager.move_window(hwnd, x, y, w, h)` из `app.core.window_manager`
- Produces:
  - `ModuleBase` — ABC с атрибутами `name`, `description`, `icon`, `config_key` и методом `create_window(...) -> ModuleWindow`
  - `ModuleWindow(module_name, config, save_fn, parent_overlay)` — QWidget с drag, position save/restore, close
  - `MODULES: list[type[ModuleBase]]` из `app.module_registry`

- [ ] **Step 1: Написать `modules/base.py`**

```python
# modules/base.py
from __future__ import annotations
from abc import ABC, abstractmethod


class ModuleBase(ABC):
    name: str        = "Unnamed"
    description: str = ""
    icon: str        = "◈"
    config_key: str  = ""   # имя поля в AppConfig, e.g. "ava_dancers"

    @abstractmethod
    def create_window(self, config, save_fn, window_manager, parent_overlay=None):
        """Instantiate and return the module's control window (ModuleWindow subclass)."""
        ...
```

- [ ] **Step 2: Написать `app/ui/module_window.py`**

```python
# app/ui/module_window.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPoint


class ModuleWindow(QWidget):
    """Base class for all module windows.
    Subclasses get: drag behavior, position persistence, close notification.
    """

    def __init__(self, module_name: str, config, save_fn, parent_overlay=None):
        super().__init__()
        self.module_name   = module_name
        self.config        = config
        self.save_fn       = save_fn
        self.parent_overlay = parent_overlay
        self._drag_pos     = QPoint()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def restore_position(self):
        """Move window to saved position if available."""
        if getattr(self.config, "position_saved", False):
            self.move(self.config.x, self.config.y)

    def save_position(self, x: int, y: int):
        self.config.x = x
        self.config.y = y
        self.config.position_saved = True
        self.save_fn()

    def start_drag(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def do_drag(self, event, window_manager=None):
        if not (event.buttons() & Qt.LeftButton):
            return
        new_pos = event.globalPosition().toPoint() - self._drag_pos
        x, y = new_pos.x(), new_pos.y()
        if window_manager:
            window_manager.move_window(int(self.winId()), x, y, self.width(), self.height())
        else:
            self.move(x, y)
        self.save_position(x, y)

    def closeEvent(self, event):
        if self.parent_overlay:
            self.parent_overlay.on_module_closed(self.module_name)
        event.accept()
```

- [ ] **Step 3: Создать заглушку `modules/ava_dancers/__init__.py`**

```python
# modules/ava_dancers/__init__.py
# Populated in Task 7
```

- [ ] **Step 4: Написать `app/module_registry.py`**

```python
# app/module_registry.py
# Static module registration. To add a new module:
#   1. Create modules/<name>/ with __init__.py exporting a ModuleBase subclass
#   2. Import it here and add to MODULES list

# from modules.ava_dancers import AvaDancersModule   # uncomment after Task 7

MODULES = [
    # AvaDancersModule,   # uncomment after Task 7
]
```

- [ ] **Step 5: Коммит**

```bash
git add modules/base.py app/ui/module_window.py app/module_registry.py modules/ava_dancers/__init__.py
git commit -m "feat: add ModuleBase ABC, ModuleWindow base class, empty module registry"
```

---

### Task 5: Overlay window

**Files:**
- Create: `app/ui/overlay.py`

**Interfaces:**
- Consumes:
  - `NtPanel`, `NtButton`, `LogPanel` из `app.ui.widgets.*`
  - `theme.get_mono_font`, `theme.PADDING`, `theme.BORDER`, `theme.BORDER_DIM`, `theme.TEXT_SECONDARY`, `theme.TEXT_DIM` из `app.ui.theme`
  - `MODULES` из `app.module_registry`
  - `WindowManager` из `app.core.window_manager`
  - `ConfigManager` из `app.core.config`
  - `ModuleBase.create_window(config, save_fn, window_manager, parent_overlay)` из `modules.base`
- Produces:
  - `Overlay(config: ConfigManager, window_manager: WindowManager)` — главное окно
  - `Overlay.WIDTH = 220`, `Overlay.HEIGHT = 420`
  - `Overlay.add_log(message, level="info")`
  - `Overlay.on_module_closed(module_name: str)`
  - `Overlay.restore_favorite_windows()`

- [ ] **Step 1: Написать `app/ui/overlay.py`**

```python
# app/ui/overlay.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication
from PySide6.QtCore import Qt, QPoint

from app.ui import theme
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.log_panel import LogPanel
from app.module_registry import MODULES


class Overlay(QWidget):
    WIDTH  = 220
    HEIGHT = 420

    def __init__(self, config, window_manager, parent=None):
        super().__init__(parent)
        self.config = config
        self.wm = window_manager
        self._drag_pos = QPoint()
        self._open_windows: dict[str, QWidget] = {}

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.WIDTH, self.HEIGHT)
        self._build_ui()

    def _build_ui(self):
        panel = NtPanel(self)
        panel.setGeometry(0, 0, self.WIDTH, self.HEIGHT)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("AVATARIA HELPER")
        title.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        close_btn = NtButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Separator ───────────────────────────────────────────────────────
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # ── Module buttons ──────────────────────────────────────────────────
        self._module_buttons: dict[str, NtButton] = {}
        for module_cls in MODULES:
            btn = NtButton(f"{module_cls.icon}  {module_cls.name}")
            btn.clicked.connect(lambda _, m=module_cls: self._toggle_module(m))
            self._module_buttons[module_cls.name] = btn
            layout.addWidget(btn)

        layout.addSpacing(8)

        # ── Log section ─────────────────────────────────────────────────────
        log_label = QLabel("LOG")
        log_label.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
        layout.addWidget(log_label)

        self.log_panel = LogPanel()
        self.log_panel.setFixedHeight(110)
        layout.addWidget(self.log_panel)

        clear_btn = NtButton("CLEAR")
        clear_btn.setMinimumHeight(26)
        clear_btn.clicked.connect(self.log_panel.clear_logs)
        layout.addWidget(clear_btn)

        layout.addStretch()

        # ── Drag bar ────────────────────────────────────────────────────────
        drag = QLabel()
        drag.setFixedHeight(4)
        drag.setStyleSheet(f"background:{theme.BORDER_DIM};")
        drag.mousePressEvent = self._drag_press
        drag.mouseMoveEvent  = self._drag_move
        layout.addWidget(drag)

    # ── Module management ────────────────────────────────────────────────────

    def _toggle_module(self, module_cls):
        name = module_cls.name
        if name in self._open_windows and self._open_windows[name].isVisible():
            self._open_windows[name].close()
            return

        module      = module_cls()
        config_sect = getattr(self.config.data, module_cls.config_key, None)
        window      = module.create_window(
            config        = config_sect,
            save_fn       = self.config.save,
            window_manager= self.wm,
            parent_overlay= self,
        )
        self._open_windows[name] = window
        window.show()

        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))

        self.add_log(f"{name} opened")
        if name in self._module_buttons:
            self._module_buttons[name].set_active(True)

    def on_module_closed(self, module_name: str):
        self._open_windows.pop(module_name, None)
        if module_name in self._module_buttons:
            self._module_buttons[module_name].set_active(False)
        self.add_log(f"{module_name} closed")

    def add_log(self, message: str, level: str = "info"):
        self.log_panel.add_log(message, level)

    def restore_favorite_windows(self):
        for module_cls in MODULES:
            config_sect = getattr(self.config.data, module_cls.config_key, None)
            if config_sect and getattr(config_sect, "favorite", False):
                self._toggle_module(module_cls)

    # ── Drag ────────────────────────────────────────────────────────────────

    def _drag_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def _drag_move(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        pos = event.globalPosition().toPoint() - self._drag_pos
        self.wm.move_window(int(self.winId()), pos.x(), pos.y(), self.WIDTH, self.HEIGHT)
        self.config.data.overlay.x = pos.x()
        self.config.data.overlay.y = pos.y()
        self.config.save()

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        for w in list(self._open_windows.values()):
            if w.isVisible():
                w.close()
        QApplication.quit()
        event.accept()
```

- [ ] **Step 2: Визуальная проверка**

Создай временный `preview_overlay.py`:

```python
# preview_overlay.py  (временный)
import sys
from PySide6.QtWidgets import QApplication
from app.core.config import ConfigManager
from app.core.window_manager import WindowManager
from app.ui.overlay import Overlay

app = QApplication(sys.argv)
cfg = ConfigManager()
wm  = WindowManager()
overlay = Overlay(cfg, wm)
overlay.show()
sys.exit(app.exec())
```

Запусти: `python preview_overlay.py`

Ожидаемо: тёмное overlay окно с заголовком, разделителем, пустым местом для кнопок модулей, секцией LOG.

- [ ] **Step 3: Коммит**

```bash
git add app/ui/overlay.py
git commit -m "feat: add Overlay window with Nothing design, module button slots, log panel"
```

---

### Task 6: AvaBot — детекция и QThread

**Files:**
- Create: `modules/ava_dancers/bot.py`
- Create: `tests/modules/__init__.py`
- Create: `tests/modules/test_ava_bot.py`

**Interfaces:**
- Consumes:
  - `ScreenCapture.get().grab(region)` из `app.core.capture`
  - `press_key(hwnd, key)` из `app.core.input_sender`
- Produces:
  - Модульные функции (доступны для тестирования и для окна):
    - `split_tiles(img: np.ndarray) -> list[np.ndarray]`
    - `detect_tile(tile: np.ndarray, min_active: int) -> tuple[int, bool]`
    - `is_fake_tile(thresh: np.ndarray) -> bool`
  - `AvaBot(QThread)` с:
    - Сигналы: `tile_detected(int, str)`, `key_pressed(str)`, `stats_updated(dict)`, `error(str)`
    - Методы: `__init__(game_hwnd, min_active)`, `stop_bot()`, `configure(min_active)`
    - `run()` — главный цикл

- [ ] **Step 1: Написать тесты для функций детекции**

```python
# tests/modules/test_ava_bot.py
import numpy as np
import pytest
from modules.ava_dancers.bot import split_tiles, detect_tile, is_fake_tile


def test_split_tiles_returns_four():
    img = np.zeros((150, 700, 3), dtype=np.uint8)
    tiles = split_tiles(img)
    assert len(tiles) == 4


def test_split_tiles_each_is_square():
    img = np.zeros((150, 700, 3), dtype=np.uint8)
    tiles = split_tiles(img)
    for tile in tiles:
        h, w = tile.shape[:2]
        assert h == w


def test_detect_empty_tile():
    # Pure black → 0 white pixels → not active
    tile = np.zeros((150, 150, 3), dtype=np.uint8)
    white, active = detect_tile(tile, min_active=3500)
    assert white == 0
    assert active is False


def test_detect_bright_tile_active():
    # Pure white → many white pixels → active
    tile = np.ones((150, 150, 3), dtype=np.uint8) * 255
    white, active = detect_tile(tile, min_active=3500)
    assert white > 3500
    assert active is True


def test_detect_tile_skips_top_75px():
    # White only in top 75px → should be ignored → not active
    tile = np.zeros((150, 150, 3), dtype=np.uint8)
    tile[:75, :] = 255
    white, active = detect_tile(tile, min_active=3500)
    assert active is False


def test_is_fake_centered_white_not_fake():
    thresh = np.zeros((75, 150), dtype=np.uint8)
    thresh[30:45, 60:90] = 255  # center
    assert is_fake_tile(thresh) is False


def test_is_fake_bottom_right_corner_is_fake():
    thresh = np.zeros((75, 150), dtype=np.uint8)
    thresh[65:75, 130:150] = 255  # bottom-right → offset_x > 15, offset_y > 8
    assert is_fake_tile(thresh) is True


def test_is_fake_empty_thresh_not_fake():
    thresh = np.zeros((75, 150), dtype=np.uint8)
    assert is_fake_tile(thresh) is False
```

- [ ] **Step 2: Запустить тесты — убедиться что FAIL**

```
pytest tests/modules/test_ava_bot.py -v
```

- [ ] **Step 3: Написать `modules/ava_dancers/bot.py`**

```python
# modules/ava_dancers/bot.py
from __future__ import annotations
import time
import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.capture import ScreenCapture
from app.core.input_sender import press_key

# Region containing all 4 dance tiles
TILE_REGION: dict = {"left": 930, "top": 1050, "width": 700, "height": 150}

# Detection parameters
_TILE_CROP    = 150   # crop each tile to this square
_SKIP_TOP     = 75    # ignore top N pixels (UI chrome)
_GRAY_THRESH  = 40    # grayscale threshold for "white"

# Tile index → keyboard key
KEY_MAP: dict[int, str] = {0: "a", 1: "s", 2: "w", 3: "d"}


# ── Pure image processing functions (no Qt, no win32) ──────────────────────

def split_tiles(img: np.ndarray) -> list[np.ndarray]:
    """Split a 4-tile row image into 4 equal cropped squares."""
    h, w = img.shape[:2]
    tw = w // 4
    tiles = []
    for i in range(4):
        col = img[:, i * tw:(i + 1) * tw]
        ch, cw = col.shape[:2]
        cs = min(_TILE_CROP, ch, cw)
        cx, cy = cw // 2, ch // 2
        tile = col[cy - cs // 2:cy + cs // 2, cx - cs // 2:cx + cs // 2]
        tiles.append(tile)
    return tiles


def detect_tile(tile: np.ndarray, min_active: int) -> tuple[int, bool]:
    """Return (white_pixel_count, is_active). Ignores top _SKIP_TOP rows."""
    gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
    gray = gray[_SKIP_TOP:, :]
    _, thresh = cv2.threshold(gray, _GRAY_THRESH, 255, cv2.THRESH_BINARY)
    white = int(np.sum(thresh == 255))
    if white < min_active:
        return white, False
    if is_fake_tile(thresh):
        return white, False
    return white, True


def is_fake_tile(thresh: np.ndarray) -> bool:
    """Return True if white mass is offset bottom-right (false positive indicator)."""
    ys, xs = np.where(thresh == 255)
    if len(xs) == 0:
        return False
    h, w = thresh.shape
    off_x = float(np.mean(xs)) - w / 2
    off_y = float(np.mean(ys)) - h / 2
    return off_x > 15 and off_y > 8


# ── QThread bot ─────────────────────────────────────────────────────────────

class AvaBot(QThread):
    tile_detected = Signal(int, str)   # tile_id, key
    key_pressed   = Signal(str)        # key name
    stats_updated = Signal(dict)       # {tile_id: white_pixel_count}
    error         = Signal(str)

    def __init__(self, game_hwnd: int, min_active: int = 3500):
        super().__init__()
        self._hwnd       = game_hwnd
        self._min_active = min_active
        self._running    = False
        self._prev_tiles: set[int]        = set()
        self._last_time:  dict[int, float] = {}
        self._cooldown   = 0.15

    def configure(self, min_active: int):
        self._min_active = min_active

    def stop_bot(self):
        self._running = False

    def run(self):
        self._running = True
        self._prev_tiles.clear()
        capture = ScreenCapture.get()

        while self._running:
            try:
                img    = capture.grab(TILE_REGION)
                tiles  = split_tiles(img)
                stats  = {}
                active = []

                for i, tile in enumerate(tiles):
                    white, is_active = detect_tile(tile, self._min_active)
                    stats[i] = white
                    if is_active:
                        active.append(i)

                self.stats_updated.emit(stats)

                for tile_id in set(active) - self._prev_tiles:
                    self._try_press(tile_id)

                self._prev_tiles = set(active)

            except Exception as exc:
                self.error.emit(str(exc))

            time.sleep(0.02)

    def _try_press(self, tile_id: int):
        now  = time.time()
        last = self._last_time.get(tile_id, 0.0)
        if now - last < self._cooldown:
            return
        key = KEY_MAP.get(tile_id)
        if not key:
            return
        self.tile_detected.emit(tile_id, key)
        if press_key(self._hwnd, key):
            self.key_pressed.emit(key)
            self._last_time[tile_id] = now
```

- [ ] **Step 4: Запустить тесты — убедиться что PASS**

```
pytest tests/modules/test_ava_bot.py -v
```

Ожидаемо: 8 PASSED.

- [ ] **Step 5: Запустить все тесты**

```
pytest tests/ -v
```

Ожидаемо: 17 PASSED (5 + 4 + 8).

- [ ] **Step 6: Коммит**

```bash
git add modules/ava_dancers/bot.py tests/modules/__init__.py tests/modules/test_ava_bot.py
git commit -m "feat: add AvaBot QThread with split_tiles/detect_tile/is_fake_tile as testable pure functions"
```

---

### Task 7: AvaDancers window + финальная регистрация модуля

**Files:**
- Modify: `modules/ava_dancers/__init__.py` (заглушка → реальный модуль)
- Create: `modules/ava_dancers/window.py`
- Modify: `app/module_registry.py` (раскомментировать AvaDancersModule)

**Interfaces:**
- Consumes:
  - `ModuleWindow(module_name, config, save_fn, parent_overlay)` из `app.ui.module_window`
  - `NtPanel`, `NtButton`, `NtStatusDot`, `LogPanel` из `app.ui.widgets.*`
  - `theme.*` из `app.ui.theme`
  - `AvaBot(game_hwnd, min_active)` из `modules.ava_dancers.bot`
  - `split_tiles`, `detect_tile` из `modules.ava_dancers.bot`
  - `ScreenCapture.get().grab(region)` из `app.core.capture`
  - `WindowManager` из `app.core.window_manager`
- Produces:
  - `AvaDancersModule` в `modules/ava_dancers/__init__.py`
  - `AvaDancersWindow(config, save_fn, window_manager, parent_overlay)` в `modules/ava_dancers/window.py`

- [ ] **Step 1: Написать `modules/ava_dancers/window.py`**

```python
# modules/ava_dancers/window.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer

from app.ui.module_window import ModuleWindow
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_panel import LogPanel
from app.ui import theme
from modules.ava_dancers.bot import AvaBot, split_tiles, detect_tile, TILE_REGION

WIDTH  = 280
HEIGHT = 330

_KEY_LABELS = ["A", "S", "W", "D"]


class AvaDancersWindow(ModuleWindow):

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("AvaDancers", config, save_fn, parent_overlay)
        self._wm  = window_manager
        self._bot: AvaBot | None = None
        self.resize(WIDTH, HEIGHT)
        self._build_ui()
        self.restore_position()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        panel = NtPanel(self)
        panel.setGeometry(0, 0, WIDTH, HEIGHT)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # Header
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        title = QLabel("AVADANCERS")
        title.setFont(theme.get_mono_font(theme.FONT_SIZE_M, bold=True))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        self._fav_btn = NtButton("★" if self.config.favorite else "☆")
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)
        close_btn = NtButton("×")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._fav_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # Start / stop
        self._start_btn = NtButton("▶  START BOT")
        self._start_btn.clicked.connect(self._toggle_bot)
        layout.addWidget(self._start_btn)

        # Test detection
        test_btn = NtButton("◎  TEST DETECTION")
        test_btn.clicked.connect(self._test_detection)
        layout.addWidget(test_btn)

        # Tile status row
        tiles_row = QHBoxLayout()
        self._tile_dots: list[NtStatusDot] = []
        for label in _KEY_LABELS:
            col = QVBoxLayout()
            dot = NtStatusDot()
            lbl = QLabel(label)
            lbl.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
            lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
            lbl.setAlignment(Qt.AlignCenter)
            col.addWidget(dot, 0, Qt.AlignCenter)
            col.addWidget(lbl, 0, Qt.AlignCenter)
            self._tile_dots.append(dot)
            tiles_row.addLayout(col)
        layout.addLayout(tiles_row)

        layout.addStretch()

        # Log
        self._log = LogPanel()
        self._log.setFixedHeight(80)
        layout.addWidget(self._log)

        # Drag bar
        drag = QLabel()
        drag.setFixedHeight(4)
        drag.setStyleSheet(f"background:{theme.BORDER_DIM};")
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    # ── Bot control ──────────────────────────────────────────────────────────

    def _toggle_bot(self):
        if self._bot and self._bot.isRunning():
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("game window not found", level="error")
            return

        self._bot = AvaBot(hwnd, self.config.min_active)
        self._bot.key_pressed.connect(lambda k: self._log.add_log(f"pressed {k.upper()}"))
        self._bot.tile_detected.connect(self._on_tile_detected)
        self._bot.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._bot.start()

        self._start_btn.set_active(True)
        self._start_btn.setText("■  STOP BOT")
        self._status_dot.set_running()
        self._log.add_log("bot started", level="success")
        if self.parent_overlay:
            self.parent_overlay.add_log("AvaDancers: bot started")

    def _stop_bot(self):
        if self._bot:
            self._bot.stop_bot()
            self._bot.wait(600)
            self._bot = None

        self._start_btn.set_active(False)
        self._start_btn.setText("▶  START BOT")
        self._status_dot.set_offline()
        for dot in self._tile_dots:
            dot.set_offline()
        self._log.add_log("bot stopped")
        if self.parent_overlay:
            self.parent_overlay.add_log("AvaDancers: bot stopped")

    def _on_tile_detected(self, tile_id: int, key: str):
        if 0 <= tile_id < 4:
            dot = self._tile_dots[tile_id]
            dot.set_running()
            QTimer.singleShot(180, dot.set_offline)

    # ── Test detection ───────────────────────────────────────────────────────

    def _test_detection(self):
        from app.core.capture import ScreenCapture
        try:
            img   = ScreenCapture.get().grab(TILE_REGION)
            tiles = split_tiles(img)
            for i, tile in enumerate(tiles):
                white, active = detect_tile(tile, self.config.min_active)
                state = "ACTIVE" if active else "empty"
                self._log.add_log(f"{_KEY_LABELS[i]}: {white}px — {state}")
        except Exception as e:
            self._log.add_log(str(e), level="error")

    # ── Favorite ─────────────────────────────────────────────────────────────

    def _toggle_favorite(self):
        self.config.favorite = not self.config.favorite
        self._fav_btn.setText("★" if self.config.favorite else "☆")
        self.save_fn()

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._bot and self._bot.isRunning():
            self._bot.stop_bot()
            self._bot.wait(600)
        super().closeEvent(event)
```

- [ ] **Step 2: Обновить `modules/ava_dancers/__init__.py`**

```python
# modules/ava_dancers/__init__.py
from modules.base import ModuleBase


class AvaDancersModule(ModuleBase):
    name       = "AvaDancers"
    description = "Dance mini-game automation"
    icon       = "◈"
    config_key = "ava_dancers"   # matches AppConfig.ava_dancers field

    def create_window(self, config, save_fn, window_manager, parent_overlay=None):
        from modules.ava_dancers.window import AvaDancersWindow
        return AvaDancersWindow(config, save_fn, window_manager, parent_overlay)
```

- [ ] **Step 3: Обновить `app/module_registry.py` — раскомментировать AvaDancersModule**

```python
# app/module_registry.py
from modules.ava_dancers import AvaDancersModule

MODULES = [
    AvaDancersModule,
    # Add new modules here — one line each
]
```

- [ ] **Step 4: Обновить `preview_overlay.py` и проверить что кнопка AvaDancers появилась**

Запусти `python preview_overlay.py` — в overlay должна появиться кнопка `◈  AVADANCERS`.

- [ ] **Step 5: Коммит**

```bash
git add modules/ava_dancers/ app/module_registry.py
git commit -m "feat: add AvaDancersModule — window with NtButton/NtStatusDot, AvaBot wired via Qt signals"
```

---

### Task 8: Переписать main.py

**Files:**
- Modify: `main.py`

**Interfaces:**
- Consumes:
  - `ConfigManager()` из `app.core.config`
  - `WindowManager()` из `app.core.window_manager`
  - `Overlay(config, wm)` из `app.ui.overlay`
  - `Overlay.WIDTH`, `Overlay.HEIGHT`
  - `Overlay.restore_favorite_windows()`

- [ ] **Step 1: Переписать `main.py`**

```python
# main.py
import os
import sys
import signal

os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.*=false"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.core.window_manager import WindowManager
from app.ui.overlay import Overlay


def main():
    app = QApplication(sys.argv)

    config = ConfigManager()
    wm     = WindowManager()

    overlay = Overlay(config, wm)

    def _exit(sig, frame):
        print("Shutting down...")
        overlay.close()

    signal.signal(signal.SIGINT, _exit)

    # Keep Python signal handling alive inside Qt event loop
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(500)

    found = wm.find_game("Аватария")
    if found:
        print("Аватария found")
        wm.attach_overlay(
            int(overlay.winId()),
            config.data.overlay.x,
            config.data.overlay.y,
            Overlay.WIDTH,
            Overlay.HEIGHT,
        )
        overlay.restore_favorite_windows()
    else:
        print("Аватария not found — running standalone")

    overlay.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Запустить приложение и проверить**

```
python main.py
```

Ожидаемо:
- Если Аватария открыта: overlay появляется внутри игрового окна
- Если нет: overlay появляется как отдельное окно
- Кнопка `◈  AVADANCERS` присутствует
- Клик по кнопке открывает окно модуля
- START BOT запускает бот (логи в log_panel)
- TEST DETECTION показывает white px count в логе

- [ ] **Step 3: Коммит**

```bash
git add main.py
git commit -m "feat: rewrite main.py — ConfigManager + WindowManager injected into Overlay"
```

---

### Task 9: Очистка — удаление старых файлов

**Files:**
- Delete: `config.py`, `overlay.py`, `ava_dancers.py`, `window_bind.py`, `logger.py`, `circle_button.py`, `test.py`
- Delete: `dancebot/dance_bot.py`, `dancebot/` (папку если пустая)
- Delete: `preview_widgets.py`, `preview_overlay.py` (временные файлы из Tasks 3/5)
- Возможные артефакты: `mss_test.png`, `gray_*.png`, `hsv_*.png`, `threshold_*.png`

- [ ] **Step 1: Проверить что ничего из старых файлов не импортируется**

```bash
grep -r "from config import\|import config\|from overlay import\|from ava_dancers import\|from window_bind import\|from logger import\|from circle_button import\|from dancebot" --include="*.py" .
```

Ожидаемо: вывод пустой (0 совпадений). Если есть — исправить импорты.

- [ ] **Step 2: Удалить старые файлы**

```bash
python -c "
import os, shutil
to_delete = [
    'config.py', 'overlay.py', 'ava_dancers.py', 'window_bind.py',
    'logger.py', 'circle_button.py', 'test.py',
    'preview_widgets.py', 'preview_overlay.py',
]
for f in to_delete:
    if os.path.exists(f):
        os.remove(f)
        print(f'deleted {f}')

if os.path.isdir('dancebot'):
    shutil.rmtree('dancebot')
    print('deleted dancebot/')

for f in os.listdir('.'):
    if f.endswith('.png'):
        os.remove(f)
        print(f'deleted {f}')
"
```

- [ ] **Step 3: Финальный прогон всех тестов**

```
pytest tests/ -v
```

Ожидаемо: 17 PASSED, 0 FAILED.

- [ ] **Step 4: Запустить приложение последний раз**

```
python main.py
```

Убедиться что всё работает: overlay, открытие AvaDancers, TEST DETECTION, START BOT.

- [ ] **Step 5: Финальный коммит**

```bash
git add -A
git commit -m "chore: remove legacy files — config.py, overlay.py, ava_dancers.py, window_bind.py, logger.py, circle_button.py, dancebot/"
```

---

## Self-Review

### 1. Spec coverage

| Требование из спецификации | Задача |
|----------------------------|--------|
| Модульная система (статическая регистрация) | Task 4 + 7 |
| Nothing Phone UI | Task 3 (виджеты) + Task 5 (overlay) + Task 7 (окно модуля) |
| PostMessage вместо pyautogui | Task 2 (`input_sender.py`) |
| QThread + Signal/Slot | Task 6 (`AvaBot`) |
| Config с dataclass + дефолтами + валидацией | Task 1 |
| WindowManager без глобальных переменных | Task 2 |
| ScreenCapture singleton | Task 2 |
| ModuleWindow базовый класс (drag, position, close) | Task 4 |
| ConfigManager через dependency injection | Task 5 + 8 |
| Удаление pyautogui, threading.Thread, globals | Task 9 |
| Bot → GUI только через сигналы | Task 6 + 7 |
| Logger через Qt сигналы (не print) | Task 6 + 7 |
| MIN_ACTIVE настраивается в UI | Task 7 (берётся из config.min_active) |
| circle_button.py удалён | Task 9 |

### 2. Placeholder scan — проблем не найдено

Все шаги содержат реальный код. Нет "TBD", "TODO", "similar to Task N".

### 3. Type consistency

- `press_key(hwnd: int, key: str) -> bool` — определено в Task 2, используется в Task 6 (`press_key(self._hwnd, key)`) ✓
- `ScreenCapture.get().grab(region: dict) -> np.ndarray` — Task 2, используется в Task 6 и Task 7 ✓
- `ModuleBase.create_window(config, save_fn, window_manager, parent_overlay)` — Task 4, реализовано в Task 7 ✓
- `ModuleBase.config_key` — Task 4, установлено в Task 7 (`config_key = "ava_dancers"`), читается в Task 5 (`getattr(self.config.data, module_cls.config_key)`) ✓
- `NtButton.set_active(bool)` — Task 3, используется в Task 5 и Task 7 ✓
- `AvaBot.stop_bot()` — Task 6, вызывается в Task 7 (`self._bot.stop_bot()`) ✓
- `AvaBot.tile_detected: Signal(int, str)` — Task 6, подключается в Task 7 (`self._bot.tile_detected.connect(self._on_tile_detected)`) ✓
- `WindowManager.move_window(hwnd, x, y, w, h)` — Task 2, используется в Task 5 и ModuleWindow.do_drag ✓
- `Overlay.WIDTH`, `Overlay.HEIGHT` — Task 5, используется в Task 8 ✓
- `AppConfig.ava_dancers: AvaDancersConfig` — Task 1, `config_key="ava_dancers"` в Task 7 → `getattr(config.data, "ava_dancers")` в Task 5 ✓
