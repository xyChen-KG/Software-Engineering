"""通过真实桌面控件和临时 HTTP 服务检查两个账号的操作流程。"""

import argparse
import os
import secrets
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPushButton, QScrollArea

from api_smoke import LocalAPI
from smoke_check import running_backend

from app.config import Settings
from app.ui.window import MainWindow


def wait_for(find, description: str):
    """等 Qt 异步请求和页面渲染完成，不把固定延迟当作成功。"""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = find()
        if result:
            return result
        QTest.qWait(15)
    raise RuntimeError(f"窗口未进入预期状态：{description}")


def find_button(window, text: str):
    """只匹配当前可操作的按钮。"""
    return next((item for item in window.findChildren(QPushButton)
                 if item.text() == text and item.isVisible() and item.isEnabled()), None)


def reveal(widget):
    """操作前滚动到对应控件，包含嵌套滚动区的情况。"""
    parent = widget.parentWidget()
    while parent:
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(widget, 3, 5)
            widget = parent
        parent = parent.parentWidget()


def click(window, text: str) -> None:
    """像用户一样使用按钮，触发 Qt 的实际点击事件。"""
    target = wait_for(lambda: find_button(window, text), text)
    reveal(target)
    QTest.mouseClick(target, Qt.MouseButton.LeftButton)


def expect(window, text: str) -> None:
    """验证文本确实显示在当前窗口，而不是隐藏页面。"""
    wait_for(lambda: any(text in item.text() for item in window.findChildren(QLabel)
                         if item.isVisible()), text)


def fill(window, name: str, value: str) -> None:
    """输入表单字段。"""
    field = window.findChild(QLineEdit, name)
    if field is None or not field.isVisible():
        raise RuntimeError(f"表单字段未显示：{name}")
    reveal(field)
    field.setText(value)


def dialog_action(window, caption, submit, values=(), *, role=None):
    """由定时器操作实际打开的模态表单或确认窗口。"""
    errors = []

    def enter() -> None:
        dialog = QApplication.activeModalWidget()
        try:
            if dialog is None:
                raise RuntimeError("没有打开模态对话框")
            for field, value in values:
                fill(dialog, field, value)
            if role is not None:
                combo = dialog.findChild(QComboBox)
                if combo is None:
                    raise RuntimeError("角色选择控件未显示")
                combo.setCurrentIndex(combo.findData(role))
            click(dialog, submit)
        except (RuntimeError, TypeError) as error:
            errors.append(error)
            if dialog is not None:
                dialog.reject()

    target = wait_for(lambda: find_button(window, caption), caption)
    reveal(target)
    QTimer.singleShot(20, enter)
    QTest.mouseClick(target, Qt.MouseButton.LeftButton)
    if errors:
        raise errors[0]


def sign_in(window, username, name, password):
    """在窗口注册，再用刚注册的账号登录。"""
    click(window, "创建账号")
    fill(window, "username", username)
    fill(window, "displayName", name)
    fill(window, "password", password)
    click(window, "注册")
    expect(window, "账号已创建")
    fill(window, "password", password)
    click(window, "登录")
    expect(window, "我的账本")


def create_book(window, name, budget=""):
    """填写账本列表中的创建表单。"""
    click(window, "＋ 新建账本")
    fill(window, "ledgerName", name)
    fill(window, "ledgerBudget", budget)
    click(window, "创建账本")


def run_flow(settings: Settings, output: Path | None) -> None:
    """验证邀请加入、账本权限、退出及服务器撤销会话。"""
    owner = MainWindow(settings)
    member = MainWindow(settings)
    owner.show()
    password = "course-" + secrets.token_urlsafe(24)
    expect(owner, "记账服务已连接")
    save_window(owner, output, "登录")
    sign_in(owner, "desktop_owner", "账本负责人", password)
    create_book(owner, "秋日野餐", "1000.05")
    expect(owner, "账本已创建")
    expect(owner, "¥1000.05")
    dialog_action(owner, "账本设置", "保存", (
        ("ledgerName", "秋日野餐"), ("ledgerBudget", "1200.25"),
    ))
    expect(owner, "¥1200.25")
    dialog_action(owner, "归档账本", "确认")
    expect(owner, "归档账本成功")
    assert find_button(owner, "邀请成员") is None
    dialog_action(owner, "恢复账本", "确认")
    expect(owner, "恢复账本成功")

    open_member_window(member, password)
    click(owner, "成员管理")
    dialog_action(owner, "邀请成员", "发送邀请", (("inviteUsername", "desktop_member"),))
    expect(owner, "邀请已发出")
    click(member, "收到的邀请")
    expect(member, "账本负责人 邀请你成为普通成员")
    save_window(member, output, "收到邀请")
    click(member, "接受邀请")
    expect(member, "已加入账本")
    expect(member, "账本成员 · 2 人")
    assert find_button(member, "账本设置") is None

    click(owner, "刷新")
    expect(owner, "小林")
    save_window(owner, output, "账本成员")
    check_membership(owner, member)
    click(owner, "退出账本")
    click(owner, "确认退出")
    expect(owner, "已退出账本")
    expect(owner, "还没有账本")
    click(owner, "我的")
    click(owner, "退出登录")
    expect(owner, "已退出登录")
    assert find_button(owner, "登录") is not None

    # 用服务器撤销当前会话，验证客户端在收到 401 时清理账本页面。
    api = LocalAPI(settings.api_url)
    api.request("POST", "/auth/logout", token=member._token, expected=204)  # pylint: disable=protected-access
    click(member, "刷新")
    expect(member, "会话已失效")
    assert find_button(member, "登录") is not None
    assert find_button(member, "账本设置") is None
    owner.close()
    member.close()
    print("PASS：两窗口注册登录、账本设置、邀请、角色调整、移除、转交、退出及会话失效。")


def open_member_window(member, password):
    """按较窄的手机宽度检查成员窗口。"""
    member.resize(360, 720)
    member.show()
    sign_in(member, "desktop_member", "小林", password)


def check_membership(owner: MainWindow, member: MainWindow) -> None:
    """同一会话在刷新后看到最新角色，失去权限后离开原账本页面。"""
    dialog_action(owner, "调整角色", "保存", (("permissionReason", "负责活动复核"),), role="reviewer")
    expect(owner, "成员角色已更新")
    click(member, "刷新")
    expect(member, "审核员")
    assert find_button(member, "账本设置") is None

    dialog_action(owner, "移除", "确认")
    expect(owner, "成员已移除")
    click(member, "刷新")
    expect(member, "不是该账本的在册成员")
    expect(member, "还没有账本")
    assert find_button(member, "退出账本") is None

    dialog_action(owner, "邀请成员", "发送邀请", (("inviteUsername", "desktop_member"),))
    expect(owner, "邀请已发出")
    click(member, "收到的邀请")
    click(member, "接受邀请")
    expect(member, "已加入账本")
    click(owner, "刷新")
    dialog_action(owner, "转交负责人", "确认提交", (("entryActionReason", "社团换届"),))
    expect(owner, "负责人已转交")
    assert find_button(owner, "账本设置") is None
    click(member, "刷新")
    wait_for(lambda: find_button(member, "账本设置"), "新负责人管理按钮")


def save_window(window: MainWindow, output: Path | None, name: str) -> None:
    """只保存临时测试账号的窗口状态。"""
    areas = [area for area in window.findChildren(QScrollArea) if area.isVisible()]
    for area in areas:
        assert area.horizontalScrollBar().maximum() == 0, f"{name} 超出可用宽度"
    if output is not None:
        if isinstance(window, MainWindow):
            original = window.size()
            for width in (360, 420, 480):
                window.resize(width, 840)
                QApplication.processEvents()
                for area in areas:
                    assert area.horizontalScrollBar().maximum() == 0, f"{name} 在 {width} 宽度溢出"
            window.resize(420, 840)
            QApplication.processEvents()
        for area in areas:
            area.verticalScrollBar().setValue(0)
        output.mkdir(parents=True, exist_ok=True)
        # 等标题的入场笔画绘完，使留存的验证材料呈现最终画面。
        QTest.qWait(260)
        if not window.grab().save(str(output / f"{name}.png")):
            raise RuntimeError("无法保存桌面检查截图")
        if isinstance(window, MainWindow):
            window.resize(original)


def main() -> None:
    """使用离屏 Qt，结束后回收临时进程及数据库。"""
    parser = argparse.ArgumentParser(description="桌面账本管理流程检查")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication([])
    with running_backend() as (api_url, _):
        run_flow(Settings(data_dir=Path("."), api_url=api_url), args.screenshots)
    application.processEvents()


if __name__ == "__main__":
    main()
