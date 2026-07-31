# preview_overlay.py  (temporary — deleted in Task 9)
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
