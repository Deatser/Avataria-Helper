# app/ui/theme.py

from PySide6.QtGui import QColor, QFont, QFontDatabase

from app.core.paths import ASSETS

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

# ── Olive (Садовник module) ──────────────────────────────────────────────────
GD_BG          = "#0b0f07"
GD_SURFACE     = "#141a0c"
GD_ELEVATED    = "#1d2612"
GD_OLIVE       = "#a8bd4f"   # the module accent — ripe olive, not lime
GD_OLIVE_SOFT  = "#c8d98a"
GD_MOSS        = "#5e7030"
GD_TEXT        = "#e4ecc9"
GD_BORDER      = "#2f3d1a"
GD_BORDER_DIM  = "#1c2510"

# ── Amber (Уборщик module) ────────────────────────────────────────────────────
# Warm hi-tech street café: dark wood decking, café-string amber light,
# stone-cream text — the opposite register from the Olive greenhouse.
JN_BG          = "#100b06"
JN_SURFACE     = "#1c130b"
JN_ELEVATED    = "#291c10"
JN_AMBER       = "#e2a24a"   # the module accent — café string light
JN_AMBER_SOFT  = "#f3cf94"
JN_WOOD        = "#7a4f2a"
JN_TEXT        = "#f1e4d2"
JN_BORDER      = "#3a2716"
JN_BORDER_DIM  = "#221609"

# ── Steel (Сноуборд module) ───────────────────────────────────────────────────
# Overcast slope at altitude: cold blue-grey snow, steel rails, ice-white
# text — muted and hard next to the Amber café's warmth.
SB_BG          = "#0a0e15"
SB_SURFACE     = "#131c29"
SB_ELEVATED    = "#1b2839"
SB_STEEL       = "#6d84c0"   # the module accent — same value as ACCENT_STEEL
SB_STEEL_SOFT  = "#aebbe0"
SB_ICE         = "#cfe8f5"
SB_TEXT        = "#e9eef7"
SB_BORDER      = "#293957"
SB_BORDER_DIM  = "#182234"

# ── Ice (Хоккей module) ───────────────────────────────────────────────────────
# Indoor rink: near-black ice under arena lights, a spark of boards-red
# against the cold blue — brighter and icier than the Snowboard's overcast
# steel, the two blues meant to read as clearly different modules.
HK_BG          = "#04101a"
HK_SURFACE     = "#0a1c2c"
HK_ELEVATED    = "#11283d"
HK_ICE         = "#67d3f5"   # the module accent — same value as ACCENT_ICE
HK_ICE_SOFT    = "#b3ecfd"
HK_RINK_RED    = "#c23b4a"   # boards/goal accent — the one warm note
HK_TEXT        = "#e8f6ff"
HK_BORDER      = "#1c3550"
HK_BORDER_DIM  = "#0f2033"

# ── Energy (Энергия window) ───────────────────────────────────────────────────
# A bar at closing time: near-black room, one warm lamp, motes of light
# drifting up through it. The accent is the yellow of the game's own energy
# icon, and it is the only saturated thing in the window.
EN_BG          = "#0b0a07"
EN_SURFACE     = "#15120b"
EN_ELEVATED    = "#211b10"
EN_YELLOW      = "#f2d24b"   # module accent — the energy icon's own yellow
EN_YELLOW_SOFT = "#f8e79a"
EN_EMBER       = "#8a6a1e"
EN_TEXT        = "#f3ecd8"
EN_BORDER      = "#3a2f14"
EN_BORDER_DIM  = "#231c0c"

# ── Geometry ─────────────────────────────────────────────────────────────────
RADIUS  = 8
PADDING = 14
SPACING = 8

# ── Typography ───────────────────────────────────────────────────────────────
# Comfortaa, and nothing else, across every window, panel and log. The old
# split — JetBrains Mono for body text, a display face for headings, Cinzel
# for accents — is gone by request; the bundled files for the other faces
# stay in assets/fonts but are no longer named by any chain.
#
# Comfortaa carries Latin, Cyrillic, digits, ∞ and the ←↓↑→ arrows the tile
# readout draws (checked against its cmap). It has no ▲ ★ ☆ ⚙, so Qt
# substitutes those from a system face glyph by glyph, as it already did.
#
# It is a proportional face, so the log panel no longer aligns in columns
# the way a monospace did — that is the cost of one family everywhere.
FONT_MAIN   = "Comfortaa"
FONT_SIZE_S = 11
FONT_SIZE_M = 13
FONT_SIZE_L = 16

_FONT_DIR = ASSETS / "fonts"

# The four getters survive as *voices* of Comfortaa rather than separate
# faces: they differ by weight and letter-spacing only, which is what keeps
# a single-family interface from reading flat. The chain remains so a
# machine without the bundled file lands on a rounded system face, not
# Courier.
_UI_CHAIN = [FONT_MAIN, "Segoe UI", "Arial"]

# The heading voice's letter-spacing, in absolute pixels. A window that
# wants one uniform rhythm — headings and body alike — passes this through
# apply_tracking() instead of the per-getter defaults below.
TRACKING_DISPLAY = 2.0

_loaded = False


def _ensure_fonts():
    """Register the bundled faces with Qt, once.

    Lazily rather than at import: addApplicationFont needs a QGuiApplication
    to exist, and this module is imported long before one does in some entry
    points. Every font getter goes through here, so whichever runs first
    does the loading.
    """
    global _loaded
    if _loaded or not _FONT_DIR.is_dir():
        return
    for path in sorted(_FONT_DIR.glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(path)) == -1:
            return          # no application object yet — try again next call
    _loaded = True


def _pick(chain: list) -> str:
    _ensure_fonts()
    families = set(QFontDatabase.families())
    for name in chain:
        if name in families:
            return name
    return chain[-1]


def _comfortaa(size: int, bold: bool, tracking: float,
               weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """One family, four voices.

    Comfortaa's Regular reads thin against these dark panels, so plain text
    sits at Medium and only emphasis goes to Bold. `tracking` is absolute
    pixels, the same unit the old chains used.
    """
    font = QFont(_pick(_UI_CHAIN), size)
    font.setWeight(QFont.Weight.Bold if bold else weight)
    font.setLetterSpacing(QFont.AbsoluteSpacing, tracking)
    return font


def get_mono_font(size: int = FONT_SIZE_M, bold: bool = False) -> QFont:
    """Body voice: switch labels, buttons, log lines, stat values."""
    return _comfortaa(size, bold, 0.2)


def get_display_font(size: int = FONT_SIZE_L, bold: bool = True) -> QFont:
    """Heading voice: window titles and section captions, mostly uppercase.

    Wide tracking is what carries these — caps in a rounded face collapse
    into a blob without it.
    """
    return _comfortaa(size, bold, TRACKING_DISPLAY,
                      weight=QFont.Weight.DemiBold)


def get_round_font(size: int = FONT_SIZE_M, bold: bool = False) -> QFont:
    """Neutral voice: plain sentence-case labels, no extra tracking."""
    return _comfortaa(size, bold, 0.0)


def get_serif_font(size: int = FONT_SIZE_S, bold: bool = False) -> QFont:
    """Accent voice: kept as a lighter, airier Comfortaa so the places that
    asked for a serif still read as a step apart."""
    return _comfortaa(size, bold, 0.9, weight=QFont.Weight.Normal)


def tracked(font: QFont, tracking: float = TRACKING_DISPLAY) -> QFont:
    """Copy of `font` with the given absolute letter-spacing."""
    out = QFont(font)
    out.setLetterSpacing(QFont.AbsoluteSpacing, tracking)
    return out


def apply_tracking(widget, tracking: float = TRACKING_DISPLAY):
    """Give `widget` and every child the same letter-spacing.

    The widgets set their own fonts in __init__ (body voice, narrow
    tracking), so a font on the parent is not inherited — the only way to
    make one window read with a single rhythm is to walk it and rewrite the
    spacing, keeping each widget's own size and weight. Call it after the
    tree is built, and again for anything created later.
    """
    from PySide6.QtWidgets import QWidget
    for child in [widget, *widget.findChildren(QWidget)]:
        child.setFont(tracked(child.font(), tracking))
