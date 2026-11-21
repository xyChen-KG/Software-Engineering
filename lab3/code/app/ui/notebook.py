"""账本首页与业务页共用的手账卡片。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.ui.forms import ROLES, STATUSES, amount_text
from app.ui.paper import Illustration, PictureButton
from app.ui.theme import label, panel


def avatar(member, size=35):
    """同一角色在各页使用同一套原型头像。"""
    name = {"owner": "avatar-cat", "reviewer": "avatar-rabbit"}.get(member["role"], "avatar-mouse")
    return Illustration(name, size, width=size)


def book_banner(ledger, *, settings=None, art="banner-picnic"):
    """原画边框内使用真实的账本名称、角色及人数。"""
    card, box = panel(margin=4)
    line = QHBoxLayout()
    line.setSpacing(3)
    line.addWidget(Illustration(art, 68, width=105))
    words = QVBoxLayout()
    words.setSpacing(2)
    words.addWidget(label(ledger["name"], "bookTitle"))
    count = sum(m["status"] == "active" for m in ledger.get("members", ()))
    role = ROLES[ledger["role"]]
    words.addWidget(label(f"{count} 位成员 · {role}" if count else role, "role"))
    line.addLayout(words, 1)
    side = QVBoxLayout()
    if settings:
        gear = PictureButton("账本设置", "gear", 24, settings)
        side.addWidget(gear)
    side.addWidget(Illustration("banner-mouse", 33, width=49))
    line.addLayout(side)
    box.addLayout(line)
    return card


def page_section(asset, title, height=27):
    """手写分栏标题保留实际文字作为读屏名称。"""
    return Illustration(asset, height, title)


def row_button(text, action):
    """原生按钮承载可换行的业务数据，各子控件将点击交给按钮处理。"""
    widget = QPushButton(text)
    widget.setObjectName("line")
    widget.setAccessibleName(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setStyleSheet("QPushButton#line {color: transparent; padding: 0;}")
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    widget.clicked.connect(lambda _: action())  # pylint: disable=no-member
    row = QHBoxLayout(widget)
    row.setContentsMargins(5, 3, 5, 3)
    row.setSpacing(5)
    return widget, row


def allow_row_clicks(widget):
    """保留按钮内原生文本标签的绘制和读屏，鼠标事件由按钮处理。"""
    for child in widget.findChildren(QWidget):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def record_row(item, action):
    """金额、状态和业务日期由服务端提供。"""
    sign = "+" if item["kind"] == "income" else "−"
    title = item["purpose"]
    widget, row = row_button(title, action)
    icon = "gift" if sign == "+" else "meal"
    if "水果" in title:
        icon = "apple"
    elif "饮" in title or "杯" in title:
        icon = "drink"
    row.addWidget(Illustration(icon, 30, width=32))
    body = QVBoxLayout()
    body.setSpacing(0)
    body.addWidget(label(title, "cardTitle"))
    body.addWidget(label(item["business_date"] + " · " + item["category"], "meta"))
    row.addLayout(body, 1)
    row.addWidget(label(f"{sign}¥{amount_text(item['amount_cents'])}",
                        "inflow" if sign == "+" else "outflow"))
    stamp = {"approved": "stamp", "pending": "pending", "rejected": "rejected",
             "voided": "voided"}.get(item["status"], "inactive")
    row.addWidget(label(STATUSES[item["status"]], stamp))
    allow_row_clicks(widget)
    return widget
