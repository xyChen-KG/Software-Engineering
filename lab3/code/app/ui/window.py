"""桌面端页面导航、请求状态及登录会话。"""

from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget,
)

from app.config import Settings
from app.ui.budget_inbox import BudgetInbox
from app.ui.client import ApiClient, Reply
from app.ui.entry_flow import EntryFlow, EntryScreen
from app.ui.entry_view import reason_form
from app.ui.forms import AuthPage, confirm_action, ledger_form, role_form
from app.ui.ledger_view import Workspace
from app.ui.report_flow import ReportFlow
from app.ui.theme import STYLE, button, label


class MainWindow(QMainWindow):
    """每次账本操作都调用服务端，退出时丢弃未完成请求的界面回调。"""

    connection_checked = Signal(bool)
    budget_session = Signal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle("小兔账本 · 多人共享记账")
        self.setMinimumSize(360, 520)
        self.setMaximumWidth(480)
        available = self.screen().availableGeometry().height()
        self.resize(420, min(840, available - 48))
        self.setStyleSheet(STYLE)
        self._api = ApiClient(settings.api_url, self)
        inbox = BudgetInbox(self, self._api)
        self.budget_session.connect(inbox.session)
        self._token = ""
        self._generation = 0
        self._refresh = self.check_connection
        self._expiry = QTimer(self)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(self._expire_session)  # pylint: disable=no-member

        root = QWidget()
        content = QVBoxLayout(root)
        content.setContentsMargins(4, 3, 4, 2)
        content.setSpacing(2)
        header = QHBoxLayout()
        header.setContentsMargins(9, 0, 6, 0)
        header.addWidget(label("✦  小兔账本", "meta"), 1)
        self._connection = label("正在连接记账服务…", "meta")
        header.addWidget(inbox.trigger)
        header.addWidget(self._connection)
        self._retry = button("刷新", self._reload)
        self._retry.setFixedHeight(23)
        self._retry.setStyleSheet("padding: 0 6px; min-height: 16px; font-size: 12px;")
        header.addWidget(self._retry)
        content.addLayout(header)
        self._message = label(style="notice")
        self._message.hide()
        content.addWidget(self._message)
        self._auth = AuthPage()
        self._workspace = Workspace()
        entries = EntryFlow(self, self._workspace, self._request, self._notice)
        self._reports = ReportFlow(self, self._workspace, self._request, entries)
        self._auth.submitted.connect(self._submit_auth)
        self._workspace.action.connect(self._workspace_action)
        self._pages = QStackedWidget()
        self._pages.addWidget(self._auth)
        self._pages.addWidget(self._workspace)
        content.addWidget(self._pages, 1)
        self.setCentralWidget(root)
        QTimer.singleShot(0, self.check_connection)

    def _notice(self, text: str, *, error: bool = False) -> None:
        self._message.setObjectName("error" if error else "notice")
        self._message.style().unpolish(self._message)
        self._message.style().polish(self._message)
        self._message.setText(text)
        self._message.setVisible(bool(text))

    def show_notice(self, text, *, error=False):
        """从业务页面提供操作反馈。"""
        self._notice(text, error=error)

    def after_leave(self):
        """退出后清理当前账本。"""
        self._workspace.show_books([])
        self._books("已退出账本。")

    def _set_busy(self, busy: bool) -> None:
        self._pages.setEnabled(not busy)
        self._retry.setEnabled(not busy)

    @property
    def _busy(self) -> bool:
        return not self._pages.isEnabled()

    def _reload(self) -> None:
        self._refresh()

    def on_refresh(self, callback: Callable) -> None:
        """让窗口刷新按钮重新载入当前页面。"""
        self._refresh = callback

    def _request(
        self, method: str, path: str, finished: Callable, *, body: dict | None = None,
        recover: Callable[[Reply], bool] | None = None,
    ) -> None:
        protected = path not in {"/auth/login", "/auth/register"}
        if self._busy or (protected and not self._token):
            return
        self._set_busy(True)
        self._notice("")
        generation = self._generation

        def done(reply: Reply) -> None:
            if generation != self._generation:
                return
            self._set_busy(False)
            if reply.status == 0:
                self._connection.setText("记账服务未连接")
            elif reply.status < 500:
                self._connection.setText("记账服务已连接")
            if reply.ok:
                try:
                    finished(reply.data)
                except (KeyError, TypeError, ValueError):
                    self._notice("服务响应格式异常，请刷新后重试。", error=True)
            elif protected and reply.status == 401:
                self._expire_session()
            elif recover is None or not recover(reply):
                self._notice(reply.message, error=True)

        self._api.send(method, path, done, token=self._token if protected else "", body=body)

    def check_connection(self) -> None:
        """保留原有启动检查入口，健康检查无需登录。"""
        if self._busy:
            return
        self._set_busy(True)
        self._connection.setText("正在连接记账服务…")

        def done(reply: Reply) -> None:
            self._set_busy(False)
            success = reply.ok and isinstance(reply.data, dict) and reply.data.get("status") == "ok"
            self._connection.setText("记账服务已连接" if success else "记账服务未连接")
            if success:
                self._notice("")
            else:
                self._notice(reply.message or "服务未就绪，请刷新后重试。", error=True)
            self.connection_checked.emit(success)

        self._api.send("GET", "/health", done)

    def _submit_auth(self, mode: str, body: dict) -> None:
        required = ("username", "password")
        if mode == "register":
            required += ("name",)
        if not all(body[field] for field in required):
            self._notice("请填写所有登录或注册字段。", error=True)
            return
        if mode == "register":
            if len(body["password"]) < 15:
                self._notice("密码至少需要 15 个字符。", error=True)
                return
            self._request("POST", "/auth/register", self._registered, body=body)
        else:
            self._request("POST", "/auth/login", self._logged_in, body=body)

    def _registered(self, profile: dict) -> None:
        self._auth.reset(username=profile["username"])
        self._notice("账号已创建，请输入密码登录。")

    def _logged_in(self, data: dict) -> None:
        token = data["access_token"]
        profile = data["user"]
        expiry = datetime.fromisoformat(data["expires_at"])
        remaining = (expiry - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            self._expire_session()
            return
        self._workspace.show_user(profile["name"], profile["username"])
        self._token = token
        self.budget_session.emit(token)
        self._generation += 1
        self._expiry.start(min(2_000_000_000, max(1, int(remaining * 1000))))
        self._auth.reset()
        self._workspace.show_books([])
        self._pages.setCurrentWidget(self._workspace)
        self._books()

    def _end_session(self, notice: str, *, expired: bool = False) -> None:
        self._generation += 1
        self._token = ""
        self._expiry.stop()
        self.budget_session.emit("")
        self._set_busy(False)
        self._workspace.clear()
        self._auth.reset()
        self._refresh = self.check_connection
        self._pages.setCurrentWidget(self._auth)
        self._notice(notice, error=expired)

    def _expire_session(self) -> None:
        # 关闭依赖旧会话的弹窗，防止过期后仍展示原账本内容。
        modal = QApplication.activeModalWidget()
        if modal is not None and modal.parent() is self:
            modal.close()
        self._end_session("会话已失效，请重新登录。", expired=True)

    def _workspace_action(self, operation: str, item) -> None:
        if self._busy or not self._token:
            return
        direct = {
            "books": self._books, "inbox": self._inbox,
            "logout": self._logout,
        }
        if operation in direct:
            direct[operation]()
            return
        if operation == "create":
            self._create(item)
        elif operation == "open":
            self.open_ledger(item["id"])
        elif operation == "members":
            self.open_ledger(item["id"], members_page=True)
        elif operation == "profile":
            self._refresh = self._profile
            self._profile()
        elif operation == "accept":
            self._accept(item)
        elif operation in {"search", "settlement", "logs"}:
            reports = {"search": self._reports.search, "settlement": self._reports.settlement,
                       "logs": self._reports.logs}
            reports[operation](item)
        elif operation == "recent":
            ledger, entry_id = item
            screen = EntryScreen(ledger["id"], return_to=lambda: self.open_ledger(ledger["id"]),
                                 back_label="返回账本")
            self._reports.entries.open_detail(screen, entry_id)
        elif operation.startswith("entry."):
            self._entry_action(operation, item)
        elif operation in {"settings", "invite", "archive", "leave"}:
            self._ledger_action(operation, item)
        else:
            self._member_action(operation, *item)

    def _entry_action(self, operation: str, ledger: dict) -> None:
        screen = EntryScreen(ledger["id"], review=operation == "entry.review")
        if operation == "entry.new":
            self._reports.entries.edit(screen, ledger)
        else:
            self._reports.entries.open_list(screen)

    def _profile(self):
        self._request("GET", "/auth/me", lambda _: self._workspace.show_profile())

    def _books(self, notice: str = "", *, error: bool = False) -> None:
        self._refresh = self._books

        def loaded(data: list[dict]) -> None:
            self._workspace.show_books(data)
            self._notice(notice, error=error)

        self._request("GET", "/ledgers", loaded)

    def _inbox(self) -> None:
        self._refresh = self._inbox
        self._request("GET", "/invitations", self._workspace.show_inbox)

    def _logout(self) -> None:
        self._request("POST", "/auth/logout", lambda _: self._end_session("已退出登录。"))

    def _create(self, body: dict) -> None:
        self._request(
            "POST", "/ledgers", lambda result: self.open_ledger(result["id"], "账本已创建。"),
            body=body,
        )

    def _accept(self, item: dict) -> None:
        self._request("POST", f"/invitations/{item['id']}/accept",
                      lambda result: self.open_ledger(result["ledger_id"], "已加入账本。"))

    def open_ledger(self, ledger_id: str, notice: str = "", *, error=False, members_page=False):
        """打开账本。权限变更时回到账本列表。"""
        path = f"/ledgers/{ledger_id}"

        def denied(reply: Reply) -> bool:
            if reply.status in {403, 404}:
                self._workspace.show_books([])
                self._books(reply.message, error=True)
                return True
            return False

        def found(ledger: dict) -> None:
            def show(members: list[dict]) -> None:
                self._refresh = lambda: self.open_ledger(ledger_id, members_page=members_page)
                ledger["members"] = members
                def ready(budget):
                    ledger["overview"] = budget
                    if members_page:
                        self._workspace.show_members(ledger)
                    else:
                        self._workspace.show_ledger(ledger, members)
                    self._notice(notice, error=error)

                self._request("GET", path + "/overview", ready, recover=denied)
            self._request("GET", path + "/members?include_exited=true", show, recover=denied)

        self._request("GET", path, found, recover=denied)

    def _mutate(
        self, ledger: dict, method: str, suffix: str, notice: str, *,
        body: dict | None = None,
    ) -> None:
        def role_changed(reply: Reply) -> bool:
            if reply.status == 403:
                self._workspace.show_books([])
                self._refresh = lambda: self.open_ledger(ledger["id"])
                self.open_ledger(ledger["id"], reply.message, error=True)
                return True
            return False

        self._request(
            method, f"/ledgers/{ledger['id']}{suffix}",
            lambda _: self.open_ledger(ledger["id"], notice, members_page=(
                suffix.startswith("/members/") or suffix == "/invitations")),
            body=body, recover=role_changed,
        )

    def _ledger_action(self, operation: str, ledger: dict) -> None:
        if operation == "settings":
            body = ledger_form(self, ledger)
            if body is not None:
                self._mutate(ledger, "PATCH", "", "账本设置已保存。", body=body)
        elif operation == "invite":
            body = role_form(self)
            if body is not None:
                self._mutate(ledger, "POST", "/invitations", "邀请已发出，请对方登录后接受。", body=body)
        elif operation == "archive":
            archived = not ledger["archived"]
            title = "归档账本" if archived else "恢复账本"
            if confirm_action(self, title, f"确认{title}「{ledger['name']}」吗？"):
                self._mutate(ledger, "PATCH", "", title + "成功。", body={"archived": archived})
        elif operation == "leave":
            self._reports.exit_preview(ledger)

    def _member_action(self, operation: str, ledger: dict, member: dict) -> None:
        path = f"/members/{member['id']}"
        if operation == "role":
            body = role_form(self, current=member["role"])
            if body is not None:
                self._mutate(ledger, "PATCH", path, "成员角色已更新。", body=body)
        elif operation == "transfer":
            message = f"把账本转交给「{member['name']}」后，你将成为普通成员。"
            reason = reason_form(self, "转交负责人", "请填写交接原因", description=message)
            if reason:
                self._mutate(ledger, "POST", "/transfer-owner", "负责人已转交。",
                             body={"member_id": member["id"], "reason": reason})
        elif confirm_action(self, "移除成员", f"确认移除「{member['name']}」？历史记录将保留。"):
            self._mutate(ledger, "DELETE", path, "成员已移除。")
