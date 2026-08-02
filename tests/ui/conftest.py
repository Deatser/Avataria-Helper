# tests/ui/conftest.py
"""Qt tests run headless, so a test suite never flashes windows on screen."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
