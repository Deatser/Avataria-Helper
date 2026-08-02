import os
import sys
import signal

# qt.multimedia.* also gates the FFmpeg backend, which dumps the full stream
# layout of every video it opens
os.environ["QT_LOGGING_RULES"] = (
    "*.debug=false;qt.qpa.*=false;qt.multimedia.*=false"
)

from PySide6.QtCore import QTimer, qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication


def _qt_msg_handler(mode, context, message):
    if "setGeometry" in message or "Unable to set geometry" in message:
        return
    if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        print(f"[Qt] {message}", file=sys.stderr)

from app.core.config import ConfigManager
from app.core.stats import StatsManager
from app.core.window_manager import WindowManager
from app.ui.overlay import Overlay
from app.ui.raise_on_click import RaiseOnClick


def main():
    qInstallMessageHandler(_qt_msg_handler)
    app = QApplication(sys.argv)

    config = ConfigManager()
    stats  = StatsManager()
    wm     = WindowManager()

    overlay = Overlay(config, wm, stats)

    # Clicking any window brings it forward — see RaiseOnClick. Kept on the
    # application and owned by it, so it outlives every window it serves.
    click_to_front = RaiseOnClick(wm, parent=app)
    app.installEventFilter(click_to_front)

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
            config.data.overlay.width,
            config.data.overlay.height,
        )
    else:
        print("Аватария not found — running standalone")

    overlay.show()
    # After the overlay is on screen, not before: a starred window opening
    # against a hidden overlay has nothing to draw a wire to.
    if found:
        overlay.restore_favorite_windows()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()