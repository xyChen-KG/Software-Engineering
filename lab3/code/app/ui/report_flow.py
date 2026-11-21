"""账本报表导航。"""

import json
from urllib.parse import urlencode

from PySide6.QtCore import QIODevice, QSaveFile, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app.ui.entry_flow import EntryScreen
from app.ui.log_view import ACTIONS, log_page
from app.ui.report_view import search_page
from app.ui.statement_view import exit_page, payment_form, statement_page


class ReportFlow:
    """所有报表使用主窗口的认证、错误处理与刷新机制。"""

    def __init__(self, window, workspace, request, entries):
        self.window = window
        self.workspace = workspace
        self.request = request
        self.entries = entries
        self.pending_payment = None

    def read(self, ledger, path, loaded):
        """当前身份退出后关闭旧页面。"""
        def denied(reply):
            if reply.status in {403, 404}:
                self.window.open_ledger(ledger["id"], reply.message, error=True)
                return True
            return False

        self.request("GET", f"/ledgers/{ledger['id']}/{path}", loaded, recover=denied)

    def search(self, ledger, params=None):
        """翻页与查看详情均保留筛选条件。"""
        params = params or {}

        def refresh():
            self.search(ledger, params)

        def action(operation, value):
            if operation == "back":
                self.window.open_ledger(ledger["id"])
            elif operation == "detail":
                self.entries.open_detail(EntryScreen(ledger["id"], return_to=refresh), value)
            else:
                self.search(ledger, value)

        def loaded(data):
            self.workspace.show_entry_page(search_page(ledger, params, data, action))
            self.window.on_refresh(refresh)

        self.read(ledger, "search?" + urlencode(params), loaded)

    def settlement(self, ledger):
        """账务发生变动后从服务端重读成员余额和结清历史。"""
        def loaded(data):
            def action(operation):
                if operation == "back":
                    self.window.open_ledger(ledger["id"])
                elif operation == "export":
                    self.export(ledger)
                else:
                    self._payment(ledger, data["members"])
            self.workspace.show_entry_page(statement_page(ledger, data, action))
            self.window.on_refresh(lambda: self.settlement(ledger))
        self.read(ledger, "settlements", loaded)

    def _payment(self, ledger, members):
        body = payment_form(self.window, members)
        if not body:
            return
        key = (ledger["id"], ledger["member_id"], body["payer"], body["payee"],
               body["amount"], body["paid_at"])
        if self.pending_payment and self.pending_payment[0] == key:
            body["request_id"] = self.pending_payment[1]
        self.pending_payment = (key, body["request_id"])

        def saved(_):
            self.pending_payment = None
            self.settlement(ledger)

        def recover(reply):
            if reply.status == 409:
                self.settlement(ledger)
                self.window.show_notice(reply.message, error=True)
                return True
            if reply.status in {403, 404}:
                self.window.open_ledger(ledger["id"], reply.message, error=True)
                return True
            return False

        self.request("POST", f"/ledgers/{ledger['id']}/settlements", saved,
                     body=body, recover=recover)

    def export(self, ledger):
        """导出服务器当前授权的个人记录，并原子替换用户选定的文件。"""
        def loaded(data):
            path, _ = QFileDialog.getSaveFileName(self.window, "导出个人账单",
                                                  "个人收支与结算.json", "JSON (*.json)")
            if not path:
                return
            target = QSaveFile(path)
            encoded = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            if (not target.open(QIODevice.OpenModeFlag.WriteOnly)
                    or target.write(encoded) != len(encoded) or not target.commit()):
                self.window.show_notice("个人账单保存失败，请检查目标目录。", error=True)
                return
            self.window.show_notice("个人账单已导出。")
        self.read(ledger, "personal-statement", loaded)

    def exit_preview(self, ledger):
        """退出前使用最新的待审记录数量和结算余额。"""
        def action(operation):
            if operation == "export":
                self.export(ledger)
            elif operation == "back":
                self.window.open_ledger(ledger["id"])
            else:
                self.request("POST", f"/ledgers/{ledger['id']}/leave",
                             lambda _: self.window.after_leave())

        def loaded(data):
            self.workspace.show_entry_page(exit_page(data, action))
            self.window.on_refresh(lambda: self.exit_preview(ledger))
        self.read(ledger, "personal-statement", loaded)

    def logs(self, ledger, params=None):
        """提供可分页的审计查询和完整事件内容。"""
        params = params or {}

        def action(operation, value):
            if operation == "back":
                self.window.open_ledger(ledger["id"])
            elif operation == "details":
                dialog = QMessageBox(self.window)
                dialog.setWindowTitle(ACTIONS.get(value["action"], value["action"]))
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setText(json.dumps(value["details"], indent=2, ensure_ascii=False))
                dialog.exec()
                dialog.deleteLater()
            else:
                self.logs(ledger, value)

        def loaded(items):
            self.workspace.show_entry_page(log_page(ledger, params, items, action))
            self.window.on_refresh(lambda: self.logs(ledger, params))
        self.read(ledger, "logs?" + urlencode({**params, "limit": 31}), loaded)
