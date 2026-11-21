"""共享账本竖屏页面的布局、手写标题和原型配色。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.ui.lettering import InkLabel
from app.ui.paper import Illustration

STYLE = """
QMainWindow, QDialog { background: #fffdf7; }
QWidget { color: #292b25; font-family: "Kaiti SC", "STKaiti", "PingFang SC"; font-size: 15px; }

QWidget#card { background: #fffef9; border: 1px solid #eedfc7; border-radius: 12px; }
QWidget#hero { background: #fff8e5; border: 1px solid #eedcaa; border-radius: 12px; }
QLabel#title { font-family: "Kaiti SC", "STKaiti", "PingFang SC"; font-size: 34px;
               font-weight: bold; color: #272721; border-bottom: 3px solid #ffd35b; }
QWidget#mint { background: #e8f5e8; border-radius: 10px; }
QWidget#rose { background: #ffebe7; border-radius: 10px; }
QWidget#sky { background: #e4f2fb; border-radius: 10px; }
QWidget#lavender { background: #f0eafb; border-radius: 10px; }
QLabel#section { font-size: 20px; font-weight: 700; }
QLabel#cardTitle { font-size: 17px; font-weight: 700; }
QLabel#muted { color: #5b5b4e; }
QLabel#brand { color: #5b4930; font-weight: 700; font-size: 15px; }

QLabel#error { color: #962f35; background: #f9e5e2; border-radius: 8px; padding: 9px 12px; }
QLabel#notice { color: #285249; background: #e3efe5; border-radius: 8px; padding: 9px 12px; }
QLineEdit, QComboBox { background: #fffefa; border: 1px solid #c9c0aa;
                      border-radius: 8px; padding: 5px 8px; min-height: 22px; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #3c7769; padding: 4px 7px; }
QComboBox::drop-down { width: 25px; }
QComboBox QAbstractItemView { background: #fffdf7; selection-background-color: #dbe9db;
                            selection-color: #292b25; }
QPushButton { background: #fffdf7; border: 1px solid #c9c0aa; border-radius: 8px;
              padding: 5px 8px; min-height: 21px; }
QPushButton:hover { background: #f8f0d7; }
QPushButton:focus { border: 2px solid #3c7769; padding: 4px 7px; }
QPushButton#primary { color: #14293d; background: #afdfff; border-color: #76b7e1;
                      font-weight: 700; }
QPushButton#primary:hover { background: #8bcdfa; }
QPushButton#danger { color: #8d3538; background:#ffe5e2; border-color:#eac3c0; }
QPushButton#nav { text-align: left; background: transparent; border-color: transparent; }
QPushButton#nav:checked, QPushButton#nav:hover { background: #fffaf0; }
QPushButton#nav:focus { border-color: #3c7769; }
QPushButton:disabled, QPushButton#primary:disabled { color: #716f63; background: #eae6da;
                                                     border-color: #ddd7c8; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: #f3eddd; width: 4px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background: #c7bda5; min-height: 24px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#amount { font-family: "Chalkboard SE", "Avenir Next"; font-size: 22px; font-weight: bold; }
QLabel#bookTitle { font-size: 28px; font-weight: bold; color:#231e19; }
QLabel#heroAmount { font-family:"Chalkboard SE", "Avenir Next"; font-size:28px; font-weight:bold; }
QLineEdit#entryAmount { background:#ffefed; font-size:24px; font-weight:bold; }
QLabel#role { color:#544929; background:#fff0c5; border-radius:8px; font-size:13px; padding:2px 5px; }
QLabel#meta { color: #72695b; font-size: 12px; }
QLabel#balanceText { font-size: 16px; font-weight: bold; color: #196245; }
QWidget#tabBar { background: #fffef9; border-top: 1px solid #eee2cb; }
QPushButton#tab { border: none; background: transparent; padding: 0; }
QPushButton#tab:checked { background: #edf7ff; border-radius: 12px; }
QPushButton#tab:focus { border: 1px solid #69add9; }
QPushButton#budgetInbox { font-size:12px; padding:1px 6px; min-height:17px; border-radius:9px; }
QPushButton#budgetInbox[unread="true"] {background:#ffe6a7; color:#663d19; border-color:#d99331;}
QPushButton#ink { color: white; background: #292724; border-radius: 17px; font-weight: bold; }
QPushButton#expense:checked { background:#ffe0dc; border-color:#ed9b93; }
QPushButton#income:checked { background:#ddf2de; border-color:#9abda1; }
QPushButton#line { border: none; border-bottom: 1px solid #eee6d7; text-align: left; }
QLabel#stamp { color: #296447; border-radius: 8px; background: #e3f5dd; padding: 2px 5px; }
QLabel#inflow { color:#246240; font-weight:bold; }
QLabel#outflow { color:#783e32; font-weight:bold; }
QLabel#pending { color:#82531f; background:#ffedc9; border-radius:8px; padding:2px 5px; }
QLabel#rejected {color:#843e3d; background:#ffe8e3; border-radius:8px; padding:2px 5px;}
QLabel#voided {color:#684573; background:#f2e8f6; border-radius:8px; padding:2px 5px;}
QLabel#inactive { color:#615b60; background:#eee9ee; border-radius:8px; padding:2px 5px; }
QWidget#scrollBody { background: #fffdf7; }
"""


def label(text: str = "", style: str = "") -> QLabel:
    """明确使用纯文本，账本名、用户名与报错不会被解释成 HTML。"""
    if style in InkLabel.SIZES:
        return InkLabel(text, style)
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if style:
        widget.setObjectName(style)
    return widget


def button(text: str, action, style: str = "") -> QPushButton:
    """绑定一个不依赖 Qt checked 参数的动作。"""
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if style:
        widget.setObjectName(style)
    widget.clicked.connect(lambda _checked=False: action())  # pylint: disable=no-member
    return widget


def panel(style: str = "card", margin: int = 11) -> tuple[QWidget, QVBoxLayout]:
    """创建统一留白的内容容器。"""
    widget = QWidget()
    widget.setObjectName(style)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(5)
    return widget, layout


def scroll_area(content: QWidget) -> QScrollArea:
    """保持页面对小窗口和长列表可用。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    return area


def page_heading(title: str, action=None, caption: str = ""):
    """建立应用各页面共用的标题区。"""
    content = QWidget()
    content.setObjectName("scrollBody")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(8, 2, 8, 8)
    layout.setSpacing(8)
    heading = QHBoxLayout()
    if title in HEADINGS:
        asset, height = HEADINGS[title]
        title_label = Illustration(asset, height, title, align="center")
    else:
        title_label = label(title, "title")
        title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    title_label.setObjectName("title")
    heading.addWidget(title_label, 1)
    if action is not None:
        heading.addWidget(button(caption, action, "primary"))
    layout.addLayout(heading)
    return content, layout


HEADINGS = {
    "我的账本": ("head-books", 138), "记一笔": ("head-entry", 94),
    "收支详情": ("head-detail", 52), "申请更正": ("head-correction", 54),
    "审核中心": ("head-review", 72), "查询与统计": ("head-search", 72),
    "账本成员": ("head-members", 92), "结算清单": ("head-settle", 65),
    "操作日志": ("head-log", 76), "收支列表": ("head-entries", 42),
}
