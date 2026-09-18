# app/ui/module_window.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPoint, QTimer

# Dragging moves the window on every mouse event; writing the config file
# that often is a disk write per pixel, which is felt as stutter. The
# position is kept in memory and flushed once the mouse has settled.
_SAVE_DELAY_MS = 400

from app.ui.crt_power_mixin import CrtPowerMixin
from app.ui.drag_mixin import BackgroundDragMixin
from app.ui.game_fit import GameFitMixin
from app.ui.resize_mixin import ResizeMixin
from app.ui.collapse_mixin import CollapseMixin
from app.ui.no_capture import exclude_from_capture


class ModuleWindow(GameFitMixin, CollapseMixin, CrtPowerMixin,
                   BackgroundDragMixin, ResizeMixin, QWidget):
    """Base for all module windows: drag, position/size persistence, resize."""

    def __init__(self, module_name: str, config, save_fn, parent_overlay=None):
        super().__init__()
        self.module_name    = module_name
        self.config         = config
        self.save_fn        = save_fn
        self.parent_overlay = parent_overlay
        self._panel         = None   # subclass assigns in _build_ui
        self._init_drag()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._init_resize()
        self._init_background_drag()
        self._init_crt_power()

    def showEvent(self, event):
        super().showEvent(event)
        # Our own windows are not part of the game, and the matcher reads the
        # game off the screen: anything of ours lying over it would be read as
        # if it were the garden.
        exclude_from_capture(self)

    # ── Running state, seen from outside ─────────────────────────────────────
    # Каждый мод называет свою кнопку по-своему («Запустить бота», «Начать
    # уборку»), и снаружи это всегда был чужой приватный метод. Здесь — три
    # общих ответа на вопросы, которые задаёт сбор ежедневного подарка
    # (см. app/core/daily_reward.py и Overlay._on_reward_seen): мод
    # сейчас работает? выключись. включись обратно.
    #
    # Имена перечислены, а не заданы каждым модом отдельно, ровно потому,
    # что они уже существуют и все разные; мод, который заведёт четвёртое
    # имя, переопределит эти методы у себя.

    # Порядок важен: у Ava Dancers `_bot_active` — это намерение (мод
    # считается включённым и между раундами), а `_running` — живой ли поток
    # прямо сейчас.
    _RUN_FLAGS   = ("_bot_active", "_running")
    _RUN_TOGGLES = ("_toggle_bot", "_toggle_running", "_toggle_cleaning")

    def _run_toggle(self):
        for name in self._RUN_TOGGLES:
            toggle = getattr(self, name, None)
            if callable(toggle):
                return toggle
        return None

    def module_is_running(self) -> bool:
        for name in self._RUN_FLAGS:
            if hasattr(self, name):
                return bool(getattr(self, name))
        return False

    def module_stop(self) -> bool:
        """Выключить мод, если он работает. True — выключили."""
        toggle = self._run_toggle()
        if toggle is None or not self.module_is_running():
            return False
        toggle()
        return True

    def module_start(self) -> bool:
        """Включить мод, если он не работает. True — включили."""
        toggle = self._run_toggle()
        if toggle is None or self.module_is_running():
            return False
        toggle()
        return True

    def module_is_playing(self) -> bool:
        """Мод не просто включён, а действительно работает по игре.

        Разница важна после перезапуска: включённым мод считается сразу, а
        дошёл ли он до игры — видно только по тому, что он в ней делает. Мод,
        которому нечем это подтвердить, отвечает «включён — значит работает»;
        Ava Dancers переопределяет и смотрит на пойманные ноты.
        """
        return self.module_is_running()

    def module_log(self, message: str, level: str = "info"):
        """Строка в лог самого мода — молча, если лога у него нет."""
        log = getattr(self, "_log", None)
        if log is not None and hasattr(log, "add_log"):
            log.add_log(message, level=level)

    # ── Position / size ──────────────────────────────────────────────────────

    def restore_position(self):
        # config хранит эталонное место — на ужатой игре окно встаёт ближе
        # к её углу во столько же раз. См. app/ui/game_fit.py.
        if getattr(self.config, "position_saved", False):
            self.move(*self.to_live_offset(self.config.x, self.config.y))

    def save_position(self, x: int, y: int):
        """Remember the position; the file write is deferred — see _init_drag.

        Записывается эталонное место, а не то, куда окно легло сейчас:
        иначе каждый перетаск на ужатой игре сохранял бы уменьшенные числа,
        и окно с каждым сеансом сползало бы к углу.
        """
        self.config.x, self.config.y = self.to_reference_offset(x, y)
        self.config.position_saved = True
        self._save_later.start()

    # ── Drag ────────────────────────────────────────────────────────────────
    # Both ends of the arithmetic have to live in the same coordinate space.
    # An attached window is moved in the game's client area, while the mouse
    # is reported on the screen; measuring the grab against Qt's own geometry
    # mixed the two, so every move landed slightly wrong and the next one
    # pulled it back — which is what the shaking was. Now only the *travel*
    # of the mouse is used, and travel means the same thing in both spaces.

    def _init_drag(self):
        self._drag_origin = QPoint()   # where the mouse went down, on screen
        self._drag_from   = QPoint()   # where the window was, in its own space
        self._save_later  = QTimer(self)
        self._save_later.setSingleShot(True)
        self._save_later.setInterval(_SAVE_DELAY_MS)
        self._save_later.timeout.connect(lambda: self.save_fn())

    def _window_origin(self) -> QPoint:
        """Top-left in whatever space this window is actually positioned in."""
        wm = getattr(self, "_wm", None)
        if wm is not None and wm.get_game_hwnd():
            origin = wm.window_origin(int(self.winId()))
            if origin is not None:
                return QPoint(*origin)
        return self.pos()

    def start_drag(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_from   = self._window_origin()

    def do_drag(self, event, window_manager=None):
        if not (event.buttons() & Qt.LeftButton):
            return
        travel = event.globalPosition().toPoint() - self._drag_origin
        target = self._drag_from + travel
        x, y = target.x(), target.y()
        if window_manager and window_manager.get_game_hwnd():
            window_manager.move_window(int(self.winId()), x, y,
                                       self.width(), self.height())
        else:
            self.move(x, y)
        self.save_position(x, y)

    # ── CrtPowerMixin hooks ──────────────────────────────────────────────────

    def _crt_closing(self):
        """Let the overlay pull its wire back as this window switches off."""
        overlay = self.parent_overlay or getattr(self, "_overlay", None)
        if overlay is not None and hasattr(overlay, "on_window_closing"):
            overlay.on_window_closing(self)

    # ── BackgroundDragMixin hooks ────────────────────────────────────────────
    # The window manager is the subclass's, and is absent while the game is
    # not attached — do_drag already falls back to a plain move then.

    def _drag_begin(self, event):
        self.start_drag(event)

    def _drag_to(self, event):
        self.do_drag(event, getattr(self, "_wm", None))

    # ── ResizeMixin hooks ────────────────────────────────────────────────────

    def _on_resize_panel(self):
        self.layout_panel()

    def resizeEvent(self, event):
        """Keep the backdrop the size of the window, however it got resized.

        The resize mixin only calls the hook while an edge is being dragged,
        so a window resized any other way — restored from config, set in
        code — was left with a panel still at its old size and a strip of
        nothing along the bottom.
        """
        super().resizeEvent(event)
        self._on_resize_panel()

    def _on_resize_done(self):
        if hasattr(self.config, "width"):
            # Тоже эталонный: окно, растянутое на ужатой игре до половины
            # её ширины, обязано остаться половиной и на развёрнутой.
            self.config.width, self.config.height = self.to_reference_size(
                self.width(), self.height())
            self.save_fn()

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.crt_close_started():
            event.ignore()   # the animation calls close() again when it ends
            return
        self._teardown()
        if self.parent_overlay:
            self.parent_overlay.on_module_closed(self.module_name)
        event.accept()
