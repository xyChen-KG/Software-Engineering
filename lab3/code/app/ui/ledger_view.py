"""竖屏账本列表、首页、成员页和底部导航。"""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from app.ui.forms import ROLES, ledger_fields
from app.ui.notebook import (
    allow_row_clicks, avatar, book_banner, page_section, record_row, row_button,
)
from app.ui.paper import Illustration, PictureButton
from app.ui.report_view import budget_panel, metrics
from app.ui.statement_view import remaining_text
from app.ui.theme import button, label, page_heading, panel, scroll_area


class Workspace(QWidget):
    """上部随页面滚动，五个主入口固定在窗口底部。"""

    action = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.account = ("", "")
        self.current_ledger = None
        self.page = None
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        self.tabs = {}
        footer = QWidget()
        footer.setObjectName("tabBar")
        navigation = QHBoxLayout(footer)
        navigation.setContentsMargins(2, 2, 2, 2)
        navigation.setSpacing(1)
        for text, key, icon, caption in (
            ("我的账本", "books", "nav-books", "账本"), ("收支列表", "entry.list", "nav-list", "明细"),
            ("记一笔", "entry.new", "nav-add", "记一笔"), ("查询与统计", "search", "nav-stats", "统计"),
            ("我的", "profile", "nav-me", "我的"),
        ):
            tab = button(text, lambda route=key: self._navigate(route), "tab")
            tab.setStyleSheet("QPushButton#tab {color: transparent;}")
            tab.setCheckable(True)
            tab.setFixedHeight(54)
            column = QVBoxLayout(tab)
            column.setContentsMargins(0, 0, 0, 1)
            column.setSpacing(0)
            column.addWidget(Illustration(icon, 30, align="center"))
            column.addWidget(label(caption, "meta"), 0, Qt.AlignmentFlag.AlignHCenter)
            allow_row_clicks(tab)
            self.tabs[key] = tab
            navigation.addWidget(tab, 1)
        self.body.addWidget(footer)

    def _navigate(self, key):
        self.action.emit(key, self.current_ledger)

    def show_user(self, name, username):
        """记录当前会话的展示身份。"""
        self.account = (name, username)

    def clear(self):
        """释放旧会话的账本内容。"""
        self.account = ("", "")
        self.current_ledger = None
        self._show(QWidget(), "books")

    def _show(self, content, active):
        if self.page is not None:
            self.body.removeWidget(self.page)
            self.page.hide()
            self.page.deleteLater()
        self.page = scroll_area(content)
        self.body.insertWidget(0, self.page, 1)
        for key, tab in self.tabs.items():
            tab.setChecked(key == active)
            has_book = self.current_ledger is not None
            tab.setEnabled(key in {"books", "profile"} or (has_book and
                           (key != "entry.new" or not self.current_ledger["archived"])))
            picture = tab.findChild(Illustration)
            if key in {"books", "profile", "search"}:
                base = {"books": "nav-books", "profile": "nav-me", "search": "nav-stats"}[key]
                picture.asset = base + "-on" if key == active else base
                picture.update()

    def show_profile(self):
        """集中呈现当前身份、邀请与退出登录。"""
        page, body = page_heading("我的")
        card, line = panel("sky")
        line.addWidget(label(self.account[0], "section"))
        line.addWidget(label("@" + self.account[1], "muted"))
        body.addWidget(card)
        body.addWidget(button("收到的邀请", lambda: self.action.emit("inbox", None)))
        if self.current_ledger:
            book = self.current_ledger
            body.addWidget(book_banner(book))
            body.addWidget(button("成员管理", lambda: self.action.emit("members", book)))
            body.addWidget(button("结算清单", lambda: self.action.emit("settlement", book)))
            body.addWidget(button("操作日志", lambda: self.action.emit("logs", book)))
        body.addWidget(button("退出登录", lambda: self.action.emit("logout", None), "danger"))
        body.addStretch()
        self._show(page, "profile")

    def show_books(self, ledgers):
        """双列账本封面，每本都可打开真实账本。"""
        self.current_ledger = None
        page, body = page_heading("我的账本")
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, book in enumerate(ledgers):
            grid.addWidget(self._cover(book, index), index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid)
        if not ledgers:
            body.addWidget(label("还没有账本", "section"))
        create = self._create_card()
        create.setVisible(not ledgers)

        def focus_form():
            create.show()
            self.page.ensureWidgetVisible(create)
            create.findChild(QLineEdit, "ledgerName").setFocus()

        body.addWidget(PictureButton("＋ 新建账本", "new-book", 70, focus_form))
        body.addWidget(create)
        body.addWidget(button("收到的邀请", lambda: self.action.emit("inbox", None), "nav"))
        body.addStretch()
        self._show(page, "books")

    def _create_card(self):
        card, body = panel(margin=12)
        body.addWidget(Illustration("head-create", 58, "创建账本"))
        form = QFormLayout()
        collect = ledger_fields(form, {})
        body.addLayout(form)
        error = label(style="error")
        error.hide()

        def submit():
            try:
                data = collect()
            except ValueError as exc:
                error.setText(str(exc))
                error.show()
                return
            self.action.emit("create", data)

        body.addWidget(error)
        body.addWidget(label("创建后可邀请成员", "meta"))
        body.addWidget(PictureButton("创建账本", "create-ink", 51, submit))
        return card

    def _cover(self, ledger, index):
        card, line = row_button("打开账本", lambda: self.action.emit("open", ledger))
        card.setAccessibleName("打开账本 · " + ledger["name"])
        card.setStyleSheet("QPushButton#line {color:transparent; background:"
                           + ("#eaf5ff" if index % 2 == 0 else "#f5effc")
                           + "; border:1px solid #d6dfde; border-radius:13px;}")
        stack = QVBoxLayout()
        stack.setSpacing(3)
        stack.addWidget(label(ledger["name"], "coverTitle"))
        stack.addWidget(label(ROLES[ledger["role"]], "role"))
        stack.addWidget(label("已归档" if ledger["archived"] else "进行中", "meta"))
        stack.addWidget(Illustration(("cover-picnic", "cover-camp")[index % 2], 76, align="center"))
        line.addLayout(stack)
        allow_row_clicks(card)
        return card

    def show_inbox(self, invitations):
        """可核对账本、邀请人和有效期。"""
        page, body = page_heading("收到的邀请")
        if not invitations:
            body.addWidget(label("目前没有待处理的邀请。", "muted"))
        for item in invitations:
            card, line = panel()
            line.addWidget(label(item["ledger_name"], "section"))
            expires = datetime.fromisoformat(item["expires_at"]).astimezone()
            line.addWidget(label(f"{item['inviter_name']} 邀请你成为{ROLES[item['role']]} · "
                                 f"{expires:%Y-%m-%d %H:%M} 到期"))
            line.addWidget(button("接受邀请", lambda invite=item: self.action.emit("accept", invite),
                                  "primary"))
            body.addWidget(card)
        body.addStretch()
        self._show(page, "profile")

    def show_ledger(self, ledger, members):
        """按原型从上至下呈现概况、预算、四个动作、最近记录和成员余额。"""
        self.current_ledger = ledger
        page, body = panel("scrollBody", 8)
        body.setSpacing(7)

        def settings():
            self.action.emit("settings", ledger)

        edit_settings = settings if ledger["role"] == "owner" else None
        body.addWidget(book_banner(ledger, settings=edit_settings))
        body.addWidget(page_section("section-overview", "账本概况"))
        info = ledger["overview"]
        body.addLayout(metrics([("总收入", info["income"]), ("总支出", info["expense"]),
                                ("活动结余", info["income"] - info["expense"])]))
        body.addWidget(budget_panel(info))
        body.addLayout(self._actions(ledger))
        card, recent = panel(margin=7)
        recent.addWidget(page_section("section-recent", "最近记录"))
        if not info["recent"]:
            recent.addWidget(label("还没有收支记录", "muted"))
        for item in info["recent"]:
            recent.addWidget(record_row(item, lambda key=item["id"]:
                                        self.action.emit("recent", (ledger, key))))
        body.addWidget(card)
        body.addWidget(self._balances(ledger, members))
        body.addLayout(self._settings(ledger))
        body.addStretch()
        self._show(page, "books")

    def _actions(self, ledger):
        row = QHBoxLayout()
        row.setSpacing(5)
        for caption, key, asset in (("记一笔", "entry.new", "pencil-tile"),
                                    ("审核中心", "entry.review", "review-tile"),
                                    ("成员管理", "members", "members-tile"),
                                    ("结算清单", "settlement", "settle-tile")):
            control = PictureButton(caption, asset, 65,
                                    lambda route=key: self.action.emit(route, ledger))
            control.setEnabled(not (key == "entry.new" and ledger["archived"])
                               and (key != "entry.review" or ledger["role"] != "member"))
            row.addWidget(control, 1)
        return row

    def _settings(self, ledger):
        row = QHBoxLayout()
        if ledger["role"] == "owner":
            caption = "恢复账本" if ledger["archived"] else "归档账本"
            row.addWidget(button(caption, lambda: self.action.emit("archive", ledger)))
        else:
            row.addWidget(button("退出账本", lambda: self.action.emit("leave", ledger), "danger"))
        row.addWidget(button("操作日志", lambda: self.action.emit("logs", ledger)))
        return row

    def _balances(self, ledger, members):
        card, body = panel(margin=6)
        body.addWidget(page_section("section-balances", "成员结算"))
        active = sum(m["status"] == "active" for m in members)
        body.addWidget(label(f"账本成员 · {active} 人", "meta"))
        grid = QGridLayout()
        grid.setSpacing(4)
        for index, member in enumerate(members):
            tile, column = panel(("sky", "rose", "lavender")[index % 3], 5)
            column.addWidget(avatar(member, 32))
            column.addWidget(label(member["name"], "cardTitle"))
            column.addWidget(label(ROLES[member["role"]] if member["status"] == "active"
                                   else "已退出", "meta"))
            column.addWidget(label(remaining_text(ledger["overview"]["balances"][member["id"]])))
            grid.addWidget(tile, index // 3, index % 3)
        body.addLayout(grid)
        return card

    def show_members(self, ledger):
        """账本成员独立页面，每项管理操作仍由服务端再次鉴权。"""
        self.current_ledger = ledger
        page, body = page_heading("账本成员")
        body.addWidget(book_banner(ledger, art="basket"))
        body.addWidget(page_section("section-members", "成员列表"))
        for member in ledger["members"]:
            body.addWidget(self._member_card(ledger, member))
        if ledger["role"] == "owner" and not ledger["archived"]:
            body.addWidget(PictureButton("邀请成员", "invite-ink", 50,
                                         lambda: self.action.emit("invite", ledger)))
        roles, lines = panel(margin=8)
        lines.addWidget(page_section("section-roles", "角色权限说明"))
        for role, permissions in (("owner", "管理成员，审核他人记录"),
                                  ("reviewer", "登记和查询收支，审核他人记录"),
                                  ("member", "登记和查询收支，申请变更")):
            row = QHBoxLayout()
            row.addWidget(avatar({"role": role}, 23))
            row.addWidget(label(ROLES[role], "role"))
            row.addWidget(label(permissions), 1)
            lines.addLayout(row)
        lines.addWidget(label("所有角色均不能自审。", "meta"))
        body.addWidget(roles)
        body.addWidget(button("返回账本", lambda: self.action.emit("open", ledger), "nav"))
        body.addStretch()
        self._show(page, "profile")

    def _member_card(self, ledger, member):
        card, body = panel(margin=7)
        row = QHBoxLayout()
        row.addWidget(avatar(member))
        row.addWidget(label(member["name"], "cardTitle"), 1)
        row.addWidget(label(ROLES[member["role"]] if member["status"] == "active"
                            else "已退出", "role"))
        body.addLayout(row)
        if ledger["role"] == "owner" and member["role"] != "owner" and member["status"] == "active":
            controls = QHBoxLayout()
            for caption, key in (("调整角色", "role"), ("转交负责人", "transfer"), ("移除", "remove")):
                controls.addWidget(button(caption, lambda op=key:
                                          self.action.emit(op, (ledger, member))))
            body.addLayout(controls)
        return card

    def show_entry_page(self, page):
        """当前业务页面使用与原型一致的底栏选中态。"""
        heading = page.findChild(QLabel, "title")
        key = {"记一笔": "entry.new", "登记退款": "entry.new", "查询与统计": "search",
               "操作日志": "profile", "结算清单": "profile"}.get(
                   heading.text() if heading else "", "entry.list")
        self._show(page, key)
