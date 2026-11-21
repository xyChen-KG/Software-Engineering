"""桌面启动入口，附带自动退出的窗口联通验证模式。"""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.config import Settings
from app.ui.window import MainWindow


def main() -> None:
    """启动桌面程序；smoke 模式只在服务联通且窗口可创建时成功退出。"""
    parser = argparse.ArgumentParser(description="多人共享记账桌面程序")
    parser.add_argument("--smoke-test", action="store_true", help="联通检查后自动退出")
    parser.add_argument("--screenshot", type=Path, help="smoke 模式下保存窗口截图")
    args = parser.parse_args()
    if args.screenshot is not None and not args.smoke_test:
        parser.error("--screenshot 需要同时指定 --smoke-test")

    application = QApplication([sys.argv[0]])
    application.setApplicationName("多人共享记账")
    window = MainWindow(Settings.from_env())

    def finish(success: bool) -> None:
        if args.screenshot is not None:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            success = window.grab().save(str(args.screenshot)) and success
        application.exit(0 if success else 1)

    if args.smoke_test:
        window.connection_checked.connect(
            lambda success: QTimer.singleShot(100, lambda: finish(success))
        )
        QTimer.singleShot(8000, lambda: application.exit(2))
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
