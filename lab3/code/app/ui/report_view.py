"""查询表单和账务统计的桌面视图。"""

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

from app.ui.forms import KINDS, STATUSES, amount_text, budget_cents, field
from app.ui.notebook import book_banner, record_row
from app.ui.paper import Illustration
from app.ui.theme import button, label, page_heading, panel

PALETTE = ("#f1a9a6", "#89bfe3", "#cdb4df", "#a7cea7", "#f2c768")


def money(value):
    """整数分按元显示，包括负结余。"""
    sign = "−" if value < 0 else ""
    return f"{sign}¥{amount_text(abs(value))}"


def combo(title, choices, selected=None):
    """空选项表示不限。"""
    widget = QComboBox()
    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    widget.setMinimumContentsLength(3)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    widget.addItem(title, "")
    for key, name in choices:
        widget.addItem(name, key)
    widget.setCurrentIndex(max(0, widget.findData(selected)))
    return widget


def metrics(values, *, stacked=False):
    """原稿的三块彩色金额卡，统计页可纵排。"""
    group = QVBoxLayout() if stacked else QHBoxLayout()
    group.setSpacing(5)
    for (title, value), style, icon in zip(values, ("mint", "rose", "sky"),
                                           ("income", "expense", "coins")):
        card, body = panel(style, 6)
        row = QHBoxLayout()
        row.setSpacing(3)
        row.addWidget(Illustration(icon, 22, width=22))
        row.addWidget(label(title, "meta"), 1)
        body.addLayout(row)
        body.addWidget(label(money(value), "amount"))
        group.addWidget(card, 1)
    return group


def budget_panel(overview):
    """左侧正式预算进度，右侧单列待审支出。"""
    level = overview["level"]
    card, body = panel("rose" if level == "over" else "card", 8)
    row = QHBoxLayout()
    left = QVBoxLayout()
    head = QHBoxLayout()
    head.addWidget(Illustration("target", 24, width=24))
    head.addWidget(label("预算", "cardTitle"), 1)
    left.addLayout(head)
    budget = overview["budget"]
    left.addWidget(label(money(budget) if budget is not None else "未设置", "heroAmount"))
    if budget:
        progress = QProgressBar()
        percent = overview["expense"] * 100 // budget
        progress.setValue(min(100, percent))
        progress.setFormat(f"已使用 {percent}%")
        color = {"warning": "#ffcf53", "over": "#f39487"}.get(level, "#a8daca")
        progress.setStyleSheet("QProgressBar {border:1px solid #d8c7a4; border-radius:6px;"
                               "background:#faf5e8; height:14px; text-align:center;}"
                               f"QProgressBar::chunk {{background:{color};border-radius:5px;}}")
        left.addWidget(progress)
    left.addWidget(label(f"正式支出 {money(overview['expense'])}", "meta"))
    if level == "over":
        left.addWidget(label(f"已超预算 · 超出 {money(overview['excess'])}", "error"))
    elif level == "warning":
        left.addWidget(label("预算即将用完", "meta"))
    row.addLayout(left, 3)
    pending = QVBoxLayout()
    pending.addWidget(Illustration("budget-bunny", 34, align="center"))
    pending.addWidget(label("待审预计支出", "meta"))
    pending.addWidget(label(money(overview["pending"]), "cardTitle"))
    row.addLayout(pending, 1)
    body.addLayout(row)
    return card


def filter_panel(members, saved, apply):
    """按当前输入组合筛选，翻页时保留所有条件。"""
    card, body = panel(margin=14)
    keyword = field("搜索用途或关键词", "keyword", 200)
    keyword.setText(saved.get("keyword", ""))
    body.addWidget(keyword)
    inputs = _choice_fields(body, members, saved)
    inputs["keyword"] = keyword
    extra = _range_fields(inputs, saved)
    body.addWidget(extra)
    error = label("", "error")
    error.hide()

    def submit():
        try:
            values = _filter_values(inputs)
        except ValueError as exc:
            error.setText(str(exc))
            error.show()
            return
        apply(values)

    row = QHBoxLayout()
    toggle = button("更多筛选", lambda: extra.setVisible(not extra.isVisible()), "nav")
    row.addWidget(toggle)
    row.addStretch()
    row.addWidget(button("清空条件", lambda: apply({})))
    row.addWidget(button("查询", submit, "primary"))
    keyword.returnPressed.connect(submit)  # pylint: disable=no-member
    body.addWidget(error)
    body.addLayout(row)
    return card


def _choice_fields(body, members, saved):
    grid = QGridLayout()
    inputs = {}
    choices = {"kind": ("全部类型", KINDS.items()), "status": ("全部状态", STATUSES.items()),
               "member_id": ("全部成员", members.items())}
    for index, (key, (title, values)) in enumerate(choices.items()):
        inputs[key] = combo(title, values, saved.get(key))
        grid.addWidget(inputs[key], 0, index)
    body.addLayout(grid)
    return inputs


def _range_fields(inputs, saved):
    extra = QWidget()
    grid = QGridLayout(extra)
    grid.setContentsMargins(0, 0, 0, 0)
    ranges = (("category", "类别"), ("start", "起始 YYYY-MM-DD"),
              ("end", "截止 YYYY-MM-DD"), ("minimum", "最低金额（元）"), ("maximum", "最高金额（元）"))
    for index, (key, hint) in enumerate(ranges):
        control = field(hint, key, 200)
        if key in saved:
            value = amount_text(saved[key]) if key in {"minimum", "maximum"} else saved[key]
            control.setText(value)
        inputs[key] = control
        grid.addWidget(control, index // 2, index % 2)
    extra.setVisible(any(key in saved for key, _ in ranges))
    return extra


def _filter_values(inputs):
    values = {}
    for key, control in inputs.items():
        value = control.currentData() if isinstance(control, QComboBox) else control.text().strip()
        if not value:
            continue
        if key in {"minimum", "maximum"}:
            value = budget_cents(value)
        if key in {"start", "end"}:
            try:
                value = date.fromisoformat(value).isoformat()
            except ValueError as exc:
                raise ValueError("日期请填写 YYYY-MM-DD") from exc
        values[key] = value
    for low, high in (("minimum", "maximum"), ("start", "end")):
        if low in values and high in values and values[low] > values[high]:
            raise ValueError("区间下限不能高于上限")
    return values


class CategoryRing(QWidget):
    """按支出金额占比绘制圆环。"""

    def __init__(self, amounts):
        super().__init__()
        self.amounts = amounts
        self.setFixedSize(88, 88)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eee9df"))
        painter.drawEllipse(3, 3, 82, 82)
        total = sum(self.amounts)
        running = previous = 0
        for index, amount in enumerate(self.amounts):
            if not total:
                break
            running += amount
            angle = round(running * 5760 / total)
            painter.setBrush(QColor(PALETTE[index % len(PALETTE)]))
            painter.drawPie(3, 3, 82, 82, 1440 - previous, previous - angle)
            previous = angle
        painter.setBrush(QColor("#fffdf7"))
        painter.drawEllipse(20, 20, 48, 48)
        painter.end()


def _category_card(groups):
    card, box = panel()
    box.addWidget(label("分类统计 · 支出占比", "section"))
    row = QVBoxLayout()
    row.addWidget(CategoryRing([values[1] for values in groups.values()]))
    legend = QGridLayout()
    for index, (name, (income, expense)) in enumerate(groups.items()):
        dot = label("●")
        dot.setStyleSheet(f"color: {PALETTE[index % len(PALETTE)]};")
        legend.addWidget(dot, index, 0)
        legend.addWidget(label(name), index, 1)
        amounts = f"支 {money(expense)}" if not income else f"收 {money(income)} · 支 {money(expense)}"
        legend.addWidget(label(amounts, "meta"), index, 2)
    if not groups:
        legend.addWidget(label("暂无有效收支", "muted"))
    row.addLayout(legend)
    row.addStretch()
    box.addLayout(row)
    return card


def _members_card(names, members):
    card, box = panel()
    box.addWidget(label("成员统计", "section"))
    for key, (income, expense) in members.items():
        name = names.get(key, "历史成员")
        box.addWidget(label(f"{name}    收款 {money(income)}    垫付 {money(expense)}"))
    if not members:
        box.addWidget(label("暂无有效收支", "muted"))
    return card


def search_page(ledger, saved, data, action):
    """展示范围内的有效收支和各状态的明细。"""
    page, body = page_heading("查询与统计")
    names = {m["id"]: m["name"] for m in ledger["members"]}
    body.addWidget(book_banner(ledger))
    body.addWidget(filter_panel(names, saved, lambda params: action("search", params)))
    summary = QHBoxLayout()
    summary.addLayout(metrics([("已通过收入", data["income"]), ("已通过支出", data["expense"]),
                               ("筛选结余", data["balance"])], stacked=True), 2)
    summary.addWidget(_category_card(data["categories"]), 3)
    body.addLayout(summary)
    body.addWidget(label("收支明细", "section"))
    for item in data["items"]:
        body.addWidget(record_row(item, lambda key=item["id"]: action("detail", key)))
    if not data["items"]:
        body.addWidget(label("没有符合条件的记录。"))
    body.addLayout(paging(saved, data, lambda params: action("search", params)))
    body.addWidget(_members_card(names, data["members"]))
    body.addWidget(button("返回账本", lambda: action("back", None), "nav"))
    body.addStretch()
    return page


def paging(params, data, load):
    """每页 30 条。"""
    row = QHBoxLayout()
    offset = params.get("offset", 0)
    if offset:
        row.addWidget(button("上一页", lambda: load({**params, "offset": max(0, offset - 30)})))
    if data["next_offset"] is not None:
        row.addWidget(button("下一页", lambda: load({**params, "offset": data["next_offset"]})))
    row.addStretch()
    return row
