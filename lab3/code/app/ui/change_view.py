"""审核中心的变更队列及原收支详情内的申请记录。"""

from PySide6.QtWidgets import QHBoxLayout, QWidget

from app.ui.forms import amount_text
from app.ui.receipts import receipts_card
from app.ui.theme import button, label, page_heading, panel

CHANGE_LABELS = {"correct": "更正", "void": "作废"}
STATES = {"pending": "待审核", "approved": "已通过", "rejected": "已驳回"}


def change_queue(data: dict, offset: int, action) -> QWidget:
    """从独立队列进入申请对应的收支详情。"""
    content, layout = page_heading("变更待审")
    layout.addWidget(button("返回审核中心", lambda: action("review", None), "nav"))
    if not data["items"]:
        layout.addWidget(label("没有待审核的更正或作废申请。", "muted"))
    for item in data["items"]:
        card, body = panel()
        body.addWidget(label(f"{CHANGE_LABELS[item['kind']]} · {item['purpose']}", "cardTitle"))
        body.addWidget(label(f"{item['applicant_name']} · {item['reason']}", "muted"))
        row = QHBoxLayout()
        row.addWidget(button("查看申请", lambda entry=item["entry_id"]: action("detail", entry)))
        row.addStretch()
        body.addLayout(row)
        layout.addWidget(card)
    paging = QHBoxLayout()
    if offset:
        paging.addWidget(button("上一页", lambda: action("page", max(0, offset - 30))))
    if data["next_offset"] is not None:
        paging.addWidget(button("下一页", lambda: action("page", data["next_offset"])))
    paging.addStretch()
    layout.addLayout(paging)
    layout.addStretch()
    return content


def change_card(change: dict, ledger: dict, names: dict, action) -> QWidget:
    """展示拟改内容、依据、凭证和审核决定。"""
    request = change["request"]
    status = request["status"]
    card, body = panel()
    body.addWidget(label(f"{CHANGE_LABELS[request['kind']]}申请 · {STATES[status]}", "section"))
    person = names.get(request["applicant_member_id"], "历史成员")
    body.addWidget(label(f"{person} · 基于内容版本 {request['base_version']}", "muted"))
    body.addWidget(label(f"申请原因  {request['reason']}"))
    proposal = request["proposal"]
    if proposal:
        body.addWidget(label(
            f"拟改为 ¥{amount_text(proposal['amount_cents'])} · {proposal['category']} · "
            f"{proposal['business_date']}", "cardTitle",
        ))
        body.addWidget(label(f"用途  {proposal['purpose']}"))
        body.addWidget(label(
            f"垫付或收款人  {names.get(proposal['payer_member_id'], '历史成员')}"
        ))
        for split in proposal["splits"]:
            participant = names.get(split["member_id"], "历史成员")
            body.addWidget(label(f"分摊  {participant} ¥{amount_text(split['amount_cents'])}"))
        if proposal["budget_reason"]:
            body.addWidget(label(f"超支原因  {proposal['budget_reason']}", "error"))
        if proposal["no_receipt_reason"]:
            body.addWidget(label(f"无凭证说明  {proposal['no_receipt_reason']}"))
    if change["receipts"]:
        body.addWidget(receipts_card("申请凭证", change["receipts"],
                                     lambda item: action("receipt", item)))
    if status != "pending":
        reviewer = names.get(request["reviewer_member_id"], "历史成员")
        body.addWidget(label(f"{reviewer} · {STATES[status]}", "muted"))
        if request["review_reason"]:
            body.addWidget(label(f"审核说明  {request['review_reason']}"))
    elif _can_decide(ledger, request):
        row = QHBoxLayout()
        row.addWidget(button("通过申请", lambda: action("change.approved", request), "primary"))
        row.addWidget(button("驳回申请", lambda: action("change.rejected", request), "danger"))
        row.addStretch()
        body.addLayout(row)
    return card


def _can_decide(ledger: dict, change: dict) -> bool:
    return (not ledger["archived"] and ledger["role"] in {"owner", "reviewer"}
            and ledger["member_id"] != change["applicant_member_id"])
