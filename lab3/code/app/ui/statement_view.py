"""结算清单、线下付款登记及退出前核对。"""

from datetime import datetime
from uuid import uuid4

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QCheckBox, QDateTimeEdit, QGridLayout, QHBoxLayout

from app.ui.forms import FormDialog, budget_cents, field
from app.ui.notebook import avatar, book_banner, page_section
from app.ui.report_view import combo, money
from app.ui.theme import button, label, page_heading, panel


def remaining_text(value):
    """从成员的角度显示应收或应补。"""
    if value == 0:
        return "已结清"
    return ("待收 " if value > 0 else "待补 ") + money(abs(value))


def _member_card(member):
    remaining = member["remaining"]
    style = "mint" if remaining > 0 else "rose" if remaining < 0 else "lavender"
    card, box = panel(style, 13)
    suffix = " · 已退出" if member["status"] == "exited" else ""
    box.addWidget(avatar(member))
    box.addWidget(label(member["name"] + suffix, "cardTitle"))
    box.addWidget(label(f"垫付 {money(member['advanced'])}　分摊 {money(member['share'])}"))
    if member["paid"] or member["received"]:
        box.addWidget(label(f"线下已付 {money(member['paid'])}　已收 {money(member['received'])}",
                            "muted"))
    box.addWidget(label(remaining_text(remaining), "cardTitle"))
    return card


def statement_page(ledger, data, action):
    """成员卡片和线下结清时间线。"""
    page, body = page_heading("结算清单")
    body.addWidget(book_banner(ledger))
    hero, info = panel("sky")
    info.addWidget(label(f"活动结余  {money(data['balance'])}", "section"))
    body.addWidget(hero)
    body.addWidget(page_section("section-balances", "成员结算"))
    grid = QGridLayout()
    for index, member in enumerate(data["members"].values()):
        grid.addWidget(_member_card(member), index // 3, index % 3)
    body.addLayout(grid)
    controls = QHBoxLayout()
    if ledger["role"] == "owner" and not ledger["archived"]:
        save = button("登记线下结清", lambda: action("record"), "primary")
        save.setEnabled(any(m["remaining"] for m in data["members"].values()))
        controls.addWidget(save)
    controls.addWidget(button("导出个人账单", lambda: action("export")))
    controls.addStretch()
    body.addLayout(controls)
    body.addWidget(label("线下结清记录", "section"))
    names = {key: m["name"] for key, m in data["members"].items()}
    for payment in data["payments"]:
        body.addWidget(_payment_card(payment, names))
    if not data["payments"]:
        body.addWidget(label("暂无线下结清记录。", "muted"))
    body.addWidget(button("返回账本", lambda: action("back"), "nav"))
    body.addStretch()
    return page


def _payment_card(payment, names):
    when = datetime.fromisoformat(payment.get("paid_at") or payment["created_at"]).astimezone()
    card, box = panel(margin=12)
    box.addWidget(label(f"{names[payment['payer']]} → {names[payment['payee']]}　"
                        f"{money(payment['amount'])}", "cardTitle"))
    box.addWidget(label(f"{when:%Y-%m-%d %H:%M}　经办人 {names[payment['actor']]}", "muted"))
    return card


def payment_form(parent, members):
    """先选择付款、收款成员，再输入实际线下支付额。"""
    def parties(sign):
        return [(key, f"{m['name']} · {money(abs(m['remaining']))}")
                for key, m in members.items() if m["remaining"] * sign > 0]

    payer = combo("选择付款人", parties(-1))
    payee = combo("选择收款人", parties(1))
    amount = field("按元填写", "paymentAmount", 24)

    backdate = QCheckBox("补记实际付款时间")
    moment = QDateTimeEdit(QDateTime.currentDateTime())
    moment.setCalendarPopup(True)
    moment.setDisplayFormat("yyyy-MM-dd HH:mm")
    moment.setEnabled(False)
    backdate.toggled.connect(moment.setEnabled)  # pylint: disable=no-member

    def collect():
        value = budget_cents(amount.text())
        if not value or not payer.currentData() or not payee.currentData():
            raise ValueError("请选择双方并填写实际结清金额")
        return {"request_id": str(uuid4()), "payer": payer.currentData(),
                "payee": payee.currentData(), "amount": value,
                "paid_at": moment.dateTime().toUTC().toString(Qt.DateFormat.ISODate)
                if backdate.isChecked() else None}

    dialog = FormDialog(parent, "登记线下结清", "保存", collect)
    dialog.fields.addRow("付款人", payer)
    dialog.fields.addRow("收款人", payee)
    dialog.fields.addRow("金额（元）", amount)
    dialog.fields.addRow(backdate)
    dialog.fields.addRow("付款时间", moment)
    result = dialog.payload if dialog.exec() else None
    dialog.deleteLater()
    return result


def exit_page(data, action):
    """展示本人成员权益及待审记录，允许在退出前下载。"""
    page, body = page_heading("退出前核对")
    card, box = panel("sky")
    box.addWidget(label(data["ledger"], "section"))
    box.addWidget(label(f"我的待审核记录　{data['pending']} 笔"))
    box.addWidget(label(remaining_text(data["member"]["remaining"]), "cardTitle"))
    body.addWidget(card)
    body.addWidget(button("导出个人账单", lambda: action("export")))
    if data["pending"]:
        for entry in data["entries"]:
            if (entry["status"] == "pending"
                    and entry["submitter_member_id"] == data["member"]["id"]):
                body.addWidget(label(f"{entry['business_date']}　{entry['purpose']}　"
                                     f"{money(entry['amount_cents'])}"))
        body.addWidget(label("先撤回待审记录或请审核员处理，再退出账本。", "error"))
    else:
        body.addWidget(label("退出后将失去账本访问权，未结算款项继续保留。"))
        body.addWidget(button("确认退出", lambda: action("leave"), "danger"))
    body.addWidget(button("返回账本", lambda: action("back"), "nav"))
    body.addStretch()
    return page
