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