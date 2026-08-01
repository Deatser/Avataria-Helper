# app/ui/theme.py
from PySide6.QtGui import QColor, QFont, QFontDatabase

# ── Backgrounds ──────────────────────────────────────────────────────────────
BG_BASE     = "#000000"
BG_SURFACE  = "#030308"
BG_ELEVATED = "#0b0b14"

# ── Borders ──────────────────────────────────────────────────────────────────
BORDER        = "#1c1c2a"
BORDER_DIM    = "#111118"
BORDER_BRIGHT = "#2a2a3a"

# ── Text ─────────────────────────────────────────────────────────────────────
TEXT_PRIMARY   = "#f0f0f0"
TEXT_SECONDARY = "#8080a0"
TEXT_DIM       = "#303042"
LOG_TS_COLOR   = "#7878a0"   # log timestamps — clearly visible

# ── Accents ──────────────────────────────────────────────────────────────────
ACCENT_RED   = "#ff0040"
ACCENT_GREEN = "#00ff88"
ACCENT_CYAN  = "#00ccff"
ACCENT_AMBER = "#ffaa22"
ACCENT_WHITE = "#ffffff"

# ── Button states ────────────────────────────────────────────────────────────
BG_BUTTON_ACTIVE = "#160008"
BG_BUTTON_HOVER  = "#10101c"

# ── Circuit traces (NtPanel animation) ───────────────────────────────────────
CIRCUIT_DIM  = "#0c2010"   # dark base color
CIRCUIT_GLOW = "#00cc44"   # bright neon green peak
DOT_COLOR    = "#0e0e1a"
DOT_SPACING  = 8
DOT_RADIUS   = 1

# ── Vaporwave (Ava Dancers module) ───────────────────────────────────────────
VW_BG           = "#050312"
VW_SURFACE      = "#0a0820"
VW_ELEVATED     = "#120d2e"
VW_CYAN         = "#00e5ff"
VW_MAGENTA      = "#ff00dd"
VW_PURPLE       = "#9944ff"
VW_GRID_COLOR   = "#0d0633"
VW_TEXT         = "#e0d0ff"
VW_BORDER       = "#2a1a66"
VW_BORDER_DIM   = "#180f44"
VW_BTN_ACTIVE   = "#0a0030"
VW_BTN_HOVER    = "#150040"

# ── Geometry ─────────────────────────────────────────────────────────────────
RADIUS  = 0
PADDING = 12
SPACING = 6

# ── Typography ───────────────────────────────────────────────────────────────
FONT_MONO          = "JetBrains Mono"
FONT_MONO_FALLBACK = "Consolas"
FONT_SIZE_S = 11
FONT_SIZE_M = 13
FONT_SIZE_L = 16

_DISPLAY_CHAIN = ["Ndot 55", "OCR A Extended", "Consolas", "Courier New"]
_ROUND_CHAIN   = ["Comfortaa", "Segoe UI", "Arial"]
_SERIF_CHAIN   = ["Cinzel", "Palatino Linotype", "Book Antiqua", "Georgia", "Times New Roman"]


def _pick(chain: list) -> str:
    families = set(QFontDatabase.families())
    for name in chain:
        if name in families:
            return name
    return chain[-1]


def get_mono_font(size: int = FONT_SIZE_M, bold: bool = False) -> QFont:
    families = set(QFontDatabase.families())
    name = FONT_MONO if FONT_MONO in families else FONT_MONO_FALLBACK
    font = QFont(name, size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
    return font


def get_display_font(size: int = FONT_SIZE_L, bold: bool = True) -> QFont:
    font = QFont(_pick(_DISPLAY_CHAIN), size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.8)
    return font


def get_round_font(size: int = FONT_SIZE_M, bold: bool = False) -> QFont:
    font = QFont(_pick(_ROUND_CHAIN), size)
    font.setBold(bold)
    return font


def get_serif_font(size: int = FONT_SIZE_S, bold: bool = False) -> QFont:
    font = QFont(_pick(_SERIF_CHAIN), size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
    return font
