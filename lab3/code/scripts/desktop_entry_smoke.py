"""从两个真实桌面窗口完成收支登记、复核和正式汇总。"""

import argparse
import os
import secrets
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from desktop_smoke import (
    click, create_book, dialog_action, expect, fill, find_button, save_window, sign_in,
)
from smoke_check import running_backend

from app.config import Settings
from app.ui.window import MainWindow


def open_ledger(settings: Settings) -> tuple[MainWindow, MainWindow]:
    """建立临时负责人和普通成员，并加入同一个账本。"""
    owner = MainWindow(settings)
    member = MainWindow(settings)
    owner.show()
    password = "ledger-" + secrets.token_urlsafe(24)
    sign_in(owner, "entry_owner", "小周", password)
    create_book(owner, "秋日野餐")
    expect(owner, "账本已创建")

    member.show()
    sign_in(member, "entry_member", "小林", password)
    click(owner, "成员管理")
    dialog_action(owner, "邀请成员", "发送邀请", (("inviteUsername", "entry_member"),))
    expect(owner, "邀请已发出")
    click(member, "收到的邀请")
    click(member, "接受邀请")
    expect(member, "已加入账本")
    click(owner, "返回账本")
    expect(owner, "账本概况")
    return owner, member


def enter_expense(member: MainWindow, output: Path | None) -> None:
    """验证非法分摊不能提交，补齐后生成待审核记录。"""
    member.resize(420, 840)
    click(member, "记一笔")
    expect(member, "分摊 · 元")
    for name, value in (
        ("entryAmount", "240.00"), ("entryCategory", "餐饮"), ("entryPurpose", "水果采购"),
        ("noReceiptReason", "现场现金采购"), ("entrySplit0", "120.00"),
    ):
        fill(member, name, value)
    click(member, "提交审核")
    expect(member, "分摊合计必须等于支出金额")
    fill(member, "entrySplit1", "120.00")
    save_window(member, output, "登记")
    click(member, "提交审核")
    expect(member, "收支详情")
    expect(member, "¥240.00  ·  待审核")
    assert find_button(member, "审核通过") is None


def review_expense(owner: MainWindow, member: MainWindow, output: Path | None) -> None:
    """确认旧审核页不能覆盖新版，驳回后可在原记录上重提。"""
    click(owner, "审核中心")
    expect(owner, "水果采购")
    save_window(owner, output, "审核中心")
    click(owner, "查看详情")
    expect(owner, "收支详情")
    save_window(owner, output, "审核详情")

    click(member, "修改记录")
    expect(member, "修改收支")
    fill(member, "entryPurpose", "水果和果汁")
    click(member, "提交审核")
    expect(member, "水果和果汁")
    dialog_action(owner, "审核通过", "确认")
    expect(owner, "账目状态或版本已变化")
    expect(owner, "水果和果汁")

    click(owner, "返回审核中心")
    expect(owner, "审核意见（驳回时必填）")
    click(owner, "驳回")
    expect(owner, "请填写驳回原因。")
    fill(owner, "inlineReviewReason", "请补用途说明")
    click(owner, "驳回")
    expect(owner, "记录已驳回")
    click(member, "刷新")
    expect(member, "请补用途说明")
    click(member, "修改并重提")
    expect(member, "分摊 · 元")
    fill(member, "entryPurpose", "野餐水果和果汁")
    click(member, "提交审核")
    expect(member, "内容版本 3")
    click(owner, "刷新")
    expect(owner, "内容版本 3")
    dialog_action(owner, "审核通过", "确认")
    expect(owner, "已审核通过。")

    click(member, "返回收支列表")
    expect(member, "正式支出\n¥240.00")
    save_window(member, output, "正式汇总")


def withdraw_expense(member: MainWindow) -> None:
    """确认相似支出可以登记，撤回后正式汇总仍为 240 元。"""
    click(member, "记一笔")
    expect(member, "分摊 · 元")
    for name, value in (
        ("entryAmount", "240"), ("entryCategory", "餐饮"), ("entryPurpose", "临时采购"),
        ("noReceiptReason", "现场现金采购"), ("entrySplit0", "240"),
    ):
        fill(member, name, value)
    confirm_similar_expense(member)
    expect(member, "临时采购")
    dialog_action(member, "撤回", "确认提交", (("entryActionReason", "重复录入"),))
    expect(member, "记录已撤回")
    click(member, "返回收支列表")
    expect(member, "正式支出\n¥240.00")


def confirm_similar_expense(member: MainWindow) -> None:
    """提交后由服务器决定是否显示相似支出确认框。"""
    timer = QTimer(member)
    handled = []

    def answer() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        timer.stop()
        if dialog.windowTitle() != "确认相似支出":
            dialog.reject()
            return
        fill(dialog, "entryActionReason", "同一活动的第二次采购")
        click(dialog, "确认提交")
        handled.append(True)

    timer.timeout.connect(answer)  # pylint: disable=no-member
    timer.start(30)
    try:
        click(member, "提交审核")
        expect(member, "已保存，等待审核")
        assert handled
    finally:
        timer.stop()
        timer.deleteLater()


def main() -> None:
    """以离屏窗口运行，验证数据仅保存在临时库。"""
    parser = argparse.ArgumentParser(description="检查桌面收支登记与审核")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication([])
    with running_backend() as (url, _):
        owner, member = open_ledger(Settings(data_dir=Path("."), api_url=url))
        enter_expense(member, args.screenshots)
        review_expense(owner, member, args.screenshots)
        withdraw_expense(member)
        owner.close()
        member.close()
    application.processEvents()
    print("PASS：两窗口登记、版本冲突、驳回重提、审核、相似支出确认、撤回与 240 元正式汇总。")


if __name__ == "__main__":
    main()
