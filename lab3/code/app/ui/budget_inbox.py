"""全局预算提醒。后台查询不切换当前页面，也不会打断正在填写的表单。"""

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from app.ui.forms import amount_text
from app.ui.theme import button, label, panel, scroll_area

PATH = "/me/budget-alerts"


class BudgetInbox(QDialog):
    """提醒按钮始终跟随当前会话，退出后丢弃旧账号的网络回包。"""

    def __init__(self, parent, api):
        super().__init__(parent)
        self.setWindowTitle("预算提醒")
        self.resize(min(390, parent.width() - 16), min(460, parent.height() - 55))
        self.api = api
        self.token = ""
        self.busy = False
        self.pending_read = None
        self.snapshot = None
        self.trigger = button("预算提醒", self.open_inbox)
        self.trigger.setObjectName("budgetInbox")
        self.trigger.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)  # pylint: disable=no-member
        body = QVBoxLayout(self)
        body.addWidget(label("预算提醒", "section"))
        self.listing = scroll_area(QWidget())
        body.addWidget(self.listing)
        body.addWidget(button("关闭", self.accept))

    def session(self, token):
        """切换身份时清空界面及网络状态。"""
        self.close()
        self.timer.stop()
        self.token = token
        self.busy = False
        self.snapshot = None
        self.pending_read = None
        self.listing.setEnabled(True)
        self.listing.setWidget(QWidget())
        self._badge(0)
        self.trigger.setVisible(bool(token))
        if token:
            self.timer.start()
            self.refresh()

    def open_inbox(self):
        """打开时立即核对服务端，后台轮询也继续更新其他窗口产生的变化。"""
        self.open()
        self.refresh()

    def refresh(self):
        """同一会话内只保留一个正在进行的收件箱请求。"""
        if self.token and not self.busy:
            self._send("GET", PATH)

    def _send(self, method, path):
        token = self.token
        self.busy = True

        def finished(reply):
            if token != self.token:
                return
            self.busy = False
            if not reply.ok:
                self.snapshot = None
                self._badge(0)
                self.listing.setWidget(label("暂时无法读取提醒，稍后将自动重试。", "muted"))
            elif method == "POST":
                self.refresh()
            elif reply.data != self.snapshot:
                self.snapshot = reply.data
                self._display(reply.data)
            if self.pending_read is not None:
                target, self.pending_read = self.pending_read, None
                self._read(target)
            elif not self.busy:
                self.listing.setEnabled(True)

        self.api.send(method, path, finished, token=token)

    def _read(self, alert_id):
        if self.token:
            self.listing.setEnabled(False)
            if self.busy:
                self.pending_read = alert_id
            else:
                self._send("POST", f"{PATH}/{alert_id}/read")

    def _badge(self, count):
        self.trigger.setText(f"预算提醒 · {count}" if count else "预算提醒")
        self.trigger.setProperty("unread", bool(count))
        self.trigger.style().unpolish(self.trigger)
        self.trigger.style().polish(self.trigger)

    def _display(self, data):
        count = data["count"]
        self._badge(count)
        page, body = panel("scrollBody", 4)
        if not count:
            body.addWidget(label("暂无未读预算提醒。", "muted"))
        for item in data["items"]:
            over = item["level"] == "over"
            card, lines = panel("rose" if over else "hero")
            lines.addWidget(label(item["ledger_name"], "cardTitle"))
            spent, budget = item["expense"], item["budget"]
            title = (f"已超预算 · 超出 ¥{amount_text(spent - budget)}" if over else
                     f"预算已使用 {spent * 100 // budget}%")
            lines.addWidget(label(title, "section"))
            lines.addWidget(label(f"正式支出 ¥{amount_text(spent)}　预算 ¥{amount_text(budget)}"))
            when = datetime.fromisoformat(item["created_at"]).astimezone()
            lines.addWidget(label(f"触发时间　{when:%Y-%m-%d %H:%M}", "meta"))
            lines.addWidget(button("标为已读", lambda key=item["id"]: self._read(key)))
            body.addWidget(card)
        body.addStretch()
        self.listing.setWidget(page)
