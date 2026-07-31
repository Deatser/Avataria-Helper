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
