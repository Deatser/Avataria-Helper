import os
import sys
import signal

os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.*=false"

from PySide6.QtCore import QTimer, qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication


def _qt_msg_handler(mode, context, message):
    if "setGeometry" in message or "Unable to set geometry" in message:
        return
    if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        print(f"[Qt] {message}", file=sys.stderr)

from app.core.config import ConfigManager
from app.core.window_manager import WindowManager
from app.ui.overlay import Overlay


def main():
    qInstallMessageHandler(_qt_msg_handler)
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