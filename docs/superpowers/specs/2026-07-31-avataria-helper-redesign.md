# Avataria Helper — Full Redesign Spec
Date: 2026-07-31

## Overview

Full redesign of the Avataria Helper game assistant. The project is a local Python application
that attaches to the Avataria game window and provides automation modules via an overlay UI.
Current codebase is treated as a working prototype; this spec describes the target architecture.

Goals:
- Clean, extensible module system (static plugin registration)
- Nothing Phone-inspired UI (black, dot-grid, monospace, red accents)
- Reliable key input via PostMessage (no focus dependency)
- Proper Qt threading (QThread + Signal/Slot, no threading.Thread)
- Future-ready for Nuitka compilation and license/subscription system

---

## 1. Tech Stack

| Component       | Solution                              | Reason                                                    |
|-----------------|---------------------------------------|-----------------------------------------------------------|
| UI              | PySide6 + QPainter custom widgets     | Full pixel control, Nothing-style without a second runtime |
| Key input       | win32api.PostMessage(hwnd, WM_KEYDOWN)| No focus dependency, direct to game message queue         |
| Threading       | QThread + Signal/Slot                 | Native Qt integration, safe UI updates from worker        |
| Win32           | pywin32                               | Direct API access for window management                   |
| Screen capture  | mss singleton                         | Fast, single instance reused across frames                |
| Config          | dataclass + JSON                      | Typed, validated, defaults built-in                       |
| Future DRM      | Nuitka compilation                    | Python → C → .exe, harder to reverse than PyInstaller     |

Not used: pyautogui (replaced by PostMessage), threading.Thread (replaced by QThread).

---

## 2. Project Structure

```
avataria-helper/
│
├── main.py                          # Entry point: QApplication, window setup
├── config.json                      # Persisted user settings
│
├── app/
│   ├── core/
│   │   ├── window_manager.py        # Win32: find game, SetParent, position overlay
│   │   ├── input_sender.py          # PostMessage key/click sender
│   │   ├── capture.py               # mss singleton, thread-safe screen capture
│   │   └── config.py                # ConfigManager: dataclass schema + JSON I/O
│   │
│   ├── ui/
│   │   ├── theme.py                 # Design tokens: colors, fonts, sizes
│   │   ├── overlay.py               # Main overlay window
│   │   ├── module_window.py         # Base QWidget class for all module windows
│   │   └── widgets/
│   │       ├── nt_button.py         # Nothing-styled button (custom QPainter)
│   │       ├── nt_panel.py          # Panel with dot-grid texture background
│   │       ├── nt_status_dot.py     # Colored status indicator dot
│   │       └── log_panel.py         # Log widget in Nothing style
│   │
│   └── module_registry.py           # Static list of all registered modules
│
└── modules/
    ├── base.py                      # ModuleBase abstract class
    └── ava_dancers/
        ├── bot.py                   # AvaBot (QThread-based)
        └── window.py                # AvaDancers module window
```

Rules:
- `app/core/` — no UI imports, no game-specific logic
- `app/ui/` — no win32 imports, no bot logic
- `modules/` — no direct win32 calls; use `input_sender` and `capture` from core
- Cross-layer communication only via interfaces and Qt signals

---

## 3. Module System

Every module inherits `ModuleBase`:

```python
# modules/base.py
from abc import ABC, abstractmethod
from app.ui.module_window import ModuleWindow

class ModuleBase(ABC):
    name: str           # Display name, e.g. "AvaDancers"
    description: str    # Short description shown in overlay
    icon: str           # Symbol for overlay button, e.g. "◈"

    @abstractmethod
    def create_window(self, parent_overlay) -> ModuleWindow:
        """Create and return the module's control window."""
        ...
```

Static registration — one file, one list:

```python
# app/module_registry.py
from modules.ava_dancers import AvaDancersModule

MODULES = [
    AvaDancersModule,
    # Add new module here — one line
]
```

The overlay reads `MODULES` at startup and creates one button per module.
Adding a new module = create `modules/name/` + register in this list.

---

## 4. Bot Threading: QThread + Signals

All bots use `QThread` instead of `threading.Thread`.
The bot never touches UI. UI never calls bot methods directly except `start()`, `stop()`, `configure()`.

```python
# modules/ava_dancers/bot.py
from PySide6.QtCore import QThread, Signal

class AvaBot(QThread):
    tile_detected = Signal(int, str)   # tile_id, key — UI updates indicator
    key_pressed   = Signal(str)        # key — UI logs the press
    stats_updated = Signal(dict)       # white_pixel counts per tile — debug panel
    error         = Signal(str)        # UI shows error message

    def run(self):
        # main detection loop — runs in worker thread
        ...

    def configure(self, settings: dict):
        # update bot parameters without restart
        ...
```

GUI connects signals in the module window:

```python
self.bot.key_pressed.connect(self.on_key_pressed)
self.bot.tile_detected.connect(self.on_tile_detected)
self.bot.error.connect(self.on_error)
```

---

## 5. Key Input

Single module, single responsibility:

```python
# app/core/input_sender.py
import win32api, win32con

VK_MAP = {
    "a": 0x41, "s": 0x53, "w": 0x57, "d": 0x44,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    # extend as needed
}

def press_key(hwnd: int, key: str) -> bool:
    """Send WM_KEYDOWN + WM_KEYUP to game window. No focus required."""
    vk = VK_MAP.get(key)
    if not vk or not hwnd:
        return False
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP,   vk, 0)
    return True
```

Bots import `press_key` from core. No bot ever imports pyautogui.

---

## 6. Screen Capture

Singleton instance, safe for multi-threaded use:

```python
# app/core/capture.py
import mss
import numpy as np
import cv2
from threading import Lock

class ScreenCapture:
    _instance = None
    _lock = Lock()

    @classmethod
    def get(cls) -> "ScreenCapture":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._sct = mss.mss()
        self._mutex = Lock()

    def grab(self, region: dict) -> np.ndarray:
        with self._mutex:
            raw = self._sct.grab(region)
        img = np.array(raw)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
```

Bots call `ScreenCapture.get().grab(region)`. One mss instance for the whole app lifetime.

---

## 7. Config System

```python
# app/core/config.py
from dataclasses import dataclass, field, asdict
import json
from pathlib import Path

@dataclass
class OverlayConfig:
    x: int = 10
    y: int = 10
    opacity: int = 220

@dataclass
class ThemeConfig:
    background: str = "8,8,8"
    radius: int = 0

@dataclass
class AvaDancersConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 200
    y: int = 200
    min_active: int = 3500    # configurable via UI slider

@dataclass
class AppConfig:
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
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
            # merge with defaults — missing keys get default values
            return self._merge(AppConfig(), raw)
        except Exception:
            return AppConfig()

    def save(self):
        self.CONFIG_FILE.write_text(
            json.dumps(asdict(self.data), indent=4, ensure_ascii=False),
            encoding="utf-8"
        )

    def _merge(self, default: AppConfig, raw: dict) -> AppConfig:
        # recursively merge raw dict into dataclass, keeping defaults for missing keys
        ...
```

---

## 8. Nothing Design System

```python
# app/ui/theme.py

# Backgrounds
BG_BASE     = "#080808"
BG_SURFACE  = "#111111"
BG_ELEVATED = "#1a1a1a"

# Borders
BORDER      = "#222222"
BORDER_DIM  = "#161616"

# Text
TEXT_PRIMARY   = "#ffffff"
TEXT_SECONDARY = "#555555"
TEXT_DIM       = "#2a2a2a"

# Accents
ACCENT_RED   = "#ff3b3b"   # active, danger, running bot
ACCENT_WHITE = "#ffffff"   # primary action
ACCENT_GREEN = "#39ff84"   # success, connected

# Typography
FONT_MONO   = "JetBrains Mono"
FONT_SIZE_S = 11
FONT_SIZE_M = 13
FONT_SIZE_L = 16
```

### Visual components:

**NtPanel** — base panel widget. `paintEvent` draws:
1. Fill: `BG_SURFACE`
2. Dot-grid overlay: 2px dots at every 8px in `#1a1a1a`, very subtle
3. Border: 1px `BORDER` around edges

**NtButton** — custom button:
- Default: `BG_ELEVATED` fill, `TEXT_PRIMARY` text, no border
- Hover: `#2a2a2a` fill
- Active/running: `ACCENT_RED` left border (3px) + `ACCENT_RED` text
- Font: monospace, uppercase, tracked

**NtStatusDot** — 8px circle:
- Gray: offline/stopped
- Green: running
- Red: error

**LogPanel** — scrolling log area:
- Monospace font, `TEXT_SECONDARY` color
- Timestamps in `TEXT_DIM`
- Errors in `ACCENT_RED`
- No scrollbar visible (custom thin track)

### Overlay layout concept:

```
┌─────────────────────────────┐
│ AVATARIA HELPER   [—] [×]   │  ← header bar, drag handle
├─────────────────────────────┤
│ · · · · · · · · · · · · · · │  ← dot-grid texture visible here
│  [◈ AVADANCERS]             │
│  [◈ HOCKEY]       (soon)   │
│  [◈ AUTOFARM]     (soon)   │
├─────────────────────────────┤
│ LOG                         │
│ 12:34:01 бот запущен        │
│ 12:34:05 нажата D           │
└─────────────────────────────┘
```

---

## 8a. ModuleWindow Base Class

Every module's control window inherits `ModuleWindow`:

```python
# app/ui/module_window.py
from PySide6.QtWidgets import QWidget

class ModuleWindow(QWidget):
    """Base class for all module control windows.
    Provides: drag behavior, position save/restore, favorite toggle,
    close event wiring to parent overlay.
    """

    def __init__(self, module_name: str, config, parent_overlay=None):
        super().__init__()
        self.module_name = module_name
        self.config = config
        self.parent_overlay = parent_overlay
        self._setup_window_flags()
        self._restore_position()

    def _setup_window_flags(self): ...   # FramelessWindowHint, WA_TranslucentBackground
    def _restore_position(self): ...     # read x/y from config
    def _save_position(self, x, y): ... # write x/y to config
    def closeEvent(self, event): ...     # notify parent overlay
```

Module windows only implement their specific UI and bot wiring.
Common behaviors (drag, close, position) live here once.

---

## 8b. ConfigManager Access Pattern

`ConfigManager` is created once in `main.py` and passed explicitly to components that need it.
No singleton, no global variable. This makes dependencies visible and testable.

```python
# main.py
config = ConfigManager()
overlay = Overlay(config)
```

Module windows receive the relevant config section, not the whole manager:

```python
# inside Overlay when opening a module
window = module.create_window(
    config=self.config.data.ava_dancers,
    save_fn=self.config.save,
    parent_overlay=self
)
```

---

## 9. Window Binding

Architecture unchanged from current approach (it works):
- Overlay and module windows are WS_CHILD of the game window via `SetParent`
- They appear only over the game, not over other apps
- Dragging is limited to the game window's client area via `limit_position()`

Improvement: `window_manager.py` replaces the current global variables with a proper class:

```python
class WindowManager:
    def __init__(self):
        self._game_hwnd: int | None = None
        self._overlay_hwnd: int | None = None

    def find_game(self, title: str) -> bool: ...
    def attach(self, child_hwnd: int): ...
    def move(self, hwnd: int, x: int, y: int, w: int, h: int): ...
    def get_game_hwnd(self) -> int | None: ...
```

Single instance passed as dependency, no module-level globals.

---

## 10. What Gets Removed

| Current | Replacement |
|---------|-------------|
| `pyautogui` for key presses | `input_sender.press_key()` |
| `threading.Thread` in bot | `QThread` |
| `print()` for logging | Qt signals → log panel |
| Global vars in window_bind.py | `WindowManager` class instance |
| Duplicate `key_map`/`keys` dicts | Single `KEY_MAP` constant |
| `mss.mss()` per frame | `ScreenCapture` singleton |
| `MIN_ACTIVE = 3500` hardcoded | Configurable, saved in config, editable via UI |
| `time.sleep(3)` debug code | Removed |
| `circle_button.py` unused | Removed. Replaced by `NtButton` in new widget system |
| `logger.py` unused | Replaced by Qt signals, or connected properly |

---

## 11. Out of Scope (This Spec)

- License/subscription system (future spec)
- Nuitka build pipeline (future spec)
- Tropicania module (future spec — same architecture, new module)
- Auto-farm club activity module (future spec — same architecture, new module)
- Hockey module (future spec — same architecture, new module)

These will each be their own spec → plan → implementation cycle.
The architecture defined here is designed to accommodate all of them without changes to core.

---

## 12. Success Criteria

- [ ] Bot reliably presses keys in the game (verified with PostMessage)
- [ ] No pyautogui anywhere in the codebase
- [ ] Adding a new module requires only: create `modules/name/`, add one line to `module_registry.py`
- [ ] Bot thread communicates with UI only via Qt signals
- [ ] All log output appears in the UI log panel, not just console
- [ ] Config survives missing keys / corrupted file (falls back to defaults)
- [ ] UI matches Nothing design language: black, dot-grid, monospace, red accents
- [ ] No global mutable state in window_manager
