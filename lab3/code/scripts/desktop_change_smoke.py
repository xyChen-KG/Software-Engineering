"""双桌面窗口验证凭证预览、记账更正、作废与正式汇总。"""

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QLabel

from desktop_entry_smoke import open_ledger
from desktop_smoke import click, dialog_action, expect, fill, find_button, save_window
from smoke_check import running_backend

from app.config import Settings
from app.ui.window import MainWindow


def make_receipt(path: Path, amount: str) -> None:
    """将带测试字样的图片作为本地凭证，不使用实际收据信息。"""
    with Image.new("RGB", (450, 240), "#fffcf0") as image:
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((18, 18, 432, 222), 20, outline="#a97c41", width=4)
        draw.text((48, 54), "SAMPLE RECEIPT", fill="#442b18", font_size=26)
        draw.text((48, 104), f"TOTAL    CNY {amount}", fill="#442b18", font_size=32)
        draw.text((48, 174), "TEST DATA", fill="#775d47", font_size=16)
        image.save(path)


def choose_receipt(window: MainWindow, path: Path) -> None:
    """点击选择入口，自动化只替代原生文件对话框的返回值。"""
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(path)], "")):
        click(window, "选择凭证图片")
    expect(window, path.name)


def check_preview(window: MainWindow, filename: str, output: Path | None) -> None:
    """从 HTTP 返回原图并在 Qt 对话框内解码显示。"""
    timer = QTimer(window)
    result = []

    def check_dialog() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        timer.stop()
        result.append(dialog.windowTitle() == "凭证预览" and any(
            item.pixmap() is not None and not item.pixmap().isNull()
            for item in dialog.findChildren(QLabel)
        ))
        try:
            expect(dialog, filename)
            save_window(dialog, output, "变更_凭证预览")
        finally:
            dialog.accept()

    timer.timeout.connect(check_dialog)  # pylint: disable=no-member
    timer.start(30)
    try:
        click(window, "查看凭证")
        expect(window, filename)
        expect(window, "收支详情")
        # 点击后的请求禁用刷新按钮，直到图片预览完成。
        click(window, "刷新")
        assert result == [True], "凭证预览未显示已解码的图片"
    finally:
        timer.stop()
        timer.deleteLater()


def create_approved(owner: MainWindow, member: MainWindow,
                    image: Path, output: Path | None) -> None:
    """普通成员上传收据登记 240 元，负责人审核。"""
    click(member, "记一笔")
    expect(member, "分摊 · 元")
    for name, value in (
        ("entryAmount", "240"), ("entryCategory", "餐饮"), ("entryPurpose", "水果采购"),
        ("entrySplit0", "120"), ("entrySplit1", "120"),
    ):
        fill(member, name, value)
    choose_receipt(member, image)
    click(member, "提交审核")
    expect(member, "¥240.00  ·  待审核")
    check_preview(member, image.name, output)

    click(owner, "审核中心")
    click(owner, "查看详情")
    dialog_action(owner, "审核通过", "确认")
    expect(owner, "已审核通过")
    click(member, "刷新")
    expect(member, "¥240.00  ·  已通过")


def review_pending(owner: MainWindow, caption: str) -> None:
    """从实际审核队列启动审核，避免依靠直达链接。"""
    click(owner, "变更待审")
    expect(owner, caption)
    click(owner, "查看申请")
    expect(owner, f"{caption}申请 · 待审核")


def correct_expense(owner: MainWindow, member: MainWindow,
                    image: Path, output: Path | None) -> None:
    """提交更正时原账仍有效，通过之后显示新旧凭证。"""
    click(member, "申请更正")
    expect(member, "申请更正")
    for name, value in (
        ("entryAmount", "260"), ("entrySplit0", "130"), ("entrySplit1", "130"),
        ("changeReason", "按照补拍的收据修改金额"),
    ):
        fill(member, name, value)
    keep = member.findChild(QCheckBox)
    assert keep is not None and keep.isChecked()
    keep.setChecked(False)
    choose_receipt(member, image)
    click(member, "提交更正申请")
    expect(member, "更正申请已提交")
    expect(member, "¥240.00  ·  已通过")
    expect(member, "拟改为 ¥260.00")
    assert find_button(member, "通过申请") is None
    click(member, "返回收支列表")
    expect(member, "正式支出\n¥240.00")

    click(owner, "返回审核中心")
    review_pending(owner, "更正")
    save_window(owner, output, "变更_审核前")
    dialog_action(owner, "通过申请", "确认")
    expect(owner, "¥260.00  ·  已通过")
    expect(owner, "版本 1 凭证")
    expect(owner, image.name)
    click(member, "刷新")
    expect(member, "正式支出\n¥260.00")
    click(member, "查看详情")
    expect(member, "内容版本 2")


def void_expense(owner: MainWindow, member: MainWindow, output: Path | None) -> None:
    """作废正式账目保留了凭证和历史，汇总归零。"""
    dialog_action(member, "申请作废", "确认提交", (("entryActionReason", "活动已取消"),))
    expect(member, "作废申请已提交")
    expect(member, "¥260.00  ·  已通过")
    click(owner, "返回变更待审")
    expect(owner, "作废")
    click(owner, "查看申请")
    expect(owner, "作废申请 · 待审核")
    dialog_action(owner, "通过申请", "确认")
    expect(owner, "¥260.00  ·  已作废")
    expect(owner, "版本 1 凭证")
    click(member, "返回收支列表")
    expect(member, "正式支出\n¥0.00")
    save_window(member, output, "变更_作废后")


def main() -> None:
    """实际运行临时后端及两个离屏桌面窗口。"""
    parser = argparse.ArgumentParser(description="检查桌面凭证与变更申请")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QApplication([])
    with TemporaryDirectory() as folder, running_backend() as (url, _):
        original = Path(folder) / "原收据.png"
        correction = Path(folder) / "补拍收据.jpg"
        make_receipt(original, "240.00")
        make_receipt(correction, "260.00")
        owner, member = open_ledger(Settings(data_dir=Path("."), api_url=url))
        owner.resize(420, 840)
        member.resize(420, 840)
        create_approved(owner, member, original, args.screenshots)
        correct_expense(owner, member, correction, args.screenshots)
        void_expense(owner, member, args.screenshots)
        owner.close()
        member.close()
    application.processEvents()
    print("PASS：两窗口上传和预览凭证，更正、作废审核，正式支出 240 → 260 → 0 元。")


if __name__ == "__main__":
    main()
