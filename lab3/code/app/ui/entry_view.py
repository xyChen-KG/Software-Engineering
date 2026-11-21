"""收支列表、详情和填写表单。"""

from dataclasses import dataclass
from uuid import uuid4

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDateEdit, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app.ui.change_view import change_card
from app.ui.forms import KINDS, STATUSES, FormDialog, amount_text, budget_cents, field
from app.ui.notebook import avatar, book_banner
from app.ui.paper import Illustration
from app.ui.receipts import ReceiptPicker, receipts_card
from app.ui.report_view import metrics
from app.ui.theme import button, label, page_heading, panel, scroll_area

REVIEW_ROLES = {"owner", "reviewer"}
PAGE_SIZE = 30


def _currency(cents: int) -> str:
    return f"¥{amount_text(abs(cents))}" if cents >= 0 else f"−¥{amount_text(-cents)}"


def list_page(data: dict, review: bool, offset: int, action, highlighted=None) -> QWidget:
    """分页显示账目与汇总，审核中心只列待审记录。"""
    ledger, entries = data["ledger"], data["entries"]
    content, layout = page_heading("审核中心" if review else "收支列表")
    layout.addWidget(book_banner(ledger))
    if review:
        summary = entries["summary"]
        layout.addLayout(metrics([("总收入", summary["income_cents"]),
                                  ("总支出", summary["expense_cents"]),
                                  ("结余", summary["balance_cents"])]))
        budget = ledger["budget_cents"]
        layout.addWidget(label("预算  " + (_currency(budget) if budget is not None else "未设置"),
                               "cardTitle"))
        _review_budget(layout, budget, summary["expense_cents"])
        tabs = QHBoxLayout()
        tabs.addWidget(label("待审核", "role"), 1)
        tabs.addWidget(button("变更待审", lambda: action("changes", None)), 1)
        layout.addLayout(tabs)
    else:
        layout.addWidget(_totals_card(entries["summary"]))
        if not ledger["archived"]:
            layout.addWidget(button("记一笔", lambda: action("new", None), "primary"))
    if not entries["items"]:
        layout.addWidget(label("没有待审核记录。" if review else "当前页没有收支记录。", "muted"))
    for index, item in enumerate(entries["items"]):
        if index == 0 and highlighted:
            layout.addWidget(_review_card(highlighted, action))
        else:
            layout.addWidget(_entry_card(item, action))
    layout.addWidget(button("返回账本", lambda: action("back", None), "nav"))
    paging = QHBoxLayout()
    if offset:
        paging.addWidget(button("上一页", lambda: action("page", max(0, offset - PAGE_SIZE))))
    if entries["next_offset"] is not None:
        paging.addWidget(button("下一页", lambda: action("page", entries["next_offset"])))
    paging.addStretch()
    layout.addLayout(paging)
    layout.addStretch()
    return content


def _review_budget(layout, budget, spent):
    if not budget or spent * 100 < budget * 80:
        return
    if spent > budget:
        text = f"已超预算 {_currency(spent - budget)}，审核前请核对本笔支出的原因。"
        style = "rose"
    else:
        text = f"预算已使用 {spent * 100 // budget}%，请核对待审核支出。"
        style = "hero"
    card, body = panel(style, 12)
    body.addWidget(label(text, "cardTitle"))
    layout.addWidget(card)


def _totals_card(summary: dict) -> QWidget:
    card, body = panel("hero")
    totals = QHBoxLayout()
    for title, key in (("正式收入", "income_cents"), ("正式支出", "expense_cents"),
                       ("活动结余", "balance_cents")):
        totals.addWidget(label(f"{title}\n{_currency(summary[key])}", "cardTitle"), 1)
    body.addLayout(totals)
    return card


def _entry_card(item: dict, action) -> QWidget:
    card, body = panel(margin=14)
    row = QHBoxLayout()
    signed = item["amount_cents"] if item["kind"] == "income" else -item["amount_cents"]
    row.addWidget(label(item["purpose"], "cardTitle"), 1)
    row.addWidget(label(_currency(signed), "cardTitle"))
    row.addWidget(button("查看详情", lambda: action("detail", item["id"])))
    body.addLayout(row)
    state = STATUSES[item["status"]]
    text = f"{item['business_date']} · {item['category']} · {state} · {item['submitter']}"
    body.addWidget(label(text, "muted"))
    return card


def _review_card(data, action):
    ledger, detail = data["ledger"], data["detail"]
    record, current = detail["snapshot"]["record"], detail["snapshot"]["content"]
    card, body = panel(margin=12)
    names = detail["member_names"]
    body.addWidget(_entry_heading(record, current, names))
    if current["splits"]:
        body.addWidget(label("分摊　" + "　".join(
            f"{names.get(s['member_id'], '历史成员')} {_currency(s['amount_cents'])}"
            for s in current["splits"])))
    for receipt in detail["receipts"].get(str(current["number"]), []):
        body.addWidget(button(receipt["filename"], lambda item=receipt: action("receipt", item)))
    body.addWidget(button("查看详情", lambda: action("detail", record["id"]), "nav"))
    if (record["status"] == "pending" and not ledger["archived"]
            and ledger["role"] in REVIEW_ROLES
            and ledger["member_id"] != record["submitter_member_id"]):
        _review_controls(body, action)
    elif ledger["member_id"] == record["submitter_member_id"]:
        body.addWidget(label("本人提交的记录须由其他审核人处理。", "role"))
    return card


def _review_controls(body, action):
    body.addWidget(label("审核意见（驳回时必填）", "cardTitle"))
    reason = field("请输入审核意见", "inlineReviewReason", 500)
    body.addWidget(reason)
    error = label("请填写驳回原因。", "error")
    body.addWidget(error)
    error.hide()
    reason.textChanged.connect(error.hide)  # pylint: disable=no-member

    def reject():
        if reason.text().strip():
            action("rejected", reason.text().strip())
        else:
            error.show()

    row = QHBoxLayout()
    row.addWidget(button("驳回", reject, "danger"))
    row.addWidget(button("审核通过", lambda: action("approved", reason.text().strip()), "primary"))
    body.addLayout(row)


def detail_page(data: dict, *, back_label: str, action) -> QWidget:
    """显示当前状态、历史及对这一笔账可做的操作。"""
    ledger, detail = data["ledger"], data["detail"]
    record, current = detail["snapshot"]["record"], detail["snapshot"]["content"]
    names = detail["member_names"]
    content, layout = page_heading("收支详情")
    layout.addWidget(_entry_heading(record, current, names))
    layout.addLayout(_detail_actions(ledger, detail, action))
    _show_budget_warning(layout, ledger, detail, record, current)
    current_receipts = detail["receipts"].get(str(current["number"]))
    if current_receipts:
        layout.addWidget(receipts_card("当前版本凭证", current_receipts,
                                       lambda item: action("receipt", item)))

    if current["splits"]:
        layout.addWidget(_detail_splits(current["splits"], names))
    for change in detail["changes"]:
        layout.addWidget(change_card(change, ledger, names, action))
    if len(detail["history"]) > 1 or detail["reviews"]:
        layout.addWidget(_history_card(detail, names, action))
    layout.addWidget(button(back_label, lambda: action("list"), "nav"))
    layout.addStretch()
    return content


def _detail_splits(splits, names):
    card, body = panel()
    body.addWidget(Illustration("split-heading", 29, "分摊明细"))
    grid = QGridLayout()
    for index, item in enumerate(splits):
        tile, column = panel(("sky", "rose")[index % 2], 6)
        column.addWidget(label(names.get(item["member_id"], "历史成员")))
        column.addWidget(label(_currency(item["amount_cents"]), "cardTitle"))
        grid.addWidget(tile, index // 2, index % 2)
    body.addLayout(grid)
    return card


def _show_budget_warning(layout, ledger, detail, record, current):
    if record["kind"] == "expense" and ledger["budget_cents"]:
        amount = current["amount_cents"] if record["status"] == "pending" else None
        for change in detail["changes"]:
            request = change["request"]
            if request["status"] == "pending" and request["proposal"]:
                amount = request["proposal"]["amount_cents"]
        if amount is not None:
            excess = detail["other_approved_expense"] + amount - ledger["budget_cents"]
            if excess > 0:
                layout.addWidget(label(f"审核通过后预计超预算 {_currency(excess)}", "error"))


def _entry_heading(record: dict, current: dict, names: dict) -> QWidget:
    card, body = panel()
    heading = QHBoxLayout()
    heading.addWidget(Illustration("gift" if record["kind"] == "income" else "meal", 32, width=32))
    heading.addWidget(label(f"{KINDS[record['kind']]} · {current['purpose']}", "section"), 1)
    body.addLayout(heading)
    body.addWidget(label(f"{_currency(current['amount_cents'])}  ·  {STATUSES[record['status']]}",
                         "heroAmount"))
    body.addWidget(label(
        f"{current['business_date']} · {current['category']} · 内容版本 {record['current_version']}",
        "muted",
    ))
    payer = "垫付人" if record["kind"] == "expense" else "收款人"
    body.addWidget(label(f"{payer}  {names.get(current['payer_member_id'], '历史成员')}"))
    body.addWidget(label(f"提交人  {names.get(record['submitter_member_id'], '历史成员')}"))
    identifier = label("记录编号  " + record["id"], "muted")
    identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    body.addWidget(identifier)
    if current["budget_reason"]:
        body.addWidget(label(f"超支原因  {current['budget_reason']}", "error"))
    if record["original_expense_id"]:
        body.addWidget(label(f"退款关联原支出  {record['original_expense_id']}", "muted"))
    if current["no_receipt_reason"]:
        body.addWidget(label(f"无凭证说明  {current['no_receipt_reason']}"))
    return card


def _detail_actions(ledger: dict, detail: dict, action) -> QHBoxLayout:
    actions = QHBoxLayout()
    record = detail["snapshot"]["record"]
    owned = ledger["member_id"] == record["submitter_member_id"]
    pending = record["status"] == "pending"
    if not ledger["archived"]:
        if owned and (pending or record["status"] == "rejected"):
            caption = "修改记录" if pending else "修改并重提"
            actions.addWidget(button(caption, lambda: action("edit"), "primary"))
        if owned and pending:
            actions.addWidget(button("撤回", lambda: action("withdrawn"), "danger"))
        if pending and not owned and ledger["role"] in REVIEW_ROLES:
            actions.addWidget(button("审核通过", lambda: action("approved"), "primary"))
            actions.addWidget(button("驳回", lambda: action("rejected"), "danger"))
        if record["status"] == "approved" and record["kind"] == "expense":
            actions.addWidget(button("登记退款", lambda: action("refund")))
        active_change = any(item["request"]["status"] == "pending" for item in detail["changes"])
        if (record["status"] == "approved" and not active_change
                and (owned or ledger["role"] == "owner")):
            actions.addWidget(button("申请更正", lambda: action("correct"), "primary"))
            actions.addWidget(button("申请作废", lambda: action("void"), "danger"))
    actions.addStretch()
    return actions


def _history_card(detail: dict, names: dict, action) -> QWidget:
    card, body = panel()
    body.addWidget(label("历史版本与审核", "section"))
    reviews = {review["version_number"]: review for review in detail["reviews"]}
    for version in reversed(detail["history"]):
        number = version["number"]
        body.addWidget(label(f"版本 {number} · {version['purpose']} · "
                             f"{_currency(version['amount_cents'])}"))
        if number in reviews:
            review = reviews[number]
            who = names.get(review["reviewer_member_id"], "历史成员")
            line = f"{who} · {STATUSES[review['result']]}"
            if review["reason"]:
                line += f" · {review['reason']}"
            body.addWidget(label(line, "muted"))
        version_receipts = detail["receipts"].get(str(number))
        if version_receipts and number != detail["snapshot"]["record"]["current_version"]:
            body.addWidget(receipts_card(f"版本 {number} 凭证", version_receipts,
                                         lambda item: action("receipt", item)))
    return card


@dataclass(frozen=True)
class EditSource:
    """当前账目来源及凭证可用于修改或申请更正。"""

    snapshot: dict | None = None
    receipts: tuple[dict, ...] = ()
    correction: bool = False
    refund: dict | None = None


@dataclass(frozen=True)
class EditorNotes:
    """分别保存无凭证说明和更正原因。"""

    no_receipt: QLineEdit
    correction: QLineEdit | None
    budget: QLineEdit


class EntryKind(QWidget):
    """原型中的支出与收入切换。"""

    changed = Signal()

    def __init__(self, initial):
        super().__init__()
        self.group = QButtonGroup(self)
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        for value, text in KINDS.items():
            choice = QPushButton(text)
            choice.setObjectName(value)
            choice.setCheckable(True)
            choice.setChecked(initial == value)
            self.group.addButton(choice)
            line.addWidget(choice)
        self.group.buttonClicked.connect(lambda _: self.changed.emit())  # pylint: disable=no-member

    @property
    def value(self):
        """读取所选业务类型。"""
        return self.group.checkedButton().objectName()


class EntryEditor(QWidget):
    """登记或编辑表单，保留失败提交时的内容及请求编号。"""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, members: list[dict], ledger: dict, source: EditSource) -> None:
        super().__init__()
        self.source = source
        self.request_id = str(uuid4())
        snapshot = source.snapshot
        content = snapshot["content"] if snapshot else {}
        record = snapshot["record"] if snapshot else {}
        title = "登记退款" if source.refund else "记一笔"
        if source.correction:
            title = "申请更正"
        elif snapshot:
            title = "修改并重提" if record["status"] == "rejected" else "修改收支"
        body, layout = page_heading(title)
        layout.addWidget(book_banner({**ledger, "members": members}))
        if source.refund:
            original = source.refund["content"]
            layout.addWidget(label(f"原支出  {original['purpose']}  "
                                   f"{_currency(original['amount_cents'])}", "role"))
        layout.addWidget(self._prepare_fields(members, ledger["member_id"], record, content))
        splits_box, self.splits = _splits_panel(members, content)
        splits_box.layout().addWidget(button("按列表成员均分", self._split_evenly))
        layout.addWidget(splits_box)
        self.receipt_picker = ReceiptPicker(source.receipts)
        layout.addWidget(self.receipt_picker)
        self.error = label(style="error")
        self.error.hide()
        layout.addWidget(self.error)
        self._connect_fields(splits_box)
        self._budget_preview(layout, ledger.get("overview", {}), content)
        actions = QHBoxLayout()
        actions.addWidget(button("取消", self.cancelled.emit))
        caption = "提交更正申请" if source.correction else "提交审核"
        actions.addWidget(button(caption, self._submit, "ink"), 2)
        layout.addLayout(actions)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(body)

    def _prepare_fields(self, members: list[dict], payer: str, record: dict, content: dict):
        card, body = panel()
        form = QFormLayout()
        self.kind = EntryKind("income" if self.source.refund else record.get("kind", "expense"))
        self.kind.setObjectName("entryKind")
        self.kind.setEnabled(not record and not self.source.refund)
        self.amount = field("例如 240.00", "entryAmount", 24)
        self.amount.setText(amount_text(content.get("amount_cents")))
        self.category = field("例如 餐饮", "entryCategory", 60)
        self.category.setText(content.get("category", "退款" if self.source.refund else ""))
        self.purpose = field("例如 水果采购", "entryPurpose", 200)
        self.purpose.setText(content.get("purpose", ""))
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setDate(QDate.fromString(content["business_date"], "yyyy-MM-dd")
                          if content else QDate.currentDate())
        self.payer = QComboBox()
        for member in members:
            self.payer.addItem(member["name"], member["id"])
        self.payer.setCurrentIndex(self.payer.findData(content.get("payer_member_id", payer)))
        no_receipt = field("支出未附凭证时必填", "noReceiptReason", 500)
        no_receipt.setText(content.get("no_receipt_reason", ""))
        for text, widget in (
            ("类型", self.kind), ("金额（元）", self.amount), ("类别", self.category),
            ("用途", self.purpose), ("业务日期", self.date), ("垫付人 / 收款人", self.payer),
            ("无凭证说明", no_receipt),
        ):
            form.addRow(text, widget)
        change_reason = None
        if self.source.correction:
            change_reason = field("说明为何需要更正", "changeReason", 500)
            form.addRow("更正原因", change_reason)
        budget = field("本笔预计超支时填写", "budgetReason", 500)
        budget.setText(content.get("budget_reason", ""))
        form.addRow("超支原因", budget)
        self.notes = EditorNotes(no_receipt, change_reason, budget)
        body.addLayout(form)
        return card

    def _split_evenly(self):
        try:
            total = budget_cents(self.amount.text())
        except ValueError:
            total = None
        if not total or not self.splits:
            self.error.setText("请先填写有效支出金额。")
            self.error.show()
            return
        share, extra = divmod(total, len(self.splits))
        for index, widget in enumerate(self.splits.values()):
            cents = share + int(index < extra)
            widget.setText(amount_text(cents) if cents else "")

    def _budget_preview(self, layout, overview, content):
        budget = overview.get("budget")
        if not budget:
            return
        spent = overview["expense"] - (content["amount_cents"] if self.source.correction else 0)
        hint = label(style="error")
        layout.addWidget(hint)

        def update():
            try:
                amount = budget_cents(self.amount.text()) or 0
            except ValueError:
                amount = 0
            excess = spent + amount - budget
            hint.setVisible(self.kind.value == "expense" and amount > 0 and excess > 0)
            if excess > 0:
                hint.setText(f"预计超过预算 {_currency(excess)}，请填写超支原因。")

        self.amount.textChanged.connect(update)  # pylint: disable=no-member
        self.kind.changed.connect(update)  # pylint: disable=no-member
        update()

    def _connect_fields(self, splits_box: QWidget) -> None:
        def update_kind() -> None:
            splits_box.setVisible(self.kind.value == "expense")
            self.error.hide()

        notes = (self.notes.no_receipt, self.notes.correction)
        for control in (self.amount, self.purpose, self.category, *notes, *self.splits.values()):
            if control is not None:
                control.textChanged.connect(self.error.hide)  # pylint: disable=no-member
        self.payer.currentIndexChanged.connect(self.error.hide)  # pylint: disable=no-member
        self.kind.changed.connect(update_kind)  # pylint: disable=no-member
        update_kind()

    def _collect(self) -> dict:
        amount = budget_cents(self.amount.text())
        if not amount:
            raise ValueError("金额须大于零。")
        purpose, category = self.purpose.text().strip(), self.category.text().strip()
        if not purpose or not category or not self.payer.currentData():
            raise ValueError("请填写用途、类别并选择垫付人或收款人。")
        parts = []
        reason = self.notes.no_receipt.text().strip()
        if self.kind.value == "expense":
            if not reason and not self.receipt_picker.has_receipts:
                raise ValueError("请填写无凭证说明。")
            parts = [{"member_id": member_id, "amount_cents": budget_cents(widget.text())}
                     for member_id, widget in self.splits.items() if widget.text().strip()]
            if sum(item["amount_cents"] for item in parts) != amount:
                raise ValueError("分摊合计必须等于支出金额。")
        data = {
            "amount_cents": amount, "category": category, "purpose": purpose,
            "business_date": self.date.date().toString("yyyy-MM-dd"),
            "payer_member_id": self.payer.currentData(), "splits": parts,
            "no_receipt_reason": reason, "budget_reason": self.notes.budget.text().strip(),
        }
        if snapshot := self.source.snapshot:
            data["revision"] = snapshot["record"]["revision"]
        else:
            data.update(kind=self.kind.value, request_id=self.request_id)
        if self.notes.correction is not None:
            change_reason = self.notes.correction.text().strip()
            if not change_reason:
                raise ValueError("请填写更正原因。")
            data.update(kind="correct", reason=change_reason)
        if self.source.refund:
            data["original_expense_id"] = self.source.refund["record"]["id"]
        data.update(self.receipt_picker.payload())
        return data

    def _submit(self) -> None:
        try:
            data = self._collect()
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
        else:
            self.error.hide()
            self.submitted.emit(data)


def _splits_panel(members: list[dict], content: dict) -> tuple[QWidget, dict[str, QLineEdit]]:
    box, body = panel()
    body.addWidget(Illustration("split-heading", 28, "分摊 · 元"))
    body.addWidget(label("参与分摊的成员填写金额，其余留空。", "muted"))
    group = QWidget()
    fields = QGridLayout(group)
    old_amounts = {part["member_id"]: part["amount_cents"] for part in content.get("splits", [])}
    splits = {}
    for position, member in enumerate(members):
        widget = field("留空不参与", f"entrySplit{position}", 24)
        widget.setText(amount_text(old_amounts.get(member["id"])))
        splits[member["id"]] = widget
        tile, stack = panel(("sky", "rose", "lavender")[position % 3], 5)
        line = QHBoxLayout()
        line.addWidget(avatar(member, 24))
        line.addWidget(label(member["name"]), 1)
        stack.addLayout(line)
        stack.addWidget(widget)
        fields.addWidget(tile, position // 2, position % 2)
    area = scroll_area(group)
    area.setFixedHeight(min(200, 22 + ((len(members) + 1) // 2) * 83))
    body.addWidget(area)
    return box, splits


def reason_form(parent: QWidget, title: str, hint: str, *, description="") -> str | None:
    """驳回、撤回和确认相似支出都必须填写具体说明。"""
    reason = field(hint, "entryActionReason", 500)

    def collect():
        value = reason.text().strip()
        if not value:
            raise ValueError("请填写说明。")
        return value

    dialog = FormDialog(parent, title, "确认提交", collect)
    if description:
        dialog.fields.addRow(label(description))
    dialog.fields.addRow("说明", reason)
    reason.setFocus()
    result = dialog.payload if dialog.exec() else None
    dialog.deleteLater()
    return result
