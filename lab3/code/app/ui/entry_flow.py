"""收支页面的导航及请求协调。"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from urllib.parse import urlencode

from app.ui.change_view import change_queue
from app.ui.client import Reply
from app.ui.entry_view import EditSource, EntryEditor, detail_page, list_page, reason_form
from app.ui.forms import confirm_action
from app.ui.receipts import preview_image


@dataclass(frozen=True)
class EntryScreen:
    """保留返回列表时的审核模式与分页位置。"""

    ledger_id: str
    review: bool = False
    offset: int = 0
    changes: bool = False
    return_to: Callable | None = None
    back_label: str = "返回查询结果"

    @property
    def path(self) -> str:
        """当前账本收支接口。"""
        return f"/ledgers/{self.ledger_id}/entries"


class EntryFlow:
    """使用主窗口统一的会话检查、忙碌状态和页面切换。"""

    def __init__(self, window, workspace, request, notice) -> None:
        self.window = window
        self.workspace = workspace
        self.request = request
        self.notice = notice

    def _denied(self, screen: EntryScreen, reply: Reply) -> bool:
        if reply.status in {403, 404}:
            self.window.open_ledger(screen.ledger_id, reply.message, error=True)
            return True
        return False

    def open_list(self, screen: EntryScreen, notice: str = "") -> None:
        """从后端重新读取记录、权限及汇总。"""
        if screen.changes:
            self._open_change_queue(screen)
            return
        parameters = urlencode({"review": str(screen.review).lower(), "offset": screen.offset})

        def show(data, highlighted=None):
            def action(operation: str, value) -> None:
                if operation == "back":
                    self.window.open_ledger(screen.ledger_id)
                elif operation == "new":
                    self.edit(screen, data["ledger"])
                elif operation == "detail":
                    self.open_detail(screen, value)
                elif operation in {"approved", "rejected"}:
                    record = highlighted["detail"]["snapshot"]["record"]
                    self.decide(screen, record, operation, reason=value)
                elif operation == "receipt":
                    record = highlighted["detail"]["snapshot"]["record"]
                    self._preview_receipt(screen, record["id"], value["id"])
                elif operation == "changes":
                    self.open_list(replace(screen, changes=True, offset=0))
                else:
                    self.open_list(replace(screen, offset=value))

            view = list_page(data, screen.review, screen.offset, action, highlighted)
            self.workspace.show_entry_page(view)
            self.window.on_refresh(lambda: self.open_list(screen))
            if notice:
                self.notice(notice)

        def loaded(data):
            items = data["entries"]["items"]
            if screen.review and items:
                self.request("GET", f"{screen.path}/{items[0]['id']}",
                             lambda detail: show(data, detail),
                             recover=lambda reply: self._denied(screen, reply))
            else:
                show(data)

        self.request("GET", f"{screen.path}?{parameters}", loaded,
                     recover=lambda reply: self._denied(screen, reply))

    def _open_change_queue(self, screen: EntryScreen) -> None:
        def action(operation: str, value) -> None:
            if operation == "review":
                self.open_list(replace(screen, changes=False, offset=0))
            elif operation == "detail":
                self.open_detail(screen, value)
            else:
                self.open_list(replace(screen, offset=value))

        def loaded(data: dict) -> None:
            self.workspace.show_entry_page(change_queue(data, screen.offset, action))
            self.window.on_refresh(lambda: self.open_list(screen))

        url = f"/ledgers/{screen.ledger_id}/changes?offset={screen.offset}"
        self.request("GET", url, loaded, recover=lambda reply: self._denied(screen, reply))

    def open_detail(
        self, screen: EntryScreen, entry_id: str, notice: str = "", *, error: bool = False,
    ) -> None:
        """读取更新后的内容、版本和可用操作。"""
        def loaded(data: dict) -> None:
            back = "返回变更待审" if screen.changes else (
                "返回审核中心" if screen.review else "返回收支列表"
            )
            if screen.return_to:
                back = screen.back_label
            view = detail_page(data, back_label=back, action=self._detail_action(screen, data))
            self.workspace.show_entry_page(view)
            self.window.on_refresh(lambda: self.open_detail(screen, entry_id))
            if notice:
                self.notice(notice, error=error)

        self.request("GET", f"{screen.path}/{entry_id}", loaded,
                     recover=lambda reply: self._denied(screen, reply))

    def _detail_action(self, screen: EntryScreen, data: dict):
        detail = data["detail"]
        snapshot = detail["snapshot"]
        record = snapshot["record"]

        def action(operation: str, item=None) -> None:
            if operation == "list":
                if screen.return_to:
                    screen.return_to()
                else:
                    self.open_list(screen)
            elif operation == "refund":
                self.edit(screen, data["ledger"], EditSource(refund=snapshot))
            elif operation in {"edit", "correct"}:
                receipts = detail["receipts"].get(str(record["current_version"]), ())
                source = EditSource(snapshot, tuple(receipts), operation == "correct")
                self.edit(screen, data["ledger"], source)
            elif operation == "receipt":
                self._preview_receipt(screen, record["id"], item["id"])
            elif operation == "void":
                self._request_void(screen, record)
            elif operation.startswith("change."):
                self._review_change(screen, item, operation.removeprefix("change."))
            else:
                self.decide(screen, record, operation)

        return action

    def _preview_receipt(self, screen: EntryScreen, entry_id: str, receipt_id: str) -> None:
        def loaded(data: dict) -> None:
            try:
                preview_image(self.window, data)
            except ValueError:
                self.notice("无法预览这张凭证。", error=True)

        path = f"{screen.path}/{entry_id}/receipts/{receipt_id}"
        self.request("GET", path, loaded, recover=lambda reply: self._denied(screen, reply))

    def edit(self, screen: EntryScreen, ledger: dict, source: EditSource = EditSource()) -> None:
        """加载可参与此次收支的在册成员，再打开编辑页。"""
        snapshot = source.snapshot

        def show(members: list[dict], overview: dict) -> None:
            if source.correction:
                members = _correction_members(members, snapshot["content"])
            editor = EntryEditor(members, {**ledger, "overview": overview}, source)
            editor.submitted.connect(lambda body: self._save(screen, snapshot, body))
            if snapshot:
                editor.cancelled.connect(
                    lambda: self.open_detail(screen, snapshot["record"]["id"])
                )
            else:
                editor.cancelled.connect(lambda: self.open_list(screen))
            self.workspace.show_entry_page(editor)
            self.window.on_refresh(lambda: self.notice("表单仍在编辑，可继续填写或提交。"))

        def loaded(members):
            self.request("GET", f"/ledgers/{screen.ledger_id}/budget",
                         lambda overview: show(members, overview),
                         recover=lambda reply: self._denied(screen, reply))

        path = f"/ledgers/{screen.ledger_id}/members"
        if source.correction:
            path += "?include_exited=true"
        self.request("GET", path, loaded,
                     recover=lambda reply: self._denied(screen, reply))

    def _save(self, screen: EntryScreen, snapshot: dict | None, body: dict) -> None:
        entry_id = snapshot["record"]["id"] if snapshot else None
        path = f"{screen.path}/{entry_id}" if entry_id else screen.path
        correction = body.get("kind") == "correct"
        if correction:
            path += "/changes"

        def recover(reply: Reply) -> bool:
            if reply.code == "possible_duplicate":
                reason = reason_form(self.window, "确认相似支出", reply.message)
                if reason:
                    self._save(screen, snapshot, {**body, "duplicate_reason": reason})
                return True
            if reply.status == 409 and entry_id:
                self.open_detail(screen, entry_id, reply.message, error=True)
                return True
            return self._denied(screen, reply)

        def saved(result: dict) -> None:
            if correction:
                self.open_detail(screen, result["entry_id"], "更正申请已提交，原账仍有效。")
            else:
                self.open_detail(screen, result["record"]["id"], "已保存，等待审核。")

        method = "PATCH" if snapshot and not correction else "POST"
        self.request(method, path, saved, body=body, recover=recover)

    def _request_void(self, screen: EntryScreen, record: dict) -> None:
        reason = reason_form(self.window, "申请作废", "说明为何需要作废这笔账")
        if reason is not None:
            body = {"kind": "void", "revision": record["revision"], "reason": reason}
            self._post_change(screen, record["id"], "/changes", body, "作废申请已提交，原账仍有效。")

    def _review_change(self, screen: EntryScreen, item: dict, result: str) -> None:
        if result == "approved":
            if not confirm_action(self.window, "通过申请", "通过后将更新正式记录与汇总。"):
                return
            reason = ""
            message = "变更已通过，正式记录与汇总已更新。"
        else:
            reason = reason_form(self.window, "驳回申请", "填写驳回申请的原因")
            if reason is None:
                return
            message = "申请已驳回，原账仍有效。"
        self._post_change(screen, item["entry_id"], f"/changes/{item['id']}/review",
                          {"result": result, "reason": reason}, message)

    def _post_change(self, screen: EntryScreen, entry_id: str, path: str,
                     body: dict, message: str) -> None:
        def recover(reply: Reply) -> bool:
            if reply.status == 409:
                self.open_detail(screen, entry_id, reply.message, error=True)
                return True
            return self._denied(screen, reply)

        self.request("POST", f"{screen.path}/{entry_id}{path}",
                     lambda _: self.open_detail(screen, entry_id, message),
                     body=body, recover=recover)

    def decide(self, screen: EntryScreen, record: dict, operation: str, *, reason=None) -> None:
        """审核、驳回和撤回均携带当前页面的并发版本。"""
        path = f"{screen.path}/{record['id']}"
        body = {"revision": record["revision"]}
        if operation == "approved":
            if not confirm_action(self.window, "审核通过", "通过后这笔账将计入正式汇总。"):
                return
        else:
            title = "驳回记录" if operation == "rejected" else "撤回记录"
            if reason is None:
                reason = reason_form(self.window, title, "填写处理原因")
            if reason is None:
                return
        if reason:
            body["reason"] = reason
        if operation == "withdrawn":
            path += "/withdraw"
        else:
            path += "/review"
            body["result"] = operation

        def recover(reply: Reply) -> bool:
            if reply.status == 409:
                self.open_detail(screen, record["id"], reply.message, error=True)
                return True
            return self._denied(screen, reply)

        message = {"approved": "已审核通过。", "rejected": "记录已驳回。", "withdrawn": "记录已撤回。"}
        self.request("POST", path, lambda _: self.open_detail(
            screen, record["id"], message[operation],
        ), body=body, recover=recover)


def _correction_members(members: list[dict], content: dict) -> list[dict]:
    """仅把已关联的历史成员带入更正表单。"""
    previous = {content["payer_member_id"], *(part["member_id"] for part in content["splits"])}
    return [member if member["status"] == "active" else
            {**member, "name": member["name"] + "（已退出）"} for member in members
            if member["status"] == "active" or member["id"] in previous]
