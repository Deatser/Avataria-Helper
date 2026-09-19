# modules/hockey/window.py
"""Хоккей — пустая оболочка окна на время переписывания мода.

Старая версия — детект шлемов, две модели предсказания, промер траектории,
планировщик броска, счётчик уровней и автоудар — целиком лежит в коммите
«LEGACY: хоккей — рабочая, но не идеальная версия». Она работала, но росла
слоями: два движка предсказания жили параллельно, режимов в настройках
накопилось больше, чем кто-либо держал в голове, а половина кнопок в окне
была скрыта, но подключена.

Поэтому здесь сейчас только рамка: заголовок, кнопка запуска (мёртвая),
кнопка настроек (панель пустая), лог — и два ряда кнопок по числу рядов.

Верхний ряд рисует разметку поверх игры: полосу ряда, а рядом одна кнопка на
оранжевую полосу у борта. Ничего не снимает, это способ посмотреть глазами,
что координаты в modules/hockey/rink.py всё ещё на месте.

Нижний ряд ловит красное в полосе у борта — см. red_watch. Кадр снимается
один на все ловящие ряды и настолько часто, насколько игра успевает
перерисовываться; каждый въезд красного печатается строкой и кладётся
снимком в templates/red_catch.

Когда красное въехало второй раз, замер закрыт: между двумя въездами прошёл
период, а из него и размаха ряда получается скорость. С этого момента от
места второго въезда едет фиолетовая рамка — во весь рост вратаря, от линии
шлема до коньков, и узкая по горизонтали, потому что отвечает она на вопрос
«где он сейчас», а на него ответ — линия. Ничего не предсказывается: скорость
измерена, борта известны, остальное арифметика (см. patrol).

По ряду на кнопку, а не все сразу, потому что проверять их надо поодиночке:
детектор считает красное внутри полосы, и полосы обязаны не пересекаться —
иначе один шлем засчитается двум рядам.

Алгоритмические модули (detect, motion, planner, trajectory, rink_area,
debug_frame, level_strip) с диска не удалены — на них никто отсюда больше не
ссылается, они лежат как справочник на время переписывания.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from app.core.capture import grab_window, set_wgc_enabled
from app.core.paths import TEMPLATES
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.vw_panel import VwPanel
from app.ui.zones_overlay import ZonesOverlay
from modules.hockey import patrol, red_watch, rink, standing
from modules.hockey.settings_panel import HockeySettingsPanel

_BACKDROP_STEM  = "snowboard_sinthwawe"
_TEMPLATES      = TEMPLATES
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# Размер, до которого окно доведено руками (config.json на 2026-08-14), он же
# минимальный: меньше — и лог перестаёт вмещать разбор ряда, ради которого он
# тут и стоит. Записан числом, а не прочитан из конфига на старте: конфиг
# переписывается при каждом ресайзе, так что минимум «как в конфиге» полз бы
# вверх за каждым растягиванием и обратно окно было бы уже не сжать.
# Временно — по мере того как мод обрастёт своим, обе пары поедут вверх.
_DEFAULT_W = 595
_DEFAULT_H = 805
_MIN_W     = _DEFAULT_W
_MIN_H     = _DEFAULT_H

_LOG_H = 150

_START_TEXT = "▶  Запустить слежение за вратарями"
# Полоса у борта рисуется только для показанных рядов: пять оранжевых
# прямоугольников сразу — это снова стопка, в которой не видно, чей какой.
_ZONE_TEXT  = "🟧  Полоса у борта — для показанных рядов"

# Ловля просит кадры чаще, чем их вообще может быть, и это намеренно. Windows
# Graphics Capture отдаёт кадр, как только окно перерисовалось, и блокирует,
# пока нового нет, — темп задаёт игра (около 40 в секунду), а таймер только не
# добавляет к нему своей задержки.
_WATCH_MS = 10

# Сколько подряд захват может падать, прежде чем ловля сдастся. Одиночный сбой
# — обычное дело под нагрузкой; полторы секунды подряд — это уже не икота.
_WATCH_FAIL_S = 1.5

# Рамка, едущая с измеренной скоростью. Фиолетовая — насыщенная, а не бледная:
# ряд 4 покрашен в #c9a6ff, и лиловая рамка на нём была бы неразличима.
_MOVER_COLOUR = "#9d3cff"

# Размеры едущей рамки берутся у самого ряда: ширина — rink.Row.body_w,
# высота — rink.Row.feet_dy, от линии шлема до коньков. То есть рамка ровно
# того размера, каким вратарь мешает броску.
#
# Ширина здесь ещё и проверяет сама себя. Рамка отражается от head_bounds, то
# есть от «борт ± полширины», — значит в момент разворота её край обязан
# упереться точно в борт ряда. Не упёрся или залез за него — body_w отмечен
# неверно, и это видно без единого замера.

# Раз в 16 мс — 60 кадров в секунду. Положение при этом считается не шагами, а
# от настоящих часов (см. patrol.Patrol.at), так что дрожание таймера рамку не
# уводит: пропущенный тик — это пропущенный кадр, а не потерянные пиксели.
_MOVE_MS = 16

# Что говорит кнопка запуска. Строкой в лог, а не серой кнопкой: серая
# кнопка сообщает «нельзя», но не сообщает почему, а причина здесь —
# единственное, что о моде сейчас стоит знать.
_DISABLED_TEXT = ("Мод переписывается с нуля — запуск отключён. Старая "
                  "версия целиком лежит в коммите LEGACY.")


def _default_backdrop() -> str:
    """Первый существующий templates/<stem>.* — то же правило, что у Ava
    Dancers и Сноуборда."""
    for suffix in _STILL_SUFFIXES:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


class HockeyWindow(ModuleWindow):
    """Рамка, лог и две кнопки. Ничего не запускает и ничего не снимает."""

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Хоккей", config, save_fn, parent_overlay)
        self._wm = window_manager
        # Читается снаружи — ModuleWindow.module_is_running. Парного
        # _toggle_running здесь намеренно нет: без него module_start() из
        # сторожа зависаний честно отвечает «этот мод включить нельзя»,
        # вместо того чтобы дёргать заглушку.
        self._running = False
        self._settings: HockeySettingsPanel | None = None

        # Один слой на обе кнопки: оверлей держит один список прямоугольников,
        # так что каждая перерисовывает его целиком — иначе включение одной
        # стирало бы другую.
        self._zones = ZonesOverlay(window_manager, reference=self)
        # Номера показанных сейчас рядов. Множество, а не флаг: ряды
        # включаются по одному и в любом сочетании, а оранжевая полоса
        # рисуется ровно для тех, что видны.
        self._rows_shown: set[int] = set()
        self._zone_shown = False

        # Ловля красного: за какими рядами смотрим и чем. Один таймер и один
        # захват на все — полосы лежат внутри одного катка, и снимать его
        # пять раз за тик значило бы платить пятикратно за одну и ту же
        # картинку.
        self._watching: set[int] = set()
        self._watches: dict[int, red_watch.RedWatch] = {}
        # Моменты въездов красного, по ряду. Их нужно ровно два: между двумя
        # въездами в одну полосу проходит целый период, и после второго ряд
        # свой замер закончил.
        self._marks: dict[int, list[float]] = {}
        self._watch_timer: QTimer | None = None
        self._catch_dir: Path | None = None
        self._watch_started = 0.0
        self._watch_frames = 0
        self._failing_since: float | None = None

        # Поиск стоячих: ряды, за которыми смотрим прямо сейчас, и то, что
        # уже нашли. Кормится теми же кадрами, что и ловля у борта.
        self._standing: dict[int, standing.StandWatch] = {}
        self._standers: dict[int, list[float]] = {}

        # Рамки, едущие с уже измеренной скоростью. Свой слой, а не общий с
        # разметкой: этот перерисовывается шестьдесят раз в секунду, и
        # пересобирать вместе с ним неподвижные полосы незачем.
        self._movers = ZonesOverlay(window_manager, reference=self)
        self._patrols: dict[int, patrol.Patrol] = {}
        self._move_timer: QTimer | None = None

        w = getattr(config, "width",  _DEFAULT_W)
        h = getattr(config, "height", _DEFAULT_H)
        self.resize(max(w, _MIN_W), max(h, _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── Сборка окна ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = VwPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.HK_BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        self._start_btn = NtButton(_START_TEXT, accent=theme.HK_ICE,
                                   upper=False)
        self._start_btn.clicked.connect(self._on_start_clicked)
        layout.addWidget(self._start_btn)

        settings_btn = NtButton("⚙  Настройки", accent=theme.HK_ICE_SOFT,
                                upper=False)
        settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(settings_btn)

        layout.addSpacing(4)
        layout.addWidget(self._section_label("РАЗМЕТКА ПОВЕРХ ИГРЫ"))

        # Кнопка на ряд, в цвете этого ряда: полосы теперь тонкие и
        # разнесённые, и главное, что о них надо видеть, — какая чья.
        rows_line = QHBoxLayout()
        rows_line.setSpacing(4)
        self._row_btns: list[NtButton] = []
        for row in rink.ROWS:
            button = NtButton(f"Ряд {row.index + 1}",
                              accent=rink.ROW_COLOURS[row.index], upper=False)
            button.clicked.connect(
                lambda _checked=False, i=row.index: self._toggle_row(i))
            rows_line.addWidget(button)
            self._row_btns.append(button)
        layout.addLayout(rows_line)

        self._zone_btn = NtButton(_ZONE_TEXT, accent=theme.ACCENT_AMBER,
                                  upper=False)
        self._zone_btn.clicked.connect(self._toggle_zone)
        layout.addWidget(self._zone_btn)

        layout.addSpacing(4)
        layout.addWidget(self._section_label("ЛОВЛЯ КРАСНОГО У БОРТА"))

        watch_line = QHBoxLayout()
        watch_line.setSpacing(4)
        self._watch_btns: list[NtButton] = []
        for row in rink.ROWS:
            button = NtButton(f"🔴  {row.index + 1}",
                              accent=rink.ROW_COLOURS[row.index], upper=False)
            button.clicked.connect(
                lambda _checked=False, i=row.index: self._toggle_watch(i))
            watch_line.addWidget(button)
            self._watch_btns.append(button)
        layout.addLayout(watch_line)

        layout.addSpacing(4)
        layout.addWidget(self._section_label("СТОЯЧИЕ ВРАТАРИ"))

        stand_line = QHBoxLayout()
        stand_line.setSpacing(4)
        self._stand_btns: list[NtButton] = []
        for row in rink.ROWS:
            button = NtButton(f"Стоит {row.index + 1}",
                              accent=rink.ROW_COLOURS[row.index], upper=False)
            button.clicked.connect(
                lambda _checked=False, i=row.index: self._toggle_stand(i))
            stand_line.addWidget(button)
            self._stand_btns.append(button)
        layout.addLayout(stand_line)

        layout.addLayout(self._build_log(), stretch=1)

        self._panel.background_failed.connect(
            lambda msg: self._log.add_log(msg, level="error"))
        self._apply_backdrop(fade=False)

        drag = NtDragHandle(dot_color=theme.HK_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        self._status_dot = NtStatusDot(accent=theme.HK_ICE)
        self._status_dot.set_stopped()

        title = QLabel("ХОККЕЙ")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.HK_ICE}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.HK_ICE)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.HK_ICE_SOFT)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)

        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._collapse_btn)
        header.addWidget(self._fav_btn)
        header.addWidget(close_btn)
        return header

    def _build_log(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)

        self._log = LogPanel()
        self._log.setMinimumHeight(_LOG_H)

        head = QHBoxLayout()
        title = QLabel("Hockey Log:")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        title.setStyleSheet(f"color:{theme.HK_TEXT}; background:transparent;")
        head.addWidget(title)
        head.addStretch()
        head.addLayout(build_log_actions(self._log, theme.HK_BORDER))
        block.addLayout(head)

        block.addWidget(self._log, stretch=1)
        return block

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        return label

    def _apply_backdrop(self, fade: bool = True):
        backdrop = getattr(self.config, "background", "") or _default_backdrop()
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self._log.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _crt_open_ready(self) -> bool:
        """Держит анимацию включения, пока у видеофона нет кадра."""
        return self._panel.backdrop_ready

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._settings is not None:
            self._settings.keep_inside_host()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    # ── Кнопки ───────────────────────────────────────────────────────────

    def _on_start_clicked(self):
        self._log.add_log_segments(
            [("Слежение не запускается — ", theme.ACCENT_AMBER),
             (_DISABLED_TEXT, theme.TEXT_SECONDARY)], level="plain")

    # ── Разметка катка поверх игры ───────────────────────────────────────

    def _toggle_row(self, index: int):
        """Показать или убрать один ряд.

        Молча: что именно нарисовалось, видно на экране, а числа ряда
        печатались одинаковой строкой на каждое нажатие и забивали лог тем,
        что и так лежит константами в rink.py.
        """
        if index in self._rows_shown:
            self._rows_shown.discard(index)
        else:
            self._rows_shown.add(index)
        self._row_btns[index].set_active(index in self._rows_shown)
        self._refresh_zones()

    def _toggle_zone(self):
        self._zone_shown = not self._zone_shown
        self._zone_btn.set_active(self._zone_shown)
        self._refresh_zones()

    def _refresh_zones(self):
        """Перерисовать разметку целиком.

        Оверлей хранит один список прямоугольников, поэтому всё включённое
        рисуется за один вызов — так любую кнопку можно щёлкать, не сбивая
        остальные. Оранжевые полосы берутся только у показанных рядов, так
        что погашенный ряд уносит свою полосу с собой.
        """
        shown = [row for row in rink.ROWS if row.index in self._rows_shown]
        boxes: list[tuple[QRect, QColor]] = [
            (self._band(row, row.wall_left, row.wall_right),
             QColor(rink.ROW_COLOURS[row.index]))
            for row in shown]
        if self._zone_shown:
            orange = QColor(rink.ZONE_COLOUR)
            boxes += [(self._band(row, *rink.board_zone(row)), orange)
                      for row in shown]
        if boxes:
            self._zones.show_zones(boxes)
        else:
            self._zones.clear()

    @staticmethod
    def _band(row, left: int, right: int) -> QRect:
        """Прямоугольник от `left` до `right` в полосе этого ряда.

        Вертикаль одна и та же у ряда и у его оранжевой полосы — см.
        rink.BAND_H. Полосы соседних рядов не пересекаются, иначе одно и то
        же красное засчиталось бы двум рядам сразу.

        По ширине ряд идёт от борта до борта, а не во весь каток: за бортом
        вратарь не бывает, и полоса туда показывала бы ряд шире, чем он есть.
        """
        return QRect(left, row.top, right - left, rink.BAND_H)

    # ── Ловля красного ───────────────────────────────────────────────────

    def _toggle_watch(self, index: int):
        """Включить или выключить ловлю в полосе одного ряда.

        Рядов может ловиться сколько угодно сразу: кадр всё равно снимается
        один на весь каток, и каждая полоса просто вырезается из него.
        """
        if index in self._watching:
            watch = self._watches.pop(index)
            self._marks.pop(index, None)
            self._watching.discard(index)
            self._log.add_log_segments(
                [(f"Ряд {index + 1} — ", rink.ROW_COLOURS[index]),
                 ("ловля выключена", theme.TEXT_SECONDARY),
                 (f".  Кадров {watch.frames}, въездов {watch.events}",
                  theme.TEXT_DIM)], level="plain")
            self._stop_capture_if_idle()
        else:
            if not self._start_capture():
                return          # игра не найдена, причина уже в логе
            self._watching.add(index)
            self._watches[index] = red_watch.RedWatch(rink.BAND_H)
            self._marks[index] = []
            # Рамка с прошлого замера этого ряда больше ничего не значит:
            # мерить заново — значит и ехать заново.
            self._drop_mover(index)
            self._log_watch_started(index)
        self._watch_btns[index].set_active(index in self._watching)

    # ── Стоячие вратари ──────────────────────────────────────────────────

    def _toggle_stand(self, index: int):
        """Посмотреть секунду на весь ряд и решить, кто в нём не двигается.

        Отвечает сам и сам же выключается — смотреть дольше секунды нечего,
        а результат либо есть, либо его нет.
        """
        if index in self._standing:
            self._standing.pop(index)
            self._stand_btns[index].set_active(False)
            self._stop_capture_if_idle()
            return
        if not self._start_capture():
            return
        self._standing[index] = standing.StandWatch()
        # Прошлый ответ по этому ряду больше не ответ.
        self._standers.pop(index, None)
        self._stand_btns[index].set_active(True)
        self._move_tick()
        self._log.add_log_segments(
            [(f"Ряд {index + 1} — ", rink.ROW_COLOURS[index]),
             (f"смотрю {standing.WINDOW_S:.0f} с, кто стоит на месте",
              theme.TEXT_SECONDARY)], level="plain")

    def _finish_standing(self, index: int):
        """Секунда вышла — сказать, кто стоял, и обвести его."""
        watch = self._standing.pop(index)
        self._stand_btns[index].set_active(False)
        found = watch.standers()
        colour = rink.ROW_COLOURS[index]

        if not found:
            self._log.add_log_segments(
                [(f"Ряд {index + 1} — ", colour),
                 ("стоячих нет", theme.TEXT_SECONDARY),
                 (f"  (кадров {watch.frames})", theme.TEXT_DIM)],
                level="plain")
        else:
            self._standers[index] = [s.x for s in found]
            spots = ",  ".join(f"x {s.x:.0f} ({s.seen * 100:.0f}% кадров)"
                               for s in found)
            self._log.add_log_segments(
                [(f"Ряд {index + 1} — ", colour),
                 (f"стоит {len(found)}", theme.ACCENT_GREEN),
                 (f":  {spots}", theme.TEXT_SECONDARY),
                 (f"  (кадров {watch.frames})", theme.TEXT_DIM)],
                level="plain")
        self._move_tick()
        self._stop_capture_if_idle()

    # ── Захват ───────────────────────────────────────────────────────────

    def _start_capture(self) -> bool:
        """Поднять цикл захвата, если он ещё не идёт. False — не с чего снимать.

        Один цикл на обе задачи, и это не экономия, а необходимость: Windows
        Graphics Capture отдаёт каждый кадр один раз на поток, так что два
        таймера, дёргающих его вперемешку, отбирали бы кадры друг у друга и
        каждый шёл бы вдвое медленнее.
        """
        if self._watch_timer is not None:
            return True
        if not self._wm.get_game_hwnd():
            self._log.add_log("Игровое окно не найдено", level="error")
            return False
        # Быстрый захват просится здесь, а не в __init__: переключатель общий
        # на всё приложение, а окна модов строятся при старте хелпера — там
        # он перевёл бы на новый бэкенд и садовника, который об этом не
        # просил.
        fast = set_wgc_enabled(True)
        self._catch_dir = red_watch.catch_dir()
        self._watch_started = time.monotonic()
        self._watch_frames = 0
        self._failing_since = None
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_tick)
        self._watch_timer.start(_WATCH_MS)
        # Молча, пока всё как обычно. Голос подаётся только на откате к
        # PrintWindow: там кадров вдвое меньше, а период меряется по кадрам —
        # это единственное, что стоит знать до, а не после замера.
        if not fast:
            self._log.add_log_segments(
                [("Быстрый захват недоступен — ", theme.ACCENT_AMBER),
                 ("снимаю через PrintWindow, кадров вдвое меньше и период "
                  "выйдет грубее", theme.TEXT_SECONDARY)], level="plain")
        return True

    def _stop_capture_if_idle(self):
        """Погасить захват, когда обе задачи закончились."""
        if not self._watching and not self._standing:
            self._stop_capture()

    def _stop_capture(self, reason: str = ""):
        """Погасить цикл и сказать, с какой частотой он успевал снимать."""
        if self._watch_timer is not None:
            self._watch_timer.stop()
            self._watch_timer = None
        for index in list(self._watching):
            self._watch_btns[index].set_active(False)
        for index in list(self._standing):
            self._stand_btns[index].set_active(False)
        self._watching.clear()
        self._watches.clear()
        self._marks.clear()
        self._standing.clear()

        elapsed = max(1e-6, time.monotonic() - self._watch_started)
        frames, self._watch_frames = self._watch_frames, 0
        segments = [("Захват остановлен", theme.ACCENT_AMBER)]
        if reason:
            segments.append((f" — {reason}", theme.ACCENT_RED))
        segments += [(f".  Снято {frames} кадров", theme.HK_ICE),
                     (f" за {elapsed:.1f} с — ", theme.TEXT_SECONDARY),
                     (f"{frames / elapsed:.0f} к/с", theme.HK_ICE)]
        self._log.add_log_segments(segments, level="plain")

    def _watch_tick(self):
        """Один кадр катка на все задачи: и ловлю у борта, и поиск стоячих."""
        if not self._watching and not self._standing:
            return
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._stop_capture("окно игры пропало")
            return
        try:
            frame = grab_window(hwnd, rink.REGION)
        except Exception as exc:
            failed_at = time.monotonic()
            if self._failing_since is None:
                self._failing_since = failed_at
            elif failed_at - self._failing_since >= _WATCH_FAIL_S:
                self._stop_capture(f"каток не снимается: {exc}")
            return      # разовый сбой захвата стоит одного кадра, не ловли
        self._failing_since = None
        self._watch_frames += 1

        now = time.monotonic()
        self._stand_tick(frame, now)
        for index in sorted(self._watching):
            watch = self._watches.get(index)
            if watch is None:
                continue        # ряд закрыл замер на этом же кадре
            row = rink.ROWS[index]
            left, top, width, height = rink.zone_box(row)
            # Полоса с запасом сверху и снизу: по нему детектор отличает
            # своего вратаря от чужого, чьё тело проходит через полосу.
            patch = frame[top - red_watch.TOP_PAD:
                          top + height + red_watch.BELOW_PX,
                          left:left + width]
            event = watch.feed(patch, now)
            if event is None:
                continue
            marks = self._marks[index]
            marks.append(event.at)
            self._report_catch(frame, row, event, len(marks))
            if len(marks) >= 2:
                # Голову берём из того же кадра, что закрыл замер: рамка
                # поедет оттуда, где вратарь был в этот момент, а не оттуда,
                # где полоса начинается.
                centre = red_watch.red_centre(patch, rink.BAND_H)
                head_x = (None if centre is None
                          else rink.RINK_LEFT + left + centre)
                self._finish_lap(index, head_x)

    def _stand_tick(self, frame, now: float):
        """Показать каждому ищущему ряду его полосу целиком, от борта до борта.

        Целиком, а не полосу у борта: стоячий стоит где угодно, и в полосу у
        борта он попадёт разве что случайно.
        """
        for index in sorted(self._standing):
            row = rink.ROWS[index]
            left, top, width, _height = rink.row_box(row)
            strip = frame[top - red_watch.TOP_PAD:
                          top + rink.BAND_H + red_watch.BELOW_PX,
                          left:left + width]
            # Ширина вратаря — она же мерка, ближе которой два пятна не могут
            # быть двумя вратарями (см. red_blobs).
            blobs = red_watch.red_blobs(strip, rink.BAND_H, row.body_w)
            watch = self._standing[index]
            watch.feed([row.wall_left + (a + b) / 2 for a, b in blobs], now)
            if watch.ready():
                self._finish_standing(index)

    def _report_catch(self, frame, row, event: red_watch.RedEvent,
                      ordinal: int):
        """Одна строка на въезд и снимок того кадра, на котором сработало."""
        name = ""
        try:
            path = red_watch.save_catch(frame, rink.zone_box(row),
                                        row.index + 1, event, self._catch_dir)
            name = path.name
        except Exception as exc:
            self._log.add_log(f"Снимок не сохранился: {exc}", level="error")

        segments = [(f"Ряд {row.index + 1} — ", rink.ROW_COLOURS[row.index]),
                    (f"появился красный ({ordinal}-й въезд)",
                     theme.ACCENT_RED),
                    (f"  {event.share * 100:.1f}%", theme.HK_ICE),
                    (f"  (было {event.before * 100:.1f}%, скачок "
                     f"{event.jump * 100:+.1f})", theme.TEXT_SECONDARY)]
        if name:
            segments.append((f"  → {name}", theme.HK_ICE_SOFT))
        self._log.add_log_segments(segments, level="plain")

    def _finish_lap(self, index: int, head_x: float | None = None):
        """Второй въезд закрывает замер: считаем период и скорость.

        Ряд после этого перестаёт ловить — не потому, что дальше нечего
        мерить, а потому что мерить дальше нужно уже иначе: третий и
        четвёртый въезд проверяли бы период на повторяемость, а это следующий
        шаг, и мешать его с первым замером незачем.
        """
        row = rink.ROWS[index]
        first, second = self._marks[index][:2]
        lap = red_watch.Lap(first=first, second=second,
                            distance=rink.round_trip(row))

        self._watching.discard(index)
        self._watches.pop(index, None)
        self._marks.pop(index, None)
        self._watch_btns[index].set_active(False)

        colour = rink.ROW_COLOURS[index]
        self._log.add_log_segments(
            [(f"Ряд {index + 1} — ", colour), ("замер готов", theme.ACCENT_GREEN)],
            level="plain")
        self._log.add_log_segments(
            [("    период ", theme.TEXT_SECONDARY),
             (f"{lap.seconds:.3f} с", theme.HK_ICE),
             ("  между двумя въездами в полосу", theme.TEXT_SECONDARY)],
            level="plain")
        self._log.add_log_segments(
            [("    ряд ", theme.TEXT_SECONDARY),
             (f"{row.wall_left}…{row.wall_right}", theme.HK_ICE),
             (f" = {row.span} px", theme.TEXT_SECONDARY),
             (f", вратарь {row.body_w} px", theme.TEXT_SECONDARY),
             ("  →  путь туда и обратно ", theme.TEXT_SECONDARY),
             (f"2×{row.span - row.body_w} = {lap.distance} px",
              theme.HK_ICE)], level="plain")
        self._log.add_log_segments(
            [("    скорость ", theme.HK_ICE_SOFT),
             (f"{lap.speed:.0f} px/с", theme.ACCENT_GREEN)], level="plain")

        self._start_mover(index, lap, head_x)
        self._stop_capture_if_idle()

    # ── Едущая рамка ─────────────────────────────────────────────────────

    def _start_mover(self, index: int, lap: red_watch.Lap,
                     head_x: float | None):
        """Пустить рамку с измеренной скоростью от того места, где был вратарь.

        Вправо: второй въезд — это въезд в полосу у **правого** борта, то есть
        в этот момент он ехал туда. Дальше рамка сама отражается от бортов —
        ничего не предсказывается, скорость уже известна, и остаётся арифметика.
        """
        row = rink.ROWS[index]
        low, high = rink.head_bounds(row)
        if head_x is None:
            head_x = high     # событие бывает только по красному, но всё же
        start = min(max(float(head_x), low), high)

        self._patrols[index] = patrol.Patrol(
            start_x=start, start_at=lap.second, speed=lap.speed,
            low=low, high=high, rightward=True)

        if self._move_timer is None:
            self._move_timer = QTimer(self)
            self._move_timer.timeout.connect(self._move_tick)
            self._move_timer.start(_MOVE_MS)
        self._move_tick()

        self._log.add_log_segments(
            [(f"Ряд {index + 1} — ", rink.ROW_COLOURS[index]),
             ("поехала рамка", _MOVER_COLOUR),
             (f" от x {start:.0f} вправо, отражается между "
              f"{low:.0f} и {high:.0f}", theme.TEXT_SECONDARY)],
            level="plain")

    @staticmethod
    def _goalie_box(row, x: float) -> QRect:
        """Весь вратарь: от линии шлема до коньков и во всю его ширину — то
        есть ровно то, что шайбе придётся облететь."""
        return QRect(int(round(x - row.body_w / 2)), row.y,
                     row.body_w, row.feet_dy)

    def _move_tick(self):
        """Перерисовать все фиолетовые рамки — и едущие, и стоячие.

        Один слой на тех и других: цвет у них общий, а отличаются они тем,
        что одни едут, а другие нет, — это и так видно.
        """
        now = time.monotonic()
        colour = QColor(_MOVER_COLOUR)
        boxes = [(self._goalie_box(rink.ROWS[index], moving.at(now)), colour)
                 for index, moving in sorted(self._patrols.items())]
        boxes += [(self._goalie_box(rink.ROWS[index], x), colour)
                  for index, spots in sorted(self._standers.items())
                  for x in spots]
        if boxes:
            self._movers.show_zones(boxes)
        else:
            self._movers.clear()

    def _drop_mover(self, index: int):
        """Убрать едущую рамку одного ряда, оставив остальные."""
        if self._patrols.pop(index, None) is None:
            return
        self._park_move_timer()
        self._move_tick()

    def _park_move_timer(self):
        """Таймер нужен только едущим — стоячая рамка нарисована и стоит."""
        if not self._patrols and self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None

    def _stop_movers(self):
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        self._patrols.clear()
        self._standers.clear()
        self._movers.clear()

    def _log_watch_started(self, index: int):
        """Одной строкой: какой ряд ловит. Границы полосы и пороги отсюда
        убраны — они лежат константами и в логе только шумели."""
        self._log.add_log_segments(
            [(f"Ряд {index + 1} — ", rink.ROW_COLOURS[index]),
             ("ловлю красное, замер закроется на втором въезде",
              theme.TEXT_SECONDARY)], level="plain")

    # ── Настройки ────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings is None:
            self._settings = HockeySettingsPanel(self.config, self.save_fn,
                                                 self)
        self._settings.toggle()

    def _toggle_favorite(self):
        self.config.favorite = not self.config.favorite
        self._fav_btn.setText("★" if self.config.favorite else "☆")
        self.save_fn()

        if self.config.favorite:
            verb, tail, colour = ("добавлено", " в автозагрузку при старте",
                                  theme.ACCENT_GREEN)
        else:
            verb, tail, colour = ("удалено", " из автозагрузки при старте",
                                  theme.ACCENT_AMBER)
        segments = [
            (f"Окно {self.module_name} ", theme.TEXT_SECONDARY),
            (verb, colour),
            (tail, theme.TEXT_SECONDARY),
        ]
        self._log.add_log_segments(segments)
        if self.parent_overlay:
            self.parent_overlay.add_log_segments(segments)

    # ── Закрытие ─────────────────────────────────────────────────────────

    def _teardown(self):
        """Ничего нашего не должно пережить окно: ни цикл захвата, ни
        разметка — она живёт в отдельном окне и сама не закроется."""
        if self._watch_timer is not None:
            self._watch_timer.stop()
            self._watch_timer = None
        self._watching.clear()
        self._watches.clear()
        self._marks.clear()
        self._standing.clear()
        self._stop_movers()
        self._zones.clear()
        self._panel.stop_background()
