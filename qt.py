"""
字幕编辑器 Qt 版（嵌入式 mpv 真预览）。

自包含：ASS 读写、txt->ass 生成、ffmpeg 渲染、调 run_asr 语音识别、颜色/时间转换、
字体枚举等纯逻辑都在本文件内；UI 用 PySide6，预览用嵌入式 libmpv。

依赖：pip install PySide6 python-mpv  + libmpv-2.dll（放本目录或加入 PATH）
另需同目录的 run_asr.py（语音识别）、系统 PATH 里的 ffmpeg/ffprobe。
启动：python subtitle_editor_qt.py
"""

import os
import sys
import json
import copy
import time
import threading
import subprocess

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QEvent, QPoint, QRect, QByteArray, Signal, qInstallMessageHandler
from PySide6.QtGui import QColor, QShortcut, QKeySequence, QPainter, QPolygonF, QPen, QPainterPath, QFont, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QHBoxLayout, QVBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QColorDialog, QFileDialog,
    QMessageBox, QAbstractItemView, QSlider, QLineEdit, QAbstractSpinBox,
    QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QSizePolicy, QScrollArea, QGridLayout,
    QStyledItemDelegate, QMenu, QTabBar, QStyle, QStyleOptionViewItem, QStackedWidget,
    QKeySequenceEdit, QTabWidget, QTreeWidget, QTreeWidgetItem,
)
import re as _re

VIDEO_FILTER = "视频 (*.mp4 *.mkv *.flv *.mov *.avi *.ts *.webm);;所有文件 (*.*)"


import subtitle_core as core


# ── mpv 加载 ───────────────────────────────────────────────────────────────
# 把本脚本目录加入 PATH，这样只要把 libmpv-2.dll 放在项目目录即可，无需改系统 PATH
_HERE = os.path.dirname(os.path.abspath(__file__))
os.environ['PATH'] = _HERE + os.pathsep + os.environ.get('PATH', '')
if hasattr(os, 'add_dll_directory'):   # Python 3.8+ Windows 需要显式声明 dll 目录
    try:
        os.add_dll_directory(_HERE)
    except OSError:
        pass
try:
    import mpv
    _MPV_OK = True
except Exception as e:
    _MPV_OK = False
    _MPV_ERR = str(e)


# ── 设置（颜色 / 覆盖开关，持久化到 editor_settings.json）─────────────────────
_SETTINGS_FILE = os.path.join(_HERE, 'editor_settings.json')
DEFAULT_SETTINGS = {
    # 皮肤主色（改它在设置里一键推导下面各派生色的深浅；默认淡橙）
    'color_skin':         '#d98c54',
    # 菜单栏（派生默认值对应 color_skin 的"中/最深"档；可在设置里单独微调）
    'color_button':       '#df9e6f',   # 一次性动作按钮（导入/识别/渲染）— 中
    'color_mode_btn':     '#b17244',   # 模式切换按钮 — 最深
    'color_toggle':       '#b17244',   # 开关类按钮（选中）— 最深
    'color_status':       '#FFD700',   # 提示文本颜色（恒黄，不参与主色推导）
    'color_history_btn':  '#000000',   # 历史按钮底色，默认纯黑
    # 批量操作栏
    'color_batch_btn':    '#df9e6f',   # 一次性批量按钮（换行/替换/聚焦）— 中
    # Tab 栏
    'color_header':       '#e8ba98',   # 选中标签底色 — 浅
    'color_tab_x':        '#6c462a',   # 标签关闭叉颜色（深，浅底下可见）
    'color_label':        '#e8ba98',   # 栏目名文字（菜单栏/工具栏/视频/波形/字幕…）
    # 播放
    'color_play':         '#df9e6f',   # 播放/暂停按钮 — 中
    'color_progress':     '#e8ba98',   # 进度条已填充 — 浅
    'color_handle':       '#df9e6f',   # 进度条小圆点 — 中
    'color_playhead':     '#f9eee5',   # 波形/字幕轨 播放线 — 亮色
    # 界面
    'color_ui_bg':        '#000000',   # 界面统一背景，默认纯黑
    'color_scrollbar':    '#e8ba98',   # 滚动条滑块 — 浅
    'color_follow':       '#e8ba98',   # 跟随条填充 / 标记行边框 — 浅
    'history_limit':      100,         # 操作历史（右上角提示）最多记录多少条
    'seek_step':          5,           # 左右键 seek 的秒数
    'follow_focus':       True,        # 是否聚焦：播放行永远高亮，勾选则自动滚动让它保持可见（不改选中）
    'select_follow':      False,       # 选随播放：选中行始终等于当前播放行（默认关闭）
    'seek_lock':          True,        # seek 锁：默认上锁（点字幕/片段行不跳转播放）
    'wave_enabled':       False,       # 是否启用音频波形（默认关闭，在「渲染视频」旁开关）
    'subtrack_enabled':   False,       # 是否显示字幕轨道（剪映式方块，可拖动调时间），默认关闭
    'wave_center':        True,        # 放大波形时播放头是否聚焦跟随
    'wave_focus_pos':     0.5,         # 波形聚焦位置：0.2靠左 / 0.5居中 / 0.8靠右
    'sub_focus_pos':      0.5,         # 字幕聚焦位置：0.3靠上 / 0.5居中 / 0.8靠下
    'ui_font_size':       13,          # 界面整体字号（px）
    'ui_radius':          10,          # 圆角大小（px）：按钮/标签页/输入框等统一圆角
    'window_geometry':    '',          # 上次窗口大小/位置（saveGeometry 的 base64），下次启动恢复
    'keys': {                          # 快捷键（可在设置里改；值为 Qt 可识别的按键名）
        'play_pause': 'Space',         # 播放/暂停
        'seek_back':  'Left',          # 快退（秒数见 seek_step）
        'seek_fwd':   'Right',         # 快进
        'set_start':  '[',             # 字幕编辑：在波形上点选设选中行的开始时间
        'set_end':    ']',             # 字幕编辑：在波形上点选设选中行的结束时间
        'delete_row': 'Del',           # 删除选中行（字幕表 / 片段表）
        'capture':    'C',             # 字幕编辑：进入/退出截取模式（波形拖选生成字幕行）
        'retime':     'R',             # 字幕编辑：进入/退出重定时（波形拖选重设选中行起止）
        'clip_in':    'I',             # 视频剪辑：设入点
        'clip_out':   'O',             # 视频剪辑：设出点
    },
    'style':              dict(core.DEFAULT_STYLE),   # 用户偏好的字体/字号/下边距/颜色/描边/换行
}
SETTINGS = dict(DEFAULT_SETTINGS)

def load_settings():
    try:
        with open(_SETTINGS_FILE, encoding='utf-8') as f:
            SETTINGS.update(json.load(f))
    except Exception:
        pass
    # 快捷键深合并：旧配置缺的键用默认补齐（保证新增动作有默认键）
    merged_keys = dict(DEFAULT_SETTINGS['keys']); merged_keys.update(SETTINGS.get('keys') or {})
    SETTINGS['keys'] = merged_keys
    # 把用户保存的样式套用为默认样式（新建/无 ass 时即用它，保证每次打开一致）
    if isinstance(SETTINGS.get('style'), dict):
        core.DEFAULT_STYLE.update(SETTINGS['style'])

def save_settings():
    try:
        with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(SETTINGS, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

class _NoScrollSpinBox(QSpinBox):
    """数值框：未聚焦时不响应滚轮（把滚轮让给上层/滚动区），
    避免鼠标只是悬停其上滚动却误调了数值。聚焦（点中）后才能滚轮调节。"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    """小数数值框：同 _NoScrollSpinBox，未聚焦时不响应滚轮。"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class _NoScrollComboBox(QComboBox):
    """下拉框：未聚焦时不响应滚轮，避免悬停滚动时误换选项。"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class _HScrollList(QListWidget):
    """Shift+滚轮横向滚动（同字幕表逻辑），用于操作历史等长文本横向查看。"""
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ShiftModifier:
            d = e.angleDelta().y() or e.angleDelta().x()
            if d:
                sb = self.horizontalScrollBar()
                sb.setValue(sb.value() - d)
            e.accept(); return
        super().wheelEvent(e)


class MergeList(QListWidget):
    """视频合并列表：可拖入外部视频文件（加到末尾），列表项可拖动重排（自上而下=合并顺序）。
    双击某项发出 previewRequested(路径)供 mpv 预览；右键发出 removeRequested 移除所选。"""
    VIDEO_EXTS = ('.mp4', '.flv', '.mkv', '.mov', '.ts', '.m4v', '.avi', '.webm')
    previewRequested = Signal(str)
    removeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)   # 内部拖动重排
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.itemDoubleClicked.connect(
            lambda it: self.previewRequested.emit(it.data(Qt.UserRole)))

    def contextMenuEvent(self, e):
        it = self.itemAt(e.pos())
        if it and not it.isSelected():
            self.clearSelection(); it.setSelected(True)
        if not self.selectedItems():
            return
        menu = QMenu(self)
        n = len(self.selectedItems())
        act = menu.addAction("移除所选" if n <= 1 else f"移除所选 {n} 项")
        act.triggered.connect(self.removeRequested.emit)
        menu.exec(e.globalPos())

    def add_paths(self, paths):
        """把视频路径加到列表末尾（按给定顺序，去重、过滤非视频）。"""
        exist = {self.item(i).data(Qt.UserRole) for i in range(self.count())}
        for p in paths:
            if not p:
                continue
            p = os.path.abspath(p)
            if os.path.splitext(p)[1].lower() in self.VIDEO_EXTS and p not in exist:
                it = QListWidgetItem(os.path.basename(p))
                it.setData(Qt.UserRole, p)
                it.setToolTip(p)
                self.addItem(it)
                exist.add(p)

    def paths(self):
        """按当前（可能被拖动重排过的）顺序返回所有文件路径。"""
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]

    # 外部文件拖入 = 添加；内部拖动 = 交给基类做重排
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.add_paths([u.toLocalFile() for u in e.mimeData().urls()])
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class ClickableLabel(QLabel):
    """可点击的 QLabel：左键点击发出 clicked 信号（用于右上角提示→操作历史）。"""
    clicked = Signal()

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class MenuTable(QTableWidget):
    """可右键的表格：重写 contextMenuEvent，发出 contextRequested(全局坐标) 信号。
    另外：选择行的边框在这里整行一次性绘制（而非逐单元格），彻底消除单元格交界处的小竖线/断裂。"""
    contextRequested = Signal(QPoint)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sel_border = QColor('#e0a060')   # 标记行边框色（由 _apply_table_hl_color 同步）
        self.mark_row = -1     # 主标记行（锚点；单行操作用它）
        self.mark_rows = set() # 标记行集合（Shift 多选；为空时退回 mark_row）。自管，不用 Qt 选择

    def _marked(self):
        """当前标记的行集合（统一出口）。"""
        if self.mark_rows:
            return self.mark_rows
        return {self.mark_row} if self.mark_row >= 0 else set()

    def wheelEvent(self, e):
        # Shift+滚轮：横向滚动（Sublime 逻辑）；普通滚轮维持竖向滚动
        if e.modifiers() & Qt.ShiftModifier:
            d = e.angleDelta().y() or e.angleDelta().x()
            if d:
                sb = self.horizontalScrollBar()
                sb.setValue(sb.value() - d)   # 上滚→左，下滚→右
            e.accept(); return
        super().wheelEvent(e)

    def contextMenuEvent(self, e):
        gp = e.globalPos()
        e.accept()
        # 延迟到事件处理栈外再弹菜单，避免 "must be a top level window" 警告
        QTimer.singleShot(0, lambda: self.contextRequested.emit(gp))

    def paintEvent(self, e):
        super().paintEvent(e)   # 先正常画（含 delegate 的选择行填充）
        cols = self.columnCount()
        deleg = self.itemDelegate()
        prows = getattr(deleg, 'play_rows', ()) if isinstance(deleg, _CellEditDelegate) else ()
        prows = [r for r in prows if 0 <= r < self.rowCount()]   # 跟随行（可多条重叠）各画整行边框
        if cols == 0 or not prows:
            return
        p = QPainter(self.viewport())
        pen = QPen(self.sel_border); pen.setWidth(2); p.setPen(pen)
        for pr in prows:
            r0 = self.visualRect(self.model().index(pr, 0))
            rn = self.visualRect(self.model().index(pr, cols - 1))
            if r0.height() <= 0:
                continue
            p.drawRect(QRect(r0.left() + 1, r0.top() + 1, rn.right() - r0.left() - 2, r0.height() - 2))


class _CellEditDelegate(QStyledItemDelegate):
    """① 把单元格编辑框上下各扩 1px，盖住单元格上下的网格线；
    ② 给「正在播放」的行画填充高亮（play_row）。
       注意：表格设了 QTableWidget::item 样式表后，QTableWidgetItem.setBackground 会失效，
       所以填充高亮必须在 delegate 的 paint 里画。选择行的边框由 MenuTable 整行绘制，不在此处。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.play_rows = set()                               # 跟随行集合（命中的字幕/弹幕，可多条），MenuTable 画边框
        self.sel_fill = QColor('#e0a060'); self.sel_fill.setAlpha(70)   # 选择行整行填充（半透明）

    def updateEditorGeometry(self, editor, option, index):
        r = QRect(option.rect)
        r.adjust(0, -1, 0, 1)
        editor.setGeometry(r)

    def paint(self, painter, option, index):
        # 选择行整行填充（画在文字底下）；跟随行的边框见 MenuTable.paintEvent
        tbl = self.parent()
        if tbl is not None and index.row() in tbl._marked():
            painter.fillRect(option.rect, self.sel_fill)
        # 选中状态完全由 MenuTable 的整行边框表达：这里把"选中/焦点"标志都去掉，
        # 让每个单元格都按"未选中"绘制 —— 否则样式引擎会逐格画选中/焦点装饰，
        # 在列与列交界处留下一截截竖线（就是选中行里那些"小竖线"）。
        opt = QStyleOptionViewItem(option)
        opt.state &= ~(QStyle.State_Selected | QStyle.State_HasFocus)
        super().paint(painter, opt, index)


def _rgba(s):
    """颜色字符串(#RRGGBB 或 #AARRGGBB) -> QSS 用的 rgba(r,g,b,a)（带不透明度）。"""
    c = QColor(s)
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()/255:.3f})"

def _blend_over(s, bg='#161616'):
    """把带透明度的颜色按 alpha 混合到不透明底色 bg 上，返回不透明 rgb()。
    用于表格选中背景：避免半透明时露出 Qt 默认蓝色选中色。"""
    c = QColor(s); b = QColor(bg); a = c.alpha() / 255
    r = int(c.red() * a + b.red() * (1 - a))
    g = int(c.green() * a + b.green() * (1 - a))
    bl = int(c.blue() * a + b.blue() * (1 - a))
    return f"rgb({r},{g},{bl})"

def _darken(s, f=0.85):
    """加深颜色但保留 alpha，返回 #AARRGGBB。"""
    c = QColor(s)
    d = QColor(int(c.red() * f), int(c.green() * f), int(c.blue() * f), c.alpha())
    return d.name(QColor.HexArgb)


def _fg(s):
    """按底色明暗返回适配的文字色：很浅的底→黑字，其余→白字（阈值偏高，使橙色这类中等亮度
    底色统一用白字，只有明显发白的底才转黑，避免同族按钮黑白混杂）。"""
    c = QColor(s)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return '#000000' if (lum > 186 and c.alpha() > 110) else '#ffffff'


def _radius_for(pad_v=6):
    """按"控件高度的一半"夹住圆角：Qt 在 border-radius ≥ 高度/2 时会退化成直角，
    所以这里把设置里的 ui_radius 夹到略小于高度一半，避免突然变方。
    pad_v=控件上下内边距(估算高度=字号+2*pad+边框余量)。"""
    font = int(SETTINGS.get('ui_font_size', 13))
    h = font + 2 * pad_v + 6
    return min(int(SETTINGS.get('ui_radius', 10)), max(0, h // 2 - 1))


def _lighten(s, amt=0.2):
    """朝白色插值变浅（amt 0~1），保留 alpha，返回 #AARRGGBB。"""
    c = QColor(s); a = max(0.0, min(1.0, amt))
    d = QColor(int(c.red() + (255 - c.red()) * a),
               int(c.green() + (255 - c.green()) * a),
               int(c.blue() + (255 - c.blue()) * a), c.alpha())
    return d.name(QColor.HexArgb)


# 派生角色 -> 实际 color_* 键（主色一键推导深浅；下游代码仍读各 color_* 键不变）
def _skin_palette(base):
    """由主色 base 推导一套深浅：开关最深、一次性动作中、装饰最浅、tab 叉深。"""
    deep = _darken(base, 0.82)          # 开关类/模式键（最深）
    mid = _lighten(base, 0.16)          # 一次性动作按钮
    light = _lighten(base, 0.40)        # tab 选中/进度条/滚动条/选区框/跟随框/标签
    return {
        'color_toggle':    deep,
        'color_mode_btn':  deep,
        'color_button':    mid,
        'color_batch_btn': mid,
        'color_play':      mid,
        'color_handle':    mid,
        'color_header':    light,
        'color_progress':  light,
        'color_scrollbar': light,
        'color_follow':    light,
        'color_tab_x':     _darken(base, 0.5),
        'color_label':     light,          # 各栏目名（菜单栏/工具栏/视频/波形/字幕…）
        'color_playhead':  _lighten(base, 0.85),   # 播放线（亮色，醒目）
    }


def apply_skin(base=None):
    """把主色推导出的各派生色写回 SETTINGS（保留 color_status/界面背景等不参与项）。"""
    base = base or SETTINGS.get('color_skin', '#d98c54')
    SETTINGS['color_skin'] = base
    SETTINGS.update(_skin_palette(base))


class GearButton(QPushButton):
    """橙底自绘齿轮设置按钮（不依赖字体 emoji）。"""
    def __init__(self):
        super().__init__()
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = SETTINGS['color_button']
        body = QColor(_darken(base)) if self.underMouse() else QColor(base)
        p.setPen(Qt.NoPen); p.setBrush(body)
        p.drawRoundedRect(r, 5, 5)
        # 齿轮：齿+圆盘用与底色对比的黑/白，中心挖孔（用按钮底色）
        p.translate(self.width() / 2, self.height() / 2)
        p.setBrush(QColor(_fg(base)))
        R = 9.0
        for i in range(8):
            p.save(); p.rotate(45 * i)
            p.drawRect(QRectF(-1.8, -R, 3.6, R * 0.5))
            p.restore()
        p.drawEllipse(QPointF(0, 0), R * 0.62, R * 0.62)
        p.setBrush(body)
        p.drawEllipse(QPointF(0, 0), 3.0, 3.0)


class TabCloseButton(QPushButton):
    """标签页上的自绘 × 关闭按钮，替代 Qt 自带的关闭叉（更轻：默认无底，悬停才显淡底）。"""
    def __init__(self):
        super().__init__()
        self.setFixedSize(16, 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hover = self.underMouse()
        # 选中标签底色浅 → 叉用深色(color_tab_x)；未选中标签底深 → 叉用浅色，保证两种底都看得清
        on_selected = False
        tb = self.parent()
        while tb is not None and not isinstance(tb, QTabBar):
            tb = tb.parent()
        if isinstance(tb, QTabBar):
            for i in range(tb.count()):
                if tb.tabButton(i, QTabBar.ButtonPosition.RightSide) is self:
                    on_selected = (i == tb.currentIndex()); break
        if hover:                       # 悬停显示一圈淡底，提示可点
            p.setPen(Qt.NoPen); p.setBrush(QColor(255, 255, 255, 38))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        base = QColor(SETTINGS.get('color_tab_x', '#6c462a')) if on_selected else QColor('#cccccc')
        pen = QPen(QColor('#ffffff') if hover else base)
        pen.setWidth(2); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx, cy, d = self.width() / 2, self.height() / 2, 3.2
        p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))


class LockButton(QPushButton):
    """seek 锁切换按钮（可选中）：上方画一把锁、下方写 "seek"。
    上锁=闭合锁(橙底)→点字幕行不 seek；解锁=开口锁(灰底)→正常 seek。底色跟随 color_batch_btn。"""
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(46, 30)   # 与锁/播放同高，避免把视频工具栏行撑高、显出缝隙
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        locked = self.isChecked()
        base = QColor(SETTINGS.get('color_toggle', '#b17244')) if locked else QColor('#4a4a4a')
        if self.underMouse():
            base = QColor(_darken(base.name()))
        p.setPen(Qt.NoPen); p.setBrush(base)
        p.drawRoundedRect(r, 5, 5)

        cx, by = self.width() / 2, 11.0   # 锁体顶 y
        # 锁体 + 锁孔（在上方）
        p.setBrush(QColor('#ffffff')); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(cx - 6, by, 12, 8), 1.5, 1.5)
        p.setBrush(base); p.drawEllipse(QPointF(cx, by + 4), 1.3, 1.3)
        # 锁梁：闭合画整条 U，解锁画开口 U
        pen = QPen(QColor('#ffffff')); pen.setWidth(2); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(cx - 3.5, by)
        path.lineTo(cx - 3.5, by - 3)
        path.arcTo(QRectF(cx - 3.5, by - 7, 7, 7), 180, -180 if locked else -150)
        if locked:
            path.lineTo(cx + 3.5, by)
        p.drawPath(path)
        # 下方 "seek" 字样
        p.setPen(QColor('#ffffff'))
        f = QFont(); f.setPixelSize(10); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(0, by + 8, self.width(), self.height() - (by + 8)), Qt.AlignCenter, "seek")


class CrossToggleButton(QPushButton):
    """自绘 十字准线开关（可选中）：开=橙底白色虚线十字，关=灰底暗色十字。
    控制鼠标悬停视频时是否显示十字准线+坐标。底色跟随 color_batch_btn。"""
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(38, 30)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        base = QColor(SETTINGS.get('color_toggle', '#b17244')) if on else QColor('#4a4a4a')
        if self.underMouse():
            base = QColor(_darken(base.name()))
        p.setPen(Qt.NoPen); p.setBrush(base)
        p.drawRoundedRect(r, 5, 5)
        pen = QPen(QColor('#ffffff') if on else QColor('#aaaaaa'))
        pen.setWidth(1); pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        cx, cy = self.width() / 2, self.height() / 2
        p.drawLine(QPointF(6, cy), QPointF(self.width() - 6, cy))
        p.drawLine(QPointF(cx, 5), QPointF(cx, self.height() - 5))


class PlayButton(QPushButton):
    """蓝底播放/暂停按钮：播放中画两条竖线，暂停时画三角。"""
    def __init__(self):
        super().__init__()
        self.setFixedSize(44, 30)
        self.setCursor(Qt.PointingHandCursor)
        self._playing = False

    def setPlaying(self, p):
        if p != self._playing:
            self._playing = p
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        base = SETTINGS['color_play']
        p.setBrush(QColor(_darken(base)) if self.underMouse() else QColor(base))
        p.drawRoundedRect(r, 6, 6)
        p.setBrush(QColor(_fg(base)))   # 播放/暂停图标随底色明暗用黑/白
        cx, cy = self.width() / 2, self.height() / 2
        if self._playing:
            bw, gap, h = 4.0, 4.0, 13.0
            p.drawRect(QRectF(cx - gap / 2 - bw, cy - h / 2, bw, h))
            p.drawRect(QRectF(cx + gap / 2, cy - h / 2, bw, h))
        else:
            p.drawPolygon(QPolygonF([QPointF(cx - 5, cy - 7), QPointF(cx - 5, cy + 7), QPointF(cx + 7, cy)]))


class VideoWidget(QWidget):
    """承载 mpv 渲染；左键点击回调（兜底，防止 Qt 接住了点击而 mpv 收不到）。
    另开启鼠标跟踪，把移动/离开回调出去用于绘制十字准线与坐标显示。"""
    def __init__(self, on_click, on_move=None, on_leave=None):
        super().__init__()
        self._on_click = on_click
        self._on_move = on_move
        self._on_leave = on_leave
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setStyleSheet("background:#000;")
        self.setMinimumWidth(360)
        self.setFocusPolicy(Qt.ClickFocus)   # 点视频可夺焦，使输入框失焦、光标消失
        self.setMouseTracking(True)           # 不按下也接收移动事件（十字准线）

    def mousePressEvent(self, e):
        self.setFocus(Qt.MouseFocusReason)
        if e.button() == Qt.LeftButton:
            self._on_click()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._on_move:
            self._on_move(e.position().toPoint())
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._on_leave:
            self._on_leave()
        super().leaveEvent(e)


class CrosshairOverlay(QWidget):
    """悬浮在视频上的十字准线：独立置顶 + 点击穿透的顶层窗口，避开 mpv 原生窗口的遮挡。
    几何对齐到视频图像矩形，内部按存的中心点画一横一竖两条虚线。"""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._pos = None   # 十字中心（本窗口局部坐标）

    def set_cross(self, x, y):
        self._pos = (x, y); self.update()

    def paintEvent(self, e):
        if self._pos is None:
            return
        p = QPainter(self)
        pen = QPen(QColor('#ffffff')); pen.setWidth(1); pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        x, y = int(self._pos[0]), int(self._pos[1])
        p.drawLine(0, y, self.width(), y)        # 横线：左→右
        p.drawLine(x, 0, x, self.height())       # 竖线：上→下


class SettingsDialog(QDialog):
    """设置：5 个颜色 + 2 个覆盖开关。确定后写回 SETTINGS 并持久化。"""
    # (section, key, label)；section 用于分组标题，None 表示同组延续。工具栏(样式栏)无颜色项。
    COLORS = [
        ('菜单栏',   'color_button',      '一次性按钮'),
        (None,       'color_toggle',      '开关按钮(选中)'),
        (None,       'color_mode_btn',    '模式切换键'),
        (None,       'color_status',      '提示文本'),
        (None,       'color_label',       '栏目名文字'),
        (None,       'color_history_btn', '历史按钮'),
        ('批量操作', 'color_batch_btn',   '按钮'),
        ('Tab 栏',   'color_header',      '标签底色'),
        (None,       'color_tab_x',       '标签关闭叉'),
        ('播放',     'color_play',        '播放/暂停键'),
        (None,       'color_progress',    '进度条'),
        (None,       'color_handle',      '进度条圆点'),
        (None,       'color_playhead',    '播放线'),
        ('界面',     'color_ui_bg',       '界面背景'),
        (None,       'color_follow',      '跟随条/标记框'),
        (None,       'color_scrollbar',   '滚动条'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self._vals = dict(SETTINGS)
        self._swatches = {}
        self._opacities = {}

        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)

        def _tab(title):
            """新建一个带竖直滚动的标签页，返回其 QVBoxLayout。"""
            sa = QScrollArea(); sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            w = QWidget(); lay = QVBoxLayout(w); lay.setSpacing(6)
            sa.setWidget(w); tabs.addTab(sa, title)
            return lay

        # ── 外观（主色 / 颜色 / 界面字号）──
        lay = _tab("外观")
        sk = QHBoxLayout(); sk.setSpacing(10)
        sk.addWidget(QLabel("主色（改它自动推导下方深浅）"))
        self.btn_skin = QPushButton(); self.btn_skin.setMinimumWidth(96)
        self._paint_swatch(self.btn_skin, self._vals.get('color_skin', '#d98c54'))
        self.btn_skin.clicked.connect(self._pick_skin)
        sk.addWidget(self.btn_skin); sk.addStretch(1)
        lay.addLayout(sk)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        r = 0
        for section, key, label in self.COLORS:
            if section:
                sec = QLabel(section); sec.setStyleSheet("color:#e0a060;font-weight:bold;margin-top:8px;")
                grid.addWidget(sec, r, 0, 1, 3); r += 1
            grid.addWidget(QLabel("　" + label), r, 0)
            btn = QPushButton(); btn.setMinimumWidth(96)
            self._paint_swatch(btn, self._vals[key])
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._swatches[key] = btn
            grid.addWidget(btn, r, 1)
            op = _NoScrollSpinBox(); op.setRange(0, 100); op.setSuffix('%'); op.setToolTip("不透明度（点中后才能滚轮调节）")
            op.setValue(round(QColor(self._vals[key]).alpha() / 255 * 100))
            op.valueChanged.connect(lambda v, k=key: self._set_opacity(k, v))
            self._opacities[key] = op
            grid.addWidget(op, r, 2)
            r += 1
        # 界面字号 / 圆角大小：并入「界面」分组（接在颜色行之后，同一网格）
        grid.addWidget(QLabel("　界面字号"), r, 0)
        self.sp_uifont = _NoScrollSpinBox(); self.sp_uifont.setRange(9, 24); self.sp_uifont.setSuffix(' px')
        self.sp_uifont.setValue(int(self._vals.get('ui_font_size', 13)))
        grid.addWidget(self.sp_uifont, r, 1); r += 1
        grid.addWidget(QLabel("　圆角大小"), r, 0)
        self.sp_radius = _NoScrollSpinBox(); self.sp_radius.setRange(0, 20); self.sp_radius.setSuffix(' px')
        self.sp_radius.setToolTip("按钮/标签页/输入框等的圆角半径（0=直角）")
        self.sp_radius.setValue(int(self._vals.get('ui_radius', 10)))
        grid.addWidget(self.sp_radius, r, 1); r += 1
        lay.addLayout(grid)
        lay.addStretch(1)

        # ── 快捷键 ──
        layk = _tab("快捷键")
        gk = QGridLayout(); gk.setColumnStretch(0, 1); gk.setColumnStretch(1, 0); gk.setHorizontalSpacing(10)
        self._key_edits = {}
        cur_keys = self._vals.get('keys') or {}
        key_rows = [
            ('play_pause', '播放 / 暂停'),
            ('seek_back',  '快退'),
            ('seek_fwd',   '快进'),
            ('set_start',  '设开始时间（波形点选）'),
            ('set_end',    '设结束时间（波形点选）'),
            ('delete_row', '删除选中行'),
            ('capture',    '截取（波形拖选生成字幕行）'),
            ('retime',     '重定时（波形拖选重设起止）'),
            ('clip_in',    '剪辑：设入点'),
            ('clip_out',   '剪辑：设出点'),
        ]
        for i, (key, label) in enumerate(key_rows):
            gk.addWidget(QLabel(label), i, 0)
            ed = QKeySequenceEdit(QKeySequence(cur_keys.get(key, '')))
            ed.setMaximumWidth(150)
            self._key_edits[key] = ed
            gk.addWidget(ed, i, 1)
        layk.addLayout(gk)
        tipk = QLabel("点输入框后按下想用的按键即可。开始/结束时间仅字幕编辑模式生效，入/出点仅剪辑模式生效。")
        tipk.setWordWrap(True); tipk.setStyleSheet("color:#999;")
        layk.addWidget(tipk)
        layk.addStretch(1)

        # ── 其他（历史 / seek / 临时文件）──（语音识别选项已移到「语音识别」弹窗内）
        layo = _tab("其他")
        g2 = QGridLayout(); g2.setColumnStretch(0, 1); g2.setColumnStretch(1, 0); g2.setHorizontalSpacing(10)
        g2.addWidget(QLabel("操作历史条数"), 0, 0)
        self.sp_history = _NoScrollSpinBox(); self.sp_history.setRange(0, 5000); self.sp_history.setSuffix(' 条'); self.sp_history.setMaximumWidth(110)
        self.sp_history.setToolTip("右上角提示的历史最多记录多少条（0=不记录）")
        self.sp_history.setValue(int(self._vals.get('history_limit', 100)))
        g2.addWidget(self.sp_history, 0, 1)
        g2.addWidget(QLabel("左右键 seek 秒数"), 1, 0)
        self.sp_seek = _NoScrollSpinBox(); self.sp_seek.setRange(1, 600); self.sp_seek.setSuffix(' 秒'); self.sp_seek.setMaximumWidth(110)
        self.sp_seek.setToolTip("按←/→键快进快退的秒数")
        self.sp_seek.setValue(int(self._vals.get('seek_step', 5)))
        g2.addWidget(self.sp_seek, 1, 1)
        g2.addWidget(QLabel("波形聚焦位置"), 2, 0)
        self.cmb_wave_focus = _NoScrollComboBox(); self.cmb_wave_focus.setMaximumWidth(110)
        self.cmb_wave_focus.setToolTip("「波形聚焦」开启时，播放头在波形可视窗口中的横向位置")
        for label, val in (("靠左", 0.2), ("居中", 0.5), ("靠右", 0.8)):
            self.cmb_wave_focus.addItem(label, val)
        self._select_combo_value(self.cmb_wave_focus, float(self._vals.get('wave_focus_pos', 0.5)))
        g2.addWidget(self.cmb_wave_focus, 2, 1)
        g2.addWidget(QLabel("字幕聚焦位置"), 3, 0)
        self.cmb_sub_focus = _NoScrollComboBox(); self.cmb_sub_focus.setMaximumWidth(110)
        self.cmb_sub_focus.setToolTip("「字幕聚焦」开启时，当前播放行在字幕表中的纵向位置")
        for label, val in (("靠上", 0.3), ("居中", 0.5), ("靠下", 0.8)):
            self.cmb_sub_focus.addItem(label, val)
        self._select_combo_value(self.cmb_sub_focus, float(self._vals.get('sub_focus_pos', 0.5)))
        g2.addWidget(self.cmb_sub_focus, 3, 1)
        layo.addLayout(g2)
        layo.addSpacing(10)
        self.btn_clear_temp = QPushButton("清空临时文件夹")
        self.btn_clear_temp.setToolTip(f"删除程序临时文件（波形缓存/预览/切割中间文件等）\n{core.temp_dir()}")
        self.btn_clear_temp.clicked.connect(self._clear_temp)
        layo.addWidget(self.btn_clear_temp)
        layo.addStretch(1)

        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        outer.addWidget(bbox)   # 按钮在标签页外，始终可见
        self.resize(520, 600)

    def _pick_skin(self):
        """选主色 → 重算各派生色，刷新主色块 + 下方所有色块/不透明度。"""
        cur = QColor(self._vals.get('color_skin', '#d98c54'))
        c = QColorDialog.getColor(cur, self, "选择主色")
        if not c.isValid():
            return
        base = c.name(QColor.HexRgb).upper()
        self._vals['color_skin'] = base
        self._paint_swatch(self.btn_skin, base)
        for k, v in _skin_palette(base).items():
            self._vals[k] = v
            if k in self._swatches:
                self._paint_swatch(self._swatches[k], v)
            if k in self._opacities:
                self._opacities[k].blockSignals(True)
                self._opacities[k].setValue(round(QColor(v).alpha() / 255 * 100))
                self._opacities[k].blockSignals(False)

    def _clear_temp(self):
        keep = []
        ed = self.parent()
        if getattr(ed, '_loaded_sub_path', None):
            keep.append(ed._loaded_sub_path)   # 正在预览的合并字幕，删了会断预览
        keep += [os.path.join(core.temp_dir(), f'dmr_arrow_{n}.png') for n in ('up', 'down')]
        n = core.clear_temp(keep=keep)
        QMessageBox.information(self, "清空临时文件",
                               f"已清空临时文件夹，删除 {n} 项（已跳过正在使用的预览/箭头图）。\n位置：{core.temp_dir()}")

    def _paint_swatch(self, btn, hexv):
        c = QColor(hexv)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        fg = '#000000' if (lum > 140 and c.alpha() > 100) else '#ffffff'
        btn.setText(c.name(QColor.HexRgb).upper())   # 只显示 RGB，透明度看右侧 % 旋钮
        rgba = f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()/255:.3f})"
        btn.setStyleSheet(f"background:{rgba};color:{fg};border:1px solid #777;border-radius:8px;padding:4px;")

    def _pick(self, key):
        # 只选 RGB，保留当前不透明度（透明度由右侧 % 旋钮控制）
        cur = QColor(self._vals[key])
        c = QColorDialog.getColor(cur, self, "选择颜色")
        if c.isValid():
            c.setAlpha(cur.alpha())
            self._vals[key] = c.name(QColor.HexArgb).upper()
            self._paint_swatch(self._swatches[key], self._vals[key])

    def _set_opacity(self, key, pct):
        c = QColor(self._vals[key]); c.setAlpha(round(pct / 100 * 255))
        self._vals[key] = c.name(QColor.HexArgb).upper()
        self._paint_swatch(self._swatches[key], self._vals[key])

    @staticmethod
    def _select_combo_value(combo, val):
        """选中下拉框中 userData 最接近 val 的项。"""
        best, bestd = 0, 1e9
        for i in range(combo.count()):
            d = abs(float(combo.itemData(i)) - val)
            if d < bestd:
                best, bestd = i, d
        combo.setCurrentIndex(best)

    def result_settings(self):
        self._vals['ui_font_size'] = self.sp_uifont.value()
        self._vals['ui_radius'] = self.sp_radius.value()
        self._vals['history_limit'] = self.sp_history.value()
        self._vals['seek_step'] = self.sp_seek.value()
        self._vals['wave_focus_pos'] = float(self.cmb_wave_focus.currentData())
        self._vals['sub_focus_pos'] = float(self.cmb_sub_focus.currentData())
        self._vals['keys'] = {k: ed.keySequence().toString() for k, ed in self._key_edits.items()}
        return self._vals


class AssDoc:
    """一个已载入的 ass 文档（统一编辑模型）：以原始行为准，保留全部 Style 与 Dialogue 字段。
    字幕与弹幕走完全相同的逻辑，kind 仅用于默认渲染目标/命名（'subtitle' | 'danmaku'）。"""
    def __init__(self, path, kind='subtitle'):
        self.path = path
        self.kind = kind
        self.play_w = self.play_h = None
        self.styles = {}            # {style_name: 工具栏样式 dict}
        self.dialogues = []         # 完整字段 dict 列表（core.parse_ass_full 的结构）
        self.cur_style = None       # 工具栏当前编辑的 Style 名
        self.applied_offset = 0     # 已应用到本文档的累计时间偏移(ms)
        self.reload()

    @property
    def name(self):
        return os.path.basename(self.path)

    def reload(self):
        try:
            self.play_w, self.play_h, self.styles, self.dialogues = core.parse_ass_full(self.path)
        except Exception:
            self.play_w = self.play_h = None; self.styles = {}; self.dialogues = []
        if not self.styles:
            self.styles = {'Default': dict(core.DEFAULT_STYLE)}
        if self.cur_style not in self.styles:
            self.cur_style = next(iter(self.styles))

    def style_names(self):
        return list(self.styles.keys())

    def texts_of_style(self, name):
        return [d['text'] for d in self.dialogues if d['style'] == name]

    def save_dialogues(self):
        core.write_dialogues_to_ass(self.path, self.dialogues)

    def patch_style(self, name, vals):
        self.styles[name] = dict(vals)
        core.patch_style_in_ass(self.path, name, vals, self.play_h)


class DanmakuStyleDialog(QDialog):
    """编辑单行弹幕的内联样式：填充色 / 不透明度 / 描边开关+宽度 / 描边色。只改当前行。
    勾选=设置该标签；不勾且原本有=移除；不勾且原本没有=保持不动。描边关=显式写 \\bord0。"""
    def __init__(self, text, parent=None, batch=False):
        super().__init__(parent)
        self._batch = batch   # 批量模式：隐藏 \move 区；未勾选项=保持各行原样(不移除)
        self.setWindowTitle("批量编辑样式" if batch else "编辑弹幕样式")
        self.resize(500, 470)
        self._text = text
        st = core.parse_inline_style(text)
        self._had_bord = st['bord'] is not None
        self._fill_color = st['fill'] or '#FFFFFF'
        self._oc_color = st['outline_color'] or '#000000'

        lay = QVBoxLayout(self)
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(8)
        grid.setColumnStretch(2, 1)   # 末列吸收多余宽度：复选框+控件靠左成组，不再贴右边

        tip_fill = "寻找 \\c 或 \\1c，改其值；没有则插入 \\c。（主色=填充色，存为 BGR）"
        tip_op   = "寻找 \\1a 或 \\alpha，改其值；没有则插入 \\1a。（00=不透明,FF=全透明，这里按不透明度%）"
        tip_bord = "寻找 \\bord：开=写 \\bord<宽度>，关=写 \\bord0"
        tip_oc   = "寻找 \\3c，改其值；没有则插入 \\3c。（描边色，存为 BGR）"

        self.chk_fill = QCheckBox("填充色"); self.chk_fill.setChecked(st['fill'] is not None)
        self.chk_fill.setToolTip(tip_fill)
        self.btn_fill = QPushButton(); self.btn_fill.setFixedSize(44, 22); self.btn_fill.setCursor(Qt.PointingHandCursor)
        self.btn_fill.setToolTip(tip_fill)
        self.btn_fill.clicked.connect(lambda: self._pick('_fill_color', self.btn_fill))
        grid.addWidget(self.chk_fill, 0, 0); grid.addWidget(self.btn_fill, 0, 1, Qt.AlignLeft)

        self.chk_op = QCheckBox("不透明度"); self.chk_op.setChecked(st['opacity'] is not None)
        self.chk_op.setToolTip(tip_op)
        self.sp_op = _NoScrollSpinBox(); self.sp_op.setRange(0, 100); self.sp_op.setSuffix(' %'); self.sp_op.setMaximumWidth(110)
        self.sp_op.setToolTip(tip_op)
        self.sp_op.setValue(st['opacity'] if st['opacity'] is not None else 100)
        grid.addWidget(self.chk_op, 1, 0); grid.addWidget(self.sp_op, 1, 1, Qt.AlignLeft)

        self.chk_bord = QCheckBox("开启描边"); self.chk_bord.setChecked(bool(st['bord'] and st['bord'] > 0))
        self.chk_bord.setToolTip(tip_bord)
        self.sp_bord = _NoScrollDoubleSpinBox(); self.sp_bord.setRange(0, 20); self.sp_bord.setSingleStep(0.5); self.sp_bord.setMaximumWidth(110)
        self.sp_bord.setToolTip(tip_bord)
        self.sp_bord.setValue(st['bord'] if st['bord'] else 1.0)
        grid.addWidget(self.chk_bord, 2, 0); grid.addWidget(self.sp_bord, 2, 1, Qt.AlignLeft)

        self.chk_oc = QCheckBox("描边色"); self.chk_oc.setChecked(st['outline_color'] is not None)
        self.chk_oc.setToolTip(tip_oc)
        self.btn_oc = QPushButton(); self.btn_oc.setFixedSize(44, 22); self.btn_oc.setCursor(Qt.PointingHandCursor)
        self.btn_oc.setToolTip(tip_oc)
        self.btn_oc.clicked.connect(lambda: self._pick('_oc_color', self.btn_oc))
        grid.addWidget(self.chk_oc, 3, 0); grid.addWidget(self.btn_oc, 3, 1, Qt.AlignLeft)
        lay.addLayout(grid)

        # ── \move 编辑（批量模式隐藏；仅当本行有 \move 才可编辑）──
        self._linking = False
        self._has_move = False
        if not self._batch:
            mv = core.parse_move(text)
            self._has_move = mv is not None
            mlbl = QLabel("移动 \\move" + ("" if self._has_move else "（本行无 \\move，不可编辑）"))
            mlbl.setStyleSheet("color:#e0a060;font-weight:bold;margin-top:6px;")
            lay.addWidget(mlbl)
            mg = QGridLayout(); mg.setHorizontalSpacing(6); mg.setVerticalSpacing(6)
            mg.setColumnStretch(5, 1)   # 末列吸收多余宽度，各列靠左对齐、不再有大间隙

            def _mkspin(lo, hi, suffix=''):
                s = _NoScrollSpinBox(); s.setRange(lo, hi); s.setMaximumWidth(96)
                if suffix:
                    s.setSuffix(suffix)
                return s
            self.sp_x1 = _mkspin(-20000, 20000); self.sp_y1 = _mkspin(-20000, 20000)
            self.sp_x2 = _mkspin(-20000, 20000); self.sp_y2 = _mkspin(-20000, 20000)
            self.sp_t1 = _mkspin(0, 3600000, ' ms'); self.sp_t2 = _mkspin(0, 3600000, ' ms')
            self.chk_linkx = QCheckBox("X 联动"); self.chk_linkx.setToolTip("起点终点 x 相同(纵向移动)：联动同时改 x1/x2")
            self.chk_linky = QCheckBox("Y 联动"); self.chk_linky.setToolTip("起点终点 y 相同(横向移动)：联动同时改 y1/y2")
            self.chk_mtime = QCheckBox("指定时间"); self.chk_mtime.setToolTip("勾选写 \\move 6 参(起止毫秒)，否则 4 参(整行时长内移动)")

            mg.addWidget(QLabel("起点  X"), 0, 0); mg.addWidget(self.sp_x1, 0, 1, Qt.AlignLeft)
            mg.addWidget(QLabel("Y"), 0, 2); mg.addWidget(self.sp_y1, 0, 3, Qt.AlignLeft); mg.addWidget(self.chk_linkx, 0, 4)
            mg.addWidget(QLabel("终点  X"), 1, 0); mg.addWidget(self.sp_x2, 1, 1, Qt.AlignLeft)
            mg.addWidget(QLabel("Y"), 1, 2); mg.addWidget(self.sp_y2, 1, 3, Qt.AlignLeft); mg.addWidget(self.chk_linky, 1, 4)
            mg.addWidget(self.chk_mtime, 2, 0); mg.addWidget(self.sp_t1, 2, 1, Qt.AlignLeft)
            mg.addWidget(QLabel("→"), 2, 2, Qt.AlignCenter); mg.addWidget(self.sp_t2, 2, 3, Qt.AlignLeft)
            lay.addLayout(mg)

            if self._has_move:
                self.sp_x1.setValue(mv['x1']); self.sp_y1.setValue(mv['y1'])
                self.sp_x2.setValue(mv['x2']); self.sp_y2.setValue(mv['y2'])
                self.chk_mtime.setChecked(mv['t1'] is not None)
                self.sp_t1.setValue(mv['t1'] or 0); self.sp_t2.setValue(mv['t2'] or 0)
                self.chk_linkx.setChecked(mv['x1'] == mv['x2'])   # x 相同→默认联动
                self.chk_linky.setChecked(mv['y1'] == mv['y2'])   # y 相同→默认联动
            for w in (self.sp_x1, self.sp_y1, self.sp_x2, self.sp_y2,
                      self.chk_linkx, self.chk_linky, self.chk_mtime, self.sp_t1, self.sp_t2):
                w.setEnabled(self._has_move)
            self.sp_t1.setEnabled(self._has_move and self.chk_mtime.isChecked())
            self.sp_t2.setEnabled(self._has_move and self.chk_mtime.isChecked())

        lay.addWidget(QLabel("批量：勾选项将写入每个选中行（未勾选项保持原样）。预览：" if self._batch else "结果预览："))
        self.preview = QLabel(); self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background:#202020;color:#bbb;padding:6px;border-radius:3px;")
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.preview, 1)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        for w in (self.chk_fill, self.chk_op, self.chk_bord, self.chk_oc):
            w.toggled.connect(self._refresh)
        self.sp_op.valueChanged.connect(self._refresh)
        self.sp_bord.valueChanged.connect(self._refresh)
        if not self._batch:
            # \move 联动 + 刷新
            self.sp_x1.valueChanged.connect(lambda *_: self._mirror('x', 1))
            self.sp_x2.valueChanged.connect(lambda *_: self._mirror('x', 2))
            self.sp_y1.valueChanged.connect(lambda *_: self._mirror('y', 1))
            self.sp_y2.valueChanged.connect(lambda *_: self._mirror('y', 2))
            self.chk_linkx.toggled.connect(lambda on: (self._mirror('x', 1) if on else self._refresh()))
            self.chk_linky.toggled.connect(lambda on: (self._mirror('y', 1) if on else self._refresh()))
            self.sp_t1.valueChanged.connect(self._refresh)
            self.sp_t2.valueChanged.connect(self._refresh)
            self.chk_mtime.toggled.connect(self._on_mtime)
        self._paint(self.btn_fill, self._fill_color)
        self._paint(self.btn_oc, self._oc_color)
        self._refresh()

    def _mirror(self, axis, src):
        """联动：勾选了对应轴联动时，把一端的值同步到另一端（保持 x1==x2 或 y1==y2）。"""
        chk = self.chk_linkx if axis == 'x' else self.chk_linky
        a = self.sp_x1 if axis == 'x' else self.sp_y1
        b = self.sp_x2 if axis == 'x' else self.sp_y2
        if chk.isChecked() and not self._linking:
            self._linking = True
            (b if src == 1 else a).setValue((a if src == 1 else b).value())
            self._linking = False
        self._refresh()

    def _on_mtime(self, on):
        self.sp_t1.setEnabled(on); self.sp_t2.setEnabled(on)
        self._refresh()

    def _paint(self, btn, hexv):
        btn.setStyleSheet(f"background:{hexv};border:1px solid #777;border-radius:3px;")

    def _pick(self, attr, btn):
        c = QColorDialog.getColor(QColor(getattr(self, attr)), self, "选择颜色")
        if c.isValid():
            setattr(self, attr, c.name().upper())
            self._paint(btn, getattr(self, attr))
            self._refresh()

    def _actions(self):
        if self._batch:
            # 批量：勾=对所有行设置；不勾=保持各行原样（不移除、不写 bord0）
            fill = ('set', self._fill_color) if self.chk_fill.isChecked() else ('keep',)
            op = ('set', self.sp_op.value()) if self.chk_op.isChecked() else ('keep',)
            oc = ('set', self._oc_color) if self.chk_oc.isChecked() else ('keep',)
            bord = ('on', self.sp_bord.value()) if self.chk_bord.isChecked() else ('keep',)
            return dict(fill=fill, opacity=op, bord=bord, outline_color=oc)
        st = core.parse_inline_style(self._text)
        fill = ('set', self._fill_color) if self.chk_fill.isChecked() else (('remove',) if st['fill'] is not None else ('keep',))
        op = ('set', self.sp_op.value()) if self.chk_op.isChecked() else (('remove',) if st['opacity'] is not None else ('keep',))
        oc = ('set', self._oc_color) if self.chk_oc.isChecked() else (('remove',) if st['outline_color'] is not None else ('keep',))
        bord = ('on', self.sp_bord.value()) if self.chk_bord.isChecked() else (('off',) if self._had_bord else ('keep',))
        return dict(fill=fill, opacity=op, bord=bord, outline_color=oc)

    def result_text(self):
        t = core.apply_inline_style(self._text, **self._actions())
        if self._has_move:
            t1 = self.sp_t1.value() if self.chk_mtime.isChecked() else None
            t2 = self.sp_t2.value() if self.chk_mtime.isChecked() else None
            t = core.apply_move(t, self.sp_x1.value(), self.sp_y1.value(),
                                self.sp_x2.value(), self.sp_y2.value(), t1, t2)
        return t

    def _refresh(self):
        self.preview.setText(self.result_text())


class MoveOffsetDialog(QDialog):
    """对多行 \\move 的所有起点/终点坐标做整体偏移：x 加 dx、y 加 dy。"""
    def __init__(self, n, parent=None):
        super().__init__(parent)
        self.setWindowTitle("整体偏移 \\move")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"对选中的 {n} 行 \\move，所有 x 加 dx、所有 y 加 dy（可负）："))
        g = QGridLayout(); g.setHorizontalSpacing(8)
        self.sp_dx = _NoScrollSpinBox(); self.sp_dx.setRange(-20000, 20000); self.sp_dx.setMaximumWidth(110)
        self.sp_dy = _NoScrollSpinBox(); self.sp_dy.setRange(-20000, 20000); self.sp_dy.setMaximumWidth(110)
        g.addWidget(QLabel("dx (横向)"), 0, 0); g.addWidget(self.sp_dx, 0, 1)
        g.addWidget(QLabel("dy (纵向)"), 1, 0); g.addWidget(self.sp_dy, 1, 1)
        g.setColumnStretch(2, 1)
        lay.addLayout(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def dx(self):
        return self.sp_dx.value()

    def dy(self):
        return self.sp_dy.value()


# 内置默认替换模板（敏感词遮挡）。用户可在替换弹窗里勾选/取消/编辑/增删，结果存进 SETTINGS。
DEFAULT_REPLACE_RULES = [
    {'find': '我操', 'repl': '我🌿', 'on': True},
    {'find': '妈',   'repl': '🐎',   'on': True},
    {'find': '傻逼', 'repl': '沙🖊', 'on': True},
]


class ReplaceDialog(QDialog):
    """批量文本替换：多条「查找→替换为」规则，可勾选启用/取消、编辑、增删，
    一键对当前标签页所有行依次替换。规则（含勾选状态）持久化在 SETTINGS['replace_rules']。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量文本替换")
        self.resize(480, 440)
        lay = QVBoxLayout(self)
        tip = QLabel("勾选要应用的替换规则（可编辑、增删）。点「应用」对当前标签页所有行依次执行；"
                     "替换为留空 = 删除该词。规则会被记住，下次打开仍在。")
        tip.setWordWrap(True); tip.setStyleSheet("color:#999;")
        lay.addWidget(tip)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["启用", "查找", "替换为"])
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        rules = SETTINGS.get('replace_rules')
        if not rules:                       # 首次使用：填入内置默认模板
            rules = [dict(r) for r in DEFAULT_REPLACE_RULES]
        for r in rules:
            self._add_row(r.get('find', ''), r.get('repl', ''), r.get('on', True))

        ops = QHBoxLayout(); ops.setSpacing(8)
        b_add = QPushButton("添加规则"); b_add.clicked.connect(lambda: self._add_row('', '', True))
        b_del = QPushButton("删除选中"); b_del.clicked.connect(self._del_selected)
        b_def = QPushButton("恢复默认模板"); b_def.clicked.connect(self._load_defaults)
        b_all = QPushButton("全选"); b_all.clicked.connect(lambda: self._set_all(True))
        b_none = QPushButton("全不选"); b_none.clicked.connect(lambda: self._set_all(False))
        for b in (b_add, b_del, b_def, b_all, b_none):
            ops.addWidget(b)
        ops.addStretch(1)
        lay.addLayout(ops)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("应用")
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_row(self, find, repl, on):
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags((chk.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
        chk.setCheckState(Qt.Checked if on else Qt.Unchecked)
        chk.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(r, 0, chk)
        self.table.setItem(r, 1, QTableWidgetItem(find))
        self.table.setItem(r, 2, QTableWidgetItem(repl))

    def _del_selected(self):
        for r in sorted({i.row() for i in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(r)

    def _load_defaults(self):
        self.table.setRowCount(0)
        for r in DEFAULT_REPLACE_RULES:
            self._add_row(r['find'], r['repl'], True)

    def _set_all(self, on):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def rules(self):
        """返回全部规则 [{'find','repl','on'}]（含未勾选的，用于持久化）；查找为空的行忽略。"""
        out = []
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, 0)
            it_f = self.table.item(r, 1); it_r = self.table.item(r, 2)
            find = it_f.text() if it_f else ''
            repl = it_r.text() if it_r else ''
            on = bool(chk and chk.checkState() == Qt.Checked)
            if find:
                out.append({'find': find, 'repl': repl, 'on': on})
        return out


class NameDialog(QDialog):
    """命名弹窗：用户填文件名（已预填默认）；后缀固定只显示不可填；输出固定在视频目录；
    含「覆盖同名」勾选（默认勾选，取消则自动改名）。extra_checks=[(key,label,默认勾)]
    可附加额外勾选项。result() -> (基础名, 是否覆盖, {extra_key: bool})。"""
    def __init__(self, title, default_base, suffix, folder, note=None, extra_checks=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 0)
        lay = QVBoxLayout(self)
        if note:
            nl = QLabel(note); nl.setWordWrap(True); nl.setStyleSheet("color:#bbb;")
            lay.addWidget(nl)
        g = QGridLayout(); g.setHorizontalSpacing(8); g.setColumnStretch(1, 1)
        g.addWidget(QLabel("文件名"), 0, 0)
        self.ed = QLineEdit(default_base)
        g.addWidget(self.ed, 0, 1)
        lbl_sfx = QLabel(suffix); lbl_sfx.setStyleSheet("color:#999;")
        g.addWidget(lbl_sfx, 0, 2)        # 固定后缀，仅显示
        lay.addLayout(g)
        # 输出目录：可编辑 + 浏览（默认视频所在目录）
        fr = QHBoxLayout(); fr.setSpacing(6)
        fr.addWidget(QLabel("输出目录"))
        self.ed_folder = QLineEdit(folder or "")
        self.ed_folder.setToolTip("合成/识别结果的保存目录，可手动修改或点「浏览…」选择")
        fr.addWidget(self.ed_folder, 1)
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse_folder)
        fr.addWidget(btn_browse)
        lay.addLayout(fr)
        self.chk_ow = QCheckBox("覆盖同名文件（取消勾选则自动改名 (2)(3)…）")
        self.chk_ow.setChecked(True)
        lay.addWidget(self.chk_ow)
        self._extra = {}
        for key, label, default in (extra_checks or []):
            cb = QCheckBox(label); cb.setChecked(bool(default))
            self._extra[key] = cb
            lay.addWidget(cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.ed.setFocus(); self.ed.selectAll()

    def _browse_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.ed_folder.text() or "")
        if d:
            self.ed_folder.setText(d)

    def result(self):
        return (self.ed.text().strip(), self.chk_ow.isChecked(),
                {k: cb.isChecked() for k, cb in self._extra.items()},
                self.ed_folder.text().strip())


_BLUE_ARM = None

def _blue_arm_cursor():
    """蓝色实心圆光标——按下截取/重定时/点选后立即套到整窗，提示"已开启某功能"（非竖线）。"""
    global _BLUE_ARM
    if _BLUE_ARM is not None:
        return _BLUE_ARM
    from PySide6.QtGui import QPixmap, QCursor, QPen
    pm = QPixmap(20, 20); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor('#2a7bff')); p.setPen(QPen(QColor('#ffffff'), 2))
    p.drawEllipse(4, 4, 12, 12)
    p.end()
    _BLUE_ARM = QCursor(pm, 10, 10)
    return _BLUE_ARM


_BLUE_IBEAM = None

def _blue_ibeam_cursor():
    """自绘蓝色输入竖线（I-beam）光标——系统 IBeamCursor 无法染色，故用 QPixmap 画一个。"""
    global _BLUE_IBEAM
    if _BLUE_IBEAM is not None:
        return _BLUE_IBEAM
    from PySide6.QtGui import QPixmap, QCursor, QPen
    pm = QPixmap(16, 26); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor('#2a7bff')); pen.setWidth(2); p.setPen(pen)
    cx = 8
    p.drawLine(cx, 3, cx, 23)          # 竖线
    p.drawLine(cx - 3, 3, cx + 3, 3)   # 顶帽
    p.drawLine(cx - 3, 23, cx + 3, 23) # 底帽
    p.end()
    _BLUE_IBEAM = QCursor(pm, cx, 13)
    return _BLUE_IBEAM


class WaveformBar(QWidget):
    """音频波形条：画整段振幅包络 + 播放头；点击/拖动按比例 seek。仅字幕编辑模式显示。
    交互模式 imode：'capture'/'retime' 拖选（半透明蓝）→ rangeSelected；'pick_start'/'pick_end'
    单击点选 → pointPicked。非 seek 模式光标为蓝色输入竖线。"""
    # 拖选模式（capture/retime）完成：(起始比例, 结束比例)；点选模式（pick_*）完成：(比例)
    rangeSelected = Signal(float, float)
    pointPicked = Signal(float)
    rsetClick = Signal(float, bool)   # R 模式单击：(比例, 是否Ctrl=设结束)

    def __init__(self, on_seek, parent=None):
        super().__init__(parent)
        self._on_seek = on_seek
        self.peaks = []          # 0~1 振幅包络（按时长定分辨率，绘制时按像素降采样）
        self.pos = 0.0           # 播放头位置 0~1（全时间轴）
        self.zoom = 1.0          # 放大倍数：1=整段铺满，>1 只显示 1/zoom 段
        self.view_start = 0.0    # 可视窗口左边缘（全时间轴 0~1）
        # 交互模式：None=seek；'capture'/'retime'=拖选；'pick_start'/'pick_end'=单击点选
        self.imode = None
        self._cap_a = None       # 拖选起点比例
        self._cap_b = None       # 拖选当前/终点比例
        self._hover_x = None     # 交互模式下鼠标悬停 x（画蓝色虚线提示）
        self._hover_y = None     # 交互模式下鼠标悬停 y（R 模式提示框定位）
        self._rset_ctrl = False  # R 模式：当前是否按住 Ctrl（设结束）
        self._rset_drag = False  # R 模式：是否已开始拖动（设范围）
        self._press_x = 0        # R 模式：按下时的 x，用于判定是否拖动
        self._hl_a = self._hl_b = None   # 临时淡蓝高亮范围（点片段时长时显示）
        self._hl_until = 0.0
        self.center_follow = bool(SETTINGS.get('wave_center', True))   # 放大时播放头是否聚焦跟随
        self.focus_pos = float(SETTINGS.get('wave_focus_pos', 0.5))    # 聚焦位置：播放头在可视窗口中的横向比例
        self._center_suspend = 0.0   # Shift+滚轮平移后，暂停居中跟随到此时刻（让平移可见）
        self.peers = []              # 联动控件（进度条轨道/字幕轨道）：缩放/平移/播放头同步重绘
        self.duration_ms = 0         # 当前视频总时长（无波形时据此决定可缩放上限）
        self._blue_ibeam = _blue_ibeam_cursor()
        self.setMinimumHeight(40)
        self.setMouseTracking(True)   # 无按键也收 mouseMove，用于 hover 虚线
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._tip = QLabel(self)      # R 模式跟随光标的提示框（设开始/设结束/设范围）
        self._tip.setVisible(False)
        self._tip.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._tip.setStyleSheet("QLabel{background:#2a7bff;color:white;padding:1px 6px;"
                                "border-radius:4px;font-size:11px;}")

    def set_mode(self, mode):
        """切换波形交互模式；非 seek 模式光标变蓝色输入竖线。"""
        self.imode = mode
        self._cap_a = self._cap_b = None
        self._rset_drag = False; self._rset_ctrl = False
        if not mode:
            self._hover_x = None
        if mode != 'rset':
            self._tip.setVisible(False)
        self.setCursor(self._blue_ibeam if mode else Qt.PointingHandCursor)
        self.update()

    def set_rset_ctrl(self, on):
        """外部（按住/松开 Ctrl）通知 R 模式刷新提示文字。"""
        if self.imode == 'rset':
            self._rset_ctrl = bool(on)
            self._update_tip()

    def _update_tip(self):
        """R 模式：根据 拖动/Ctrl 状态更新提示文字并定位到光标右下。"""
        if self.imode != 'rset' or self._hover_x is None:
            self._tip.setVisible(False); return
        txt = "设范围" if self._rset_drag else ("设结束" if self._rset_ctrl else "设开始")
        self._tip.setText(txt); self._tip.adjustSize()
        x = int(self._hover_x) + 14
        y = (int(self._hover_y) + 8) if self._hover_y is not None else 4
        x = max(0, min(x, self.width() - self._tip.width() - 2))
        y = max(0, min(y, self.height() - self._tip.height() - 2))
        self._tip.move(x, y); self._tip.setVisible(True); self._tip.raise_()

    def set_center_follow(self, on):
        self.center_follow = bool(on)
        if on:
            self.recenter()

    def set_focus_pos(self, f):
        """设置聚焦位置（播放头在可视窗口的横向比例 0~1），并立即按需重新对齐。"""
        self.focus_pos = max(0.0, min(1.0, float(f)))
        if self.center_follow:
            self.recenter()

    def recenter(self):
        """把可视窗口对齐到当前播放头处于 focus_pos 比例位置。"""
        if self.zoom > 1.0:
            span = self._span()
            self.view_start = min(max(0.0, self.pos - span * self.focus_pos), max(0.0, 1.0 - span))
            self.update()

    def show_range(self, a, b, ms=3000):
        """用淡蓝色高亮一段时间范围（比例 a~b），停留 ms 毫秒后自动消失。"""
        self._hl_a, self._hl_b = max(0.0, min(1.0, a)), max(0.0, min(1.0, b))
        self._hl_until = time.time() + ms / 1000.0
        self.update()
        QTimer.singleShot(ms + 30, self.update)   # 到点后再重绘一次让它消失

    def add_peer(self, w):
        if w not in self.peers:
            self.peers.append(w)

    def _repaint(self):
        """重绘自身 + 所有联动控件（进度条轨道/字幕轨道），保证缩放/播放头同步。"""
        self.update()
        for w in self.peers:
            w.update()

    def _span(self):
        return 1.0 / self.zoom            # 可视窗口覆盖的时间轴比例

    def _max_zoom(self):
        caps = []
        if self.peaks:
            caps.append(len(self.peaks) / max(1, self.width()))   # 波形分辨率上限（约 1 桶/像素）
        if self.duration_ms:
            caps.append(self.duration_ms / 400.0)   # 最小窗口约 0.4 秒（放得更大、定位更精）
        return max(1.0, max(caps)) if caps else 1.0

    def _clamp_view(self):
        self.view_start = max(0.0, min(1.0 - self._span(), self.view_start))

    def zoom_by(self, factor, f_cursor, x_ratio):
        """以 f_cursor(全轴比例) 为锚点缩放；x_ratio=锚点在控件中的横向比例。联动重绘。"""
        self.zoom = max(1.0, min(self._max_zoom(), self.zoom * factor))
        if self.zoom <= 1.0:
            self.view_start = 0.0
        else:
            self.view_start = f_cursor - x_ratio / self.zoom
            self._clamp_view()
        self._repaint()

    def pan_by(self, sign):
        """Shift+滚轮平移可视窗口，并暂停居中跟随 3 秒。联动重绘。"""
        self.view_start += sign * self._span() * 0.15
        self.suspend_center()
        self._clamp_view(); self._repaint()

    def suspend_center(self, sec=3):
        """暂停居中跟随 sec 秒（手动拉进度条/拉波形/点波形/平移时调用）。"""
        self._center_suspend = time.time() + sec

    def set_peaks(self, peaks):
        self.peaks = list(peaks or [])
        self.zoom = 1.0; self.view_start = 0.0   # 新数据复位缩放
        self._repaint()

    def set_pos(self, frac):
        frac = max(0.0, min(1.0, frac))
        span = self._span()
        if self.center_follow and self.zoom > 1.0 and time.time() >= self._center_suspend:
            # 聚焦跟随：可视窗口随播放头滚动，使播放头保持在 focus_pos 比例处
            vs = min(max(0.0, frac - span * self.focus_pos), max(0.0, 1.0 - span))
            moved_px = max(abs(vs - self.view_start), abs(frac - self.pos)) / span * max(1, self.width())
            if moved_px >= 1:                # 仅达 ~1px 才更新状态+重画，让微小位移逐帧累积
                self.pos = frac; self.view_start = vs; self._repaint()
            return
        # 不居中（或未放大）：只移动播放头、不滚动视图；播放头可能移出可视窗口
        if abs(frac - self.pos) / span * max(1, self.width()) >= 1:
            self.pos = frac
            self._repaint()

    def _frac_at(self, x):
        """像素 x → 全时间轴比例 0~1。"""
        return max(0.0, min(1.0, self.view_start + (x / max(1, self.width())) / self.zoom))

    def _seek_at(self, x):
        if self._on_seek:
            self._on_seek(self._frac_at(x))

    def mousePressEvent(self, e):
        if self.imode:
            if e.button() == Qt.LeftButton:
                self._cap_a = self._cap_b = self._frac_at(e.position().x())
                if self.imode == 'rset':
                    self._rset_drag = False
                    self._rset_ctrl = bool(e.modifiers() & Qt.ControlModifier)
                    self._press_x = e.position().x()
                    self._update_tip()
                self.update()
            return   # 交互模式下非左键不 seek（右键退出由全局 eventFilter 处理）
        self._seek_at(e.position().x())

    def mouseMoveEvent(self, e):
        if self.imode:
            self._hover_x = e.position().x(); self._hover_y = e.position().y()   # 记录悬停位置
            if (e.buttons() & Qt.LeftButton) and self._cap_a is not None \
                    and self.imode in ('capture', 'retime', 'rset'):
                self._cap_b = self._frac_at(e.position().x())
                if self.imode == 'rset' and abs(e.position().x() - self._press_x) >= 4:
                    self._rset_drag = True                    # 移动超阈值 → 进入"设范围"
            if self.imode == 'rset':
                self._rset_ctrl = bool(e.modifiers() & Qt.ControlModifier)
                self._update_tip()
            self.update()
            return
        if e.buttons() & Qt.LeftButton:
            self._seek_at(e.position().x())

    def leaveEvent(self, e):
        if self._hover_x is not None:
            self._hover_x = self._hover_y = None
            self._tip.setVisible(False)
            self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.imode and self._cap_a is not None:
            if self.imode == 'rset':                          # R 模式：拖=范围，点=开始，Ctrl点=结束
                a = self._cap_a; b = self._cap_b if self._cap_b is not None else a
                dragged = self._rset_drag and (abs(b - a) * max(1, self.width()) / self._span() >= 3)
                ctrl = bool(e.modifiers() & Qt.ControlModifier) or self._rset_ctrl
                self._cap_a = self._cap_b = None; self._rset_drag = False
                self.update()
                if dragged:
                    lo, hi = sorted((a, b)); self.rangeSelected.emit(lo, hi)
                else:
                    self.rsetClick.emit(a, ctrl)
                return
            if self.imode in ('pick_start', 'pick_end'):     # 单击点选
                f = self._cap_a
                self._cap_a = self._cap_b = None
                self.update()
                self.pointPicked.emit(f)
                return
            a, b = sorted((self._cap_a, self._cap_b))         # 拖选 capture/retime
            self._cap_a = self._cap_b = None
            self.update()
            if (b - a) * max(1, self.width()) / self._span() >= 3:   # 至少拖 ~3px 才算有效
                self.rangeSelected.emit(a, b)
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if not (self.peaks or self.duration_ms):
            return
        dy = e.angleDelta().y() or e.angleDelta().x()
        if dy == 0:
            return
        if e.modifiers() & Qt.ShiftModifier:   # Shift+滚轮：平移可视窗口（放大后才有可移动空间）
            self.pan_by(-1 if dy > 0 else 1); return
        mx = e.position().x()
        self.zoom_by(1.25 if dy > 0 else 1 / 1.25, self._frac_at(mx), mx / max(1, self.width()))

    def paintEvent(self, e):
        W, H = self.width(), self.height()
        if W <= 0 or H <= 0:
            return
        p = QPainter(self)
        mid = H / 2.0
        if not self.peaks:
            p.setPen(QColor('#444'))
            p.drawLine(0, int(mid), W, int(mid))
            p.end(); return
        n = len(self.peaks)
        span = self._span()
        amp_h = mid * 0.9        # 波形竖向只占区域高度的 90%（上下各留 5% 余白）
        head_x = int((self.pos - self.view_start) / span * W) if span else -1   # 明暗分界像素（播放头竖线交给 PlayheadOverlay）
        played = QColor(SETTINGS.get('color_progress', '#c8916b'))
        rest = QColor(played); rest.setAlpha(90)        # 播放头之后的部分淡一些
        for x in range(W):
            lo = int((self.view_start + x / W * span) * n)
            hi = int((self.view_start + (x + 1) / W * span) * n)
            lo = max(0, min(n - 1, lo)); hi = max(lo + 1, min(n, hi))
            amp = max(self.peaks[lo:hi])
            h = amp * amp_h
            p.setPen(played if x <= head_x else rest)
            p.drawLine(x, int(mid - h), x, int(mid + h))
        # 播放头竖线由 PlayheadOverlay 统一绘制（贯通字幕轨+波形轨），此处只用 head_x 区分明暗
        # 拖选（截取/重定时）：半透明蓝色覆盖选区
        if self.imode in ('capture', 'retime', 'rset') and self._cap_a is not None and self._cap_b is not None:
            a, b = sorted((self._cap_a, self._cap_b))
            xa = int((a - self.view_start) / span * W)
            xb = int((b - self.view_start) / span * W)
            xa = max(0, min(W, xa)); xb = max(0, min(W, xb))
            p.fillRect(xa, 0, max(1, xb - xa), H, QColor(40, 130, 255, 90))
        # 临时淡蓝高亮范围（点片段「时长」时显示该片段范围，停留约 3 秒）
        if self._hl_a is not None and time.time() < self._hl_until:
            a, b = sorted((self._hl_a, self._hl_b))
            xa = int((a - self.view_start) / span * W)
            xb = int((b - self.view_start) / span * W)
            xa = max(0, min(W, xa)); xb = max(0, min(W, xb))
            p.fillRect(xa, 0, max(1, xb - xa), H, QColor(120, 180, 255, 70))
        # 交互模式悬停：蓝色虚线提示当前位置
        if self.imode and self._hover_x is not None and 0 <= self._hover_x <= W:
            pen = QPen(QColor('#2a7bff')); pen.setStyle(Qt.DashLine); pen.setWidth(1)
            p.setPen(pen)
            p.drawLine(int(self._hover_x), 0, int(self._hover_x), H)
        p.end()


class SeekTrack(QWidget):
    """可缩放的进度条轨道：与波形共用同一缩放窗口（从 wave 读取 zoom/view_start/pos）。
    滚轮缩放(以光标为锚)、Shift+滚轮平移，点击/拖动按比例 seek。4 小时视频放大后可精细拖动。"""
    def __init__(self, wave, on_seek, parent=None):
        super().__init__(parent)
        self.wave = wave            # 共享时间轴状态的波形控件
        self._on_seek = on_seek
        self.setFixedHeight(20)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _frac_at(self, x):
        w = self.wave
        return max(0.0, min(1.0, w.view_start + (x / max(1, self.width())) / w.zoom))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._on_seek:
            self._on_seek(self._frac_at(e.position().x()))

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.LeftButton) and self._on_seek:
            self._on_seek(self._frac_at(e.position().x()))

    def wheelEvent(self, e):
        w = self.wave
        if not (w.peaks or w.duration_ms):
            return
        dy = e.angleDelta().y() or e.angleDelta().x()
        if dy == 0:
            return
        if e.modifiers() & Qt.ShiftModifier:
            w.pan_by(-1 if dy > 0 else 1); return
        mx = e.position().x()
        w.zoom_by(1.25 if dy > 0 else 1 / 1.25, self._frac_at(mx), mx / max(1, self.width()))

    def paintEvent(self, e):
        W, H = self.width(), self.height()
        if W <= 0 or H <= 0:
            return
        w = self.wave
        span = 1.0 / w.zoom
        head_x = (w.pos - w.view_start) / span * W
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        gh = max(7, H * 0.45)          # 轨道(groove)细一些、竖向居中；圆点比它大，像滑块手柄
        gy = (H - gh) / 2.0
        grad = gh / 2.0
        p.setBrush(QColor('#2a2a2a'))                       # 底轨
        p.drawRoundedRect(QRectF(0, gy, W, gh), grad, grad)
        if head_x > 0:                                      # 已播放填充
            p.setBrush(QColor(SETTINGS.get('color_progress', '#c8916b')))
            p.drawRoundedRect(QRectF(0, gy, min(float(W), head_x), gh), grad, grad)
        # 播放头圆点：直径=整条高度，无描边
        hx = max(H / 2.0, min(W - H / 2.0, head_x))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SETTINGS.get('color_handle', '#e0703a')))
        p.drawEllipse(QPointF(hx, H / 2.0), H / 2.0, H / 2.0)
        p.end()


class SubtitleTrack(QWidget):
    """字幕轨道（剪映式）：每条字幕一个方块沿时间轴排布；方块左右竖线手柄可拖动调整开始/结束。
    与波形共用缩放窗口（从 wave 读 zoom/view_start/duration），随滚轮放大缩小，放在波形上方。"""
    HANDLE = 5   # 边缘手柄命中宽度(px)

    def __init__(self, wave, editor, parent=None):
        super().__init__(parent)
        self.wave = wave
        self.ed = editor
        self.setFixedHeight(26)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._drag = None        # 拖动状态 dict：{row, part:'start'|'end'|'move', pms, s0, e0, moved}
        self._split_x = None     # 右键分割时显示的蓝色虚线位置

    def _clip_mode(self):
        return self.ed.mode == 'clip'

    def _dia(self):
        if self._clip_mode():
            return self.ed.clips            # 剪辑模式：片段当作"字幕"在轨道上显示
        d = self.ed.cur_doc()
        return d.dialogues if d else []

    def _sel_row(self):
        """当前选中行：剪辑模式取片段表，字幕模式取字幕表。"""
        return self.ed.clip_table.mark_row if self._clip_mode() else self.ed.table.mark_row

    def _text_of(self, ev, i):
        if self._clip_mode():
            return f"片段{i + 1}"
        return (ev.get('text') or '').replace('\\N', '').replace('\\n', ' ')

    def _frac_at(self, x):
        w = self.wave
        return max(0.0, min(1.0, w.view_start + (x / max(1, self.width())) / w.zoom))

    def _x_of(self, frac):
        w = self.wave
        return (frac - w.view_start) * w.zoom * self.width()

    def _ms_at(self, x):
        dur = self.wave.duration_ms
        return int(self._frac_at(x) * dur) if dur else 0

    def wheelEvent(self, e):   # 缩放/平移交给波形（联动）
        w = self.wave
        if not (w.peaks or w.duration_ms):
            return
        dy = e.angleDelta().y() or e.angleDelta().x()
        if dy == 0:
            return
        if e.modifiers() & Qt.ShiftModifier:
            w.pan_by(-1 if dy > 0 else 1); return
        mx = e.position().x()
        w.zoom_by(1.25 if dy > 0 else 1 / 1.25, self._frac_at(mx), mx / max(1, self.width()))

    def _hit(self, x):
        """命中测试：返回 (行号, 'start'|'end'|'body') 或 None。
        左右手柄仅"选中"的那条才命中（白线只在选中块上显示）；其余块整体算 body。"""
        dur = self.wave.duration_ms
        dia = self._dia()
        if not dur or not dia:
            return None
        sel = self._sel_row()
        if 0 <= sel < len(dia):   # 选中块的手柄优先
            e = dia[sel]; xa = self._x_of(e['start'] / dur); xb = self._x_of(e['end'] / dur)
            if -self.HANDLE <= xb <= self.width() + self.HANDLE and abs(x - xb) <= self.HANDLE:
                return (sel, 'end')
            if -self.HANDLE <= xa <= self.width() + self.HANDLE and abs(x - xa) <= self.HANDLE:
                return (sel, 'start')
        for i, e in enumerate(dia):
            xa = self._x_of(e['start'] / dur); xb = self._x_of(e['end'] / dur)
            if xa <= x <= xb and xb >= 0 and xa <= self.width():
                return (i, 'body')
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit(e.position().x())
        if not hit:
            return
        i, part = hit
        if self._clip_mode():
            self.ed._select_clip_row(i, seek=(part == 'body'))
        else:
            self.ed._select_sub_row(i, seek=(part == 'body'))   # 点方块体才跳转；抓手柄只选中
        dia = self._dia()
        ev = dia[i]
        self._drag = {'row': i, 'part': part, 'pms': self._ms_at(e.position().x()),
                      's0': ev['start'], 'e0': ev['end'], 'moved': False}
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag:
            dg = self._drag; i = dg['row']; dia = self._dia()
            if 0 <= i < len(dia):
                if not dg['moved']:
                    if not self._clip_mode():   # 片段无撤销栈，仅字幕记录撤销
                        self.ed._push_undo("移动字幕" if dg['part'] == 'move' else "拖动调整字幕时间")
                    dg['moved'] = True
                ev = dia[i]; ms = self._ms_at(x)
                if dg['part'] == 'start':
                    ev['start'] = max(0, min(ms, ev['end'] - 20))
                elif dg['part'] == 'end':
                    ev['end'] = max(ev['start'] + 20, ms)
                else:                                   # move：整体平移，保持时长
                    length = dg['e0'] - dg['s0']
                    ns = max(0, dg['s0'] + (ms - dg['pms']))
                    ev['start'], ev['end'] = ns, ns + length
                self.update()
            return
        hit = self._hit(x)
        self.setCursor(Qt.SizeHorCursor if (hit and hit[1] in ('start', 'end'))
                       else (Qt.OpenHandCursor if hit else Qt.PointingHandCursor))

    def mouseReleaseEvent(self, e):
        if self._drag:
            moved = self._drag.get('moved')
            self._drag = None
            if moved:
                if self._clip_mode():
                    self.ed._refill_clip_table()   # 片段：刷新片段表
                else:
                    self.ed._sub_track_commit()    # 字幕：写回文件 + 刷新表格
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        hit = self._hit(e.position().x())
        if hit:
            if self._clip_mode():
                self.ed._select_clip_row(hit[0], seek=True)   # 片段：双击跳到片段
            else:
                self.ed._edit_sub_text(hit[0])     # 字幕：双击 → 右侧编辑该字幕文本

    def contextMenuEvent(self, e):
        x = e.pos().x()
        hit = self._hit(x)
        menu = QMenu(self)
        if self._clip_mode():                  # 剪辑模式：片段轨右键
            if hit:
                act_del = menu.addAction("删除该片段")
                menu.addSeparator()
                act_cap = menu.addAction("截取")
                chosen = menu.exec(e.globalPos())
                if chosen is act_del:
                    self.ed._delete_clip(hit[0])
                elif chosen is act_cap:
                    self.ed._arm_capture_from_track()
            else:
                act_add = menu.addAction("在此处添加片段")
                menu.addSeparator()
                act_cap = menu.addAction("截取")
                chosen = menu.exec(e.globalPos())
                if chosen is act_add:
                    self.ed._add_clip_at(self._ms_at(x))
                elif chosen is act_cap:
                    self.ed._arm_capture_from_track()
            self.update(); return
        if hit:                                # 右键某条字幕：分割 / 删除 / 截取
            i = hit[0]
            self._split_x = x; self.update()   # 蓝色虚线提示分割点
            act_split = menu.addAction("在此处分割字幕")
            act_del = menu.addAction("删除该字幕")
            menu.addSeparator()
            act_cap = menu.addAction("截取")
            chosen = menu.exec(e.globalPos())
            if chosen is act_split:
                self.ed._split_sub(i, self._ms_at(x))
            elif chosen is act_del:
                self.ed._delete_sub(i)
            elif chosen is act_cap:
                self.ed._arm_capture_from_track()
        else:                                  # 右键空白：添加字幕 / 截取
            act_add = menu.addAction("在此处添加字幕")
            menu.addSeparator()
            act_cap = menu.addAction("截取")
            chosen = menu.exec(e.globalPos())
            if chosen is act_add:
                self.ed._add_sub_at(self._ms_at(x))
            elif chosen is act_cap:
                self.ed._arm_capture_from_track()
        self._split_x = None; self.update()

    def paintEvent(self, e):
        W, H = self.width(), self.height()
        if W <= 0 or H <= 0:
            return
        dur = self.wave.duration_ms
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        if not dur:
            p.end(); return
        sel = self._sel_row()
        col = SETTINGS.get('color_button', '#df9e6f')
        block = QColor(col); block.setAlpha(205)
        bsel = QColor(SETTINGS.get('color_toggle', '#b17244'))
        fg = QColor(_fg(col)); handle = QColor('#ffffff')
        f = self.font(); f.setPixelSize(11); p.setFont(f); fm = p.fontMetrics()
        y, bh = 2, H - 4

        def draw_block(i, ev):
            xa = self._x_of(ev['start'] / dur); xb = self._x_of(ev['end'] / dur)
            if xb < 0 or xa > W:
                return
            x0 = max(0.0, xa); x1 = min(float(W), xb); bw = max(2.0, x1 - x0)
            r = QRectF(x0, y, bw, bh)
            p.setPen(Qt.NoPen); p.setBrush(bsel if i == sel else block)
            p.drawRoundedRect(r, 3, 3)
            if bw > 18:                                   # 够宽才写文字
                txt = self._text_of(ev, i)
                p.setPen(fg)
                p.drawText(r.adjusted(4, 0, -4, 0), int(Qt.AlignVCenter | Qt.AlignLeft),
                           fm.elidedText(txt, Qt.ElideRight, int(bw - 8)))
            if i == sel:                                  # 左右竖线手柄：仅选中块显示
                p.setPen(QPen(handle, 2))
                if xa >= -1:
                    p.drawLine(QPointF(x0 + 1, y + 2), QPointF(x0 + 1, y + bh - 2))
                if xb <= W + 1:
                    p.drawLine(QPointF(x1 - 1, y + 2), QPointF(x1 - 1, y + bh - 2))

        dia = self._dia()
        for i, ev in enumerate(dia):                      # 先画其余字幕块
            if i != sel:
                draw_block(i, ev)
        if 0 <= sel < len(dia):                           # 选中块最后画，盖在所有块之上不被遮挡
            draw_block(sel, dia[sel])
        # 播放头竖线由 PlayheadOverlay 统一绘制（贯通字幕轨+波形轨），此处不再单独画
        if self._split_x is not None:                     # 右键分割：蓝色虚线提示分割点
            pen = QPen(QColor('#2a7bff')); pen.setStyle(Qt.DashLine); pen.setWidth(1)
            p.setPen(pen)
            p.drawLine(QPointF(self._split_x, 0), QPointF(self._split_x, H))
        p.end()


class PlayheadOverlay(QWidget):
    """贯通的单条播放头：盖在「字幕轨 + 波形轨」之上画一条竖线，
    避免两个控件各画一条线导致对不齐/颜色不一致。覆盖整个 wave_pane，
    用波形的缩放窗口换算 x，y 从字幕轨顶部一直延伸到波形轨底部。"""
    def __init__(self, wave, subtrack, waveform, parent):
        super().__init__(parent)
        self.wave = wave
        self.subtrack = subtrack
        self.waveform = waveform
        self.setAttribute(Qt.WA_TransparentForMouseEvents)   # 不挡点击，纯装饰层
        self.setAttribute(Qt.WA_TranslucentBackground)       # 背景透明，只露出竖线（不遮挡下方波形/字幕轨）

    def paintEvent(self, e):
        w = self.wave; W = self.width()
        if W <= 0 or not w.zoom:
            return
        hx = (w.pos - w.view_start) * w.zoom * W       # 与字幕轨/波形轨同一换算，保证对齐
        if not (0 <= hx <= W):
            return
        top = self.subtrack if self.subtrack.isVisible() else self.waveform
        y0 = top.y()
        y1 = self.waveform.y() + self.waveform.height()
        p = QPainter(self)
        p.setPen(QPen(QColor(SETTINGS.get('color_playhead', '#f9eee5')), 2))
        p.drawLine(QPointF(hx, y0), QPointF(hx, y1))
        p.end()


class ClipExportDialog(QDialog):
    """导出片段对话框：树形罗列 视频→片段，勾选要导出的；勾选视频=全选其片段。"""
    def __init__(self, videos, concat_default, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出片段")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("勾选要导出的片段（勾选视频名 = 全选该视频的片段）："))
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        for v in videos:
            top = QTreeWidgetItem(self.tree, [os.path.basename(v['path'])])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            top.setToolTip(0, v['path'])
            top.setExpanded(True)
            for ci, c in enumerate(v['clips']):
                ch = QTreeWidgetItem(top, [f"片段{ci + 1}    {core.ms_to_ass(c['start'])} → {core.ms_to_ass(c['end'])}"])
                ch.setFlags(ch.flags() | Qt.ItemIsUserCheckable)
                ch.setCheckState(0, Qt.Checked)   # 默认全选
        self.tree.expandAll()
        lay.addWidget(self.tree, 1)
        # 导出形式：两个互斥的勾选框（与上方片段勾选样式一致，不用圆点）
        lay.addWidget(QLabel("导出形式："))
        self.cb_each = QCheckBox("片段导出（每个片段导出一个视频）")
        self.cb_one = QCheckBox("合并导出（每个视频导出一个合并视频）")
        (self.cb_one if concat_default else self.cb_each).setChecked(True)
        self.cb_each.toggled.connect(lambda on: self._exclusive(self.cb_each, on))
        self.cb_one.toggled.connect(lambda on: self._exclusive(self.cb_one, on))
        lay.addWidget(self.cb_each); lay.addWidget(self.cb_one)
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(self.accept); bbox.rejected.connect(self.reject)
        lay.addWidget(bbox)
        self.resize(480, 520)

    def _exclusive(self, src, on):
        """两个导出形式勾选框互斥：勾选一个则取消另一个，且始终保持一个被勾选。"""
        other = self.cb_one if src is self.cb_each else self.cb_each
        if on:
            other.blockSignals(True); other.setChecked(False); other.blockSignals(False)
        elif not other.isChecked():
            src.blockSignals(True); src.setChecked(True); src.blockSignals(False)   # 不允许两个都不选

    def selection(self):
        """返回 {视频下标: [片段下标,...]}（仅已勾选的片段）。"""
        result = {}
        for vi in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(vi)
            picked = [ci for ci in range(top.childCount())
                      if top.child(ci).checkState(0) == Qt.Checked]
            if picked:
                result[vi] = picked
        return result

    def concat(self):
        return self.cb_one.isChecked()


class Editor(QMainWindow):
    _wave_ready = Signal(str, object)   # 后台波形生成完成 -> 主线程回填(video_path, peaks)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("字幕编辑器")
        self.resize(1280, 760)
        self.setAcceptDrops(True)

        self.docs = []              # 已载入的 AssDoc 列表（每个标签页一个）
        self.cur = -1               # 当前活动文档下标（-1=无）
        self.playW, self.playH = 1920, 1080
        self.cur_video = None       # 当前激活视频路径（None=无）
        self._coord_text = None     # 悬停视频时的坐标文本（None=不在显示）
        self._wave_loading = None   # 正在后台生成波形的视频路径（去重）

        # 多视频：可导入多个，一次激活一个；每个视频各自带一组片段
        self.videos = []            # [{'path': str, 'clips': [{'start':ms,'end':ms}, ...]}]
        self._active_vid = -1       # 当前激活视频在 self.videos 中的下标

        # 视频剪辑模式状态
        self.mode = 'subtitle'      # 'subtitle' | 'clip'
        self._clip_in = None        # 当前入点(ms)
        self._clip_out = None       # 当前出点(ms)
        self.clips = []             # 激活视频的片段列表（指向 videos[_active_vid]['clips']）
        self.clip_concat = False    # True=拼成一个文件；False=各导出一个
        self._clip_anchor = -1      # 片段表多选锚点（Shift 框选用）

        self.task = {'running': False, 'kind': None, 'done': False, 'error': None,
                     'percent': 0, 'output': None, 'cancelled': False}
        self._proc = None           # 当前任务的子进程句柄（render/asr/clip），用于中止
        self._cancel = False        # 是否用户主动停止
        self._populating = False
        self._sub_loaded = False    # 当前视频是否已往 mpv 加载过字幕轨
        self._loaded_sub_path = None  # 当前 mpv 已加载的字幕文件路径（可能是合并后的临时文件）
        self._play_row = -1           # 跟随条当前填充高亮的播放行
        self._anchor_row = -1         # 多选锚点（Shift 框选用）
        self._follow_suspend_until = 0.0   # 此刻之前暂停"聚焦自动滚动"（用户刚手动点过行）
        self.player = None            # 先占位，_build_ui 里可能用到（mpv 真正初始化在 _init_mpv）

        self._build_ui()
        self._init_mpv()
        self._wave_ready.connect(self._on_wave_ready)   # 后台波形完成 → 主线程回填
        self.waveform.rangeSelected.connect(self._on_wave_range)   # 波形拖选 → 截取插入 / 重定时
        self.waveform.pointPicked.connect(self._on_wave_point)     # 波形点选 → 设开始/结束时间
        self.waveform.rsetClick.connect(self._on_rset_click)       # R 模式单击 → 设开始 / Ctrl设结束

        # 防抖写盘
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._do_save)

        # 后台任务轮询
        self._task_timer = QTimer(self); self._task_timer.timeout.connect(self._poll_task)
        self._task_timer.start(300)

        # 预览进度 → 高亮当前行
        self._hl_timer = QTimer(self); self._hl_timer.timeout.connect(self._highlight_current)
        self._hl_timer.start(200)

        self._load_fonts()
        self.update_buttons()
        self._status("未导入视频 — 点「导入视频」开始")

        # 应用级按键拦截：左右键 seek、空格暂停（编辑文本/数值时放行）
        QApplication.instance().installEventFilter(self)

        sc_undo = QShortcut(QKeySequence.Undo, self)   # Ctrl+Z 撤销
        sc_undo.activated.connect(self._undo)

        geo = SETTINGS.get('window_geometry')   # 恢复上次窗口大小/位置
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode('ascii')))
            except Exception:
                pass

    # ── 界面 ────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(8, 8, 8, 8); root.setSpacing(6)
        self._import_btns = []   # 菜单栏按钮（视频/ASS 导入），用 color_button 改色
        self._batch_btns = []    # 批量操作按钮（换行/聚焦），用 color_batch_btn 改色
        self._theme_labels = []  # 栏目名标签（菜单栏/工具栏/批量操作），随主色变
        self._area_labels = []   # 区标签（视频区/字幕区/波形区），比栏目名更浅一级
        self._last_status = "…"  # 最近一条状态提示（选中字数清除后恢复它）
        self._status_history = []  # 操作历史：右上角提示内容（带时间），最新在末尾
        self._undo_stack = []      # 撤销栈：每项 (描述, 状态快照)
        self._suspend_undo = False # 恢复/批量刷新期间不记录新快照
        self._last_undo_key = None # 同类连续操作（如连续调字号）合并用

        # ── 顶栏（始终可见）：模式切换 + 状态提示 + 设置 ──
        self.toolbar = QWidget(); self.toolbar.setObjectName('toolbar')
        bar = QHBoxLayout(self.toolbar); bar.setContentsMargins(6, 4, 6, 4); bar.setSpacing(8)
        # 模式切换：字幕编辑 / 视频剪辑（互斥，左侧 mpv 预览两模式共享）
        self.btn_mode_sub = QPushButton("字幕编辑"); self.btn_mode_sub.setCheckable(True); self.btn_mode_sub.setChecked(True)
        self.btn_mode_clip = QPushButton("视频剪辑"); self.btn_mode_clip.setCheckable(True)
        self.btn_mode_merge = QPushButton("视频合并"); self.btn_mode_merge.setCheckable(True)
        self.btn_mode_sub.setToolTip("字幕/弹幕编辑模式")
        self.btn_mode_clip.setToolTip("视频剪辑模式：标记入/出点，无损快切导出（不涉及字幕）")
        self.btn_mode_merge.setToolTip("视频合并模式：拖入多个完整视频，拖动排序后首尾相接合并为一个")
        self.btn_mode_sub.clicked.connect(lambda: self._set_mode('subtitle'))
        self.btn_mode_clip.clicked.connect(lambda: self._set_mode('clip'))
        self.btn_mode_merge.clicked.connect(lambda: self._set_mode('merge'))
        bar.addWidget(self.btn_mode_sub); bar.addWidget(self.btn_mode_clip); bar.addWidget(self.btn_mode_merge)
        bar.addStretch(1)
        self.btn_stop = QPushButton("停止")   # 停止当前任务（渲染/识别/切割）；仅运行时显示
        self.btn_stop.setToolTip("停止当前正在进行的渲染 / 语音识别 / 切割导出")
        self.btn_stop.clicked.connect(self.stop_task)
        self.btn_stop.setStyleSheet(
            "QPushButton{background:#b03030;color:#fff;padding:6px 14px;border:none;border-radius:10px;}"
            "QPushButton:hover{background:#c84040;}")
        self.btn_stop.setVisible(False)
        bar.addWidget(self.btn_stop)
        self.lbl_status = ClickableLabel("…")   # 统一提示（含选中字数/坐标）；点击看操作历史
        self.lbl_status.setToolTip("点击查看操作历史")
        self.lbl_status.clicked.connect(self._show_history)
        bar.addWidget(self.lbl_status)
        self.btn_settings = GearButton()
        self.btn_settings.setToolTip("设置"); self.btn_settings.clicked.connect(self.open_settings)
        bar.addWidget(self.btn_settings)
        root.addWidget(self.toolbar)

        # ── 操作栏（仅字幕编辑可见）：视频导入 / 导入 ASS / 语音识别 / 渲染视频 ──
        self.action_bar = QWidget()
        ab = QHBoxLayout(self.action_bar); ab.setContentsMargins(0, 0, 0, 0); ab.setSpacing(8)
        self.btn_imp_video = QPushButton("视频导入"); self.btn_imp_video.setToolTip("导入视频（也可直接拖入窗口）")
        self.btn_imp_video.clicked.connect(lambda: self.import_video())
        self.btn_imp_ass = QPushButton("导入 ASS"); self.btn_imp_ass.setToolTip("导入字幕/弹幕 ass（也可直接拖入；每个一个标签页）")
        self.btn_imp_ass.clicked.connect(lambda: self.import_ass())
        self._import_btns += [self.btn_imp_video, self.btn_imp_ass]   # 菜单栏按钮：color_button
        self.btn_asr = QPushButton("语音识别"); self.btn_asr.clicked.connect(self.run_asr)
        self.btn_render = QPushButton("渲染视频"); self.btn_render.clicked.connect(self.render_video)
        self.btn_wave_enable = QPushButton("开启波形"); self.btn_wave_enable.setCheckable(True)
        self.btn_wave_enable.setChecked(bool(SETTINGS.get('wave_enabled', False)))
        self.btn_wave_enable.setToolTip("开启后在视频下方显示音频波形（字幕编辑/视频剪辑都可用），默认关闭")
        self.btn_wave_enable.toggled.connect(self._toggle_wave_enable)
        ab.addWidget(self._bar_label("菜单栏"))
        for b in (self.btn_imp_video, self.btn_imp_ass, self.btn_asr, self.btn_render):
            ab.addWidget(b)
        ab.addWidget(self.btn_wave_enable)
        ab.addStretch(1)
        root.addWidget(self.action_bar)

        # ── 剪辑菜单栏（仅视频剪辑模式可见）：波形开关（与字幕模式的「波形」同步）──
        self.clip_action_bar = QWidget()
        cab = QHBoxLayout(self.clip_action_bar); cab.setContentsMargins(0, 0, 0, 0); cab.setSpacing(8)
        self.btn_wave_enable_clip = QPushButton("开启波形"); self.btn_wave_enable_clip.setCheckable(True)
        self.btn_wave_enable_clip.setChecked(bool(SETTINGS.get('wave_enabled', False)))
        self.btn_wave_enable_clip.setToolTip("开启后视频下方显示波形；可用「截取」在波形上拖选直接添加片段")
        self.btn_wave_enable_clip.toggled.connect(self._toggle_wave_enable)
        self._wave_enable_btns = [self.btn_wave_enable, self.btn_wave_enable_clip]
        self.btn_imp_video_clip = QPushButton("视频导入"); self.btn_imp_video_clip.setToolTip("导入视频（也可直接拖入窗口）")
        self.btn_imp_video_clip.clicked.connect(lambda: self.import_video())
        self._import_btns.append(self.btn_imp_video_clip)   # 菜单栏按钮：color_button
        cab.addWidget(self._bar_label("菜单栏"))
        cab.addWidget(self.btn_imp_video_clip)
        cab.addWidget(self.btn_wave_enable_clip); cab.addStretch(1)
        root.addWidget(self.clip_action_bar)

        # ── 样式工具栏：样式下拉 + 该 Style 的字体/字号/下边距/描边/字色/描边色 ──
        # 改这里 = 就地 patch 当前文档里选中那条 Style；哪些参数对该 Style 有效由数据自动判定后置灰
        self.style_bar = QWidget()
        sb = QHBoxLayout(self.style_bar); sb.setContentsMargins(0, 0, 0, 0)
        self.cmb_style = _NoScrollComboBox(); self.cmb_style.setMinimumWidth(130)
        self.cmb_style.setToolTip("选择要编辑的样式 Style；下面各项改的就是这条 Style")
        self.cmb_style.currentIndexChanged.connect(self._on_style_selected)
        self.cmb_font = _NoScrollComboBox(); self.cmb_font.setMinimumWidth(180)
        self.cmb_font.setEditable(True)
        self.sp_size = _NoScrollSpinBox(); self.sp_size.setRange(1, 400)
        self.sp_margin = _NoScrollSpinBox(); self.sp_margin.setRange(0, 100); self.sp_margin.setSingleStep(1); self.sp_margin.setSuffix('%')
        self.sp_outline = _NoScrollDoubleSpinBox(); self.sp_outline.setRange(0, 20); self.sp_outline.setSingleStep(0.5)
        # 字色/描边色：标签 + 旁边一个小方块色块（点方块选色）
        self.btn_color = QPushButton(); self.btn_color.setFixedSize(22, 22); self.btn_color.setCursor(Qt.PointingHandCursor)
        self.btn_color.setToolTip("点击选择字色"); self.btn_color.clicked.connect(lambda: self._pick_color('font_color', self.btn_color))
        self.btn_ocolor = QPushButton(); self.btn_ocolor.setFixedSize(22, 22); self.btn_ocolor.setCursor(Qt.PointingHandCursor)
        self.btn_ocolor.setToolTip("点击选择描边色"); self.btn_ocolor.clicked.connect(lambda: self._pick_color('outline_color', self.btn_ocolor))

        sb.addWidget(self._bar_label("工具栏"))
        sb.addWidget(QLabel("样式")); sb.addWidget(self.cmb_style)
        for w, lab in ((self.cmb_font, "字体"), (self.sp_size, "字号"),
                       (self.sp_margin, "下边距"), (self.sp_outline, "描边")):
            sb.addWidget(QLabel(lab)); sb.addWidget(w)
        sb.addWidget(QLabel("字色")); sb.addWidget(self.btn_color)
        sb.addWidget(QLabel("描边色")); sb.addWidget(self.btn_ocolor)
        sb.addStretch(1)
        root.addWidget(self.style_bar)

        # 参数键 -> 控件（数据驱动置灰用）
        self._style_ctrls = {
            'font_name': self.cmb_font, 'font_size': self.sp_size,
            'margin_bottom': self.sp_margin, 'outline': self.sp_outline,
            'font_color': self.btn_color, 'outline_color': self.btn_ocolor,
        }

        # ── 批量子工具栏：时间偏移 / 换行（作用于当前标签页整篇）──
        self.batch_bar = QWidget()
        bb = QHBoxLayout(self.batch_bar); bb.setContentsMargins(0, 0, 0, 0)
        self.sp_offset = _NoScrollSpinBox(); self.sp_offset.setRange(-600000, 600000); self.sp_offset.setSingleStep(50); self.sp_offset.setSuffix(' ms')
        self.sp_offset.valueChanged.connect(self._offset_changed)
        self.sp_wrap = _NoScrollSpinBox(); self.sp_wrap.setRange(0, 200)
        self.sp_wrap.setToolTip("改完按回车或点别处即自动按此字数换行")
        self.sp_wrap.editingFinished.connect(self.apply_wrap)   # 回车/失焦自动应用换行（无变化则无操作）
        self.btn_replace = QPushButton("文本替换"); self.btn_replace.setToolTip("对当前标签页所有行的文本批量查找替换（支持默认模板、勾选、批量应用）")
        self.btn_replace.clicked.connect(self.apply_replace)
        self._batch_btns += [self.btn_replace]   # 批量操作按钮：color_batch_btn
        bb.addWidget(self._bar_label("批量操作"))
        bb.addWidget(QLabel("时间偏移")); bb.addWidget(self.sp_offset)
        bb.addWidget(QLabel("换行字数")); bb.addWidget(self.sp_wrap)
        bb.addWidget(self.btn_replace)
        bb.addStretch(1)
        self.btn_focus = QPushButton("聚焦"); self.btn_focus.setToolTip("立即把当前播放行滚到字幕表中央（一次性）")
        self.btn_focus.clicked.connect(self.focus_play_row)
        self._batch_btns.append(self.btn_focus)   # 一次性动作：胶囊样式
        self.btn_follow = QPushButton("字幕聚焦"); self.btn_follow.setCheckable(True)
        self.btn_follow.setChecked(bool(SETTINGS.get('follow_focus', True)))
        self.btn_follow.setToolTip("开启后播放时自动滚动字幕表，让当前播放行保持在聚焦位置（设置可调靠上/居中/靠下）")
        self.btn_follow.toggled.connect(self._toggle_follow)
        self.btn_sel_follow = QPushButton("选随"); self.btn_sel_follow.setCheckable(True)
        self.btn_sel_follow.setChecked(bool(SETTINGS.get('select_follow', False)))
        self.btn_sel_follow.setToolTip("开启后选中行始终为当前播放行（便于用快捷键设其开始/结束时间）")
        self.btn_sel_follow.toggled.connect(self._toggle_sel_follow)
        self.btn_cross = CrossToggleButton(); self.btn_cross.setChecked(False)   # 默认关闭十字准线
        self.btn_cross.setToolTip("开关：鼠标悬停视频时显示十字准线+坐标")
        self.btn_cross.toggled.connect(self._toggle_cross)
        self.btn_capture = QPushButton("截取"); self.btn_capture.setCheckable(True)
        self.btn_capture.setToolTip("截取模式：在波形上拖选一段，插入空白字幕行（拖一次后自动退出，Esc 取消）")
        self.btn_capture.toggled.connect(self._toggle_capture)
        self.btn_wave_center = QPushButton("波形聚焦"); self.btn_wave_center.setCheckable(True)
        self.btn_wave_center.setChecked(bool(SETTINGS.get('wave_center', True)))
        self.btn_wave_center.setToolTip("开启后放大波形时，播放头保持在聚焦位置（设置可调靠左/居中/靠右；关闭则波形不随播放滚动）")
        self.btn_wave_center.toggled.connect(self._toggle_wave_center)
        # 注：聚焦/选随/字幕居中 → 字幕区；波形居中/截取 → 波形区；十字 → 视频控制行（见下方各处安置）
        root.addWidget(self.batch_bar)

        # 工具栏里可编辑参数控件：回车或点别处后收起编辑光标
        self._param_widgets = (self.cmb_font, self.sp_size, self.sp_margin,
                               self.sp_outline, self.sp_offset, self.sp_wrap)

        # 样式控件变更 → patch 当前 Style
        self.cmb_font.currentTextChanged.connect(lambda *_: self._style_changed('font'))
        self.sp_size.valueChanged.connect(lambda *_: self._style_changed('size'))
        self.sp_margin.valueChanged.connect(lambda *_: self._style_changed('margin'))
        self.sp_outline.valueChanged.connect(lambda *_: self._style_changed('outline'))

        # 主区：左=视频 / 右=统一 ASS 编辑（标签页切换多个 ass）
        split = QSplitter(Qt.Horizontal); self.split = split

        # ── 左：视频（顶部一个标签页显示当前视频，× 关闭，与右侧统一）──
        self.left_pane = QWidget(); self.left_pane.setObjectName('leftpane')
        self.left_pane.setStyleSheet("QWidget#leftpane{background:#000000;}")   # 视频区纯黑
        lv = QVBoxLayout(self.left_pane)
        lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(4)
        self.vtabbar = QTabBar(); self.vtabbar.setExpanding(False); self.vtabbar.setTabsClosable(False)
        self.vtabbar.setUsesScrollButtons(True); self.vtabbar.setDrawBase(False)
        self.vtabbar.setVisible(False)   # 无视频时隐藏
        self.vtabbar.currentChanged.connect(self._on_video_tab_changed)   # 切换激活视频
        self.video_frame = VideoWidget(self.toggle_pause, self._video_hover, self._video_leave)
        self.crosshair = CrosshairOverlay(self)   # 视频十字准线浮层
        # 视频区 = 顶部小工具条（十字 / 导出帧）+ 画面；纯黑底
        self.btn_frame = QPushButton("导出帧"); self.btn_frame.setToolTip("把当前画面导出为 PNG 图片")
        self.btn_frame.clicked.connect(self.export_frame)
        self.video_pane = QWidget(); self.video_pane.setObjectName('videopane')
        self.video_pane.setStyleSheet("QWidget#videopane{background:#000000;}")
        vpl = QVBoxLayout(self.video_pane); vpl.setContentsMargins(0, 0, 0, 0); vpl.setSpacing(0)
        self.video_toolbar = QWidget()
        vtb = QHBoxLayout(self.video_toolbar); vtb.setContentsMargins(4, 2, 4, 2); vtb.setSpacing(8)
        vtb.addWidget(self._bar_label("V", area=True, tip="视频区 video")); vtb.addWidget(self.btn_cross); vtb.addWidget(self.btn_frame); vtb.addStretch(1)
        self.video_toolbar.setVisible(False)   # 未导入视频前隐藏（视频/十字/导出帧那一行）
        vpl.addWidget(self.video_toolbar); vpl.addWidget(self.vtabbar); vpl.addWidget(self.video_frame, 1)   # tab 在工具栏下
        # 波形区 = 顶部小工具条（截取 / 波形居中）+ 波形本体；深灰底
        self.waveform = WaveformBar(self._waveform_seek)
        self.wave_pane = QWidget(); self.wave_pane.setObjectName('wavepane')
        self.wave_pane.setStyleSheet("QWidget#wavepane{background:#181818;}")
        wpl = QVBoxLayout(self.wave_pane); wpl.setContentsMargins(0, 0, 0, 0); wpl.setSpacing(0)   # 0 间距让字幕轨/波形播放线相接
        wtb = QHBoxLayout(); wtb.setContentsMargins(4, 2, 4, 0); wtb.setSpacing(8)
        self.btn_subtrack = QPushButton("字幕轨"); self.btn_subtrack.setCheckable(True)
        self.btn_subtrack.setChecked(bool(SETTINGS.get('subtrack_enabled', False)))
        self.btn_subtrack.setToolTip("显示字幕轨道：每条字幕一个方块，拖左右竖线可调开始/结束（随滚轮缩放）")
        self.btn_subtrack.toggled.connect(self._toggle_subtrack)
        wtb.addWidget(self._bar_label("W", area=True, tip="波形区 wave")); wtb.addWidget(self.btn_capture)
        wtb.addWidget(self.btn_wave_center); wtb.addWidget(self.btn_subtrack); wtb.addStretch(1)
        self.subtrack = SubtitleTrack(self.waveform, self)   # 字幕轨道（放波形上方）
        self.subtrack.setVisible(False)
        wpl.addLayout(wtb); wpl.addWidget(self.subtrack); wpl.addWidget(self.waveform, 1)
        # 单条贯通播放头：盖在字幕轨+波形轨之上画一条线（取代两控件各画一条、对不齐）
        self.playhead_overlay = PlayheadOverlay(self.waveform, self.subtrack, self.waveform, self.wave_pane)
        self.waveform.add_peer(self.playhead_overlay)
        self.wave_pane.installEventFilter(self)   # 监听 wave_pane 尺寸变化，同步覆盖层几何
        # 视频 / 波形 之间放可拖动分界线
        self.lsplit = QSplitter(Qt.Vertical)
        self.lsplit.setChildrenCollapsible(False)
        self.lsplit.addWidget(self.video_pane)
        self.lsplit.addWidget(self.wave_pane)
        self.lsplit.setStretchFactor(0, 1); self.lsplit.setStretchFactor(1, 0)
        self.lsplit.setSizes([600, 110])
        lv.addWidget(self.lsplit, 1)
        # 进度条区 = 播放 / 进度 / 时间 / seek 锁；独立深灰底
        self.transport = QWidget(); self.transport.setObjectName('transport')
        self.transport.setStyleSheet("QWidget#transport{background:#202020;}")
        ctl = QHBoxLayout(self.transport); ctl.setContentsMargins(6, 2, 6, 2)
        self.btn_play = PlayButton(); self.btn_play.clicked.connect(self.toggle_pause)
        # 进度条改为可缩放轨道，与波形共用缩放窗口（滚轮放大/Shift平移，4小时视频可精细拖动）
        self.seektrack = SeekTrack(self.waveform, self._waveform_seek)
        self.seektrack.setToolTip("滚轮放大进度条、Shift+滚轮左右移动；点击/拖动跳转")
        self.waveform.add_peer(self.seektrack)   # 波形缩放/播放头变化 → 联动重绘进度条
        self.waveform.add_peer(self.subtrack)    # 同步重绘字幕轨道
        self.lbl_time = QLabel("0:00 / 0:00")
        self.btn_lock = LockButton()
        self.btn_lock.setChecked(bool(SETTINGS.get('seek_lock', True)))   # 默认上锁
        self.btn_lock.setToolTip("锁定后点击字幕/片段行不跳转播放(seek)；解锁则点击跳转")
        self.btn_lock.toggled.connect(self._toggle_seek_lock)
        ctl.addWidget(self.btn_play); ctl.addWidget(self.seektrack, 1); ctl.addWidget(self.lbl_time)
        ctl.addWidget(self.btn_lock)
        lv.addWidget(self.transport)
        split.addWidget(self.left_pane)

        # ── 右：统一 ASS 编辑栏（标签页 + 一张表，字幕/弹幕同款逻辑）──
        self.right_pane = QWidget(); self.right_pane.setObjectName('rightpane')
        self.right_pane.setStyleSheet("QWidget#rightpane{background:#242424;}")   # 表格区灰色
        rv = QVBoxLayout(self.right_pane)
        rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(4)
        self.tabbar = QTabBar(); self.tabbar.setExpanding(False); self.tabbar.setTabsClosable(False)
        self.tabbar.setUsesScrollButtons(True); self.tabbar.setDrawBase(False)
        self.tabbar.setVisible(False)   # 无 ASS 时隐藏（避免关完残留旧标签底色）
        self.tabbar.currentChanged.connect(self._on_tab_changed)   # 关闭叉改用自绘 TabCloseButton（见 _add_doc）
        self.tabbar.setMovable(True)                 # 拖动标签调整字幕图层顺序（最左=最上层）
        self.tabbar.tabMoved.connect(self._on_tab_moved)
        # 字幕区工具条：标签 + 聚焦 / 选随 / 字幕居中；无 ASS 时整条隐藏（避免空浮）
        self.sub_toolbar = QWidget()
        stb = QHBoxLayout(self.sub_toolbar); stb.setContentsMargins(4, 2, 4, 0); stb.setSpacing(8)
        stb.addWidget(self._bar_label("A", area=True, tip="字幕区 ASS"))
        stb.addWidget(self.btn_focus); stb.addWidget(self.btn_sel_follow); stb.addWidget(self.btn_follow)
        stb.addStretch(1)
        self.sub_toolbar.setVisible(False)   # 无 ASS 时隐藏（_on_tab_changed 控制）
        rv.addWidget(self.sub_toolbar)       # 工具条在上
        rv.addWidget(self.tabbar)            # 标签在工具条之下（与视频区统一）
        self.table = MenuTable(0, 5)
        self.table.contextRequested.connect(self._table_menu)   # 右键插入/删除整行
        self.table.setHorizontalHeaderLabels(["#", "开始", "结束", "样式", "文本"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)   # 不用 Qt 选择，行标记自管（消除系统蓝）
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setItemDelegate(_CellEditDelegate(self.table))   # 编辑框盖住网格线
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        for i in range(4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Interactive)   # 文本列按真实宽度
        hh.setStretchLastSection(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)   # 逐像素滚动：聚焦可精确定位到任意比例
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.itemChanged.connect(self._item_changed)
        self._sc_del = QShortcut(self._key_seq('delete_row'), self.table)
        self._sc_del.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_del.activated.connect(self._delete_row)
        rv.addWidget(self.table, 1)
        # 右侧用堆叠容器：page0=字幕编辑(现有)，page1=视频剪辑，page2=视频合并
        self.clip_pane = self._build_clip_pane()
        self.merge_pane = self._build_merge_pane()
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.right_pane)   # index 0
        self.right_stack.addWidget(self.clip_pane)    # index 1
        self.right_stack.addWidget(self.merge_pane)   # index 2
        split.addWidget(self.right_stack)

        split.setSizes([520, 760])
        root.addWidget(split, 1)

        self.table.setVisible(False)   # 启动时无 ASS → 不显示表头
        self._set_mode('subtitle')     # 初始模式：字幕编辑（统一各栏可见性）
        self._apply_widget_colors()

    # ── 视频剪辑面板 ──────────────────────────────────────────────────────
    def _build_clip_pane(self):
        """视频剪辑面板：标记入/出点、片段列表、导出形式与导出按钮（不涉及 ass）。"""
        pane = QWidget(); pane.setObjectName('clippane')
        pane.setStyleSheet("QWidget#clippane{background:#242424;}")   # 与字幕区 right_pane 同灰
        v = QVBoxLayout(pane); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(8)
        # 未导入视频前整块内容隐藏（只留默认灰底）；导入后由 update_buttons 显示
        self.clip_body = QWidget()
        cv = QVBoxLayout(self.clip_body); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(8)
        v.addWidget(self.clip_body)
        self.clip_body.setVisible(False)

        # S 区标签（片段区 segment，与「视频合并」右侧同名）
        seg_top = QHBoxLayout(); seg_top.setSpacing(8)
        seg_top.addWidget(self._bar_label(
            "S", area=True,
            tip="片段区 segment：标记入/出点添加片段，可导出为多个片段或拼成一个"))
        seg_top.addStretch(1)
        cv.addLayout(seg_top)

        # 标记行：设入/出点 + 当前入出显示 + 添加片段
        mark = QHBoxLayout(); mark.setSpacing(8)
        self.btn_clip_in = QPushButton("设入点"); self.btn_clip_in.setToolTip("把当前播放位置设为入点（快捷键见设置）")
        self.btn_clip_out = QPushButton("设出点"); self.btn_clip_out.setToolTip("把当前播放位置设为出点（快捷键见设置）")
        self.btn_clip_add = QPushButton("添加片段"); self.btn_clip_add.setToolTip("把当前入/出点加入下方片段列表")
        self.btn_clip_in.clicked.connect(self._clip_set_in)
        self.btn_clip_out.clicked.connect(self._clip_set_out)
        self.btn_clip_add.clicked.connect(self._clip_add)
        self.lbl_clip_io = QLabel("入 —    出 —")
        mark.addWidget(self.btn_clip_in); mark.addWidget(self.btn_clip_out)
        mark.addWidget(self.lbl_clip_io, 1); mark.addWidget(self.btn_clip_add)
        cv.addLayout(mark)

        # 片段列表（与字幕表同款：MenuTable 整行边框高亮 + delegate 去蓝/去分割线，自管行标记）
        self.clip_table = MenuTable(0, 4)
        self.clip_table.setHorizontalHeaderLabels(["#", "开始", "结束", "时长"])
        self.clip_table.verticalHeader().setVisible(False)
        self.clip_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clip_table.setSelectionMode(QAbstractItemView.NoSelection)   # 不用 Qt 选择（消除系统蓝）
        self.clip_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.clip_table.setItemDelegate(_CellEditDelegate(self.clip_table))
        self.clip_table.setShowGrid(False)
        hh = self.clip_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.Stretch)
        self.clip_table.cellClicked.connect(self._clip_row_clicked)
        self.clip_table.itemChanged.connect(self._clip_item_changed)
        self.clip_table.contextRequested.connect(self._clip_menu)   # 右键：删除 / 导出所选
        self._sc_clip_del = QShortcut(self._key_seq('delete_row'), self.clip_table)
        self._sc_clip_del.setContext(Qt.WidgetWithChildrenShortcut)
        self._sc_clip_del.activated.connect(self._clip_delete_selected)
        cv.addWidget(self.clip_table, 1)

        # 导出行：仅「导出片段」（导出形式在导出对话框里选）
        exp = QHBoxLayout(); exp.setSpacing(8)
        self.btn_export_clips = QPushButton("导出片段"); self.btn_export_clips.clicked.connect(self.export_clips_dialog)
        exp.addStretch(1); exp.addWidget(self.btn_export_clips)
        cv.addLayout(exp)
        return pane

    # ── 视频合并面板 ──────────────────────────────────────────────────────
    def _build_merge_pane(self):
        """视频合并面板（右侧 S 区）：拖入/添加多个完整视频，拖动列表项排序，双击预览、右键移除，
        调 merge_mp4 首尾相接合并为一个。"""
        pane = QWidget(); pane.setObjectName('mergepane')
        pane.setStyleSheet("QWidget#mergepane{background:#242424;}")   # 与字幕区 right_pane 同灰
        v = QVBoxLayout(pane); v.setContentsMargins(8, 8, 8, 8); v.setSpacing(8)

        # 顶部：S 区标签（hover 提示） + 添加视频 / 清空
        top = QHBoxLayout(); top.setSpacing(8)
        top.addWidget(self._bar_label(
            "S", area=True,
            tip="片段区 segment：拖入视频加入合并列表；双击预览、右键移除、拖动列表项调整顺序"
                "（自上而下首尾相接）；分辨率/编码不一致会自动归一后再合并"))
        btn_add = QPushButton("添加视频"); btn_add.setToolTip("选择视频加入合并列表（也可直接拖入窗口）")
        btn_add.clicked.connect(self._merge_add)
        btn_clear = QPushButton("清空"); btn_clear.clicked.connect(self._merge_clear)
        top.addWidget(btn_add); top.addWidget(btn_clear); top.addStretch(1)
        v.addLayout(top)

        self.merge_list = MergeList()
        self.merge_list.previewRequested.connect(self._merge_preview)   # 双击：左侧预览该视频
        self.merge_list.removeRequested.connect(self._merge_remove)     # 右键：移除所选
        self.merge_list.model().rowsInserted.connect(lambda *a: self.update_buttons())
        self.merge_list.model().rowsRemoved.connect(lambda *a: self.update_buttons())
        v.addWidget(self.merge_list, 1)

        # 合并行：硬件编码开关 + 合并按钮
        run = QHBoxLayout(); run.setSpacing(8)
        self.cb_merge_hw = QCheckBox("硬件编码 (NVENC)")
        self.cb_merge_hw.setChecked(True)
        self.cb_merge_hw.setToolTip("分辨率不一致需重编码时用显卡编码，更快更省 CPU；无 NVENC 会自动回退软件编码")
        run.addWidget(self.cb_merge_hw)
        self.cb_merge_replace = QCheckBox("合并后替换源文件")
        self.cb_merge_replace.setToolTip("合并成功后：把所有源视频移入回收站，并将合并视频改名为【首个视频】的完整文件名"
                                         "（相当于用合并结果替换第一个源文件）。不勾则仅生成「合并视频.mp4」，源文件保留")
        run.addWidget(self.cb_merge_replace)
        run.addStretch(1)
        self.btn_merge = QPushButton("合并"); self.btn_merge.clicked.connect(self.merge_videos)
        run.addWidget(self.btn_merge)
        v.addLayout(run)
        return pane

    def _merge_add(self):
        if self.task['running']:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "添加要合并的视频", "", VIDEO_FILTER)
        if files:
            self.merge_list.add_paths(files)
            self.update_buttons()

    def _merge_remove(self):
        for it in self.merge_list.selectedItems():
            self.merge_list.takeItem(self.merge_list.row(it))
        self.update_buttons()

    def _merge_clear(self):
        self.merge_list.clear()
        self.update_buttons()

    def _merge_preview(self, path):
        """双击合并列表项：在左侧 mpv 预览该视频。"""
        if path and os.path.exists(path):
            self._import_path(path)

    def _apply_bars_visible(self):
        """样式工具栏 / 批量操作栏：仅在字幕编辑模式且已载入 ASS 时显示。"""
        show = (self.mode == 'subtitle') and bool(self.docs)
        self.style_bar.setVisible(show)
        self.batch_bar.setVisible(show)

    def _set_mode(self, mode):
        """切换 字幕编辑 / 视频剪辑 / 视频合并 模式：左侧 mpv 预览共享，右侧面板与相关工具栏按模式显隐。"""
        self.mode = mode
        is_sub = (mode == 'subtitle')
        is_clip = (mode == 'clip')
        is_merge = (mode == 'merge')
        self.btn_mode_sub.setChecked(is_sub)
        self.btn_mode_clip.setChecked(is_clip)
        self.btn_mode_merge.setChecked(is_merge)
        # 菜单栏仅字幕、剪辑菜单栏仅剪辑；合并模式两者都不显示；工具栏/批量栏 仅字幕且已载入 ASS（见 _apply_bars_visible）
        self.action_bar.setVisible(is_sub)
        self.clip_action_bar.setVisible(is_clip)
        self._apply_bars_visible()
        self.right_stack.setCurrentIndex(0 if is_sub else (1 if is_clip else 2))
        self.btn_cross.setVisible(True)        # 十字准线在所有模式的 V 区都可用
        self._exit_wave_modes()                # 切模式一律退出波形交互（截取/重定时/点选）
        self._video_leave()                    # 切模式收起十字准线/坐标，hover 时再现
        self._refresh_sub()                    # 字幕→恢复预览；其他→移除字幕轨（守卫处理）
        self._ensure_waveform()                # 切到字幕模式且有视频时按需生成波形
        # 轨道按钮按模式改名：字幕模式=字幕轨，剪辑模式=片段轨
        self.btn_subtrack.setText("字幕轨" if is_sub else "片段轨")
        self.btn_subtrack.setToolTip(
            "显示字幕轨道：每条字幕一个方块，拖左右竖线/方块体可调时间（随滚轮缩放）" if is_sub
            else "显示片段轨：每个片段一个方块，拖左右竖线/方块体可调起止（随滚轮缩放）")
        self._apply_waveform_visible()
        if hasattr(self, 'subtrack'):
            self.subtrack.update()             # 切模式后轨道按新数据重绘
        self.update_buttons()

    # ── 剪辑：入/出点 / 片段列表 ──────────────────────────────────────────
    def _cur_pos_ms(self):
        if not (self.player and self.cur_video):
            return None
        try:
            pos = self.player.time_pos
        except Exception:
            pos = None
        return int(pos * 1000) if pos is not None else None

    def _clip_set_in(self):
        ms = self._cur_pos_ms()
        if ms is None:
            self._status("无视频/无法获取当前时间"); return
        self._clip_in = ms
        if self._clip_out is not None and self._clip_out <= ms:
            self._clip_out = None            # 入点越过原出点则清空出点
        self._update_clip_io_label()

    def _clip_set_out(self):
        ms = self._cur_pos_ms()
        if ms is None:
            self._status("无视频/无法获取当前时间"); return
        self._clip_out = ms
        self._update_clip_io_label()

    def _update_clip_io_label(self):
        i = core.ms_to_ass(self._clip_in) if self._clip_in is not None else "—"
        o = core.ms_to_ass(self._clip_out) if self._clip_out is not None else "—"
        self.lbl_clip_io.setText(f"入 {i}    出 {o}")

    def _clip_add(self):
        if self._clip_in is None or self._clip_out is None:
            self._status("请先设入点和出点"); return
        if self._clip_out <= self._clip_in:
            self._status("出点必须大于入点"); return
        s, e = self._clip_in, self._clip_out
        self.clips.append({'start': s, 'end': e})
        self._clip_in = self._clip_out = None   # 加完清空入/出点，方便标下一段
        self._update_clip_io_label()
        self._refill_clip_table()
        self.update_buttons()
        self._status(f"已添加片段 {core.ms_to_ass(s)} → {core.ms_to_ass(e)}")

    def _clip_add_range(self, start_ms, end_ms):
        """波形截取 → 直接把 [start,end] 加为一个片段（剪辑模式）。"""
        if end_ms <= start_ms:
            self._status("截取的片段无效（结束≤开始）"); return
        self.clips.append({'start': int(start_ms), 'end': int(end_ms)})
        self._refill_clip_table()
        self.update_buttons()
        self._status(f"已截取片段 {core.ms_to_ass(start_ms)} → {core.ms_to_ass(end_ms)}")

    def _refill_clip_table(self):
        self.clip_table.blockSignals(True)
        self.clip_table.setRowCount(len(self.clips))
        for r, c in enumerate(self.clips):
            num = QTableWidgetItem(str(r + 1)); num.setFlags(num.flags() & ~Qt.ItemIsEditable)
            dur = QTableWidgetItem(core.ms_to_ass(max(0, c['end'] - c['start'])))
            dur.setFlags(dur.flags() & ~Qt.ItemIsEditable)
            self.clip_table.setItem(r, 0, num)
            self.clip_table.setItem(r, 1, QTableWidgetItem(core.ms_to_ass(c['start'])))
            self.clip_table.setItem(r, 2, QTableWidgetItem(core.ms_to_ass(c['end'])))
            self.clip_table.setItem(r, 3, dur)
        self.clip_table.blockSignals(False)

    def _clip_item_changed(self, item):
        r, col = item.row(), item.column()
        if col not in (1, 2) or not (0 <= r < len(self.clips)):
            return
        try:
            ms = max(0, core.ass_to_ms(item.text().strip()))
        except Exception:
            self._refill_clip_table(); self._status("时间格式应为 H:MM:SS.cs"); return
        self.clips[r]['start' if col == 1 else 'end'] = ms
        if self.clips[r]['end'] <= self.clips[r]['start']:
            self._status("注意：该片段出点≤入点")
        self._refill_clip_table()

    def _clip_row_clicked(self, r, c):
        # 标记行（与字幕表一致）：单击=单选+锚点；Shift+单击=锚点到本行整段框选。再按 seek 锁决定是否跳转
        if (QApplication.keyboardModifiers() & Qt.ShiftModifier) and self._clip_anchor >= 0:
            lo, hi = sorted((self._clip_anchor, r))
            self.clip_table.mark_rows = set(range(lo, hi + 1))
        else:
            self._clip_anchor = r
            self.clip_table.mark_rows = {r}
        self.clip_table.mark_row = r
        self.clip_table.viewport().update()
        if self.btn_lock.isChecked():               # 已上锁：只标记，不跳转/不高亮（也是 seek 类）
            return
        if 0 <= r < len(self.clips) and self.player and self.cur_video:
            cl = self.clips[r]
            dur = self._player_duration()
            if c == 3 and dur:                      # 时长列：波形上淡蓝高亮该片段范围 3 秒（不跳转）
                self.waveform.show_range(cl['start'] / (dur * 1000), cl['end'] / (dur * 1000))
                return
            t = cl['end'] if c == 2 else cl['start']   # 结束列→跳结束，其余→跳开始
            try:
                self.player.command('seek', t / 1000, 'absolute', 'exact')
            except Exception:
                pass

    def _select_clip_row(self, i, seek=True):
        """片段轨点选某片段 → 选中片段表对应行（seek=True 且未上锁时跳到片段起点），并滚动到它。"""
        if not (0 <= i < len(self.clips)):
            return
        self.clip_table.mark_row = i; self.clip_table.mark_rows = {i}; self._clip_anchor = i
        self.clip_table.viewport().update()
        self.clip_table.scrollToItem(self.clip_table.item(i, 0), QAbstractItemView.EnsureVisible)
        if seek and not self.btn_lock.isChecked() and self.player and self.cur_video:
            try:
                self.player.command('seek', self.clips[i]['start'] / 1000, 'absolute', 'exact')
            except Exception:
                pass
        if hasattr(self, 'subtrack'):
            self.subtrack.update()

    def _add_clip_at(self, ms):
        """片段轨右键空白 → 以点击点为中心、按当前缩放定宽添加一个片段。"""
        w = self.waveform
        dur = w.duration_ms or (ms + 1500)
        width_px = max(1, self.subtrack.width())
        if w.duration_ms and w.zoom:
            half = max(150, min(int(40 * w.duration_ms / (w.zoom * width_px)), 5000))
        else:
            half = 750
        start = max(0, int(ms - half)); end = min(int(dur), int(ms + half))
        if end <= start:
            end = start + 300
        self.clips.append({'start': start, 'end': end})
        self._refill_clip_table(); self.update_buttons()
        self._select_clip_row(len(self.clips) - 1, seek=False)
        self._status(f"已添加片段 {core.ms_to_ass(start)} → {core.ms_to_ass(end)}")

    def _delete_clip(self, i):
        """片段轨右键 → 删除第 i 个片段。"""
        if not (0 <= i < len(self.clips)):
            return
        self.clips.pop(i)
        self.clip_table.mark_row = -1; self.clip_table.mark_rows = set(); self._clip_anchor = -1
        self._refill_clip_table(); self.update_buttons()
        if hasattr(self, 'subtrack'):
            self.subtrack.update()
        self._status("已删除片段")

    def _clip_marked_rows(self):
        """当前标记的有效片段行下标（升序）。"""
        return sorted(r for r in self.clip_table._marked() if 0 <= r < len(self.clips))

    def _clip_delete_selected(self):
        rows = self._clip_marked_rows()
        if not rows:
            return
        for r in reversed(rows):
            self.clips.pop(r)
        self.clip_table.mark_row = -1; self.clip_table.mark_rows = set(); self._clip_anchor = -1
        self._refill_clip_table()
        self.update_buttons()
        self._status(f"已删除 {len(rows)} 个片段")

    def _clip_menu(self, gpos):
        """片段列表右键菜单：删除片段 / 导出所选片段。"""
        rows = self._clip_marked_rows()
        menu = QMenu(self)
        act_del = menu.addAction("删除片段" if len(rows) <= 1 else f"删除选中 {len(rows)} 个片段")
        act_exp = menu.addAction("导出所选片段" if len(rows) <= 1 else f"导出所选 {len(rows)} 个片段")
        act_del.setEnabled(bool(rows))
        act_exp.setEnabled(bool(rows) and bool(self.cur_video) and not self.task['running'])
        chosen = menu.exec(gpos)
        if chosen is act_del:
            self._clip_delete_selected()
        elif chosen is act_exp:
            self.export_clips(only=rows)

    def export_clips(self, only=None):
        """导出片段。only=行下标列表则只导出所选片段；否则导出全部。"""
        if not self.cur_video or self.task['running']:
            return
        if only:
            clips = [dict(self.clips[i]) for i in only if 0 <= i < len(self.clips)]
        else:
            clips = [dict(c) for c in self.clips]
        if not clips:
            return
        for c in clips:
            if c['end'] <= c['start']:
                self._status("存在无效片段（出点≤入点），请修正"); return
        video = self.cur_video
        concat = self.clip_concat
        folder = os.path.dirname(video)
        ext = os.path.splitext(video)[1] or '.mp4'
        stem = os.path.splitext(os.path.basename(video))[0]
        if concat:
            res = self._ask_name("导出片段（拼成一个）", f"{stem}_剪辑", ext)
            if not res:
                return
            base, ow, _, folder = res
            out_one = os.path.join(folder, base + ext)
            if not ow:
                out_one = core.unique_path(out_one)
        else:
            res = self._ask_name("导出片段（各导出一个）", f"{stem}_片段", f"N{ext}",
                                 note=f"每个片段将命名为 名1{ext}、名2{ext} …")
            if not res:
                return
            base, ow, _, folder = res
        self._begin_task('clip')
        self._status("开始导出片段…")

        def work():
            temps = []
            try:
                n = len(clips)
                if concat:
                    for i, c in enumerate(clips):
                        tmp = os.path.join(core.temp_dir(), f'dmr_clip_{i}{ext}')
                        core.cut_clip(video, c['start'], c['end'], tmp,
                                      lambda p, i=i: self.task.__setitem__('percent', int((i + p / 100) / n * 90)),
                                      on_proc=self._set_proc)
                        temps.append(tmp)
                    self.task['percent'] = 92
                    core.concat_clips(temps, out_one)
                    self.task['percent'] = 100
                    self.task['output'] = out_one
                else:
                    outs = []
                    for i, c in enumerate(clips):
                        out = os.path.join(folder, f"{base}{i + 1}{ext}")
                        if not ow:
                            out = core.unique_path(out)
                        core.cut_clip(video, c['start'], c['end'], out,
                                      lambda p, i=i: self.task.__setitem__('percent', int((i + p / 100) / n * 100)),
                                      on_proc=self._set_proc)
                        outs.append(out)
                    self.task['output'] = outs
                self.task['done'] = True
            except Exception as e:
                if self._cancel:
                    self.task['cancelled'] = True
                else:
                    self.task['error'] = str(e)
            finally:
                for t in temps:
                    core.delete_file(t)
                self._proc = None
                self.task['running'] = False
        threading.Thread(target=work, daemon=True).start()
        self.update_buttons()

    def export_clips_dialog(self):
        """「导出片段」：弹出树形对话框，跨多个视频勾选片段后导出（命名自动避免覆盖）。"""
        if self.task['running']:
            return
        if not self.videos:
            self._status("请先导入视频"); return
        if not any(v['clips'] for v in self.videos):
            self._status("还没有任何片段，先在波形上截取或设入/出点添加"); return
        dlg = ClipExportDialog(self.videos, self.clip_concat, self)
        if dlg.exec() != QDialog.Accepted:
            return
        sel = dlg.selection()
        if not sel:
            self._status("未勾选任何片段"); return
        concat = dlg.concat()
        jobs = []   # [(video_path, [clip dict,...])]
        for vi, cis in sel.items():
            v = self.videos[vi]
            cs = [dict(v['clips'][ci]) for ci in cis if 0 <= ci < len(v['clips'])]
            for c in cs:
                if c['end'] <= c['start']:
                    self._status(f"「{os.path.basename(v['path'])}」存在无效片段（出点≤入点）"); return
            if cs:
                jobs.append((v['path'], cs))
        if not jobs:
            return
        self._begin_task('clip')
        self._status("开始导出片段…")

        def work():
            temps = []
            try:
                outputs = []
                total = sum(len(cs) for _, cs in jobs); done = 0
                for video, cs in jobs:
                    folder = os.path.dirname(video)
                    ext = os.path.splitext(video)[1] or '.mp4'
                    stem = os.path.splitext(os.path.basename(video))[0]
                    if concat:
                        parts = []
                        for c in cs:
                            tmp = os.path.join(core.temp_dir(), f'dmr_clip_{len(temps)}{ext}')
                            temps.append(tmp)
                            core.cut_clip(video, c['start'], c['end'], tmp,
                                          lambda p, d=done, t=total: self.task.__setitem__('percent', int((d + p / 100) / t * 100)),
                                          on_proc=self._set_proc)
                            parts.append(tmp); done += 1
                        out_one = core.unique_path(os.path.join(folder, f"{stem}_剪辑{ext}"))
                        core.concat_clips(parts, out_one)
                        outputs.append(out_one)
                    else:
                        for i, c in enumerate(cs):
                            out = core.unique_path(os.path.join(folder, f"{stem}_片段{i + 1}{ext}"))
                            core.cut_clip(video, c['start'], c['end'], out,
                                          lambda p, d=done, t=total: self.task.__setitem__('percent', int((d + p / 100) / t * 100)),
                                          on_proc=self._set_proc)
                            outputs.append(out); done += 1
                self.task['output'] = outputs
                self.task['done'] = True
            except Exception as e:
                if self._cancel:
                    self.task['cancelled'] = True
                else:
                    self.task['error'] = str(e)
            finally:
                for t in temps:
                    core.delete_file(t)
                self._proc = None
                self.task['running'] = False
        threading.Thread(target=work, daemon=True).start()
        self.update_buttons()

    def merge_videos(self):
        """视频合并：把列表里的多个视频按当前顺序首尾相接合并为一个。
        以子进程方式调用 run_merge.py（内部走 DMR.utils.video_merge：分辨率/时间基归一 + 按需 NVENC 重编码）。"""
        if self.task['running']:
            return
        paths = self.merge_list.paths()
        if len(paths) < 2:
            self._status("请至少添加 2 个视频再合并"); return
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            self._status(f"文件不存在：{os.path.basename(missing[0])}"); return

        folder = os.path.dirname(paths[0])
        default_out = os.path.join(folder, "合并视频.mp4")
        out, _ = QFileDialog.getSaveFileName(self, "保存合并结果", default_out,
                                             "视频 (*.mp4);;所有文件 (*.*)")
        if not out:
            return
        if not os.path.splitext(out)[1]:
            out += '.mp4'

        replace_mode = self.cb_merge_replace.isChecked()
        if replace_mode:
            first = paths[0]
            if QMessageBox.question(
                    self, "合并后替换源文件",
                    f"合并成功后将：\n"
                    f"• 把这 {len(paths)} 个源文件移入回收站\n"
                    f"• 把合并视频改名为首个视频：{os.path.basename(first)}\n\n确定继续？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return

        hw = self.cb_merge_hw.isChecked()
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_merge.py')
        task_json = os.path.join(core.temp_dir(), f'dmr_merge_{int(time.time())}.json')
        with open(task_json, 'w', encoding='utf-8') as f:
            json.dump({'inputs': paths, 'output': out, 'hw_encode': hw}, f, ensure_ascii=False)

        self._begin_task('merge')
        self._status(f"开始合并 {len(paths)} 个视频…")

        def work():
            try:
                p = subprocess.Popen([sys.executable, '-u', script, task_json],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='ignore')
                self._set_proc(p)
                tail = ''
                for line in p.stdout:                  # 透传/收集子进程输出，末段用于报错
                    tail = (tail + line)[-4000:]
                p.wait()
                if self._cancel:
                    self.task['cancelled'] = True
                elif p.returncode == 0 and os.path.exists(out):
                    final_out, ok = out, True
                    if replace_mode:
                        try:
                            final_out = self._merge_replace_sources(out, paths)
                        except Exception as e:
                            ok = False
                            self.task['error'] = f"合并成功，但替换源文件失败：{e}（合并文件已保留：{out}）"
                    if ok:
                        self.task['output'] = final_out
                        self.task['done'] = True
                else:
                    last = [l for l in tail.strip().splitlines() if l.strip()]
                    self.task['error'] = last[-1] if last else f"合并失败（退出码 {p.returncode}）"
            except Exception as e:
                if self._cancel:
                    self.task['cancelled'] = True
                else:
                    self.task['error'] = str(e)
            finally:
                try:
                    os.remove(task_json)
                except OSError:
                    pass
                self._proc = None
                self.task['running'] = False
        threading.Thread(target=work, daemon=True).start()
        self.update_buttons()

    def _merge_replace_sources(self, merged, paths):
        """合并成功后的善后：把所有源文件移入回收站，再把合并结果改名为【首个视频】的完整文件名。
        返回最终文件路径。在子线程中调用，只做文件操作、不碰 UI。"""
        import shutil
        from send2trash import send2trash
        first = os.path.abspath(paths[0])
        merged = os.path.abspath(merged)
        # 1) 源文件移入回收站（跳过恰好与合并结果同路径的，避免误删成品）
        for p in paths:
            ap = os.path.abspath(p)
            if ap == merged:
                continue
            if os.path.exists(ap):
                send2trash(ap)
        # 2) 合并结果改名为首个视频的完整文件名（含其原扩展名，"一模一样"）
        if merged != first:
            if os.path.exists(first):
                send2trash(first)   # 兜底：同名仍占用时再清一次
            shutil.move(merged, first)
        return first

    def _bar_label(self, text, area=False, tip=None):
        """各工具行/功能区最左的统一栏目名（加粗、主题色）。area=True 用更浅一级（V/A/W 区标签）。
        tip：鼠标悬停提示。"""
        lb = QLabel(text)
        if tip:
            lb.setToolTip(tip)
        (self._area_labels if area else self._theme_labels).append(lb)
        self._style_bar_label(lb, area)
        return lb

    def _style_bar_label(self, lb, area=False):
        col = SETTINGS.get('color_label', '#e8ba98')
        if area:
            col = _lighten(col, 0.35)   # 区标签（V/A/W）比栏目名更浅一级；字号与标题栏标签一致
        lb.setStyleSheet(f"color:{_rgba(col)};font-weight:bold;padding:0 8px 0 2px;")

    def _paint_btn_color(self, btn, color):
        """给按钮上指定底色（悬停自动加深）。统一圆角矩形；文字色按底色明暗自动黑/白。"""
        r = _radius_for(6)
        btn.setStyleSheet(
            f"QPushButton{{background:{_rgba(color)};color:{_fg(color)};padding:6px 14px;border:none;border-radius:{r}px;}}"
            f"QPushButton:hover{{background:{_rgba(_darken(color))};}}")

    def _apply_widget_colors(self):
        """应用可设置的控件颜色（界面统一背景由 build_qss 处理，这里管按钮/标签/历史/标记色）。"""
        r = _radius_for(6)        # 按钮圆角（按高度夹住，避免变直角）
        r_tab = _radius_for(5)    # 标签更矮，单独算
        r_chip = _radius_for(2)   # 状态提示块更矮
        # Tab 栏：选中标签底色（视频/ASS 两条标签栏统一），默认浅橙
        hb = SETTINGS['color_header']; c = QColor(hb)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        tfg = '#000000' if (lum > 140 and c.alpha() > 100) else '#ffffff'
        tab_qss = ("QTabBar::tab{background:#2a2a2a;color:#bbb;padding:5px 12px;margin:2px;"
                   f"border-radius:{r_tab}px;}}"   # 全圆角矩形（四角），与按钮统一
                   f"QTabBar::tab:selected{{background:{_rgba(hb)};color:{tfg};}}")
        self.tabbar.setStyleSheet(tab_qss)
        self.vtabbar.setStyleSheet(tab_qss)
        # 菜单栏按钮（导入视频 / 导入 ASS）→ color_button
        for b in self._import_btns:
            self._paint_btn_color(b, SETTINGS['color_button'])
        # 批量操作按钮（换行 / 聚焦）→ color_batch_btn
        for b in self._batch_btns:
            self._paint_btn_color(b, SETTINGS['color_batch_btn'])
        # 开关按钮：选中=最深的 color_toggle，未选中=灰；圆角矩形
        bc = SETTINGS.get('color_toggle', SETTINGS['color_batch_btn'])
        toggle_qss = (
            f"QPushButton{{background:#4a4a4a;color:#fff;padding:6px 14px;border:none;border-radius:{r}px;}}"
            "QPushButton:hover{background:#5a5a5a;}"
            f"QPushButton:checked{{background:{_rgba(bc)};color:{_fg(bc)};}}"
            f"QPushButton:checked:hover{{background:{_rgba(_darken(bc))};}}")
        self.btn_follow.setStyleSheet(toggle_qss)
        self.btn_sel_follow.setStyleSheet(toggle_qss)   # 「选随」同款开关样式
        self.btn_capture.setStyleSheet(toggle_qss)      # 「截取」同款开关样式
        self.btn_wave_center.setStyleSheet(toggle_qss)  # 「波形居中」同款开关样式
        self.btn_subtrack.setStyleSheet(toggle_qss)     # 「字幕轨」同款开关样式
        self.btn_wave_enable.setStyleSheet(toggle_qss)  # 「波形」启用开关同款样式
        self.btn_wave_enable_clip.setStyleSheet(toggle_qss)   # 剪辑面板的「波形」开关同款
        self.btn_lock.update()    # 锁按钮自绘，底色跟随 color_batch_btn
        self.btn_cross.update()   # 十字开关自绘，底色跟随 color_batch_btn
        # 模式切换（checkable 分段按钮）：选中=底色，未选=灰
        def _seg(btn, base):
            btn.setStyleSheet(
                f"QPushButton{{background:#4a4a4a;color:#fff;padding:6px 14px;border:none;border-radius:{r}px;}}"
                "QPushButton:hover{background:#5a5a5a;}"
                f"QPushButton:checked{{background:{_rgba(base)};color:{_fg(base)};}}"
                f"QPushButton:checked:hover{{background:{_rgba(_darken(base))};}}")
        for b in (self.btn_mode_sub, self.btn_mode_clip, self.btn_mode_merge):
            _seg(b, SETTINGS.get('color_mode_btn', '#b17244'))
        # 剪辑动作按钮（设入/出点、添加片段）→ color_batch_btn
        for b in (self.btn_clip_in, self.btn_clip_out, self.btn_clip_add):
            self._paint_btn_color(b, SETTINGS['color_batch_btn'])
        for lb in getattr(self, '_theme_labels', []):   # 栏目名随主色
            self._style_bar_label(lb, area=False)
        for lb in getattr(self, '_area_labels', []):     # 区标签（更浅一级）随主色
            self._style_bar_label(lb, area=True)
        # 历史按钮（右上角提示文本=点开操作历史）：底色 color_history_btn(默认纯黑) + 文字 color_status
        self.lbl_status.setStyleSheet(
            f"background:{_rgba(SETTINGS['color_history_btn'])};color:{_rgba(SETTINGS['color_status'])};"
            f"padding:2px 8px;border-radius:{r_chip}px;")
        self.btn_stop.setStyleSheet(   # 停止键（红）：圆角跟随设置
            f"QPushButton{{background:#b03030;color:#fff;padding:6px 14px;border:none;border-radius:{r}px;}}"
            "QPushButton:hover{background:#c84040;}")
        self.btn_settings.update()   # 自绘齿轮，重绘即用最新 color_button
        self._apply_table_hl_color()   # 跟随条填充 / 标记行边框色跟随 color_follow

    # ── 文档/标签管理 ────────────────────────────────────────────────────
    def cur_doc(self):
        return self.docs[self.cur] if 0 <= self.cur < len(self.docs) else None

    def _add_doc(self, path, kind='subtitle', select=True):
        """载入一个 ass 为新文档（已载入同路径则切到它），建标签页。"""
        for i, d in enumerate(self.docs):
            if os.path.normpath(d.path) == os.path.normpath(path):
                if select:
                    self.tabbar.setCurrentIndex(i)
                return self.docs[i]
        doc = AssDoc(path, kind)
        self.docs.append(doc)
        self.tabbar.blockSignals(True)
        idx = self.tabbar.addTab(doc.name)
        self.tabbar.setTabToolTip(idx, doc.path)
        btn = TabCloseButton(); btn.setToolTip("关闭该 ASS")   # 自绘 × 替代 Qt 自带关闭叉
        # 延迟执行：避免在按钮自身的 clicked 里删掉它所在的标签（会卡一下/光标变忙）
        btn.clicked.connect(lambda _=False, b=btn: QTimer.singleShot(0, lambda: self._close_tab_button(b)))
        self.tabbar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, btn)
        if select:
            self.tabbar.setCurrentIndex(idx)
        self.tabbar.blockSignals(False)
        self._refresh_tab_tooltips()
        if select:
            self._on_tab_changed(idx)   # 手动刷新（首个标签时 currentChanged 不会触发）
        return doc

    def _close_tab_button(self, btn):
        """自绘标签 × 被点：按按钮反查它所在的标签下标再关闭（标签删除会移位，故每次现查）。"""
        for i in range(self.tabbar.count()):
            if self.tabbar.tabButton(i, QTabBar.ButtonPosition.RightSide) is btn:
                self._on_tab_close(i)
                return

    # ── 字幕图层顺序（多 ass 叠加时谁在上层）：拖动标签即可调整 ────────────
    def _on_tab_moved(self, frm, to):
        """用户拖动字幕标签后：把 self.docs 同步成新顺序（最左=最上层），刷新预览叠加。"""
        if frm == to:
            return
        if 0 <= frm < len(self.docs) and 0 <= to < len(self.docs):
            self.docs.insert(to, self.docs.pop(frm))   # docs 顺序跟随标签顺序
        self.cur = self.tabbar.currentIndex()          # 被拖的标签仍为当前页
        self._refresh_tab_tooltips()
        self._refresh_sub()                            # 按新图层顺序重叠预览
        d = self.cur_doc()
        if d:
            self._status(f"已调整图层：「{d.name}」→ 第 {to + 1}/{len(self.docs)} 层（越靠左越在上层）")

    def _refresh_tab_tooltips(self):
        """标签悬停提示里标明当前图层位置（越靠左越在上层）。"""
        n = self.tabbar.count()
        for i in range(min(n, len(self.docs))):
            self.tabbar.setTabToolTip(
                i, f"{self.docs[i].path}\n图层 {i + 1}/{n}（越靠左越在上层；拖动标签可调整）")

    def _on_tab_changed(self, idx):
        self.cur = idx
        d = self.cur_doc()
        self.tabbar.setVisible(bool(self.docs))  # 无 ASS 时整条标签栏隐藏，避免删完残留底色
        self.table.setVisible(bool(self.docs))   # 没有 ASS 时不显示表头(# 开始 结束 样式 文本)
        self.sub_toolbar.setVisible(bool(self.docs))   # 无 ASS 时隐藏字幕区工具条（聚焦/选随/字幕居中）
        self._apply_bars_visible()                     # 工具栏/批量栏 随有无 ASS 显隐
        self.table.mark_row = -1; self.table.mark_rows = set(); self._anchor_row = -1   # 换标签清除行标记
        self.sp_offset.blockSignals(True)
        self.sp_offset.setValue(d.applied_offset if d else 0)
        self.sp_offset.blockSignals(False)
        self._reload_style_dropdown()
        self._refill_table()
        self.update_buttons()
        self._refresh_sub()

    def _on_tab_close(self, idx):
        if 0 <= idx < len(self.docs):
            self.docs.pop(idx)
            self.tabbar.blockSignals(True)
            self.tabbar.removeTab(idx)
            self.tabbar.blockSignals(False)
            self.cur = self.tabbar.currentIndex()
            self._on_tab_changed(self.cur)
            self._refresh_tab_tooltips()
            self._status("已关闭该 ASS 标签")

    def close_current_doc(self):
        if self.docs:
            self._on_tab_close(self.tabbar.currentIndex())

    def _rebuild_video_tabs(self):
        """左侧视频标签栏：每个已导入视频一个标签（文件名+悬停路径+× 关闭），当前=激活视频。"""
        self.vtabbar.blockSignals(True)
        while self.vtabbar.count():
            self.vtabbar.removeTab(0)
        for v in self.videos:
            idx = self.vtabbar.addTab(os.path.basename(v['path']))
            self.vtabbar.setTabToolTip(idx, v['path'])
            btn = TabCloseButton(); btn.setToolTip("关闭视频")
            # 延迟执行：避免在按钮自身的 clicked 里把按钮所在标签删掉（会卡一下/光标变忙）
            btn.clicked.connect(lambda _=False, p=v['path']: QTimer.singleShot(0, lambda: self._close_video_by_path(p)))
            self.vtabbar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, btn)
        if 0 <= self._active_vid < len(self.videos):
            self.vtabbar.setCurrentIndex(self._active_vid)
        self.vtabbar.blockSignals(False)
        self.vtabbar.setVisible(bool(self.videos))   # 空时整条隐藏

    def _init_mpv(self):
        self.player = None
        if not _MPV_OK:
            self._status(f"未加载 libmpv，预览不可用：{_MPV_ERR}")
            return
        try:
            self.player = mpv.MPV(
                wid=str(int(self.video_frame.winId())),
                vo='gpu', hwdec='auto', osc=False, keep_open='yes',
                sub_auto='no',   # 不自动加载同名字幕，字幕轨完全由我们管理
                input_default_bindings=True, input_vo_keyboard=True,
            )
            @self.player.event_callback('file-loaded')
            def _on_loaded(event):
                self._sub_loaded = False
                self._loaded_sub_path = None
                self._refresh_sub()
            # 点击/键盘交互统一在 Qt 层处理（mpv 嵌入模式下收不到输入）
        except Exception as e:
            self.player = None
            self._status(f"libmpv 初始化失败：{e}")

    # ── 字体 / 颜色 ──────────────────────────────────────────────────────
    def _load_fonts(self):
        fonts = core.installed_fonts() or []
        pinned = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'FangSong']
        ordered = [f for f in pinned if f in fonts] + [f for f in fonts if f not in pinned]
        self.cmb_font.blockSignals(True)
        self.cmb_font.clear(); self.cmb_font.addItems(ordered or pinned)
        self.cmb_font.blockSignals(False)

    def _pick_color(self, key, btn):
        d = self.cur_doc()
        if not d or not d.cur_style:
            return
        vals = d.styles.get(d.cur_style, {})
        cur = QColor(vals.get(key, '#FFFFFF'))
        c = QColorDialog.getColor(cur, self, "选择颜色")
        if c.isValid():
            self._push_undo("修改字色" if key == 'font_color' else "修改描边色")
            self._paint_color_btn(btn, c.name().upper())
            self._suspend_undo = True       # 内部 _apply 不再重复记录
            try:
                self._apply_style_from_controls(extra={key: c.name().upper()})
            finally:
                self._suspend_undo = False

    def _paint_color_btn(self, btn, hexv):
        # 小方块色块：只显示颜色，无文字。用 QPushButton 选择器限定，避免样式级联到其 tooltip（否则提示会带底色）
        btn.setStyleSheet(f"QPushButton{{background:{hexv};border:1px solid #777;border-radius:3px;}}")

    # ── 样式下拉 / 控件 <-> 当前文档的当前 Style ──────────────────────────
    def _reload_style_dropdown(self):
        """按当前文档重填样式下拉，并刷新各控件值与置灰。"""
        d = self.cur_doc()
        self.cmb_style.blockSignals(True)
        self.cmb_style.clear()
        if d:
            self.cmb_style.addItems(d.style_names())
            if d.cur_style in d.style_names():
                self.cmb_style.setCurrentText(d.cur_style)
        self.cmb_style.blockSignals(False)
        self._fill_style_controls()

    def _on_style_selected(self, _idx=None):
        d = self.cur_doc()
        if not d:
            return
        name = self.cmb_style.currentText()
        if name in d.styles:
            d.cur_style = name
            self._fill_style_controls()

    def _fill_style_controls(self):
        """把当前文档当前 Style 的值填进控件，并按 inline 覆盖分析置灰/提示。"""
        self._populating = True
        d = self.cur_doc()
        s = (d.styles.get(d.cur_style) if d else None) or dict(core.DEFAULT_STYLE)
        i = self.cmb_font.findText(s['font_name'])
        if i < 0:
            self.cmb_font.addItem(s['font_name']); i = self.cmb_font.findText(s['font_name'])
        self.cmb_font.setCurrentIndex(i)
        self.sp_size.setValue(int(s['font_size']))
        self.sp_margin.setValue(round(s['margin_bottom'] * 100))   # 比例(0~1) -> 显示百分比
        self.sp_outline.setValue(float(s['outline']))
        self._paint_color_btn(self.btn_color, s['font_color'])
        self._paint_color_btn(self.btn_ocolor, s['outline_color'])
        self._populating = False
        self._apply_overrides_greyout()

    def _apply_overrides_greyout(self):
        """数据驱动：扫描当前 Style 名下所有 Dialogue 的 inline 标签，按结果置灰/提示对应控件。
        locked=全行覆盖→置灰；partial=部分覆盖→可编辑+警告；free=可编辑。"""
        d = self.cur_doc()
        if not d or not d.cur_style:
            for w in self._style_ctrls.values():
                w.setEnabled(True); w.setToolTip("")
            return
        texts = d.texts_of_style(d.cur_style)
        st = core.analyze_style_overrides(texts)
        total = len(texts)
        for param, w in self._style_ctrls.items():
            status = st.get(param, 'free')
            if status == 'locked':
                w.setEnabled(False)
                w.setToolTip(f"该样式全部 {total} 行都用 inline 标签覆盖了此项，改 Style 无效"
                             "（如需改外观请改对应转换器后重新转换）")
            elif status == 'partial':
                hit = sum(1 for t in texts if core._OVERRIDE_PATTERNS[param].search(t or ''))
                w.setEnabled(True)
                w.setToolTip(f"{total} 行中 {hit} 行被 inline 覆盖，改这里只影响其余 {total - hit} 行")
            else:
                w.setEnabled(True); w.setToolTip("")

    def _apply_style_from_controls(self, extra=None):
        """读控件 -> 组装 Style 值 -> 写进当前文档并就地 patch 文件 + 刷新预览。"""
        d = self.cur_doc()
        if not d or not d.cur_style:
            return
        vals = dict(d.styles.get(d.cur_style) or core.DEFAULT_STYLE)
        vals['font_name'] = self.cmb_font.currentText()
        vals['font_size'] = self.sp_size.value()
        vals['margin_bottom'] = self.sp_margin.value() / 100   # 显示百分比 -> 比例(0~1)
        vals['outline'] = self.sp_outline.value()
        if extra:
            vals.update(extra)
        d.patch_style(d.cur_style, vals)
        SETTINGS['style'] = dict(vals)   # 记住为偏好，ASR/新建沿用
        save_settings()
        self._schedule_save()            # 防抖刷新预览

    def _style_changed(self, key=None, *a):
        if self._populating:
            return
        self._push_undo("修改样式", merge_key=f"style:{key}")
        self._apply_style_from_controls()

    # ── 列表（统一表：# / 开始 / 结束 / 样式 / 文本）────────────────────────
    def _refill_table(self):
        self._populating = True
        self._play_row = -1   # 重建表后旧高亮行作废，下一次跟随重新画
        deleg = self.table.itemDelegate()
        if isinstance(deleg, _CellEditDelegate):
            deleg.play_rows = set()
        d = self.cur_doc()
        dia = d.dialogues if d else []
        self.table.setRowCount(0)
        self.table.setRowCount(len(dia))
        for i, e in enumerate(dia):
            self._set_cell(i, 0, str(i + 1), editable=False)
            self._set_cell(i, 1, core.ms_to_ass(e['start']))
            self._set_cell(i, 2, core.ms_to_ass(e['end']))
            self._set_cell(i, 3, e['style'])
            self._set_cell(i, 4, e['text'])   # 原文含 {\\...} 标签（选项 B）
        self._populating = False
        self.table.resizeColumnToContents(4)   # 文本列按真实宽度，超出栏宽出现横滚条
        if hasattr(self, 'subtrack'):
            self.subtrack.update()             # 字幕数据变化 → 同步重绘字幕轨道

    def _save_doc(self):
        """把当前文档的 Dialogue 整体写回其文件，并防抖刷新预览。"""
        d = self.cur_doc()
        if d:
            d.save_dialogues()
            self._schedule_save()

    def _set_cell(self, r, c, text, editable=True):
        it = QTableWidgetItem(text)
        if not editable:
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, c, it)

    def _item_changed(self, item):
        if self._populating:
            return
        d = self.cur_doc()
        if not d:
            return
        r, c = item.row(), item.column()
        if r >= len(d.dialogues):
            return
        e = d.dialogues[r]
        if c == 4:        # 文本（原文，含标签）
            self._push_undo(f"修改第{r+1}行文本", merge_key=f"text:{r}")
            e['text'] = item.text()
            self._save_doc()
        elif c == 3:      # 样式名
            self._push_undo(f"修改第{r+1}行样式", merge_key=f"stylename:{r}")
            e['style'] = item.text().strip()
            self._save_doc()
            self._apply_overrides_greyout()   # 换样式可能改变占灰
        elif c in (1, 2):
            try:
                ms = core.ass_to_ms(item.text())
            except Exception:
                ms = None
            if ms is None:
                item.setBackground(QColor('#5a2030'))
                return
            item.setBackground(QColor('#1a1a1a'))
            self._push_undo(f"修改第{r+1}行时间", merge_key=f"time:{r}:{c}")
            e['start' if c == 1 else 'end'] = ms
            self._save_doc()

    def _delete_row(self):
        d = self.cur_doc()
        if not d:
            return
        r = self.table.mark_row
        if 0 <= r < len(d.dialogues):
            self._push_undo(f"删除第{r+1}行")
            del d.dialogues[r]
            self.table.mark_row = -1; self.table.mark_rows = set()   # 行已删，清除标记
            self._refill_table()
            self.update_buttons()
            self._save_doc()

    def _table_menu(self, gpos):
        """表右键菜单：点在多选区内→批量菜单；否则单行菜单（插入/删除/编辑样式）。"""
        d = self.cur_doc()
        if not d:
            return
        vp_pos = self.table.viewport().mapFromGlobal(gpos)
        row = self.table.indexAt(vp_pos).row()
        if not (0 <= row < len(d.dialogues)):
            return                      # 没点在某一行上：不弹菜单
        if row in self.table.mark_rows and len(self.table.mark_rows) > 1:
            self._batch_menu(gpos, sorted(self.table.mark_rows))   # 在多选区内右键 → 批量
            return
        # 单行：右键即选中该行（清掉多选）
        self.table.mark_row = row; self.table.mark_rows = {row}; self._anchor_row = row
        self.table.viewport().update()
        menu = QMenu(self)
        act_ins_above = menu.addAction("在上方插入一行")
        act_ins_below = menu.addAction("在下方插入一行")
        act_del = menu.addAction("删除该行")
        act_style = menu.addAction("编辑弹幕样式…")
        menu.addSeparator()
        wave_ok = bool(self.cur_video) and bool(self.waveform.peaks)
        act_retime = menu.addAction("波形定时 R（拖=范围 · 点=开始 · Ctrl点=结束）"); act_retime.setEnabled(wave_ok)
        chosen = menu.exec(gpos)
        if chosen is act_del:
            self._delete_row()
        elif chosen is act_ins_above:
            self._insert_row(row)
        elif chosen is act_ins_below:
            self._insert_row(row + 1)        # 在下一行之上插入 = 本条之下
        elif chosen is act_style:
            self._edit_danmaku_style(row)
        elif chosen is act_retime:
            self._arm_rset()

    # ── 波形交互：截取 / 重定时 / 点选设时间 ──────────────────────────────
    def _wave_ready_for(self, need_row):
        """检查波形交互前置条件：字幕模式 + 有 ASS + 有波形；need_row 时还需选中一行。"""
        if self.mode != 'subtitle' or not self.cur_doc():
            self._status("需要在字幕编辑模式且已有一个 ASS"); return False
        if not self.cur_video or not self.waveform.peaks:
            self._status("需要已导入视频并生成波形"); return False
        if need_row and not (0 <= self.table.mark_row < len(self.cur_doc().dialogues)):
            self._status("请先选中一条字幕"); return False
        return True

    def _set_wave_mode(self, mode):
        """统一进入/退出波形交互模式：设波形模式 + 整窗蓝色"已开启"光标（None=复原）。"""
        self.waveform.set_mode(mode)
        cw = self.centralWidget()
        if cw is not None:
            cw.setCursor(_blue_arm_cursor()) if mode else cw.unsetCursor()

    def _exit_wave_modes(self):
        """退出所有波形交互模式（恢复 seek，光标复原，开关复位）。"""
        self._set_wave_mode(None)
        if self.btn_capture.isChecked():
            self.btn_capture.blockSignals(True); self.btn_capture.setChecked(False); self.btn_capture.blockSignals(False)

    def _toggle_capture(self, on):
        if on:
            if self.mode == 'merge':
                self._status("合并模式不支持截取"); self.btn_capture.setChecked(False); return
            if not self.cur_video or not self.waveform.peaks:
                self._status("截取需要已导入视频并开启波形"); self.btn_capture.setChecked(False); return
            if self.mode == 'subtitle' and not self.cur_doc():
                self._status("字幕模式截取需要先有一个 ASS"); self.btn_capture.setChecked(False); return
            self._set_wave_mode('capture')
            if self.mode == 'clip':
                self._status("截取：在波形上拖选一段添加为片段（拖一次后自动退出，再按或 Esc 退出）")
            else:
                self._status("截取：在波形上拖选一段插入字幕行（拖一次后自动退出，再按或 Esc 退出）")
        elif self.waveform.imode == 'capture':
            self._set_wave_mode(None)

    def _arm_capture_from_track(self):
        """字幕轨右键「截取」：进入截取模式（等同操作栏的截取按钮）。"""
        if not self.btn_capture.isChecked():
            self.btn_capture.setChecked(True)      # 触发 _toggle_capture(True) 进入截取
        else:
            self._set_wave_mode('capture')

    def _arm_rset(self):
        """R 三合一：进入波形交互模式——拖动=设范围（重定时），单击=设开始，Ctrl+单击=设结束。"""
        if self.btn_capture.isChecked():       # 退出截取
            self.btn_capture.blockSignals(True); self.btn_capture.setChecked(False); self.btn_capture.blockSignals(False)
        if not self._wave_ready_for(need_row=True):
            return
        self._set_wave_mode('rset')
        self._status("R：拖动=设范围（重定时） · 单击=设开始 · Ctrl+单击=设结束（鼠标旁有提示；再按 R 或 Esc 退出）")

    def _on_rset_click(self, frac, is_end):
        """R 模式单击完成：无 Ctrl=设开始，按 Ctrl=设结束；之后自动退出。"""
        dur = self._player_duration()
        if dur:
            ms = int(frac * dur * 1000)
            self._set_row_time(self.table.mark_row, 'end' if is_end else 'start', ms)
        self._exit_wave_modes()

    def _arm_wave_pick(self, which):
        """[ / ]：进入波形点选模式（蓝色竖线光标），单击波形设选中行的开始/结束。"""
        if self.btn_capture.isChecked():       # 退出截取
            self.btn_capture.blockSignals(True); self.btn_capture.setChecked(False); self.btn_capture.blockSignals(False)
        if not self._wave_ready_for(need_row=True):
            return
        self._set_wave_mode('pick_start' if which == 'start' else 'pick_end')
        self._status(f"在波形上单击设{'开始' if which == 'start' else '结束'}时间（再按或 Esc 退出）")

    def _on_wave_range(self, a, b):
        """波形拖选完成：截取（字幕模式→插入字幕行 / 剪辑模式→添加片段）或重定时；之后自动退出。"""
        dur = self._player_duration()
        if dur:
            s, e = int(a * dur * 1000), int(b * dur * 1000)
            if self.waveform.imode == 'capture':
                self._clip_add_range(s, e) if self.mode == 'clip' else self._insert_dialogue_at(s, e)
            elif self.waveform.imode in ('retime', 'rset'):   # rset 拖动 = 设范围（重定时）
                self._retime_selected(s, e)
        self._exit_wave_modes()

    def _on_wave_point(self, frac):
        """波形点选完成：设选中行的开始/结束；之后自动退出。"""
        dur = self._player_duration()
        if dur:
            ms = int(frac * dur * 1000)
            if self.waveform.imode == 'pick_start':
                self._set_row_time(self.table.mark_row, 'start', ms)
            elif self.waveform.imode == 'pick_end':
                self._set_row_time(self.table.mark_row, 'end', ms)
        self._exit_wave_modes()

    def _retime_selected(self, start_ms, end_ms):
        """把选中行的开始/结束重设为给定 ms（保持选中）。"""
        d = self.cur_doc(); r = self.table.mark_row
        if not d or not (0 <= r < len(d.dialogues)):
            self._status("请先选中一条字幕再重定时"); return
        ev = d.dialogues[r]
        self._push_undo(f"重设第{r+1}行时间")
        ev['start'], ev['end'] = int(start_ms), int(end_ms)
        self._refill_table()
        self.table.mark_row = r; self.table.mark_rows = {r}; self._anchor_row = r
        self.table.viewport().update()
        self._save_doc()
        self._status(f"已重设第{r+1}行：{core.ms_to_ass(start_ms)} → {core.ms_to_ass(end_ms)}")

    def _insert_dialogue_at(self, start_ms, end_ms):
        """在当前 ASS 按时间顺序插入一条空文本字幕行（start/end 给定），并选中滚动到它。"""
        d = self.cur_doc()
        if not d:
            self._status("没有可插入的 ASS"); return
        dia = d.dialogues
        templ = dia[self.table.mark_row] if 0 <= self.table.mark_row < len(dia) else (dia[0] if dia else None)
        new = {'layer': templ['layer'] if templ else '0',
               'start': int(start_ms), 'end': int(end_ms),
               'style': templ['style'] if templ else (d.cur_style or 'Default'),
               'name': '', 'marginl': '0', 'marginr': '0', 'marginv': '0',
               'effect': '', 'text': ''}
        idx = 0
        while idx < len(dia) and dia[idx]['start'] <= new['start']:   # 按 start 升序定位
            idx += 1
        self._push_undo("截取插入字幕行")
        dia.insert(idx, new)
        self._refill_table()
        self.table.mark_row = idx; self.table.mark_rows = {idx}; self._anchor_row = idx
        self.table.viewport().update()
        self.table.scrollToItem(self.table.item(idx, 0), QAbstractItemView.EnsureVisible)
        self._follow_suspend_until = time.time() + 4   # 别让自动聚焦立刻滚走
        self.update_buttons()
        self._save_doc()
        self._status(f"已插入字幕行 {core.ms_to_ass(start_ms)} → {core.ms_to_ass(end_ms)}（请输入文字）")

    # ── 多选批量操作 ──────────────────────────────────────────────────────
    def _batch_menu(self, gpos, rows):
        """多选右键菜单：全部删除 / 整体偏移 \\move / 批量编辑样式。"""
        d = self.cur_doc()
        evs = [d.dialogues[i] for i in rows]
        all_move = all(core.parse_move(e['text']) is not None for e in evs)
        menu = QMenu(self)
        act_del = menu.addAction(f"删除选中的 {len(rows)} 行")
        act_move = menu.addAction("整体偏移 \\move（x / y）…")
        if not all_move:
            act_move.setEnabled(False)
            act_move.setText("整体偏移 \\move（有行无 \\move，不可用）")
        act_style = menu.addAction(f"批量编辑样式（{len(rows)} 行）…")
        chosen = menu.exec(gpos)
        if chosen is act_del:
            self._batch_delete(rows)
        elif chosen is act_move:
            self._batch_move_offset(rows)
        elif chosen is act_style:
            self._batch_style(rows)

    def _batch_delete(self, rows):
        d = self.cur_doc()
        self._push_undo(f"删除选中的 {len(rows)} 行")
        for i in sorted(rows, reverse=True):     # 倒序删，下标不串
            if 0 <= i < len(d.dialogues):
                del d.dialogues[i]
        self.table.mark_row = -1; self.table.mark_rows = set(); self._anchor_row = -1
        self._refill_table(); self.update_buttons(); self._save_doc()

    def _batch_move_offset(self, rows):
        d = self.cur_doc()
        dlg = MoveOffsetDialog(len(rows), self)
        if dlg.exec() != QDialog.Accepted:
            return
        dx, dy = dlg.dx(), dlg.dy()
        if dx == 0 and dy == 0:
            return
        self._push_undo(f"整体偏移 {len(rows)} 行 \\move")
        for i in rows:
            ev = d.dialogues[i]
            mv = core.parse_move(ev['text'])
            if not mv:
                continue
            ev['text'] = core.apply_move(ev['text'], mv['x1'] + dx, mv['y1'] + dy,
                                         mv['x2'] + dx, mv['y2'] + dy, mv['t1'], mv['t2'])
        self._refill_table(); self._save_doc()
        self._status(f"已对 {len(rows)} 行 \\move 偏移 x{dx:+} y{dy:+}")

    def _batch_style(self, rows):
        d = self.cur_doc()
        dlg = DanmakuStyleDialog('', self, batch=True)
        if dlg.exec() != QDialog.Accepted:
            return
        acts = dlg._actions()
        if all(v[0] == 'keep' for v in acts.values()):
            return                       # 没勾任何项
        self._push_undo(f"批量编辑 {len(rows)} 行样式")
        for i in rows:
            ev = d.dialogues[i]
            ev['text'] = core.apply_inline_style(ev['text'], **acts)
        self._refill_table(); self._save_doc()
        self._status(f"已批量编辑 {len(rows)} 行样式")

    def _edit_danmaku_style(self, row):
        """弹窗编辑该行内联样式（填充色/不透明度/描边/描边色），确定后只改这一行。"""
        d = self.cur_doc()
        if not d or not (0 <= row < len(d.dialogues)):
            return
        ev = d.dialogues[row]
        dlg = DanmakuStyleDialog(ev['text'], self)
        if dlg.exec() == QDialog.Accepted:
            new_text = dlg.result_text()
            if new_text != ev['text']:
                self._push_undo(f"编辑第{row+1}行弹幕样式")
                ev['text'] = new_text
                self._refill_table()
                self._save_doc()

    def _set_row_time(self, row, which, ms=None):
        """把第 row 行的 开始/结束（which='start'|'end'）设为 ms；ms=None 时取当前播放时间。"""
        d = self.cur_doc()
        if not d:
            return
        if not (0 <= row < len(d.dialogues)):
            self._status("请先选中一行（可开启「选随」让选中跟随播放）"); return
        if ms is None:
            ms = self._cur_pos_ms()
            if ms is None:
                self._status("无视频/无法获取当前时间"); return
        ms = max(0, int(ms))
        e = d.dialogues[row]
        label = '开始' if which == 'start' else '结束'
        self._push_undo(f"设第{row+1}行{label}时间")
        e[which] = ms
        self._refill_table(); self._save_doc()
        warn = "（注意：结束早于开始）" if e['end'] < e['start'] else ""
        self._status(f"已将第{row+1}行{label}设为 {core.ms_to_ass(ms)}{warn}")

    def _insert_row(self, row):
        """在下标 row 处插入一行：开始/结束按相邻行自动填写（填补上一行结束~下一行开始的间隙，
        无间隙则给默认 1.5 秒），样式/layer 沿用相邻行模板，文本空。"""
        d = self.cur_doc()
        if not d:
            return
        dia = d.dialogues
        templ = dia[row] if row < len(dia) else (dia[row - 1] if row - 1 >= 0 else None)
        prev = dia[row - 1] if row - 1 >= 0 else None      # 新行的上一行
        nxt = dia[row] if row < len(dia) else None          # 新行的下一行
        DEF = 1500
        if prev and nxt:
            start = int(prev['end']); end = int(nxt['start'])
            if end - start < 100:                           # 几乎无间隙 → 贴着上一行给默认时长
                start = int(prev['end']); end = start + DEF
        elif prev:                                          # 末尾插入
            start = int(prev['end']); end = start + DEF
        elif nxt:                                           # 开头插入
            end = int(nxt['start']); start = max(0, end - DEF)
        else:
            start, end = 0, DEF
        new = {'layer': templ['layer'] if templ else '0', 'start': start, 'end': end,
               'style': templ['style'] if templ else (d.cur_style or 'Default'),
               'name': '', 'marginl': '0', 'marginr': '0', 'marginv': '0',
               'effect': '', 'text': ''}
        self._push_undo(f"插入一行（第{row+1}行处）")
        dia.insert(row, new)
        self._refill_table()
        self.table.mark_row = row; self.table.mark_rows = {row}; self._anchor_row = row
        self.table.viewport().update()   # 标记新插入的行
        self.update_buttons()
        self._save_doc()

    def toggle_pause(self):
        if self.player and self.cur_video:
            try:
                self.player.pause = not self.player.pause
            except Exception:
                pass

    def _seek_rel(self, sec):
        if self.player and self.cur_video:
            try:
                self.player.command('seek', sec, 'relative', 'exact')
            except Exception:
                pass

    def _key_seq(self, action):
        """按动作名取配置的快捷键 QKeySequence（无则空）。"""
        return QKeySequence((SETTINGS.get('keys') or {}).get(action, ''))

    def _refresh_shortcuts(self):
        """设置里改了快捷键后，刷新由 QShortcut 承载的快捷键（删除行）。"""
        seq = self._key_seq('delete_row')
        self._sc_del.setKey(seq)
        self._sc_clip_del.setKey(seq)

    def _match_key(self, event):
        """把按下的按键组合与 SETTINGS['keys'] 比对，命中则返回动作名，否则 None。"""
        try:
            pressed = QKeySequence(event.keyCombination()).toString()
        except Exception:
            try:
                pressed = QKeySequence(int(event.modifiers().value) | event.key()).toString()
            except Exception:
                pressed = QKeySequence(event.key()).toString()
        if not pressed:
            return None
        for action, ks in (SETTINGS.get('keys') or {}).items():
            if ks and QKeySequence(ks).toString() == pressed:
                return action
        return None

    def _focused_param(self):
        """当前焦点若是某个参数控件（或其内部编辑器，如 spinbox/可编辑 combo 的 QLineEdit），
        返回那个参数控件本身；否则返回 None。"""
        fw = QApplication.focusWidget()
        if fw is None:
            return None
        for p in self._param_widgets:
            if p is fw or p.isAncestorOf(fw):
                return p
        return None

    def eventFilter(self, obj, event):
        et = event.type()
        if obj is getattr(self, 'wave_pane', None) and et == QEvent.Resize:
            self.playhead_overlay.setGeometry(self.wave_pane.rect())   # 覆盖层始终铺满波形区
            self.playhead_overlay.raise_()
        if et in (QEvent.KeyPress, QEvent.KeyRelease) and event.key() == Qt.Key_Control \
                and self.waveform.imode == 'rset':
            self.waveform.set_rset_ctrl(et == QEvent.KeyPress)   # 按/松 Ctrl 即时刷新 R 提示
        if et == QEvent.KeyPress:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QAbstractSpinBox)):
                # 参数框按回车 = 编辑完成，收起编辑光标（不拦截，让值正常提交）
                if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self._focused_param():
                    self._focused_param().clearFocus()
            else:
                if event.key() == Qt.Key_Escape and self.waveform.imode:
                    self._exit_wave_modes(); return True   # Esc 退出任意波形交互模式
                # 回车：选中字幕行后直接进入文本编辑（焦点在按钮上时放行，避免吞掉按钮回车）
                if self.mode == 'subtitle' and event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                        and not isinstance(fw, QPushButton) \
                        and self.table.state() != QAbstractItemView.EditingState \
                        and 0 <= self.table.mark_row < self.table.rowCount():
                    self._edit_sub_text(self.table.mark_row); return True
                # 上/下方向键：移动选中行（仅字幕模式；下拉框等用方向键的控件放行）
                if self.mode == 'subtitle' and event.key() in (Qt.Key_Up, Qt.Key_Down) \
                        and not isinstance(fw, QComboBox):
                    self._move_sel(-1 if event.key() == Qt.Key_Up else 1); return True
                action = self._match_key(event)
                step = int(SETTINGS.get('seek_step', 5))
                if action == 'capture':                      # 截取（字幕/剪辑都可用）
                    self.btn_capture.toggle(); return True   # 已自带：再按一次退出
                if action == 'retime' and self.mode == 'subtitle':
                    self._exit_wave_modes() if self.waveform.imode == 'rset' else self._arm_rset()
                    return True
                if action == 'play_pause':
                    self.toggle_pause(); return True
                if action == 'seek_back':
                    self._seek_rel(-step); return True
                if action == 'seek_fwd':
                    self._seek_rel(step); return True
                if action == 'set_start' and self.mode == 'subtitle':
                    self._exit_wave_modes() if self.waveform.imode == 'pick_start' else self._arm_wave_pick('start')
                    return True
                if action == 'set_end' and self.mode == 'subtitle':
                    self._exit_wave_modes() if self.waveform.imode == 'pick_end' else self._arm_wave_pick('end')
                    return True
                if action == 'clip_in' and self.mode == 'clip':
                    self._clip_set_in(); return True
                if action == 'clip_out' and self.mode == 'clip':
                    self._clip_set_out(); return True
        elif et == QEvent.ContextMenu and self.waveform.imode:
            self._exit_wave_modes(); return True   # 波形交互模式开启时，右键 = 退出（并吞掉右键菜单）
        elif et in (QEvent.MouseButtonPress, QEvent.NonClientAreaMouseButtonPress):
            # 正在编辑单元格时：仅当点击落在表格视口矩形【之外】才提交收起；
            # 点编辑框内部（选字/移光标）或任意单元格都在视口内 → 不提交，编辑继续。
            if self.table.state() == QAbstractItemView.EditingState:
                try:
                    gp = event.globalPosition().toPoint()
                except Exception:
                    gp = QCursor.pos()
                vp = self.table.viewport()
                if not vp.rect().contains(vp.mapFromGlobal(gp)):
                    fw = QApplication.focusWidget()
                    if fw is not None:
                        fw.clearFocus()
            # 点击参数框以外的任何位置（含窗口标题栏等非客户区）→ 收起编辑光标
            p = self._focused_param()
            if p is not None:
                if not (obj is p or (isinstance(obj, QWidget) and p.isAncestorOf(obj))):
                    p.clearFocus()
            # 点到表格空白处、或点到视频区 → 取消行选择（选择行边框消失）
            try:
                hit_blank = (obj is self.table.viewport()
                             and not self.table.indexAt(event.position().toPoint()).isValid())
                hit_video = (obj is self.video_frame
                             or (isinstance(obj, QWidget) and self.video_frame.isAncestorOf(obj)))
                if hit_blank or hit_video:
                    self.table.mark_row = -1; self.table.mark_rows = set(); self._anchor_row = -1
                    self.table.viewport().update()      # 点空白/视频 → 取消行标记
            except Exception:
                pass
        return super().eventFilter(obj, event)

    @staticmethod
    def _fmt(sec):
        sec = int(sec or 0)
        return f"{sec//60}:{sec%60:02d}"

    def _player_duration(self):
        try:
            return self.player.duration if self.player else None
        except Exception:
            return None

    # ── 音频波形 ───────────────────────────────────────────────────────────
    def _waveform_seek(self, frac):
        """点击/拖动波形条 或 进度条 → 按比例跳转；手动 seek 时暂停波形居中，
        免得点哪就被立刻拉回中央（暂停几秒后自动恢复跟随）。"""
        self.waveform.suspend_center()   # 暂停居中跟随（拉进度条/拉波形/点波形都经这里）
        dur = self._player_duration()
        if self.player and dur:
            try:
                self.player.command('seek', dur * frac, 'absolute', 'exact')
            except Exception:
                pass
        self.waveform.set_pos(frac)   # 立即反馈，不等下一帧

    def _apply_waveform_visible(self):
        """波形区显隐：波形本体在「波形」开启且有数据时显示；字幕/片段轨隶属于波形轨——
        必须波形已开启，且「字幕轨/片段轨」开启：字幕模式需有 ASS，剪辑模式需有视频。"""
        # 合并模式暂不显示单视频波形（左侧已是 EDL 合并预览；合并波形/片段轨为后续迭代）
        wave_on = (bool(SETTINGS.get('wave_enabled')) and bool(self.waveform.peaks)
                   and self.mode != 'merge')
        track_on = wave_on and bool(SETTINGS.get('subtrack_enabled'))
        sub_on = track_on and (bool(self.docs) if self.mode == 'subtitle' else bool(self.cur_video))
        self.wave_pane.setVisible(wave_on)
        self.waveform.setVisible(wave_on)
        self.subtrack.setVisible(sub_on)
        if hasattr(self, 'playhead_overlay'):
            self.playhead_overlay.setVisible(wave_on)
            if wave_on:
                self.playhead_overlay.setGeometry(self.wave_pane.rect())
                self.playhead_overlay.raise_()

    def _start_waveform(self, path):
        """后台线程生成（带缓存）波形，完成后经信号回主线程回填。"""
        self._wave_loading = path
        def work(p=path):
            try:
                peaks = core.get_waveform(p)
            except Exception:
                peaks = []
            self._wave_ready.emit(p, peaks)
        threading.Thread(target=work, daemon=True).start()

    def _ensure_waveform(self):
        """波形已启用 + 有视频 + 尚无波形 + 未在生成 → 触发后台生成（两模式通用）。"""
        if not SETTINGS.get('wave_enabled') or not self.cur_video:
            return
        if self.waveform.peaks or self._wave_loading == self.cur_video:
            return
        self._start_waveform(self.cur_video)

    def _on_wave_ready(self, path, peaks):
        if path != self.cur_video:
            return                      # 期间已换/清视频，丢弃
        self._wave_loading = None
        self.waveform.set_peaks(peaks)
        self._apply_waveform_visible()

    # ── 视频十字准线 / 坐标 ────────────────────────────────────────────────
    def _video_image_rect(self):
        """视频图像在 video_frame 内的实际显示矩形（video_frame 局部坐标，Qt 逻辑像素）。"""
        W, H = self.video_frame.width(), self.video_frame.height()
        if W <= 0 or H <= 0:
            return None
        if self.player and self.cur_video:
            # 1) 最准：mpv 真正绘制的输出区域（osd-dimensions 给出黑边 margin ml/mt/mr/mb
            #    与输出尺寸 w/h）。用 W/w、H/h 比例把它换算回 Qt 逻辑坐标 —— 这样无论 mpv
            #    报的是物理还是逻辑像素、屏幕缩放是多少，比例都自动抵消，不会再偏。
            try:
                od = self.player.osd_dimensions
                w, h = od['w'], od['h']
                ml, mt, mr, mb = od['ml'], od['mt'], od['mr'], od['mb']
                iw, ih = w - ml - mr, h - mt - mb
                if w > 0 and h > 0 and iw > 0 and ih > 0:
                    return QRectF(ml * W / w, mt * H / h, iw * W / w, ih * H / h)
            except Exception:
                pass
        # 2) 退回：用 mpv 实际显示分辨率(dwidth/dheight)按"保持宽高比+居中"估算（mpv 默认行为）。
        #    优先用 dwidth/dheight 而非 playW/playH —— 后者可能来自 ASS 的 PlayRes，与视频真实宽高比不符。
        vw = vh = 0
        if self.player and self.cur_video:
            try:
                vw, vh = self.player.dwidth or 0, self.player.dheight or 0
            except Exception:
                vw = vh = 0
        if not (vw and vh):
            vw, vh = self.playW or 0, self.playH or 0
        if vw <= 0 or vh <= 0:
            return None
        scale = min(W / vw, H / vh)
        dw, dh = vw * scale, vh * scale
        return QRectF((W - dw) / 2, (H - dh) / 2, dw, dh)

    def _toggle_cross(self, on):
        self.btn_cross.update()
        if not on:
            self._video_leave()   # 关闭时立即收起准线+坐标

    def _toggle_follow(self, on):
        """「跟随」按钮：持久化到设置，下次启动沿用。"""
        SETTINGS['follow_focus'] = bool(on)
        save_settings()

    def _toggle_sel_follow(self, on):
        """「选随」按钮：选中行始终等于播放行；持久化到设置。"""
        SETTINGS['select_follow'] = bool(on)
        save_settings()

    def _toggle_wave_center(self, on):
        """「波形居中」按钮：放大波形时播放头居中跟随；持久化到设置。"""
        SETTINGS['wave_center'] = bool(on)
        save_settings()
        if hasattr(self, 'waveform'):
            self.waveform.set_center_follow(bool(on))

    def _toggle_seek_lock(self, on):
        """seek 锁开关：持久化默认状态并重绘锁图标。"""
        SETTINGS['seek_lock'] = bool(on)
        save_settings()
        self.btn_lock.update()

    def _toggle_subtrack(self, on):
        """「字幕轨」开关：显示/隐藏字幕轨道；持久化。"""
        SETTINGS['subtrack_enabled'] = bool(on)
        save_settings()
        if hasattr(self, 'subtrack'):
            self._apply_waveform_visible()

    def _select_sub_row(self, i, seek=True):
        """字幕轨道点选某条 → 选中该行（seek=True 且未上锁时跳转），并滚动到它。"""
        d = self.cur_doc()
        if not d or not (0 <= i < len(d.dialogues)):
            return
        self.table.mark_row = i; self.table.mark_rows = {i}; self._anchor_row = i
        self.table.viewport().update()
        self.table.scrollToItem(self.table.item(i, 0), QAbstractItemView.EnsureVisible)
        self._follow_suspend_until = time.time() + 4
        if seek and not self.btn_lock.isChecked() and self.player and self.cur_video:
            try:
                self.player.command('seek', d.dialogues[i]['start'] / 1000, 'absolute', 'exact')
            except Exception:
                pass

    def _sub_track_commit(self):
        """字幕轨道拖动结束：写回文件并刷新表格。"""
        self._refill_table()
        self._save_doc()

    def _add_sub_at(self, ms):
        """字幕轨道右键空白处 → 以点击点为中心、左右各延伸约 40px 插入一条空字幕；
        延伸的时间宽度随当前缩放变化（放大越多 → 时间越短，方块视觉宽度大致恒定）。"""
        w = self.waveform
        dur = w.duration_ms or (ms + 1500)
        width_px = max(1, self.subtrack.width())
        if w.duration_ms and w.zoom:
            ms_per_px = w.duration_ms / (w.zoom * width_px)
            half = int(40 * ms_per_px)                 # 左右各约 40 像素
            half = max(150, min(half, 5000))           # 限制在合理范围
        else:
            half = 750
        start = max(0, int(ms - half)); end = min(int(dur), int(ms + half))
        if end <= start:
            end = start + 300
        self._insert_dialogue_at(start, end)           # 复用：排序插入+选中+保存

    def _split_sub(self, i, ms):
        """把第 i 条字幕在 ms 处分割成两条（前半保留原文，后半留空）。"""
        d = self.cur_doc()
        if not d or not (0 <= i < len(d.dialogues)):
            return
        ev = d.dialogues[i]
        if not (ev['start'] < ms < ev['end']):
            self._status("分割点需落在该字幕时间范围内"); return
        self._push_undo("分割字幕")
        new = dict(ev); new['start'] = int(ms); new['text'] = ''   # 后半段：留空文本
        ev['end'] = int(ms)                                        # 前半段：到分割点
        d.dialogues.insert(i + 1, new)
        self._refill_table(); self._save_doc()
        self.table.mark_row = i; self.table.mark_rows = {i}; self._anchor_row = i
        self.table.viewport().update()
        self._status("已分割字幕")

    def _delete_sub(self, i):
        """字幕轨道右键 → 删除第 i 条字幕。"""
        d = self.cur_doc()
        if not d or not (0 <= i < len(d.dialogues)):
            return
        self._push_undo(f"删除第{i+1}行")
        del d.dialogues[i]
        self.table.mark_row = -1; self.table.mark_rows = set(); self._anchor_row = -1
        self._refill_table(); self.update_buttons(); self._save_doc()

    def _edit_sub_text(self, i):
        """双击字幕轨道某条 → 在右侧表格里打开该行文本进行编辑。"""
        if not (0 <= i < self.table.rowCount()):
            return
        self._select_sub_row(i, seek=False)
        item = self.table.item(i, 4)
        if item:
            self.table.setCurrentItem(item)
            self.table.editItem(item)

    def _toggle_wave_enable(self, on):
        """「波形」开关：启用/关闭音频波形（两模式通用）；持久化到设置；同步两处开关。"""
        SETTINGS['wave_enabled'] = bool(on)
        save_settings()
        for b in getattr(self, '_wave_enable_btns', []):   # 同步操作栏 + 剪辑面板两个开关
            if b.isChecked() != on:
                b.blockSignals(True); b.setChecked(on); b.blockSignals(False)
        if not hasattr(self, 'waveform'):   # 构建期 setChecked 触发时波形控件尚未创建
            return
        if on:
            self._ensure_waveform()
        self._apply_waveform_visible()

    def _video_hover(self, pos):
        """鼠标在视频图像区内：浮层对齐图像矩形、画十字线，提示栏显示视频像素坐标 + 视频长宽。"""
        if not self.btn_cross.isChecked():   # 十字开关开启即可（所有模式的 V 区通用）
            return
        rect = self._video_image_rect()
        if not self.cur_video or rect is None or not rect.contains(QPointF(pos)):
            self._video_leave(); return
        # 用 (尺寸-1) 作分母：让能落到的最后一个像素正好映射到满量程(playW/playH)，
        # 否则"铺满控件"的那个轴最后一像素只能到 (W-1)/W*playW ≈ 差 2~4 像素够不到最大值。
        vx = int(round((pos.x() - rect.x()) / max(1.0, rect.width() - 1) * self.playW))
        vy = int(round((pos.y() - rect.y()) / max(1.0, rect.height() - 1) * self.playH))
        vx = max(0, min(self.playW, vx)); vy = max(0, min(self.playH, vy))
        tl = self.video_frame.mapToGlobal(QPoint(int(rect.x()), int(rect.y())))
        self.crosshair.setGeometry(tl.x(), tl.y(), int(rect.width()), int(rect.height()))
        self.crosshair.set_cross(pos.x() - rect.x(), pos.y() - rect.y())
        if not self.crosshair.isVisible():
            self.crosshair.show()
        self._coord_text = f"({vx}, {vy})  {self.playW}×{self.playH}"   # 坐标 + 视频长宽
        if not self.task['running']:   # 任务进行中不抢提示栏（让识别/渲染进度显示）
            self.lbl_status.setText(self._coord_text)
            self.lbl_status.setToolTip("视频坐标 (x, y) ＋ 视频长宽 (宽×高)")

    def _video_leave(self):
        if self.crosshair.isVisible():
            self.crosshair.hide()
        self._coord_text = None
        if self.task['running']:   # 任务进行中不抢提示栏
            return
        self.lbl_status.setText(self._last_status)
        self.lbl_status.setToolTip("点击查看操作历史")

    def _on_cell_clicked(self, r, c):
        self._follow_suspend_until = time.time() + 4   # 刚点了行 → 几秒内别让聚焦把视图滚走
        mods = QApplication.keyboardModifiers()
        if mods & Qt.ControlModifier:
            # Ctrl+点击：加选；点已选则减选（不跳转播放）
            rows = set(self.table._marked())   # 以当前已选为基准
            if r in rows:
                rows.discard(r)
            else:
                rows.add(r)
            self.table.mark_rows = rows
            self._anchor_row = r
            self.table.mark_row = r if r in rows else (next(iter(rows)) if rows else -1)
            self.table.viewport().update()
            return
        # Shift+点击：从锚点到本行整段框选；普通点击：单行并设为新锚点
        if (mods & Qt.ShiftModifier) and self._anchor_row is not None and self._anchor_row >= 0:
            lo, hi = sorted((self._anchor_row, r))
            self.table.mark_rows = set(range(lo, hi + 1))
        else:
            self._anchor_row = r
            self.table.mark_rows = {r}
        self.table.mark_row = r                        # 主标记/锚点（单行操作用）
        self.table.viewport().update()
        if self.btn_lock.isChecked():                  # 已上锁：只标记，不跳转播放
            return
        d = self.cur_doc()
        if d and 0 <= r < len(d.dialogues) and self.player and self.cur_video:
            e = d.dialogues[r]
            t = e['end'] if c == 2 else e['start']   # 结束列(2)→跳结束
            try:
                self.player.command('seek', t / 1000, 'absolute', 'exact')
            except Exception as ex:
                self._status(f"跳转失败：{ex}")

    def _move_sel(self, delta):
        """上/下方向键：把选中行上移/下移一行（单选）；未上锁则同时跳转到该行开始。"""
        d = self.cur_doc()
        if not d or not d.dialogues:
            return
        n = len(d.dialogues)
        cur = self.table.mark_row
        nxt = max(0, min(n - 1, cur + delta)) if 0 <= cur < n else (0 if delta > 0 else n - 1)
        self.table.mark_row = nxt; self.table.mark_rows = {nxt}; self._anchor_row = nxt
        self.table.viewport().update()
        self.table.scrollToItem(self.table.item(nxt, 0), QAbstractItemView.EnsureVisible)
        self._follow_suspend_until = time.time() + 4   # 刚手动选了行 → 几秒内别被自动居中抢走
        if not self.btn_lock.isChecked() and self.player and self.cur_video:
            try:
                self.player.command('seek', d.dialogues[nxt]['start'] / 1000, 'absolute', 'exact')
            except Exception:
                pass

    def _highlight_current(self):
        self._update_char_count()
        if not self.player:
            return
        try:
            self.btn_play.setPlaying(not self.player.pause)
        except Exception:
            pass
        try:
            pos = self.player.time_pos
        except Exception:
            pos = None
        if pos is None:
            return
        dur = self._player_duration()
        self.lbl_time.setText(f"{self._fmt(pos)} / {self._fmt(dur)}")
        if dur:
            self.waveform.duration_ms = int(dur * 1000)   # 供无波形时决定缩放上限
            self.waveform.set_pos(pos / dur)   # 移动播放头（联动重绘进度条轨道）
        ms = pos * 1000
        if self.table.state() == QAbstractItemView.EditingState:
            return
        # 跟随条选行：只在播放时间落在某条字幕/弹幕的 [start,end) 区间内才跟随；空档则不跟随任何。
        #  · 命中多条（重叠）时：上一跟随行仍命中则保持它（不乱跳），否则取最近开始的那条。
        d = self.cur_doc()
        dia = d.dialogues if d else []
        active = [i for i, e in enumerate(dia) if e['start'] <= ms < e['end']]
        if active:
            cur = self._play_row if self._play_row in active else max(active, key=lambda i: dia[i]['start'])
        else:
            cur = -1   # 不在任何字幕/弹幕区间内 → 不跟随、不高亮
        changed = (cur != self._play_row)
        self._set_play_rows(active, cur)   # 命中多条则多条边框；空档为空集（不改变用户选中）
        # 选随：开启则选中行始终等于当前播放行（便于用快捷键设其开始/结束时间）
        # 但不覆盖用户手动建立的多行选择（Shift/Ctrl 选了 >1 行时不抢）
        if self.btn_sel_follow.isChecked() and cur >= 0 and self.table.mark_row != cur \
                and len(self.table.mark_rows) <= 1:
            self.table.mark_row = cur; self.table.mark_rows = {cur}; self._anchor_row = cur
            self.table.viewport().update()
        # 跟随：开启则自动滚动让当前播放行保持在聚焦位置（不动选中）；刚手动操作过的几秒内不抢
        if changed and cur >= 0 and self.btn_follow.isChecked() \
                and time.time() >= self._follow_suspend_until:
            self._scroll_row_to_frac(self.table, cur, float(SETTINGS.get('sub_focus_pos', 0.5)))

    def _apply_table_hl_color(self):
        """刷新 选择行填充色 / 跟随行边框色 为当前 color_follow，并重绘表格（字幕表 + 片段表）。"""
        col = QColor(SETTINGS.get('color_follow', '#e0a060'))
        for tbl in (self.table, getattr(self, 'clip_table', None)):
            if tbl is None:
                continue
            deleg = tbl.itemDelegate()
            if isinstance(deleg, _CellEditDelegate):
                fill = QColor(col); fill.setAlpha(70)
                deleg.sel_fill = fill          # 选择行整行填充色
            tbl.sel_border = col               # 跟随行整行边框色
            tbl.viewport().update()

    def focus_play_row(self):
        """「聚焦」：立即把当前跟随行滚到聚焦位置（一次性，不论是否开启字幕聚焦）。"""
        if 0 <= self._play_row < self.table.rowCount():
            self._scroll_row_to_frac(self.table, self._play_row, float(SETTINGS.get('sub_focus_pos', 0.5)))
            self._follow_suspend_until = 0   # 手动聚焦后自动聚焦可立即接管

    def _scroll_row_to_frac(self, tbl, row, frac):
        """滚动表格，使第 row 行的中心位于可视区域 frac 比例处（0=顶 1=底）。需逐像素滚动模式。"""
        if not (0 <= row < tbl.rowCount()):
            return
        sb = tbl.verticalScrollBar()
        vp_h = tbl.viewport().height()
        row_h = tbl.rowHeight(row)
        content_y = tbl.rowViewportPosition(row) + sb.value()      # 行顶在内容坐标系的 y
        target = int(content_y + row_h / 2 - frac * vp_h)          # 让行中心落在 frac*视高
        sb.setValue(max(sb.minimum(), min(sb.maximum(), target)))

    def _set_play_rows(self, rows, primary):
        """设置跟随行集合（命中的多条字幕/弹幕，各画整行边框）+ 主跟随行 primary（用于滚动/聚焦）。"""
        deleg = self.table.itemDelegate()
        if not isinstance(deleg, _CellEditDelegate):
            return
        rows = set(rows)
        deleg.play_rows = rows
        self._play_row = primary
        self._apply_table_hl_color()   # 同步高亮色并重绘（含跟随行边框）

    def _update_char_count(self):
        """框选文本时右上角提示显示「选中 N 字」，字数按 core 的字符宽度规则算
        （中文算 1、英文算 0.5）；没选中时恢复上一条状态提示。"""
        if self.task['running']:   # 任务进行中：提示栏归任务进度独占，不被坐标/字数抢写
            return
        fw = QApplication.focusWidget()
        sel = fw.selectedText() if isinstance(fw, QLineEdit) else ''
        if sel:
            w = core.text_width(sel)
            ws = f"{w:g}"   # 2.0 显示成 2，2.5 仍显示 2.5
            self.lbl_status.setText(f"选中 {ws} 字")
            self.lbl_status.setToolTip(core.char_width_desc())   # 悬停说明字数怎么算
        elif self._coord_text:
            self.lbl_status.setText(self._coord_text)   # 悬停视频时显示坐标，优先于普通提示
            self.lbl_status.setToolTip("视频坐标 (x, y)")
        else:
            self.lbl_status.setText(self._last_status)
            self.lbl_status.setToolTip("点击查看操作历史")

    # ── 保存（防抖）─────────────────────────────────────────────────────
    def _schedule_save(self):
        if self.cur_video:
            self._save_timer.start(350)

    def _preview_ass_path(self):
        """mpv 要加载的字幕：把所有已载入文档按【标签顺序】叠加成一个临时 ass 预览。
        图层顺序 = 标签顺序：最左标签=最上层，最右标签=最底层（拖动标签可调整）。
        只有一个时直接用它本身。渲染走同一函数，所见即所得。"""
        paths = [d.path for d in self.docs if d.path and os.path.exists(d.path)]
        if not paths:
            return None
        if len(paths) == 1:
            return paths[0]
        # 最右(paths[-1])为底图；其余按"从右到左"依次叠加，使最左(paths[0])落在最顶层
        base, overlays = paths[-1], list(reversed(paths[:-1]))
        tmp = os.path.join(core.temp_dir(), 'dmr_preview_merged.ass')
        try:
            return core.merge_preview(tmp, base, overlays)
        except Exception:
            cur = self.cur_doc()
            return cur.path if cur else paths[0]

    def _refresh_sub(self):
        """刷新 mpv 预览字幕，全权管理唯一字幕轨：
        - 目标路径不变（内容可能变）→ sub-reload 重读
        - 目标变了 → 先移除旧轨再加载新轨
        - 无目标（清空）→ 移除字幕轨
        调用方不要再手动改 self._loaded_sub_path。"""
        if not self.player:
            return
        if self.mode in ('clip', 'merge'):
            # 剪辑/合并模式不叠加字幕：移除已加载的字幕轨
            if self._loaded_sub_path is not None:
                try:
                    self.player.command('sub-remove')
                except Exception:
                    pass
                self._loaded_sub_path = None
            return
        target = self._preview_ass_path()
        try:
            if target and target == self._loaded_sub_path:
                self.player.command('sub-reload')
            elif target:
                if self._loaded_sub_path is not None:
                    self.player.command('sub-remove')   # 移除旧字幕轨，避免叠加/残留
                self.player.command('sub-add', target, 'select')
                self._loaded_sub_path = target
            else:
                if self._loaded_sub_path is not None:
                    self.player.command('sub-remove')   # 清空：移除字幕
                    self._loaded_sub_path = None
        except Exception:
            pass

    def _do_save(self):
        """防抖定时器回调：文件已在编辑时即时写回，这里只刷新 mpv 预览。"""
        self._refresh_sub()

    # ── 动作 ────────────────────────────────────────────────────────────
    def import_video(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择视频（可多选）", "", VIDEO_FILTER)
        for p in paths:                 # 可一次导入多个：各建标签，最后一个激活
            self._import_path(p)

    def _import_path(self, path):
        if self.task['running']:
            return
        if not os.path.exists(path):
            self._status(f"路径无效：{path}")
            return
        for i, v in enumerate(self.videos):     # 已导入过 → 直接切到它
            if os.path.normpath(v['path']) == os.path.normpath(path):
                self._activate_video(i)
                self._status(f"已切换到（{path}）")
                return
        self.videos.append({'path': path, 'clips': []})   # 新视频：追加并激活
        self._activate_video(len(self.videos) - 1)
        self._status(f"已导入（{path}）"
                     + ("" if self.docs else "（无 ASS，点「语音识别」生成或「导入 ASS」）"))

    def _activate_video(self, idx):
        """激活第 idx 个视频（idx 无效=清空所有视频状态）。切换预览、波形、片段表。"""
        if not (0 <= idx < len(self.videos)):
            self._active_vid = -1
            self.cur_video = None
            self.clips = []
            self._clip_in = self._clip_out = None; self._update_clip_io_label()
            self._stop_player_async()
            self._video_leave()
            self._loaded_sub_path = None
            self._wave_loading = None
            self.waveform.pos = 0.0; self.waveform.duration_ms = 0
            self.waveform.set_peaks([])
            self.lbl_time.setText("0:00 / 0:00")
            self._refill_clip_table()
            self.clip_table.mark_row = -1; self.clip_table.mark_rows = set(); self._clip_anchor = -1
            self._rebuild_video_tabs()
            self._apply_waveform_visible()
            self.update_buttons()
            if hasattr(self, 'subtrack'):
                self.subtrack.update()
            return
        self._active_vid = idx
        v = self.videos[idx]
        self.cur_video = v['path']
        self.clips = v['clips']             # 片段表/轨道随激活视频切换
        self._clip_in = self._clip_out = None; self._update_clip_io_label()
        core.set_video(v['path'])
        self._sub_loaded = False
        self._loaded_sub_path = None
        self._wave_loading = None
        self.waveform.pos = 0.0; self.waveform.duration_ms = 0
        self.waveform.set_peaks([])
        self._refill_clip_table()
        self.clip_table.mark_row = -1; self.clip_table.mark_rows = set(); self._clip_anchor = -1
        try:
            self.playW, self.playH = core.video_size(v['path'])
        except Exception:
            pass
        self._rebuild_video_tabs()
        self._apply_waveform_visible()
        self.update_buttons()
        if self.player:
            try:
                self.player.play(v['path'])   # 播放后 file-loaded 回调会刷新字幕预览
            except Exception as e:
                self._status(f"预览播放失败：{e}")
        self._ensure_waveform()              # 字幕模式则后台生成音频波形（带缓存）
        if hasattr(self, 'subtrack'):
            self.subtrack.update()

    def _on_video_tab_changed(self, idx):
        if 0 <= idx < len(self.videos) and idx != self._active_vid:
            self._activate_video(idx)

    def _close_video_by_path(self, path):
        """关闭指定视频（× 按钮）。若关的是激活视频，自动激活相邻一个；全关则清空。"""
        idx = next((i for i, v in enumerate(self.videos) if v['path'] == path), -1)
        if idx < 0:
            return
        was_active = (idx == self._active_vid)
        del self.videos[idx]
        if not self.videos:
            self._activate_video(-1)
            self._status("已关闭全部视频")
            return
        if was_active:
            self._activate_video(min(idx, len(self.videos) - 1))
        else:
            if idx < self._active_vid:
                self._active_vid -= 1
            self._rebuild_video_tabs()
        self._status("已关闭视频")

    def _stop_player_async(self):
        """后台线程停止 mpv 播放（同步 stop 会卡 GUI）。"""
        if self.player:
            def _stop(pl=self.player):
                try:
                    pl.command('stop')
                except Exception:
                    pass
            threading.Thread(target=_stop, daemon=True).start()

    def import_ass(self, path=None, kind='subtitle'):
        """导入一个 ass（字幕或弹幕，同一逻辑）为新标签页。"""
        if self.task['running']:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "选择 ASS（字幕或弹幕）", "",
                                                  "ASS 字幕 (*.ass);;所有文件 (*.*)")
        if not path or not os.path.exists(path):
            return
        self._add_doc(path, kind)   # 触发 _on_tab_changed → 刷新表/样式下拉/预览
        self._status(f"已导入（{path}）")

    def clear_video(self):
        """关闭当前激活视频（兼容旧调用）。"""
        if self.cur_video:
            self._close_video_by_path(self.cur_video)

    # 拖拽：视频→导入视频；ASS→新建标签编辑（字幕/弹幕同款）
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        # 支持一次拖入多个文件：每个 .ass 各开一个标签，其余按视频导入（多个视频各建标签，最后一个激活）
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if self.mode == 'merge':
            # 合并模式：拖入窗口=加入右侧 S 区合并列表（仅视频，.ass 忽略）
            self.merge_list.add_paths(paths)
            self.update_buttons()
            return
        for p in paths:                       # 视频先导入，确保后续 ass 能基于视频分辨率预览
            if not p.lower().endswith('.ass'):
                self._import_path(p)
        for p in paths:
            if p.lower().endswith('.ass'):
                self.import_ass(p)

    # ── 任务启动/停止 ──────────────────────────────────────────────────────
    def _set_proc(self, p):
        """后台线程回调：记录当前任务的子进程句柄，供停止用。"""
        self._proc = p

    def _pause_preview(self):
        """任务开始时暂停 mpv 预览，释放 CPU/GPU 给渲染/识别（预览持续解码会拖慢）。"""
        if self.player and self.cur_video:
            try:
                self.player.pause = True
            except Exception:
                pass

    def _begin_task(self, kind):
        """统一任务启动：复位状态、暂停预览、刷新按钮（含显示停止键）。"""
        self._cancel = False
        self._proc = None
        self.task.update(running=True, kind=kind, done=False, error=None,
                         cancelled=False, percent=0, output=None, t0=time.time())
        self._pause_preview()
        self.update_buttons()

    @staticmethod
    def _fmt_elapsed(secs):
        secs = int(round(secs or 0))
        return f"{secs // 60}分{secs % 60}秒" if secs >= 60 else f"{secs}秒"

    def stop_task(self):
        """停止当前任务：终止子进程；worker 捕获后按「已停止」处理。"""
        if not self.task['running']:
            return
        self._cancel = True
        p = self._proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
        self.btn_stop.setEnabled(False)   # 防重复点击，任务结束后由 update_buttons 复位
        self._status("正在停止…")

    # ── 命名弹窗 / 输出路径 ────────────────────────────────────────────────
    def _ask_name(self, title, default_base, suffix_label, note=None, extra_checks=None):
        """弹命名窗（输出目录默认视频目录，可改）。返回 (基础名, 是否覆盖, 额外勾选dict, 输出目录) 或 None。"""
        folder = os.path.dirname(self.cur_video) if self.cur_video else ''
        dlg = NameDialog(title, default_base, suffix_label, folder, note=note,
                         extra_checks=extra_checks, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return None
        base, ow, extra, out_folder = dlg.result()
        if not base:
            self._status("文件名不能为空"); return None
        out_folder = out_folder or folder
        try:
            os.makedirs(out_folder, exist_ok=True)   # 用户选的目录可能尚不存在，按需创建
        except Exception as e:
            self._status(f"输出目录无效：{e}"); return None
        return base, ow, extra, out_folder

    def _resolve_path(self, base, ext, overwrite, folder=None):
        """输出目录(默认视频目录) + 基础名 + 后缀；不覆盖则自动改名避免重名。"""
        folder = folder or os.path.dirname(self.cur_video)
        path = os.path.join(folder, base + ext)
        return path if overwrite else core.unique_path(path)

    def export_frame(self):
        """导出当前画面为 PNG（字幕编辑/视频剪辑都可用）。"""
        if not self.player or not self.cur_video or self.task['running']:
            return
        stem = os.path.splitext(os.path.basename(self.cur_video))[0]
        try:
            secs = int(self.player.time_pos or 0)
        except Exception:
            secs = 0
        tlabel = f"{secs // 3600}-{(secs % 3600) // 60:02d}-{secs % 60:02d}"   # H-MM-SS，文件名安全
        res = self._ask_name("导出当前帧", f"{stem}_{tlabel}", ".png")
        if not res:
            return
        base, ow, _, folder = res
        path = self._resolve_path(base, ".png", ow, folder)
        try:
            # 'subtitles'=带已渲染字幕、视频原分辨率（剪辑模式无字幕则为干净画面）
            self.player.command('screenshot-to-file', path, 'subtitles')
            self._status(f"已导出当前帧：{path}")
        except Exception as e:
            self._status(f"导出帧失败：{e}")

    def run_asr(self):
        if not self.cur_video or self.task['running']:
            return
        stem = os.path.splitext(os.path.basename(self.cur_video))[0]
        res = self._ask_name("语音识别", f"{stem}(字幕)", ".ass",
                             note="将对当前视频做语音识别生成字幕，按当前样式/换行字数。耗时数分钟，首次需加载模型。",
                             extra_checks=[('ass', "生成 .ass（建议勾选）", True),
                                           ('srt', "生成 .srt（可导入剪映）", False),
                                           ('txt', "生成 .txt", False),
                                           ('show', "在命令行显示识别进度输出", False)])
        if not res:
            return
        base, ow, extra, folder = res
        asr_ass = bool(extra.get('ass', True))
        asr_srt = bool(extra.get('srt', False))
        asr_trash = not bool(extra.get('txt', False))   # 不勾「生成 .txt」= 识别后删中间 txt
        asr_show = bool(extra.get('show', False))
        if not asr_ass and not asr_srt:
            self._status("请至少勾选「生成 .ass」或「生成 .srt」"); return
        exts = ('.ass', '.txt', '.srt')   # 同号唯一：三者一起避开重名
        def _paths(b):
            return {x: os.path.join(folder, b + x) for x in exts}
        gp = _paths(base)
        if not ow and any(os.path.exists(p) for p in gp.values()):
            i = 2
            while any(os.path.exists(os.path.join(folder, f"{base}({i}){x}")) for x in exts):
                i += 1
            gp = _paths(f"{base}({i})")
        gen_ass = gp['.ass'] if asr_ass else None
        gen_txt = gp['.txt']
        gen_srt = gp['.srt'] if asr_srt else None
        self._asr_out_ass = gen_ass   # 识别完成后据此新建标签（未生成则不载入）
        self._asr_out_srt = gen_srt   # 完成提示里告知 srt 路径
        self._begin_task('asr')
        self._status("开始语音识别…")   # 入历史（进行中的刷新不入）

        # 在主线程取出视频/偏好样式/当前换行字数，传给后台统一接口（产出带样式+已换行的 ass）
        video = core.VIDEO_PATH
        gen_style = dict(SETTINGS.get('style') or {})
        gen_wrap = self.sp_wrap.value()

        def work():
            try:
                core.generate_subtitle_ass(
                    video, style=gen_style, out_ass=gen_ass, do_asr=True,
                    out_txt=gen_txt, show_output=asr_show,
                    trash_txt=asr_trash, wrap_chars=gen_wrap,
                    on_proc=self._set_proc, out_srt=gen_srt, make_ass=asr_ass)
                self.task['done'] = True
            except Exception as e:
                if self._cancel:
                    self.task['cancelled'] = True
                else:
                    self.task['error'] = str(e)
            finally:
                self._proc = None
                self.task['running'] = False
        threading.Thread(target=work, daemon=True).start()
        self.update_buttons()

    def render_video(self):
        if not self.cur_video or not self.docs or self.task['running']:
            return
        # 渲染所见即所得：把所有标签页叠加后的预览 ass 烧进视频
        target = self._preview_ass_path()
        if not target or not os.path.exists(target):
            self._status("没有可渲染的 ASS")
            return
        stem = os.path.splitext(os.path.basename(self.cur_video))[0]
        res = self._ask_name("渲染视频", f"{stem}(字幕)", ".mp4")
        if not res:
            return
        base, ow, _, folder = res
        out_path = self._resolve_path(base, ".mp4", ow, folder)
        core.ASS_PATH = target
        self._begin_task('render')
        self._status("开始渲染视频…")   # 入历史（进行中的刷新不入）

        def work():
            try:
                out = core.burn(lambda p: self.task.__setitem__('percent', p),
                                on_proc=self._set_proc, out_path=out_path)
                self.task['output'] = out; self.task['done'] = True
            except Exception as e:
                if self._cancel:
                    self.task['cancelled'] = True
                else:
                    self.task['error'] = str(e)
            finally:
                self._proc = None
                self.task['running'] = False
        threading.Thread(target=work, daemon=True).start()
        self.update_buttons()

    def _offset_changed(self, val):
        # 即时应用：按与上次已应用值的增量平移当前标签页全部行（来回调可逆）
        d = self.cur_doc()
        if not d:
            self.sp_offset.blockSignals(True); self.sp_offset.setValue(0); self.sp_offset.blockSignals(False)
            return
        delta = val - d.applied_offset
        if not delta or not d.dialogues:
            d.applied_offset = val
            return
        self._push_undo("调整时间偏移", merge_key="offset")
        for e in d.dialogues:
            e['start'] = max(0, e['start'] + delta)
            e['end'] = max(0, e['end'] + delta)
        d.applied_offset = val
        self._refill_table()
        self._save_doc()

    def apply_wrap(self):
        # 改「换行字数」回车/失焦时自动调用：对当前标签页按字数重排；含 inline 标签行(弹幕)跳过。
        # 幂等：先算出新文本，无任何变化就直接返回（不记撤销、不刷新），避免失焦时反复触发。
        d = self.cur_doc()
        if not d or not d.dialogues:
            return
        n = self.sp_wrap.value()
        new_texts = {}; skipped = 0
        for i, e in enumerate(d.dialogues):
            if '{' in e['text']:          # 弹幕等带覆盖标签的行不换行
                skipped += 1
                continue
            logical = e['text'].replace('\\N', '').replace('\\n', '')   # 去旧软换行
            nt = core.wrap_text(logical, n).replace('\n', '\\n')        # 存成 ass 软换行字面量
            if nt != e['text']:
                new_texts[i] = nt
        if not new_texts:
            return                        # 无变化：什么都不做
        self._push_undo("批量换行")
        for i, nt in new_texts.items():
            d.dialogues[i]['text'] = nt
        self._refill_table()
        self._save_doc()
        msg = (f"已按每行 {n} 字换行 {len(new_texts)} 条" if n > 0 else f"已清除 {len(new_texts)} 条换行")
        if skipped:
            msg += f"（跳过 {skipped} 条含标签行）"
        self._status(msg)

    def apply_replace(self):
        """批量文本替换：按规则表（默认模板 + 自定义，可勾选）对当前标签页所有行依次替换。"""
        d = self.cur_doc()
        if not d or not d.dialogues:
            self._status("当前没有可替换的文本"); return
        dlg = ReplaceDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        rules = dlg.rules()
        SETTINGS['replace_rules'] = rules    # 记住规则与勾选状态（即使本次没替换到内容）
        save_settings()
        active = [(r['find'], r['repl']) for r in rules if r['on'] and r['find']]
        if not active:
            self._status("没有启用任何替换规则"); return

        # 先算出每行替换后的文本，确认确有改动再 push undo + 落盘
        new_texts, total = [], 0
        for e in d.dialogues:
            t = e['text']
            for find, repl in active:
                c = t.count(find)
                if c:
                    total += c
                    t = t.replace(find, repl)
            new_texts.append(t)
        if not total:
            self._status("未找到可替换内容（规则已保存）"); return

        self._push_undo("批量替换文本")
        changed = 0
        for e, t in zip(d.dialogues, new_texts):
            if t != e['text']:
                e['text'] = t; changed += 1
        self._refill_table()
        self._save_doc()
        self._status(f"已替换 {total} 处（{changed} 行），应用了 {len(active)} 条规则")

    # ── 后台任务轮询 ─────────────────────────────────────────────────────
    def _poll_task(self):
        t = self.task
        if t['running']:
            el = self._fmt_elapsed(time.time() - t.get('t0', time.time()))   # 实时已用时
            if self._cancel:
                self._status(f"正在停止…（已用 {el}）", log=False)
                return
            if t['kind'] == 'render':
                self._status(f"渲染中… {t['percent']}%（已用 {el}）", log=False)
            elif t['kind'] == 'clip':
                self._status(f"切割导出中… {t['percent']}%（已用 {el}）", log=False)
            elif t['kind'] == 'merge':
                self._status(f"合并中…（已用 {el}）", log=False)
            else:
                self._status(f"{core.task_state.get('msg', '处理中…')}（已用 {el}）", log=False)
            return
        label = {'render': '渲染', 'clip': '切割导出', 'asr': '语音识别', 'merge': '视频合并'}.get(t['kind'], '任务')
        if t.get('cancelled'):
            t['cancelled'] = False
            self._status(f"{label}已停止")
            self.update_buttons()
            return
        if t['error']:
            self._status(f"{label}失败：{t['error']}")
            t['error'] = None; self.update_buttons()
            return
        if t['done']:
            t['done'] = False
            el = f"（耗时 {self._fmt_elapsed(time.time() - t.get('t0', time.time()))}）"
            if t['kind'] == 'render':
                self._status(f"渲染完成（√）{el} {t['output']}")
            elif t['kind'] == 'merge':
                self._status(f"合并完成（√）{el} {t['output']}")
            elif t['kind'] == 'clip':
                out = t['output']
                if isinstance(out, list):
                    self._status(f"切割完成（√）{el}共导出 {len(out)} 个片段")
                else:
                    self._status(f"切割完成（√）{el} {out}")
            else:
                out = getattr(self, '_asr_out_ass', None)
                if out and os.path.exists(out):
                    self.import_ass(out, kind='subtitle')   # 新建标签载入生成的字幕
                srt = getattr(self, '_asr_out_srt', None)
                extra = f"，srt: {srt}" if (srt and os.path.exists(srt)) else ""
                self._status(f"语音识别完成（√）{el}{extra}")
            self.update_buttons()

    # ── 按钮状态：导入常亮；有视频→识别亮；有视频+字幕→渲染亮 ──────────────
    def update_buttons(self):
        busy = self.task['running']
        has_video = bool(self.cur_video)
        has_subs = len(self.docs) > 0
        self.btn_asr.setEnabled(has_video and not busy)
        self.btn_render.setEnabled(has_video and has_subs and not busy)
        self._paint_btn(self.btn_asr, self.btn_asr.isEnabled())
        self._paint_btn(self.btn_render, self.btn_render.isEnabled())
        self.btn_export_clips.setEnabled(any(v['clips'] for v in self.videos) and not busy)
        self._paint_btn(self.btn_export_clips, self.btn_export_clips.isEnabled())
        if hasattr(self, 'btn_merge'):     # 视频合并：列表里≥2个文件且空闲才可点
            self.btn_merge.setEnabled(self.merge_list.count() >= 2 and not busy)
            self._paint_btn(self.btn_merge, self.btn_merge.isEnabled())
        self.btn_frame.setEnabled(has_video and not busy)   # 导出帧：两模式共享
        self._paint_btn(self.btn_frame, self.btn_frame.isEnabled())
        self.btn_stop.setVisible(busy)      # 停止键仅任务运行时出现
        self.btn_stop.setEnabled(busy)
        self.video_toolbar.setVisible(has_video)   # 视频/十字/导出帧 行：有视频才显示
        self.transport.setVisible(has_video)       # 进度条/播放 行：有视频才显示
        self.clip_body.setVisible(has_video)       # 剪辑面板内容：有视频才显示（否则空灰）

    def _paint_btn(self, btn, active):
        # 一次性动作按钮（识别/渲染/导出帧/导出片段）：圆角矩形，"中"档色
        r = _radius_for(6)
        if active:
            c = SETTINGS['color_button']
            btn.setStyleSheet(f"QPushButton{{background:{_rgba(c)};color:{_fg(c)};padding:6px 14px;border:none;border-radius:{r}px;}}"
                              f"QPushButton:hover{{background:{_rgba(_darken(c))};}}")
        else:
            btn.setStyleSheet(f"QPushButton{{background:#3a3a3a;color:#777;padding:6px 14px;border:none;border-radius:{r}px;}}")

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            SETTINGS.update(dlg.result_settings())
            save_settings()
            # 重建样式（进度条/圆点/滚动条颜色）+ 重绘按钮、播放键
            QApplication.instance().setStyleSheet(build_qss())
            self.update_buttons()
            self.btn_play.update()
            self._apply_widget_colors()   # 标题/×按钮/状态栏/齿轮颜色即时生效（含跟随条/边框色刷新）
            self._refresh_shortcuts()     # 删除行等快捷键即时生效
            self.waveform.set_focus_pos(float(SETTINGS.get('wave_focus_pos', 0.5)))   # 波形聚焦位置即时生效
            self._status("设置已保存")

    # ── 撤销（Ctrl+Z）──────────────────────────────────────────────────────
    def _snapshot(self):
        """抓取当前可撤销状态：所有文档的 Dialogue/Style/偏移（按路径标识）。"""
        return {
            'docs': [{
                'path':           d.path,
                'dialogues':      copy.deepcopy(d.dialogues),
                'styles':         copy.deepcopy(d.styles),
                'cur_style':      d.cur_style,
                'applied_offset': d.applied_offset,
            } for d in self.docs],
        }

    def _push_undo(self, desc, merge_key=None):
        """在改动发生【之前】调用，记录可撤销快照并把操作写入历史。
        merge_key 相同的连续操作（如反复调字号）只保留最早一个快照，避免碎片。"""
        if self._suspend_undo:
            return
        if merge_key is not None and merge_key == self._last_undo_key and self._undo_stack:
            return   # 合并：沿用已存在的更早快照
        self._undo_stack.append((desc, self._snapshot()))
        self._last_undo_key = merge_key if merge_key is not None else object()
        if len(self._undo_stack) > 100:
            del self._undo_stack[0]
        self._status(desc)   # 顺便记入操作历史

    def _undo(self):
        if not self._undo_stack:
            self._status("没有可撤销的操作")
            return
        desc, snap = self._undo_stack.pop()
        self._last_undo_key = None
        self._suspend_undo = True
        try:
            # 按路径匹配回填到现有文档（撤销不跨标签增删），并回写各文件
            for i, sd in enumerate(snap['docs']):
                if i < len(self.docs) and os.path.normpath(self.docs[i].path) == os.path.normpath(sd['path']):
                    d = self.docs[i]
                    d.dialogues = copy.deepcopy(sd['dialogues'])
                    d.styles = copy.deepcopy(sd['styles'])
                    d.cur_style = sd['cur_style']
                    d.applied_offset = sd['applied_offset']
                    d.save_dialogues()
                    for sname, svals in d.styles.items():
                        core.patch_style_in_ass(d.path, sname, svals, d.play_h)
            self._reload_style_dropdown()
            self._refill_table()
            cur = self.cur_doc()
            self.sp_offset.blockSignals(True)
            self.sp_offset.setValue(cur.applied_offset if cur else 0)
            self.sp_offset.blockSignals(False)
            self._refresh_sub()
            self.update_buttons()
        finally:
            self._suspend_undo = False
        self._status(f"已撤销：{desc}")

    def _status(self, msg, log=True):
        """更新右上角提示。log=True 才记入操作历史；进度型刷新（识别中/渲染中%）传 log=False，
        避免轮询反复记录同一句。"""
        self._last_status = msg
        self.lbl_status.setText(msg)
        if not log:
            return
        lim = int(SETTINGS.get('history_limit', 100))
        if lim <= 0:
            self._status_history.clear()
            return
        # 连续相同内容不重复记录（按内容部分比较，忽略时间戳）
        if self._status_history and self._status_history[-1].split('  ', 1)[-1] == msg:
            return
        self._status_history.append(f"{time.strftime('%H:%M:%S')}  {msg}")
        if len(self._status_history) > lim:
            del self._status_history[:len(self._status_history) - lim]

    def _show_history(self):
        """点击右上角提示 → 弹窗显示操作历史（最新在上）。"""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"操作历史（共 {len(self._status_history)} 条）")
        dlg.resize(480, 440)
        v = QVBoxLayout(dlg)
        lst = _HScrollList()
        lst.setWordWrap(False); lst.setTextElideMode(Qt.ElideNone)   # 长行不省略，可横向滚动
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        if self._status_history:
            for line in self._status_history:   # 最旧在上、最新在下
                lst.addItem(line)
            lst.setCurrentRow(lst.count() - 1)   # 聚焦最新一条
            lst.scrollToBottom()                 # 打开即滚到底部
        else:
            lst.addItem("（暂无操作历史）")
        v.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()

    def closeEvent(self, e):
        if self.task['running']:
            label = {'render': '渲染', 'clip': '切割导出', 'asr': '语音识别', 'merge': '视频合并'}.get(self.task['kind'], '任务')
            box = QMessageBox(self)
            box.setWindowTitle("关闭")
            box.setIcon(QMessageBox.Warning)
            box.setText(f"正在{label}，关闭程序会停止该任务。")
            box.setInformativeText("确定要关闭吗？")
            yes = box.addButton("关闭并停止", QMessageBox.YesRole)
            box.addButton("继续运行", QMessageBox.NoRole)
            box.exec()
            if box.clickedButton() is not yes:
                e.ignore(); return
            self._cancel = True
            p = self._proc
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
        try:   # 记住窗口大小/位置，下次启动恢复
            SETTINGS['window_geometry'] = bytes(self.saveGeometry().toBase64()).decode('ascii')
            save_settings()
        except Exception:
            pass
        if self.player:
            try:
                self.player.terminate()
            except Exception:
                pass
        super().closeEvent(e)


DARK_QSS = """
QWidget{background:#1a1a1a;color:#ddd;font-family:'Microsoft YaHei';font-size:12px;}
QPushButton{background:#555;color:#fff;padding:6px 14px;border:none;border-radius:10px;}
QPushButton:hover{background:#666;}
QPushButton:disabled{background:#3a3a3a;color:#777;}
QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit{background:#2a2a2a;border:1px solid #3a3a3a;border-radius:6px;padding:2px 4px;}
QSpinBox::up-button,QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:top right;width:18px;background:#3a3a3a;border-left:1px solid #555;}
QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;width:18px;background:#3a3a3a;border-left:1px solid #555;}
QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover,QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:#4a4a4a;}
QSlider::groove:horizontal{height:6px;background:#333;border-radius:3px;}
QTableWidget{background:#242424;outline:0;}
QTableWidget::item{border:none;}
QTableWidget::item:focus{border:none;outline:0;}
QHeaderView::section{background:#222;color:#888;border:none;padding:4px;}
QScrollBar:vertical{background:#000000;width:12px;margin:0;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:#000000;}
QScrollBar:horizontal{background:#000000;height:12px;margin:0;}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}
QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:#000000;}
"""


def build_qss():
    """基础样式 + 由设置决定的颜色（进度条/小点/滚动条）+ 上下箭头图。"""
    prog = _rgba(SETTINGS['color_progress']); handle = _rgba(SETTINGS['color_handle']); sb = _rgba(SETTINGS['color_scrollbar'])
    handle_h = _rgba(_darken(SETTINGS['color_handle'])); sb_h = _rgba(_darken(SETTINGS['color_scrollbar']))
    ui = _rgba(SETTINGS.get('color_ui_bg', '#000000'))   # 统一界面背景（菜单/工具/批量/tab 栏）
    # 选中行不再填充底色（改由 delegate 画边框高亮），故选中背景设透明、文字保持正常色
    colored = f"""
QWidget{{background:{ui};}}
QLabel{{background:transparent;}}
QTableView{{selection-background-color:transparent;selection-color:#ddd;}}
QTableWidget::item:selected{{background:transparent;color:#ddd;}}
QAbstractItemView QLineEdit{{padding:0px 1px;margin:0;border:0;border-radius:0;background:#202020;color:#fff;selection-background-color:#3a6ea5;selection-color:#fff;}}
QSlider::sub-page:horizontal{{background:{prog};border-radius:3px;}}
QSlider::handle:horizontal{{background:{handle};width:12px;margin:-4px 0;border-radius:6px;}}
QSlider::handle:horizontal:hover{{background:{handle_h};}}
QScrollBar::handle:vertical{{background:{sb};border-radius:5px;min-height:28px;}}
QScrollBar::handle:vertical:hover{{background:{sb_h};}}
QScrollBar::handle:horizontal{{background:{sb};border-radius:5px;min-width:28px;}}
QScrollBar::handle:horizontal:hover{{background:{sb_h};}}
"""
    fs = int(SETTINGS.get('ui_font_size', 13))
    rad_btn = _radius_for(8)   # 全局按钮（padding 8）圆角，按高度夹住
    rad_in = _radius_for(4)    # 输入框（padding 4，更矮）单独夹
    sizing = f"""
QWidget{{font-size:{fs}px;}}
QPushButton{{padding:8px 16px;border-radius:{rad_btn}px;}}
QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit{{border-radius:{rad_in}px;}}
QTableWidget::item{{padding:6px 5px;}}
QHeaderView::section{{padding:6px;}}
QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit{{padding:4px 6px;}}
"""
    return DARK_QSS + colored + sizing + _make_arrow_pngs()


def _make_arrow_pngs():
    """运行时生成上/下白色三角箭头小图（Qt QSS 的 up/down-arrow 需要图片），返回 QSS 片段。"""
    from PySide6.QtGui import QPixmap
    paths = {}
    for name, up in (('up', True), ('down', False)):
        pm = QPixmap(10, 7); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor('#ffffff')); p.setPen(Qt.NoPen)
        if up:
            p.drawPolygon(QPolygonF([QPointF(5, 1), QPointF(9, 6), QPointF(1, 6)]))
        else:
            p.drawPolygon(QPolygonF([QPointF(1, 1), QPointF(9, 1), QPointF(5, 6)]))
        p.end()
        fp = os.path.join(core.temp_dir(), f'dmr_arrow_{name}.png')
        pm.save(fp, 'PNG')
        paths[name] = fp.replace('\\', '/')
    return (f"QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{{image:url({paths['up']});width:10px;height:7px;}}"
            f"QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{{image:url({paths['down']});width:10px;height:7px;}}")


def _qt_msg_filter(mode, ctx, msg):
    # 过滤掉无害的 QFont::setPointSize 警告（编辑型字体下拉 + 像素字号样式表的已知噪音）
    if 'setPointSize' in msg:
        return
    try:
        sys.stderr.write(msg + '\n')
    except Exception:
        pass


def main():
    qInstallMessageHandler(_qt_msg_filter)
    app = QApplication(sys.argv)
    load_settings()
    app.setStyleSheet(build_qss())
    win = Editor()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
