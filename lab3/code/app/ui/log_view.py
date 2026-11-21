"""按时间、经办人、对象编号和类型查阅操作历史。"""

from datetime import date, datetime, time
from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout

from app.ui.forms import ROLES, STATUSES, field
from app.ui.notebook import avatar, book_banner
from app.ui.report_view import combo, money, paging
from app.ui.theme import button, label, page_heading, panel

ACTIONS = {
    "ledger.created": "创建账本", "ledger.updated": "修改账本", "ledger.owner_transferred": "转交负责人",
    "membership.joined": "成员加入", "membership.role_changed": "调整角色",
    "membership.removed": "移除成员", "membership.left": "成员退出",
    "invitation.created": "邀请成员", "invitation.revoked": "撤销邀请",
    "entry.submitted": "提交收支", "entry.edited": "修改收支", "entry.resubmitted": "重新提交",
    "entry.approved": "审核通过", "entry.rejected": "驳回记录", "entry.withdrawn": "撤回记录",
    "entry.conflict": "账目冲突", "change.submitted": "变更申请", "change.approved": "通过申请",
    "change.rejected": "驳回申请", "change.conflict": "申请冲突", "settlement.recorded": "线下结清",
}


def log_filters(names, params, apply):
    """界面按本地日期选择时间范围，接口采用带时区时间。"""
    card, box = panel(margin=14)
    grid = QGridLayout()
    actor = combo("全部成员", names.items(), params.get("actor_member_id"))
    event = combo("全部操作", ACTIONS.items(), params.get("action"))
    target = field("记录编号", "objectId", 36)
    target.setText(params.get("object_id", ""))
    start = field("起始 YYYY-MM-DD", "logStart", 10)
    end = field("截止 YYYY-MM-DD", "logEnd", 10)
    start.setText(params.get("start", "")[:10])
    end.setText(params.get("end", "")[:10])
    error = label("", "error")
    error.hide()

    def submit():
        try:
            filters = {"actor_member_id": actor.currentData(), "action": event.currentData()}
            if target.text().strip():
                filters["object_id"] = str(UUID(target.text().strip()))
            for key, control, hour in (("start", start, time.min), ("end", end, time.max)):
                if control.text():
                    day = datetime.combine(date.fromisoformat(control.text()), hour)
                    filters[key] = day.astimezone().isoformat()
            if start.text() and end.text() and start.text() > end.text():
                raise ValueError("日期顺序")
            apply({k: v for k, v in filters.items() if v})
        except ValueError:
            error.setText("请检查起止日期和记录编号。")
            error.show()

    grid.addWidget(target, 0, 0, 1, 2)
    grid.addWidget(button("查询日志", submit, "primary"), 0, 2)
    grid.addWidget(actor, 1, 0)
    grid.addWidget(event, 1, 1, 1, 2)
    grid.addWidget(start, 2, 0)
    grid.addWidget(end, 2, 1, 1, 2)
    box.addLayout(grid)
    box.addWidget(error)
    return card


def _summary(item, names):
    details = item["details"]
    if item["action"] == "membership.role_changed":
        yield (f"{names.get(item['object_id'], '历史成员')}　"
               f"{ROLES[details['before']]} → {ROLES[details['after']]}")
    elif item["action"] == "ledger.owner_transferred":
        yield (f"{names.get(details['previous_owner'], '历史成员')} → "
               f"{names.get(details['new_owner'], '历史成员')}")
    elif item["action"] == "settlement.recorded":
        yield (f"{names.get(details['payer'], '历史成员')} → "
               f"{names.get(details['payee'], '历史成员')}　{money(details['amount_cents'])}")
    elif "amount_cents" in details:
        yield f"金额　{money(details['amount_cents'])}"
    previous, current = details.get("before"), details.get("after")
    if isinstance(previous, dict) and isinstance(current, dict):
        if "amount_cents" in previous and "amount_cents" in current:
            yield f"金额　{money(previous['amount_cents'])} → {money(current['amount_cents'])}"
    if details.get("previous_status") in STATUSES and details.get("status") in STATUSES:
        yield f"{STATUSES[details['previous_status']]} → {STATUSES[details['status']]}"


def _event_card(item, members, names, action):
    card, box = panel(margin=12)
    when = datetime.fromisoformat(item["created_at"]).astimezone()
    event_name = ACTIONS.get(item["action"], item["action"])
    box.addWidget(label(f"●  {when:%m月%d日 %H:%M}", "meta"))
    header = QHBoxLayout()
    member = members.get(item["actor_member_id"])
    if member:
        header.addWidget(avatar(member, 30))
    header.addWidget(label(f"经办人  {names.get(item['actor_member_id'], '历史成员')}"), 1)
    header.addWidget(label(event_name, "role"))
    box.addLayout(header)
    details = item["details"]
    for line in _summary(item, names):
        box.addWidget(label(line))
    reason = details.get("review_reason") or details.get("reason") or details.get("purpose")
    if reason:
        box.addWidget(label(f"原因　{reason}" if details.get("reason") else str(reason)))
    target = label("对象编号  " + item["object_id"], "muted")
    target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.addWidget(target)
    box.addWidget(button("查看操作内容", lambda event=item: action("details", event)))
    return card


def log_page(ledger, params, items, action):
    """从最新操作开始展示原因和记录编号。"""
    page, body = page_heading("操作日志")
    members = {m["id"]: m for m in ledger["members"]}
    names = {key: m["name"] for key, m in members.items()}

    def reload_page(values):
        action("query", values)

    body.addWidget(book_banner(ledger))
    body.addWidget(log_filters(names, params, reload_page))
    for item in items[:30]:
        body.addWidget(_event_card(item, members, names, action))
    if not items:
        body.addWidget(label("没有符合条件的操作。"))
    next_offset = params.get("offset", 0) + 30 if len(items) > 30 else None
    body.addLayout(paging(params, {"next_offset": next_offset}, reload_page))
    body.addWidget(button("返回账本", lambda: action("back", {}), "nav"))
    body.addStretch()
    return page
