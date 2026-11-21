"""用真实桌面窗口验证退款来源、组合查询、线下结清与退出前导出。"""

import argparse
import json
import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QLineEdit, QPushButton

from desktop_change_smoke import create_approved, make_receipt
from desktop_entry_smoke import open_ledger
from desktop_smoke import click, create_book, dialog_action, expect, fill, save_window, wait_for
from smoke_check import running_backend

from app.config import Settings


def home(window):
    """从当前页面通过账本列表重新读取最新账本。"""
    click(window, "我的账本")
    click(window, "打开账本")
    expect(window, "预算即将用完")


def check_global_alerts(owner, member, output):
    """另一账号调整预算时，在账本以外收到提醒并保留正在填写的账本名。"""
    home(owner)
    click(owner, "成员管理")
    dialog_action(owner, "调整角色", "保存", (("permissionReason", "本周轮值复核"),), role="reviewer")
    expect(owner, "成员角色已更新")
    click(owner, "返回账本")
    click(owner, "操作日志")
    expect(owner, "本周轮值复核")
    save_window(owner, output, "角色调整日志")
    click(owner, "我的账本")
    click(owner, "预算提醒 · 1")
    inbox = wait_for(QApplication.activeModalWidget, "负责人预算提醒")
    expect(inbox, "预算已使用 80%")
    click(inbox, "标为已读")
    expect(inbox, "暂无未读预算提醒")
    click(inbox, "关闭")

    click(member, "我的账本")
    click(member, "＋ 新建账本")
    fill(member, "ledgerName", "尚未提交的草稿")
    click(owner, "打开账本")
    dialog_action(owner, "账本设置", "保存", (("ledgerBudget", "200"),))
    click(member, "预算提醒 · 1")
    inbox = wait_for(QApplication.activeModalWidget, "审核员预算提醒")
    expect(inbox, "已超预算 · 超出 ¥40.00")
    save_window(inbox, output, "审核员超支提醒")
    click(inbox, "标为已读")
    expect(inbox, "暂无未读预算提醒")
    click(inbox, "关闭")
    assert member.findChild(QLineEdit, "ledgerName").text() == "尚未提交的草稿"
    dialog_action(owner, "账本设置", "保存", (("ledgerBudget", "300"),))
    expect(owner, "预算即将用完")
    click(member, "打开账本")
    click(member, "水果采购")
    expect(member, "收支详情")


def refund(owner, member, output):
    """在已有支出上登记退款并由另一位成员审核。"""
    click(member, "登记退款")
    expect(member, "原支出  水果采购")
    fill(member, "entryAmount", "20")
    fill(member, "entryPurpose", "退回水果押金")
    click(member, "提交审核")
    expect(member, "退款关联原支出")
    home(owner)
    save_window(owner, output, "综合_预算概况")
    click(owner, "审核中心")
    expect(owner, "审核意见（驳回时必填）")
    expect(owner, "退回水果押金")
    dialog_action(owner, "审核通过", "确认")
    expect(owner, "已审核通过")


def search(owner, output):
    """用类别、关键词、金额和日期筛选，核对过滤前后的结余。"""
    home(owner)
    click(owner, "查询与统计")
    expect(owner, "筛选结余")
    expect(owner, "−¥220.00")
    save_window(owner, output, "综合_查询统计")
    click(owner, "更多筛选")
    for key, value in (("keyword", "采购"), ("category", "餐饮"), ("minimum", "240"),
                       ("maximum", "240"), ("start", date.today().isoformat())):
        fill(owner, key, value)
    click(owner, "查询")
    expect(owner, "−¥240.00")
    click(owner, "清空条件")
    expect(owner, "−¥220.00")


def settle(owner, output):
    """模拟线下付 50 元，结算应由 120 元减至 70 元。"""
    click(owner, "返回账本")
    click(owner, "结算清单")
    expect(owner, "待补 ¥120.00")
    errors = []

    def enter():
        dialog = QApplication.activeModalWidget()
        try:
            choices = dialog.findChildren(QComboBox)
            assert len(choices) == 2
            for choice in choices:
                choice.setCurrentIndex(1)
            fill(dialog, "paymentAmount", "50")
            click(dialog, "保存")
        except (AssertionError, RuntimeError) as exc:
            errors.append(exc)
            dialog.reject()

    QTimer.singleShot(50, enter)
    click(owner, "登记线下结清")
    if errors:
        raise errors[0]
    expect(owner, "待收 ¥70.00")
    expect(owner, "待补 ¥70.00")
    expect(owner, "活动结余  −¥220.00")
    save_window(owner, output, "综合_结算清单")


def export_and_leave(member, path, output):
    """核对导出的个人金额与付款编号，并在未结清时退出。"""
    home(member)
    click(member, "退出账本")
    expect(member, "待收 ¥70.00")
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
        click(member, "导出个人账单")
        wait_for(path.exists, "导出的个人 JSON 文件")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["member"]["remaining"] == 7000 and data["payments"][0]["amount"] == 5000
    assert len(data["entries"]) == 2
    save_window(member, output, "综合_退出核对")
    click(member, "确认退出")
    expect(member, "已退出账本")
    return data["payments"][0]["id"]


def check_logs(owner, payment_id, output):
    """按对象与日期筛选，退出后仍可看到该成员的欠款和日志。"""
    click(owner, "刷新")
    expect(owner, "已退出")
    expect(owner, "待收 ¥70.00")
    click(owner, "返回账本")
    click(owner, "操作日志")
    expect(owner, "成员退出")
    save_window(owner, output, "日志时间线")
    fill(owner, "objectId", payment_id)
    fill(owner, "logStart", date.today().isoformat())
    fill(owner, "logEnd", date.today().isoformat())
    click(owner, "查询日志")
    expect(owner, "线下结清")
    # 等待服务端回包及新页替换，此时只保留线下结清事件。
    wait_for(lambda: sum(b.isVisible() and b.text() == "查看操作内容"
                         for b in owner.findChildren(QPushButton)) == 1, "筛选后的单个事件")
    expect(owner, "小周 → 小林　¥50.00")
    save_window(owner, output, "综合_操作日志")


def budget_overrun(owner, output):
    """负责人收紧预算，再登记一笔预计超支的支出。"""
    home(owner)
    dialog_action(owner, "账本设置", "保存", (("ledgerBudget", "200"),))
    expect(owner, "已超预算 · 超出 ¥40.00")
    click(owner, "记一笔")
    expect(owner, "分摊 · 元")
    for name, value in (("entryAmount", "10"), ("entryPurpose", "补买纸杯"),
                        ("entryCategory", "用品"), ("noReceiptReason", "摊位未开票")):
        fill(owner, name, value)
    click(owner, "按列表成员均分")
    expect(owner, "预计超过预算 ¥50.00")
    click(owner, "提交审核")
    expect(owner, "请填写超支原因后提交")
    fill(owner, "budgetReason", "临时增加参加人数")
    click(owner, "提交审核")
    expect(owner, "超支原因  临时增加参加人数")
    click(owner, "我的账本")
    click(owner, "打开账本")
    expect(owner, "正式支出 ¥240.00")
    expect(owner, "待审预计支出")
    expect(owner, "¥10.00")
    expect(owner, "已超预算 · 超出 ¥40.00")
    save_window(owner, output, "综合_预算超支")


def capture_books(window, output):
    """用同一临时用户的两个真实账本检查窄屏双列封面及创建区域。"""
    click(window, "我的账本")
    create_book(window, "社团迎新")
    expect(window, "账本已创建")
    click(window, "我的账本")
    expect(window, "秋日野餐")
    click(window, "＋ 新建账本")
    save_window(window, output, "账本列表")


def main():
    """独立临时数据库，测试账号与示例收据均不进入实际账本。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication([])
    with TemporaryDirectory() as folder, running_backend() as (url, _):
        owner, member = open_ledger(Settings(data_dir=Path("."), api_url=url))
        owner.resize(420, 840)
        member.resize(420, 840)
        dialog_action(owner, "账本设置", "保存", (("ledgerBudget", "300"),))
        expect(owner, "¥300.00")
        image = Path(folder) / "收据.png"
        make_receipt(image, "240.00")
        create_approved(owner, member, image, None)
        check_global_alerts(owner, member, args.screenshots)
        refund(owner, member, args.screenshots)
        search(owner, args.screenshots)
        settle(owner, args.screenshots)
        payment_id = export_and_leave(member, Path(folder) / "个人账单.json", args.screenshots)
        check_logs(owner, payment_id, args.screenshots)
        budget_overrun(owner, args.screenshots)
        capture_books(owner, args.screenshots)
        owner.close()
        member.close()
    app.processEvents()
    print("PASS：双窗口退款、组合查询、预算、部分结清、导出、退出留账与日志筛选。")


if __name__ == "__main__":
    main()
