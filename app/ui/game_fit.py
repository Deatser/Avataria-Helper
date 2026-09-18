# app/ui/game_fit.py
"""Окно помощника, живущее в долях игры, а не в пикселях экрана.

В config.json у каждого окна лежат x, y, width, height. Записаны они при
развёрнутой игре и до сих пор понимались буквально: столько-то пикселей от
угла клиентской области. Свернуть игру в четверть экрана — и окно шириной
420 пикселей закрывает её половину, а стоящее в (420, 500) оказывается за
краем и обрезается.

Здесь эти числа читаются как эталонные: место и размер при игре эталонного
размера. Живое место считается от них масштабом игры (см. app/core/
game_geometry.py), а обратно в config пишется опять эталонное — иначе
каждый перетаск на ужатой игре записывал бы уменьшенные числа, и окно
съезжало бы к углу с каждым сеансом.

Само содержимое окна при этом не перевёрстывается, а **показывается
уменьшенным**: панель остаётся своего, расчётного размера, а окно
показывает её через QGraphicsView со сжатием. Шрифты, отступы, плитки,
видеофон — всё уезжает одним куском и в тех же пропорциях, и ни одному окну
не нужно знать, что оно уменьшено.

Вид заводится лениво — только когда игра действительно поменяла размер.
Пока она развёрнута, панель остаётся обычным дочерним виджетом, каким была
всегда, и весь этот файл ни на что не влияет.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView

from app.core.game_geometry import geometry

# Ниже этого окно помощника читать уже нечем: подписи на плитках
# превращаются в серую рябь. Игра может быть и меньше — окно просто
# перестанет за ней ужиматься и займёт большую её долю.
MIN_UI_SCALE = 0.35


class _ScaledView(QGraphicsView):
    """Вид, который отдаёт окну всё, что не забрал ни один виджет внутри.

    Фон окна и его края — это и есть перетаскивание и ресайз
    (BackgroundDragMixin, ResizeMixin), и живут они на mousePressEvent
    самого окна. Вид же накрывает окно целиком, и без этой пересылки окно
    переставало бы и таскаться, и меняться в размере: все нажатия оседали
    бы в нём, а кнопкам внутри доставалось бы только то, что попало прямо
    в них.
    """

    def __init__(self, scene, host):
        super().__init__(scene, host)
        self._host = host
        self._edge_cursor = False
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    # ── Мышь ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if not event.isAccepted() or self._host_busy():
            self._host.mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not event.isAccepted() or self._host_busy():
            self._host.mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        # Без кнопки — ради подсказки курсора у края; с кнопкой — только
        # пока окно действительно тащат или тянут за край, иначе начатый
        # перетаск обрывался бы, стоило курсору проехать над кнопкой.
        if self._host_busy() or not event.buttons():
            self._host.mouseMoveEvent(event)
            self._sync_edge_cursor(event)

    def _host_busy(self) -> bool:
        return bool(getattr(self._host, "_bg_dragging", False)
                    or getattr(self._host, "_rsz_dir", 0))

    def _sync_edge_cursor(self, event):
        """Курсор ресайза принадлежит окну, а видно курсор вида.

        Только у края и только когда состояние меняется: в остальное время
        курсор ставят себе виджеты внутри сцены, и перебивать их каждым
        движением мыши значит гасить их собственные подсказки.
        """
        on_edge = bool(self._host._edge_dir(event.position().toPoint()))
        if on_edge == self._edge_cursor:
            return
        self._edge_cursor = on_edge
        if on_edge:
            self.viewport().setCursor(self._host.cursor())
        else:
            self.viewport().unsetCursor()


class GameFitMixin:
    """Подмешивается **перед** CollapseMixin и ResizeMixin.

    Окно вызывает `_init_game_fit(panel)` после того, как построило свой
    интерфейс, и `layout_panel()` вместо прежней расстановки панели.
    """

    # ── Заведение ────────────────────────────────────────────────────────────

    def _init_collapse(self, panel, window_manager=None):
        """Единственный крючок, который зовёт каждое окно после сборки."""
        super()._init_collapse(panel, window_manager)
        self._init_game_fit(panel)

    def _init_game_fit(self, panel):
        if getattr(self, "_fit_panel", None) is panel:
            return
        self._fit_panel = panel
        self._ui_scale  = 1.0
        self._fit_view: QGraphicsView | None = None
        self._fit_scene: QGraphicsScene | None = None
        self._fit_min   = (self.minimumWidth(), self.minimumHeight())

    @property
    def ui_scale(self) -> float:
        return getattr(self, "_ui_scale", 1.0)

    # ── Появление окна ───────────────────────────────────────────────────────

    def showEvent(self, event):
        """Подогнать окно под игру прежде, чем его успеют увидеть.

        Окно собирается по эталонным числам из config, а открыть его могут
        когда игра уже ужата: до сих пор такое окно вылезало за её край во
        весь свой развёрнутый размер и оставалось таким до первой смены
        размера игры — которой за весь сеанс могло и не случиться.

        До super(), а не после: показ запускает анимацию включения, а она
        снимает окно таким, какое оно в этот миг, и снимать надо уже
        подогнанное.
        """
        self.fit_to_game()
        super().showEvent(event)

    # ── Где лежат эталонные числа этого окна ─────────────────────────────────

    def _fit_config(self):
        """Секция config с эталонными местом и размером этого окна.

        У окна мода это его собственная секция, и она лежит в self.config
        целиком. Главное окно помощника держит там весь ConfigManager и
        отвечает на этот вопрос само.
        """
        return getattr(self, "config", None)

    def fit_room_height(self) -> int:
        """Сколько высоты у окна вообще есть — по игре, а не по монитору.

        Окно живёт внутри игры и вылезти за неё не может; предел в высоту
        монитора на игре в четверть экрана не ограничивает ровно ничего.
        Ноль — мерить не по чему, и звавший обходится без предела.
        """
        picture = self._fit_geometry().current
        if picture is not None and picture.valid:
            return picture.height
        screen = self.screen()
        return screen.availableGeometry().height() if screen is not None else 0

    # ── Масштаб содержимого ──────────────────────────────────────────────────

    def set_ui_scale(self, scale: float):
        """Показывать содержимое окна во столько раз крупнее или мельче."""
        scale = max(MIN_UI_SCALE, float(scale))
        if getattr(self, "_fit_panel", None) is None:
            return
        if abs(scale - self.ui_scale) < 0.005 and (
                scale == 1.0 or self._fit_view is not None):
            return

        self._ui_scale = scale
        if self._fit_view is None:
            if scale == 1.0:
                return          # игра эталонного размера — заводить нечего
            self._build_fit_view()
        self._fit_view.resetTransform()
        self._fit_view.scale(scale, scale)
        self._apply_min_size()
        self.layout_panel()

    def _apply_min_size(self):
        """Свой минимум окно задаёт в расчётных пикселях.

        Он жёсткий — Qt не даст окну стать меньше, — и на ужатой игре именно
        он не давал уменьшить окно вообще: минимум в 519 пикселей на игре
        шириной 800 это уже две трети её ширины.
        """
        min_w, min_h = self._fit_min
        width  = max(1, int(min_w * self.ui_scale))
        height = max(1, int(min_h * self.ui_scale))
        # И не больше самой игры: минимум жёсткий, Qt не даст окну стать
        # меньше него — а окно, которому минимум велит быть больше игры,
        # Windows прижимает к дальнему краю и не даёт сдвинуть вовсе.
        picture = self._fit_geometry().current
        if picture is not None and picture.valid:
            width, height = min(width, picture.width), min(height, picture.height)
        self.setMinimumSize(width, height)

    def _build_fit_view(self):
        """Убрать панель в сцену и показать её через сжимающий вид.

        В обратную сторону это не разбирается: вернуть панель обратно в
        дочерние виджеты значит второй раз пересадить всё дерево на живом
        окне — с видеофоном, анимациями и нативно прицепленным к игре
        родителем. Вид с единичным преобразованием выглядит ровно так же,
        как его отсутствие, и обходится дешевле, чем эта пересадка.
        """
        panel = self._fit_panel
        # Окно ещё не масштабировано — его минимум сейчас и есть расчётный.
        self._fit_min = (self.minimumWidth(), self.minimumHeight())
        scene = QGraphicsScene(self)
        view  = _ScaledView(scene, self)
        view.setFrameShape(QFrame.NoFrame)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Иначе вид центрирует сцену в себе, и уменьшенная панель отходит
        # от угла окна на половину освободившегося места.
        view.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        view.setStyleSheet("background: transparent; border: none;")
        view.setAttribute(Qt.WA_TranslucentBackground)
        view.viewport().setAutoFillBackground(False)
        view.setRenderHints(QPainter.Antialiasing |
                            QPainter.SmoothPixmapTransform)
        # Без этого сцена панель не возьмёт: addWidget работает только с
        # виджетом без родителя, а панель — ребёнок окна. Молча: проверять
        # нечего, и промах виден только тем, что окно осталось прежним, а
        # поверх него лёг пустой вид, съедающий мышь.
        panel.setParent(None)
        scene.addWidget(panel).setPos(0, 0)
        panel.show()
        view.setGeometry(0, 0, self.width(), self.height())
        view.show()
        # Ниже всех прочих детей окна: анимация включения (CrtPowerOn)
        # рисуется поверх содержимого и должна остаться поверх.
        view.lower()
        self._fit_scene, self._fit_view = scene, view

    # ── Расстановка панели ───────────────────────────────────────────────────

    def layout_panel(self):
        """Панель на всё окно — в своём, расчётном размере.

        Без вида это буквально размер окна, как было всегда. С видом —
        размер окна, делённый на масштаб: панель остаётся крупной, а
        мелким её делает уже вид.
        """
        panel = getattr(self, "_fit_panel", None)
        if panel is None:
            return
        if self._fit_view is None:
            panel.setGeometry(0, 0, self.width(), self.height())
            return
        width  = max(1, int(round(self.width()  / self._ui_scale)))
        height = max(1, int(round(self.height() / self._ui_scale)))
        panel.setGeometry(0, 0, width, height)
        self._fit_scene.setSceneRect(0, 0, width, height)
        self._fit_view.setGeometry(0, 0, self.width(), self.height())

    # ── Свёрнутая высота ─────────────────────────────────────────────────────

    def _collapsed_height(self) -> int:
        """CollapseMixin меряет по шапке внутри панели — а она расчётная."""
        return max(1, int(round(super()._collapsed_height() * self.ui_scale)))

    # ── Перевод места окна ───────────────────────────────────────────────────
    # Окно стоит в клиентской области игры, а масштабируется вместе с её
    # картинкой — она бывает уже клиентской области на ширину полей. Отсюда
    # и слагаемое: сначала дойти до угла картинки, дальше считать масштабом.

    def _picture_offset(self) -> tuple[int, int]:
        """Насколько картинка игры отступает от угла клиентской области.

        Спрашивается у геометрии, а не меряется здесь заново: там картинка
        и клиентская область — пара из одного замера. Свежая клиентская
        область в паре с картинкой, померенной тактом раньше, даёт отступ
        размером со сдвиг окна игры — а разворот из полноэкранки двигает
        его всегда, и все окна помощника уезжали на этот сдвиг разом.
        """
        return self._fit_geometry().offset_in_client

    def _fit_wm(self):
        return getattr(self, "_wm", None) or getattr(self, "wm", None)

    def fit_scale(self) -> float:
        """Во сколько раз это окно меньше эталонного — с тем же полом, что
        и у содержимого.

        Один и тот же множитель на коробку окна, на его место и на масштаб
        показа: разойдись они, и содержимое либо обрежется по краю окна,
        либо не достанет до него.
        """
        return max(MIN_UI_SCALE, self._fit_geometry().scale)

    def _fit_geometry(self):
        """Геометрия того окна игры, над которым висит это окно.

        Не общая: Тропикания — отдельное окно со своим размером, и мерить
        её окна масштабом Аватарии значит ломать оба мода разом.
        """
        wm = self._fit_wm()
        hwnd = wm.get_game_hwnd() if wm is not None else None
        return geometry(hwnd)

    def _fit_scales(self) -> tuple[float, float]:
        """Доли картинки игры по каждой оси отдельно — только для места.

        Размер окна меряется одним числом на обе оси (fit_scale), иначе
        окно перекашивало бы вслед за игрой. А вот место — это ровно доля
        ширины и доля высоты, и они разные: потяните игру за правый край,
        и общий множитель min(…) увёл бы окна ещё и вверх, хотя по высоте
        не изменилось ничего. Это и есть та самая ломаная езда окон.

        Множители тут настоящие, без пола MIN_UI_SCALE: пол держит окно
        читаемым, но если считать по нему ещё и место, окно на совсем
        маленькой игре уезжает за её правый и нижний край.
        """
        geom = self._fit_geometry()
        ref, cur = geom.reference, geom.current
        if ref is None or cur is None or not ref.valid or not cur.valid:
            return 1.0, 1.0
        return cur.width / ref.width, cur.height / ref.height

    def to_live_offset(self, x: int, y: int) -> tuple[int, int]:
        """Эталонное место окна → место в нынешней клиентской области."""
        scale_x, scale_y = self._fit_scales()
        off_x, off_y = self._picture_offset()
        return int(round(x * scale_x)) + off_x, int(round(y * scale_y)) + off_y

    def to_reference_offset(self, x: int, y: int) -> tuple[int, int]:
        """И обратно — то, что уходит в config.json.

        Теми же двумя множителями, что и вперёд: разойдись они, и каждый
        перетаск записывал бы не то место, куда окно положили.
        """
        scale_x, scale_y = self._fit_scales()
        off_x, off_y = self._picture_offset()
        return (int(round((x - off_x) / (scale_x or 1.0))),
                int(round((y - off_y) / (scale_y or 1.0))))

    def _keep_inside_picture(self, x: int, y: int,
                             width: int, height: int) -> tuple[int, int]:
        """Не дать окну вылезти за картинку игры.

        Место — доля картинки, а размер снизу упирается в MIN_UI_SCALE: на
        маленькой игре окно занимает большую долю, чем занимало, и стоящее
        у правого края вылезает за него. Тогда его подпирают к краю — а в
        config остаётся прежнее место, и на развёрнутой игре окно вернётся
        ровно туда, где стояло.
        """
        picture = self._fit_geometry().current
        if picture is None or not picture.valid:
            return x, y
        off_x, off_y = self._picture_offset()
        return (min(max(off_x, x), off_x + max(0, picture.width  - width)),
                min(max(off_y, y), off_y + max(0, picture.height - height)))

    def to_reference_size(self, width: int, height: int) -> tuple[int, int]:
        scale = self.ui_scale or 1.0
        return max(1, int(round(width / scale))), max(1, int(round(height / scale)))

    # ── Подгонка под игру ────────────────────────────────────────────────────

    def fit_box(self, config=None) -> tuple[int, int, int, int] | None:
        """Где и какого размера окно должно лежать при нынешней игре.

        None — считать не по чему: игры нет или эталон не записан. Одно
        место на всех, кто задаёт окну коробку (fit_to_game и первая
        расстановка из main.py): считай они каждый по-своему, окно на
        старте лежало бы не там, куда его кладёт первая же подгонка.
        """
        cfg = config if config is not None else self._fit_config()
        if cfg is None or not hasattr(cfg, "width"):
            return None
        geom = self._fit_geometry()
        if geom.reference is None or geom.current is None:
            return None

        scale  = self.fit_scale()
        # Не больше самой игры. Пол MIN_UI_SCALE держит содержимое
        # читаемым, но окно шире или выше игры не помещается в неё ни при
        # каком месте: Windows прижимает такое окно к дальнему краю и не
        # даёт сдвинуть ни на пиксель — это и есть «примагнитило к углу».
        width  = min(max(1, int(round(cfg.width  * scale))), geom.current.width)
        height = min(max(1, int(round(cfg.height * scale))), geom.current.height)
        x, y   = self.to_live_offset(getattr(cfg, "x", 0), getattr(cfg, "y", 0))
        x, y   = self._keep_inside_picture(x, y, width, height)
        return x, y, width, height

    def fit_to_game(self, config=None) -> bool:
        """Переставить и перемерить окно под нынешний размер игры.

        False — подгонять не по чему: игры нет или эталон не записан, и
        тогда окно остаётся ровно там, где стояло.
        """
        if getattr(self, "_fit_panel", None) is None:
            return False    # окно ещё не собрано: двигать коробку, не умея
                            # ужать содержимое, значит его же и обрезать
        box = self.fit_box(config)
        if box is None:
            return False
        x, y, width, height = box
        self.set_ui_scale(self.fit_scale())
        # Отдельно от set_ui_scale: игра могла стать меньше, не поменяв
        # масштаба — он упирается в пол MIN_UI_SCALE, — и минимум окна
        # остался бы от прошлого, большего её размера.
        self._apply_min_size()

        # Сначала Qt, потом Windows: прицепленное к игре окно двигает
        # SetWindowPos, но Qt держит своё представление о геометрии и
        # подменяет его при первой же перерисовке, если ему не сказать.
        self.resize(width, height)
        wm = self._fit_wm()
        if wm is not None and wm.get_game_hwnd():
            wm.move_window(int(self.winId()), x, y, width, height)
        else:
            self.move(x, y)
        self.layout_panel()
        return True
