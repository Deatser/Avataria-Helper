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

from app.core.capture import release_all_capture
from app.core.config import ConfigManager
from app.core.launcher import open_game
from app.core.term_log import tlog
from app.core.stats import StatsManager
from app.core.tropikania_config import TropikaniaConfigManager
from app.core.tropikania_stats import TropikaniaStatsManager
from app.core.window_manager import WindowManager
from app.ui.overlay import Overlay
from app.ui.tropikania_overlay import TropikaniaOverlay
from app.ui.raise_on_click import RaiseOnClick


# Куда поставить окно помощника, если сохранённое место оказалось негодным —
# отступ от левого верхнего угла игры.
_FALLBACK_POS = (60, 60)


def _repair_overlay_position(config, wm, overlay):
    """Вправить позицию окна, если она вне клиентской области игры.

    Прицепленное окно живёт в координатах клиентской области игры, а
    отцепленное — в экранных, и config хранит последнее сохранённое из двух.
    Запуск без игры, другой монитор, другое разрешение — и в файле остаются
    координаты, по которым окно оказывается за краем экрана: видимое по всем
    признакам, но не видное глазами (2026-08-12: x был -934).
    """
    rect = wm.get_game_rect()
    if rect is None:
        return
    # Считаем по живому месту, а не по тому, что записано: в config лежат
    # эталонные числа, и на ужатой игре они больше её клиентской области
    # сами по себе — окно при этом стоит там, где надо.
    ov = config.data.overlay
    x, y, width, height = overlay.live_geometry()
    fits_x = 0 <= x <= max(0, rect.width  - width)
    fits_y = 0 <= y <= max(0, rect.height - height)
    if fits_x and fits_y:
        return
    ov.x, ov.y = _FALLBACK_POS
    config.save()
    tlog(f"Окно помощника стояло вне игры — переставили в {_FALLBACK_POS}")


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

    # Прежде всего — привести игру в рабочий вид: поднять из лаунчера, если
    # её нет, и развернуть, если она свёрнута (см. app/core/launcher.py). До
    # того как это закончится, окна помощника не показываются: их позиции
    # считаются от клиентской области игры, и на неразвёрнутой они слипаются
    # в углу.
    open_game()

    found = wm.find_game("Аватария")
    # Всегда, а не только когда игра уже нашлась: она может подняться позже,
    # и слежение само объявит её окно основным, как только увидит. См.
    # GameWatch.check.
    overlay.start_game_watch()
    if found:
        tlog("Аватария found")
        _repair_overlay_position(config, wm, overlay)
        wm.attach_overlay(
            int(overlay.winId()),
            *overlay.live_geometry(),
        )
    # Молча, если игры нет: помощник и так поднимается сам по себе, а
    # почему её нет — уже сказал лаунчер строкой выше.

    overlay.show()
    # Куда именно легло окно помощника — единственный способ отличить «его
    # не показали» от «оно за чужим окном» или «уехало за край экрана».
    on_screen = wm.window_rect_screen(int(overlay.winId()))
    tlog(f"Окно помощника: на экране {on_screen}, "
         f"в конфиге ({config.data.overlay.x}, {config.data.overlay.y}), "
         f"видно: {overlay.isVisible()}")
    # After the overlay is on screen, not before: a starred window opening
    # against a hidden overlay has nothing to draw a wire to.
    if found:
        overlay.restore_favorite_windows()

    # Own WindowManager and its own config/stats files — Tropikania and
    # Avataria are separate game windows and separate records.
    tropikania_config = TropikaniaConfigManager()
    tropikania_stats  = TropikaniaStatsManager()
    tropikania_wm = WindowManager()
    tropikania_overlay = None
    tropikania_found = tropikania_wm.find_game("Тропикания")
    if tropikania_found:
        tlog("Тропикания found")
        tropikania_overlay = TropikaniaOverlay(tropikania_config, tropikania_wm,
                                               tropikania_stats, stats)
        tropikania_wm.attach_overlay(
            int(tropikania_overlay.winId()),
            tropikania_config.data.overlay.x,
            tropikania_config.data.overlay.y,
            tropikania_config.data.overlay.width,
            tropikania_config.data.overlay.height,
        )
        tropikania_overlay.show()
        tropikania_overlay.restore_favorite_windows()

    # A Windows Graphics Capture session owns a background thread and a GPU
    # surface, and unlike the GDI cache it is shared rather than owned by the
    # worker that made it — so no worker can be the one to clean it up.
    app.aboutToQuit.connect(release_all_capture)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()