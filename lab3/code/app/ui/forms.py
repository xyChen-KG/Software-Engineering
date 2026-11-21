"""注册登录和账本管理的输入控件。"""

import re
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget,
)

from app.models.validation import MAX_INTEGER
from app.ui.paper import Illustration
from app.ui.theme import button, label, panel, scroll_area

STATUSES = {
    "pending": "待审核", "approved": "已通过", "rejected": "已驳回",
    "withdrawn": "已撤回", "voided": "已作废",
}
KINDS = {"income": "收入", "expense": "支出"}
ROLES = {"owner": "负责人", "reviewer": "审核员", "member": "普通成员"}


def budget_cents(value: str) -> int | None:
    """将用户填写的元精确换成分，允许未设置和零预算。"""
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"[0-9]{1,17}(?:\.[0-9]{1,2})?", value) is None:
        raise ValueError("预算须为非负金额，最多保留两位小数。")
    yuan, _, fraction = value.partition(".")
    cents = int(yuan) * 100 + int(fraction.ljust(2, "0"))
    if cents > MAX_INTEGER:
        raise ValueError("预算超出金额范围。")
    return cents


def amount_text(cents: int | None) -> str:
    """为表单生成不会丢失精度的金额字符串。"""
    if cents is None:
        return ""
    yuan, fraction = divmod(cents, 100)
    return f"{yuan}.{fraction:02d}"


def field(hint: str, name: str, maximum: int) -> QLineEdit:
    """明确字段提示、长度及交互测试标识。"""
    widget = QLineEdit()
    widget.setPlaceholderText(hint)
    widget.setObjectName(name)
    widget.setMaxLength(maximum)
    return widget


class AuthPage(QWidget):
    """同一表单切换登录或注册，密码仅在本次提交时读取。"""

    submitted = Signal(str, dict)

    def __init__(self) -> None:
        super().__init__()
        self.registering = False
        self.username = field("3–32 位字母、数字或下划线", "username", 32)
        self.password = field("输入密码", "password", 128)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.display_name = field("账本成员看到的名字", "displayName", 100)
        self.title = label("登录", "title")
        self.hint = label("输入账号和密码，打开你的账本。", "muted")
        self.submit = button("登录", self._submit, "ink")
        self.switch = button("创建账号", self._switch)
        self.password.returnPressed.connect(self._submit)  # pylint: disable=no-member

        form, layout = panel(margin=18)
        layout.setSpacing(12)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        self.fields = QFormLayout()
        self.fields.setSpacing(14)
        self.fields.addRow("用户名", self.username)
        self.fields.addRow("显示姓名", self.display_name)
        self.fields.addRow("密码", self.password)
        layout.addLayout(self.fields)
        layout.addWidget(self.submit)
        layout.addWidget(self.switch)
        self.form = form
        content = QWidget()
        content.setObjectName("scrollBody")
        column = QVBoxLayout(content)
        column.setContentsMargins(14, 16, 14, 12)
        column.setSpacing(18)
        column.addWidget(Illustration("head-books", 150, "我的账本"))
        column.addWidget(form)
        column.addStretch()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area(content))
        self.reset()

    def reset(self, *, username: str = "") -> None:
        """清空之前的表单状态，回到登录模式。"""
        self.registering = False
        self.username.setText(username)
        self.password.clear()
        self.display_name.clear()
        self._render()

    def _switch(self) -> None:
        self.registering = not self.registering
        self.password.clear()
        self._render()

    def _render(self) -> None:
        self.fields.setRowVisible(self.display_name, self.registering)
        self.title.setText("创建账号" if self.registering else "登录")
        self.hint.setText("设置登录密码，至少 15 个字符。" if self.registering else
                          "输入账号和密码，打开你的账本。")
        self.submit.setText("注册" if self.registering else "登录")
        self.switch.setText("返回登录" if self.registering else "创建账号")

    def _submit(self) -> None:
        if not self.form.isEnabled():
            return
        body = {"username": self.username.text().strip(), "password": self.password.text()}
        if self.registering:
            body["name"] = self.display_name.text().strip()
        self.password.clear()
        self.submitted.emit("register" if self.registering else "login", body)


class FormDialog(QDialog):
    """可在打开的对话框内修正本地输入，无需重复填写。"""

    def __init__(self, parent: QWidget, title: str, submit: str, collect: Callable) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(min(360, parent.width() - 20))
        self.collect = collect
        self.payload = None
        self.fields = QFormLayout()
        self.error = label(style="error")
        self.error.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label(title, "section"))
        layout.addLayout(self.fields)
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = button("取消", self.reject)
        confirm = button(submit, self._submit, "primary")
        confirm.setDefault(True)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        layout.addLayout(actions)

    def _submit(self) -> None:
        try:
            self.payload = self.collect()
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
        else:
            self.accept()


def ledger_fields(form, data):
    """创建页与设置对话框共用同一套输入和金额校验。"""
    name = field("例如 秋日野餐", "ledgerName", 100)
    name.setText(data.get("name", ""))
    budget = field("可留空，按元填写", "ledgerBudget", 24)
    budget.setText(amount_text(data.get("budget_cents")))
    form.addRow("账本名称", name)
    form.addRow("预算（元）", budget)

    def collect():
        title = name.text().strip()
        if not title:
            raise ValueError("请输入账本名称。")
        return {"name": title, "budget_cents": budget_cents(budget.text())}

    return collect


def ledger_form(parent: QWidget, ledger: dict) -> dict | None:
    """编辑已有账本。"""
    form = QFormLayout()
    collect = ledger_fields(form, ledger)
    dialog = FormDialog(parent, "账本设置", "保存", collect)
    dialog.fields.addRow(form)
    result = dialog.payload if dialog.exec() else None
    dialog.deleteLater()
    return result


def role_form(parent: QWidget, *, current: str | None = None) -> dict | None:
    """邀请使用对方已注册的用户名，角色只能选普通成员或审核员。"""
    username = field("对方注册时填写的用户名", "inviteUsername", 32)
    reason = field("填写调整原因（最多 500 字）", "permissionReason", 500) if current else None
    role = QComboBox()
    for value in ("member", "reviewer"):
        role.addItem(ROLES[value], value)
    if current:
        role.setCurrentIndex(role.findData(current))

    def collect() -> dict:
        payload = {"role": role.currentData()}
        if current is None:
            value = username.text().strip()
            if re.fullmatch(r"[a-zA-Z0-9_]{3,32}", value) is None:
                raise ValueError("请输入有效的用户名。")
            payload["username"] = value
        if reason is not None:
            payload["reason"] = reason.text().strip()
            if not payload["reason"]:
                raise ValueError("请填写调整角色的原因。")
        return payload

    dialog = FormDialog(parent, "调整角色" if current else "邀请成员", "保存" if current else
                        "发送邀请", collect)
    if current is None:
        dialog.fields.addRow("对方用户名", username)
        username.setFocus()
    dialog.fields.addRow("成员角色", role)
    if reason is not None:
        dialog.fields.addRow("变更原因", reason)
    result = dialog.payload if dialog.exec() else None
    dialog.deleteLater()
    return result


def confirm_action(parent: QWidget, title: str, message: str) -> bool:
    """使用纯文本显示确认信息。"""
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(message)
    dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
    dialog.button(QMessageBox.StandardButton.Ok).setText("确认")
    dialog.button(QMessageBox.StandardButton.Cancel).setText("取消")
    accepted = dialog.exec() == QMessageBox.StandardButton.Ok
    dialog.deleteLater()
    return accepted
