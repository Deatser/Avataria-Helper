# app/ui/theme.py
from PySide6.QtGui import QColor, QFont, QFontDatabase

# ── Backgrounds — neutral near-black with a faint violet cast ────────────────
BG_BASE     = "#08070d"
BG_SURFACE  = "#100e18"
BG_ELEVATED = "#191622"

# ── Hairlines (low-contrast separation, never saturated) ─────────────────────
BORDER        = "#232030"
BORDER_DIM    = "#17151f"
BORDER_BRIGHT = "#332f45"

# ── Text ─────────────────────────────────────────────────────────────────────
TEXT_PRIMARY   = "#ecebf3"
TEXT_SECONDARY = "#a5a2ba"
TEXT_DIM       = "#4a4760"
LOG_TS_COLOR   = "#6f6c8a"   # log timestamps — readable, not shouting

# ── Accents — one violet chrome accent + muted semantic colours ──────────────
ACCENT       = "#a06bff"   # chrome accent (default for buttons, panel edge)
ACCENT_SOFT  = "#c9a6ff"
ACCENT_RED   = "#ff5c78"
ACCENT_GREEN = "#4bdb96"
ACCENT_CYAN  = "#4cc9f0"
ACCENT_ICE   = "#67d3f5"   # Хоккей — light, icy blue
ACCENT_STEEL = "#6d84c0"   # Сноуборд — deeper, muted steel blue
ACCENT_AMBER = "#f5b544"
ACCENT_WHITE = "#ffffff"

# ── Button body ──────────────────────────────────────────────────────────────
BG_BUTTON        = "#191622"
BG_BUTTON_HOVER  = "#221e2e"

# ── Ambient circuit traces (NtPanel) — same violet family, low contrast ──────
CIRCUIT_DIM  = "#1b1630"
CIRCUIT_GLOW = "#6f4bd8"
DOT_COLOR    = "#181524"
DOT_SPACING  = 10
DOT_RADIUS   = 1
PANEL_RADIUS = 12

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
RADIUS  = 8
PADDING = 14
SPACING = 8

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
