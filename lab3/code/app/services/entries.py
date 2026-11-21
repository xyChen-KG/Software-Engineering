"""登记、修改重提、撤回及审核的业务规则。"""

from dataclasses import dataclass
from uuid import UUID

from app.errors import (
    ConflictError, DuplicateEntryError, NotFoundError, PermissionDeniedError, ValidationError,
)
from app.models import EntryRecord, EntrySnapshot, EntryStatus, EntryType, RecordVersion, Role
from app.models.changes import ChangeDetail
from app.models.entry_workflow import EntryDetail, EntryDraft, EntryPage, Review
from app.models.receipts import ReceiptFile, ReceiptSelection
from app.services.ledgers import LedgerAccess, LedgerService
from app.services.receipts import prepare_receipts, validated_files, verify_replay
from app.storage.store import Repositories

REVIEW_ROLES = {Role.OWNER, Role.REVIEWER}
EDITABLE = {EntryStatus.PENDING, EntryStatus.REJECTED}


@dataclass(frozen=True, kw_only=True)
class Submission:
    """请求标识可安全重试，当内容相同且第一次已提交时返回原记录。"""

    request_id: UUID
    kind: EntryType
    draft: EntryDraft
    original_expense_id: UUID | None = None
    receipts: ReceiptSelection = ReceiptSelection()


@dataclass(frozen=True, kw_only=True)
class EntryChange:
    """每次改动都带上操作者所看到的并发版本。"""

    revision: int
    operation: str
    draft: EntryDraft | None = None
    reason: str = ""
    receipts: ReceiptSelection = ReceiptSelection()


@dataclass(frozen=True, kw_only=True)
class EntryQuery:
    """分页和审核中心筛选条件。"""

    status: EntryStatus | None = None
    limit: int = 30
    offset: int = 0
    review: bool = False


class EntryService:
    """请求在同一事务中校验最新成员身份、业务状态并写入历史。"""

    def __init__(self, ledgers: LedgerService) -> None:
        self.ledgers = ledgers
        self.store = ledgers.store
        self.clock = ledgers.auth.clock

    def submit(self, token: str, ledger_id: UUID, submission: Submission) -> EntrySnapshot:
        """相同提交编号只创建一次；另一笔相似支出需要说明。"""
        draft = submission.draft
        with self.store.transaction() as repositories:
            scope = self.ledgers.scope(repositories, token, ledger_id)
            record = EntryRecord(
                ledger_id=ledger_id, submitter_member_id=scope.membership.id,
                kind=submission.kind, request_id=submission.request_id,
                original_expense_id=submission.original_expense_id, created_at=self.clock(),
            )
            content = draft.version(record, 1, self.clock())
            if repositories.entries.has_request(record.request_id):
                result = repositories.entries.add(record, content)
                files = validated_files(submission.receipts)
                verify_replay(repositories.receipts, result.record, files)
                return result
            self.ensure_open(scope)
            self.check_budget(repositories, scope, record, content)
            self.check_duplicate(repositories, record, content, draft)
            result = repositories.entries.add(record, content)
            files = prepare_receipts(repositories.receipts, record, content, submission.receipts,
                                     initial=True)
            repositories.receipts.link_version(record, 1, files)
            self.ledgers.log(repositories, scope.membership, record.id, "entry.submitted", {
                "amount_cents": content.amount_cents, "purpose": content.purpose,
                "receipt_ids": files,
                "confirmed_duplicate_reason": draft.duplicate_reason.strip(),
            })
            return result

    def change(
        self, token: str, ledger_id: UUID, entry_id: UUID, change: EntryChange,
    ) -> EntrySnapshot:
        """先校验权限，再核对版本和状态，冲突单独留痕。"""
        with self.store.transaction() as repositories:
            scope = self.ledgers.scope(repositories, token, ledger_id)
            self.ensure_open(scope)
            record = self.record_in_ledger(repositories, ledger_id, entry_id)
            self._authorize(scope, record, change.operation)
            valid_states = EDITABLE if change.operation == "edit" else {EntryStatus.PENDING}
            if record.revision == change.revision and record.status in valid_states:
                return self._apply_change(repositories, scope, record, change)
            self.ledgers.log(repositories, scope.membership, record.id, "entry.conflict", {
                "operation": change.operation, "expected_revision": change.revision,
                "current_revision": record.revision, "current_status": record.status.value,
            })
        # 冲突日志已提交，失败响应不会将它一起回滚。
        raise ConflictError("账目状态或版本已变化，请查看最新记录后重试")

    def _apply_change(
        self, repositories: Repositories, scope: LedgerAccess, record: EntryRecord,
        change: EntryChange,
    ) -> EntrySnapshot:
        details = {
            "previous_status": record.status.value, "previous_revision": record.revision,
            "reason": change.reason.strip(),
        }
        if change.operation == "edit":
            if change.draft is None:
                raise ValidationError("请填写更新后的收支内容")
            content = change.draft.version(record, record.current_version + 1, self.clock())
            self.check_budget(repositories, scope, record, content)
            self.check_duplicate(repositories, record, content, change.draft)
            previous = repositories.entries.version(record.id, record.current_version)
            details["before"] = {"amount_cents": previous.amount_cents, "purpose": previous.purpose}
            details["after"] = {"amount_cents": content.amount_cents, "purpose": content.purpose}
            details["confirmed_duplicate_reason"] = change.draft.duplicate_reason.strip()
            result = repositories.entries.append_version(content, expected_revision=record.revision)
            files = prepare_receipts(repositories.receipts, record, content, change.receipts)
            repositories.receipts.link_version(record, content.number, files)
            details["receipt_ids"] = files
            event = "resubmitted" if record.status == EntryStatus.REJECTED else "edited"
        else:
            status = EntryStatus(change.operation)
            if status in {EntryStatus.REJECTED, EntryStatus.WITHDRAWN} and not details["reason"]:
                raise ValidationError("驳回或撤回须填写原因")
            result = repositories.entries.resolve_pending(record, status)
            if status in {EntryStatus.APPROVED, EntryStatus.REJECTED}:
                repositories.entry_workflow.save_review(Review(
                    entry_id=record.id, ledger_id=record.ledger_id,
                    version_number=record.current_version, reviewer_member_id=scope.membership.id,
                    result=status, reason=details["reason"], reviewed_at=self.clock(),
                ))
            if status == EntryStatus.APPROVED and record.kind == EntryType.EXPENSE:
                repositories.budget_alerts.after_change(
                    repositories.reporting, scope.ledger, scope.ledger.budget_cents,
                    result.content.amount_cents, self.clock(),
                )
            event = status.value
        details.update(status=result.record.status.value, revision=result.record.revision,
                       content_version=result.record.current_version)
        self.ledgers.log(repositories, scope.membership, record.id, "entry." + event, details)
        return result

    def page(
        self, token: str, ledger_id: UUID, query: EntryQuery,
    ) -> tuple[LedgerAccess, EntryPage]:
        """在册成员看共享收支，审核中心要求当前审核权限。"""
        with self.store.transaction(write=False) as repositories:
            scope = self.ledgers.scope(repositories, token, ledger_id)
            if query.review and scope.membership.role not in REVIEW_ROLES:
                raise PermissionDeniedError("当前身份没有审核权限")
            status = EntryStatus.PENDING if query.review else query.status
            page = repositories.entry_workflow.list_page(
                ledger_id, status, query.limit, query.offset,
            )
            return scope, page

    def detail(
        self, token: str, ledger_id: UUID, entry_id: UUID,
    ) -> tuple[LedgerAccess, EntryDetail]:
        """读取当前版、历史版及已退出成员的显示姓名。"""
        with self.store.transaction(write=False) as repositories:
            scope = self.ledgers.scope(repositories, token, ledger_id)
            record = self.record_in_ledger(repositories, ledger_id, entry_id)
            members = repositories.ledgers.members(ledger_id, include_exited=True)
            names = {member.id: repositories.users.get(member.user_id).name for member in members}
            return scope, EntryDetail(
                snapshot=repositories.entries.snapshot(entry_id),
                history=repositories.entries.history(entry_id),
                reviews=repositories.entry_workflow.reviews(entry_id),
                member_names=names,
                other_approved_expense=(repositories.reporting.budget(scope.ledger, record.id)
                                        ["expense"] if scope.ledger.budget_cents else 0),
                receipts=repositories.receipts.version_metadata(record),
                changes=tuple(
                    ChangeDetail(request=change,
                                 receipts=repositories.receipts.change_metadata(change.id))
                    for change in repositories.changes.history(entry_id)
                ),
            )

    def get_receipt(
        self, token: str, ledger_id: UUID, entry_id: UUID, receipt_id: UUID,
    ) -> ReceiptFile:
        """当前在册成员可查看该笔记录的版本和申请凭证。"""
        with self.store.transaction(write=False) as repositories:
            self.ledgers.scope(repositories, token, ledger_id)
            record = self.record_in_ledger(repositories, ledger_id, entry_id)
            return repositories.receipts.get(record, receipt_id)

    @staticmethod
    def record_in_ledger(
        repositories: Repositories, ledger_id: UUID, entry_id: UUID,
    ) -> EntryRecord:
        """按账本范围定位记录。"""
        record = repositories.entries.get(entry_id)
        if record.ledger_id != ledger_id:
            raise NotFoundError("此账本中没有这笔收支")
        return record

    @staticmethod
    def ensure_open(scope: LedgerAccess) -> None:
        """归档后的记录仅可查看。"""
        if scope.ledger.archived:
            raise ConflictError("账本已归档，恢复后才能处理收支")

    @staticmethod
    def _authorize(scope: LedgerAccess, record: EntryRecord, operation: str) -> None:
        actor = scope.membership
        own = actor.id == record.submitter_member_id
        if operation in {"edit", "withdrawn"}:
            if not own:
                raise PermissionDeniedError("只有提交人可修改或撤回")
        elif operation in {"approved", "rejected"}:
            if own or actor.role not in REVIEW_ROLES:
                raise PermissionDeniedError("仅负责人或审核员能审核他人账目")
        else:
            raise ValidationError("无效的收支操作")

    @staticmethod
    def check_budget(repositories, scope, record, content):
        """以正式支出加上本笔金额判断，修改记录时排除原账。"""
        limit = scope.ledger.budget_cents
        if limit and record.kind == EntryType.EXPENSE and not content.budget_reason.strip():
            spent = repositories.reporting.budget(scope.ledger, exclude=record.id)["expense"]
            if spent + content.amount_cents > limit:
                yuan, cents = divmod(spent + content.amount_cents - limit, 100)
                raise ValidationError(f"预计超过预算 ¥{yuan}.{cents:02d}，请填写超支原因后提交")

    @staticmethod
    def check_duplicate(
        repositories: Repositories, record: EntryRecord, content: RecordVersion, draft: EntryDraft,
    ) -> None:
        """要求申请人核对类似的其他支出。"""
        if record.kind == EntryType.EXPENSE and not draft.duplicate_reason.strip():
            if repositories.entries.has_similar_expense(content):
                raise DuplicateEntryError("有相似支出，请核对后填写说明再继续")
